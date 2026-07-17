"""Cancellation of spoken turns (G4c — VOICE_STREAMING §7).

One idempotent operation with three legs, always in this order:

1. **Generation** — every cancel handle in the GenerationRegistry fires
   (a streaming adapter registers its subprocess kill there; pending deltas
   were never truth, so nothing else needs cleanup).
2. **Queue** — every unfinished VoiceRequest flips to `cancelled` with the
   frozen `voice.speak.cancelled` event (VoiceQueue.cancel_turn).
3. **Playback** — the engine's current player process is stopped. Queue
   before playback on purpose: when the player dies, its row is already
   `cancelled`, so the broker's failure path is a no-op instead of marking
   a barged-in chunk `failed`.

Only the broker/engine ever touch audio — cancellation never spawns a
second speaker path (ADR-005).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from dan.logging import get_logger
from dan.store.db import close_quietly
from dan.store.event_store import create_event_store
from dan.voice.queue import VoiceQueue


_LOGGER = get_logger("voice.cancellation")


class GenerationRegistry:
    """Kill handles for in-flight brain generations, keyed by turn id.

    A streaming adapter registers how to terminate its subprocess; barge-in
    cancels everything registered. Thread-safe; cancelling an empty registry
    is a no-op by design (idempotency of the whole operation).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._handles: dict[str, Callable[[], None]] = {}
        self._cancelling = False

    def register(self, turn_id: str, cancel: Callable[[], None]) -> None:
        with self._lock:
            if self._cancelling:
                # Cancellation in progress: immediately invoke cancel to prevent
                # any new generation from starting (FIX-09: tombstone race).
                cancel()
                return
            self._handles[str(turn_id)] = cancel

    def unregister(self, turn_id: str) -> None:
        with self._lock:
            self._handles.pop(str(turn_id), None)

    def active_count(self) -> int:
        with self._lock:
            return len(self._handles)

    def cancel_all(self) -> list[str]:
        """Fire every registered cancel handle; return the turn ids cancelled.

        The coordinator tombstones exactly these turn ids, so a generation with
        no queue rows yet is still blocked from enqueuing a late delta (FIX-09)."""
        with self._lock:
            self._cancelling = True
            handles = list(self._handles.items())
            self._handles.clear()
        cancelled: list[str] = []
        for turn_id, cancel in handles:
            try:
                cancel()
            except Exception:  # noqa: BLE001 — a dead process must not stop the sweep
                _LOGGER.exception("generation cancel handle for turn %s raised.", turn_id)
            cancelled.append(turn_id)
        with self._lock:
            self._cancelling = False
        return cancelled


class CancellationCoordinator:
    def __init__(
        self,
        connection_factory: Callable[[], Any],
        *,
        generation_registry: GenerationRegistry,
        engine: Any,
    ) -> None:
        self._connect = connection_factory
        self._registry = generation_registry
        self._engine = engine

    def cancel_active_speech(
        self,
        *,
        reason: str,
        source: str | None = None,
    ) -> dict[str, Any]:
        generation_turn_ids = self._registry.cancel_all()
        # Tombstone the still-generating turns FIRST, before the cancel sweep:
        # a generation dying from the SIGTERM above can emit one last delta, and
        # this closes the window in which it could enqueue a fresh row (FIX-09).
        self._tombstone_turns(set(generation_turn_ids))
        queue_cancelled, queue_turn_ids, queue_speech_ids = self._cancel_queued(
            reason=reason,
            interruption_source=source,
        )
        # Tombstone the queue turns too (a filler timer is not tied to a live
        # generation), so the UNION of everything cancelled refuses late rows.
        tombstoned = self._tombstone_turns(
            set(generation_turn_ids) | set(queue_turn_ids)
        )
        playback_stopped = self._stop_playback()
        cancelled_speech_id = queue_speech_ids[0] if queue_speech_ids else None
        previous_turn_id = queue_turn_ids[0] if queue_turn_ids else None
        if source is None:
            normalized_source = "voice"
        else:
            normalized_source = str(source).strip() or "voice"
        result = {
            "reason": reason,
            "cancellation_reason": reason,
            "interruption_reason": reason,
            "interrupted_previous_response": queue_cancelled > 0,
            "cancelled_speech_id": cancelled_speech_id,
            "previous_turn_id": previous_turn_id,
            "new_turn_source": "PTT" if (source or "") == "ptt" else normalized_source,
            "generation_cancelled": len(generation_turn_ids),
            "queue_cancelled": queue_cancelled,
            "playback_stopped": playback_stopped,
            "tombstoned_turns": tombstoned,
        }
        if len(generation_turn_ids) or queue_cancelled or playback_stopped or tombstoned:
            _LOGGER.info("cancelled active speech: %s", result)
        return result

    # -- legs ------------------------------------------------------------------

    def _cancel_queued(
        self, *, reason: str, interruption_source: str | None
    ) -> tuple[int, list[str], list[str]]:
        conn = self._connect()
        try:
            queue = VoiceQueue(conn, event_store=create_event_store(conn))
            rows = [
                (str(row[0]), str(row[1]) if row[1] is not None else None)
                for row in conn.execute(
                    "SELECT id, turn_id FROM voice_queue "
                    "WHERE status IN ('queued', 'speaking') AND turn_id IS NOT NULL "
                    "ORDER BY rowid DESC"
                ).fetchall()
            ]
            queue_turn_ids: list[str] = []
            for row_turn_id in [turn_id for (_, turn_id) in rows if turn_id is not None]:
                if row_turn_id not in queue_turn_ids:
                    queue_turn_ids.append(row_turn_id)
            cancelled_request_ids: list[str] = []
            for turn_id in queue_turn_ids:
                cancelled_request_ids_for_turn: list[str] = []
                queue.cancel_turn(
                    turn_id,
                    reason=reason,
                    interruption_source=interruption_source,
                    cancelled_request_ids=cancelled_request_ids_for_turn,
                )
                cancelled_request_ids.extend(cancelled_request_ids_for_turn)
            return len(cancelled_request_ids), queue_turn_ids, cancelled_request_ids
        finally:
            close_quietly(conn)

    def _tombstone_turns(self, turn_ids: set[str]) -> int:
        if not turn_ids:
            return 0
        conn = self._connect()
        try:
            return VoiceQueue(conn).tombstone_turns(turn_ids)
        finally:
            close_quietly(conn)

    def _stop_playback(self) -> bool:
        stop = getattr(self._engine, "stop_playback", None)
        if not callable(stop):
            return False
        try:
            stop()
        except Exception:  # noqa: BLE001 — a stuck player must not break the cancel
            _LOGGER.exception("engine stop_playback raised.")
            return False
        return True


__all__ = ["CancellationCoordinator", "GenerationRegistry"]
