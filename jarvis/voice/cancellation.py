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

from jarvis.logging import get_logger
from jarvis.store.db import close_quietly
from jarvis.store.event_store import create_event_store
from jarvis.voice.queue import VoiceQueue


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

    def register(self, turn_id: str, cancel: Callable[[], None]) -> None:
        with self._lock:
            self._handles[str(turn_id)] = cancel

    def unregister(self, turn_id: str) -> None:
        with self._lock:
            self._handles.pop(str(turn_id), None)

    def active_count(self) -> int:
        with self._lock:
            return len(self._handles)

    def cancel_all(self) -> int:
        with self._lock:
            handles = list(self._handles.items())
            self._handles.clear()
        for turn_id, cancel in handles:
            try:
                cancel()
            except Exception:  # noqa: BLE001 — a dead process must not stop the sweep
                _LOGGER.exception("generation cancel handle for turn %s raised.", turn_id)
        return len(handles)


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

    def cancel_active_speech(self, *, reason: str) -> dict[str, Any]:
        generation_cancelled = self._registry.cancel_all()
        queue_cancelled = self._cancel_queued()
        playback_stopped = self._stop_playback()
        result = {
            "reason": reason,
            "generation_cancelled": generation_cancelled,
            "queue_cancelled": queue_cancelled,
            "playback_stopped": playback_stopped,
        }
        if generation_cancelled or queue_cancelled or playback_stopped:
            _LOGGER.info("cancelled active speech: %s", result)
        return result

    # -- legs ------------------------------------------------------------------

    def _cancel_queued(self) -> int:
        conn = self._connect()
        try:
            queue = VoiceQueue(conn, event_store=create_event_store(conn))
            turn_ids = [
                str(row[0])
                for row in conn.execute(
                    "SELECT DISTINCT turn_id FROM voice_queue "
                    "WHERE status IN ('queued', 'speaking') AND turn_id IS NOT NULL"
                ).fetchall()
            ]
            return sum(queue.cancel_turn(turn_id) for turn_id in turn_ids)
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
