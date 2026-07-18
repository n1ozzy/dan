"""Cancellation of spoken turns (G4c — VOICE_STREAMING §7).

One idempotent operation with three legs, always in this order:

1. **Generation** — every cancel handle in the GenerationRegistry fires
   (a streaming adapter registers its subprocess kill there; pending deltas
   were never truth, so nothing else needs cleanup).
2. **Queue** — every unfinished VoiceRequest flips to `cancelled` with the
   frozen `voice.speak.cancelled` event (VoiceQueue.cancel_turn).
3. **Playback** — the broker that owns the native player is stopped. Queue
   before playback on purpose: when the player dies, its row is already
   `cancelled`, so the broker's failure path is a no-op instead of marking
   a barged-in chunk `failed`.

Only the broker touches the player — cancellation never creates a second
speaker path (ADR-005).
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from typing import Any

from dan.logging import get_logger
from dan.store.db import close_quietly
from dan.store.event_store import create_event_store
from dan.voice.queue import VoiceQueue

_LOGGER = get_logger("voice.cancellation")


class GenerationRegistration:
    __slots__ = ("_generation_id", "_turn_id")

    def __init__(self, turn_id: str) -> None:
        self._turn_id = turn_id
        self._generation_id = uuid.uuid4().hex


class _GenerationCancellationEpoch:
    __slots__ = ("closed", "turn_ids")

    def __init__(self, turn_ids: list[str]) -> None:
        self.turn_ids = dict.fromkeys(turn_ids)
        self.closed = False


class GenerationRegistry:
    """Kill handles for in-flight brain generations, keyed by turn id.

    A streaming adapter registers how to terminate its subprocess; barge-in
    cancels everything registered. Thread-safe; cancelling an empty registry
    is a no-op by design (idempotency of the whole operation).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._handles: dict[
            str, tuple[GenerationRegistration, Callable[[], None]]
        ] = {}
        self._cancellation_epoch: _GenerationCancellationEpoch | None = None

    def register(
        self, turn_id: str, cancel: Callable[[], None]
    ) -> GenerationRegistration:
        normalized_turn_id = str(turn_id)
        registration = GenerationRegistration(normalized_turn_id)
        cancel_now = False
        with self._condition:
            epoch = self._cancellation_epoch
            if epoch is not None:
                epoch.turn_ids.setdefault(normalized_turn_id, None)
                cancel_now = True
            else:
                self._handles[normalized_turn_id] = (registration, cancel)
        if cancel_now:
            try:
                cancel()
            except Exception:  # noqa: BLE001 — a dead process already satisfies cancel
                _LOGGER.exception(
                    "generation cancel handle for turn %s raised.", normalized_turn_id
                )
        return registration

    def unregister(self, registration: GenerationRegistration) -> None:
        if not isinstance(registration, GenerationRegistration):
            raise TypeError("unregister requires a GenerationRegistration token")
        with self._condition:
            current = self._handles.get(registration._turn_id)
            if current is not None and current[0] is registration:
                self._handles.pop(registration._turn_id, None)

    def active_count(self) -> int:
        with self._condition:
            return len(self._handles)

    def cancel_all(self) -> list[str]:
        """Fire every registered cancel handle; return the turn ids cancelled.

        The coordinator tombstones exactly these turn ids, so a generation with
        no queue rows yet is still blocked from enqueuing a late delta (FIX-09)."""
        epoch = self.begin_cancellation()
        return self.finish_cancellation(epoch)

    def begin_cancellation(self) -> _GenerationCancellationEpoch:
        with self._condition:
            while self._cancellation_epoch is not None:
                self._condition.wait()
            handles = list(self._handles.items())
            self._handles.clear()
            epoch = _GenerationCancellationEpoch([turn_id for turn_id, _ in handles])
            self._cancellation_epoch = epoch
        for turn_id, (_, cancel) in handles:
            try:
                cancel()
            except Exception:  # noqa: BLE001 — a dead process must not stop the sweep
                _LOGGER.exception("generation cancel handle for turn %s raised.", turn_id)
        return epoch

    def finish_cancellation(
        self, epoch: _GenerationCancellationEpoch
    ) -> list[str]:
        with self._condition:
            if epoch.closed:
                return list(epoch.turn_ids)
            if self._cancellation_epoch is not epoch:
                raise RuntimeError("generation cancellation epoch is not active")
            epoch.closed = True
            self._cancellation_epoch = None
            turn_ids = list(epoch.turn_ids)
            self._condition.notify_all()
            return turn_ids


class CancellationCoordinator:
    def __init__(
        self,
        connection_factory: Callable[[], Any],
        *,
        generation_registry: GenerationRegistry,
        playback_owner: Any,
    ) -> None:
        self._connect = connection_factory
        self._registry = generation_registry
        self._playback_owner = playback_owner

    def cancel_active_speech(
        self,
        *,
        reason: str,
        source: str | None = None,
    ) -> dict[str, Any]:
        epoch = self._registry.begin_cancellation()
        try:
            (
                queue_cancelled,
                queue_turn_ids,
                queue_speech_ids,
                generation_turn_ids,
                tombstoned,
            ) = self._cancel_queued(
                reason=reason,
                interruption_source=source,
                capture_generation_turn_ids=lambda: self._registry.finish_cancellation(
                    epoch
                ),
            )
        finally:
            self._registry.finish_cancellation(epoch)
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
        self,
        *,
        reason: str,
        interruption_source: str | None,
        capture_generation_turn_ids: Callable[[], list[str]],
    ) -> tuple[int, list[str], list[str], list[str], int]:
        conn = self._connect()
        try:
            queue = VoiceQueue(conn, event_store=create_event_store(conn))
            (
                cancelled_request_ids,
                queue_turn_ids,
                generation_turn_ids,
                tombstoned,
            ) = queue.cancel_active(
                reason=reason,
                interruption_source=interruption_source,
                capture_generation_turn_ids=capture_generation_turn_ids,
            )
            return (
                len(cancelled_request_ids),
                queue_turn_ids,
                cancelled_request_ids,
                generation_turn_ids,
                tombstoned,
            )
        finally:
            close_quietly(conn)

    def _stop_playback(self) -> bool:
        stop = getattr(self._playback_owner, "stop_playback", None)
        if not callable(stop):
            return False
        try:
            stop()
        except Exception:  # noqa: BLE001 — a stuck player must not break the cancel
            _LOGGER.exception("playback owner stop_playback raised.")
            return False
        return True


__all__ = [
    "CancellationCoordinator",
    "GenerationRegistration",
    "GenerationRegistry",
]
