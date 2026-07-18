"""The only queue consumer and audio-player caller in DAN."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from dan.logging import get_logger
from dan.store.db import close_quietly
from dan.store.event_store import create_event_store
from dan.voice.models import VoiceRequest
from dan.voice.queue import VoiceQueue
from dan.voice.tts import PlaybackCancelled, SynthesizedChunk

_LOGGER = get_logger("voice.broker")
DEFAULT_POLL_INTERVAL_SECONDS = 0.05
INTERRUPT_WATCH_INTERVAL_SECONDS = 0.01


class VoiceBrokerError(RuntimeError):
    """Base error for broker lifecycle ownership failures."""


class VoiceBrokerOwnershipError(VoiceBrokerError):
    """Raised when startup would create a second synthesis or playback owner."""


class VoiceBrokerShutdownTimeout(VoiceBrokerError):
    """Raised when broker or synthesis ownership cannot be quiesced in time."""


class VoiceBroker:
    def __init__(
        self,
        connection_factory: Callable[[], Any],
        *,
        engine: Any,
        player: Any,
        config: Any | None = None,
        poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not callable(getattr(engine, "synthesize", None)):
            raise TypeError("VoiceBroker requires a snapshot-only TTS engine")
        if not callable(getattr(player, "play", None)) or not callable(
            getattr(player, "stop", None)
        ):
            raise TypeError("VoiceBroker requires one AudioPlayer owner")
        self._connect = connection_factory
        self._engine = engine
        self._player = player
        self._poll_interval = poll_interval
        self._monotonic = monotonic
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._thread: threading.Thread | None = None
        self._drain_lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._ownership_condition = threading.Condition()
        self._active_syntheses = 0
        self._synthesis_futures: set[Future[SynthesizedChunk]] = set()
        self._interrupt_watchers: dict[threading.Thread, threading.Event] = {}
        self._ownership_blocked = False
        self._executor: ThreadPoolExecutor | None = self._new_executor()
        self._executor_stopped = False

    @staticmethod
    def _new_executor() -> ThreadPoolExecutor:
        return ThreadPoolExecutor(max_workers=1, thread_name_prefix="dan-tts")

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._ownership_blocked:
                raise VoiceBrokerOwnershipError(
                    "previous broker shutdown did not prove synthesis quiescence"
                )
            if self._thread is not None and self._thread.is_alive():
                return
            with self._ownership_condition:
                if (
                    self._active_syntheses
                    or self._synthesis_futures
                    or self._interrupt_watchers
                ):
                    self._ownership_blocked = True
                    raise VoiceBrokerOwnershipError(
                        "synthesis or interrupt watcher owner is still live; "
                        "refusing a second broker"
                    )
                if self._executor is None or self._executor_stopped:
                    self._executor = self._new_executor()
                    self._executor_stopped = False
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="dan-voice-broker",
                daemon=True,
            )
            self._thread.start()

    def stop(self, *, join_timeout: float = 5.0) -> None:
        timeout = max(0.0, float(join_timeout))
        deadline = self._monotonic() + timeout
        with self._lifecycle_lock:
            self._stop.set()
            with self._ownership_condition:
                for future in tuple(self._synthesis_futures):
                    future.cancel()
                for watcher_stop in tuple(self._interrupt_watchers.values()):
                    watcher_stop.set()
            try:
                self.stop_playback()
            except Exception as exc:
                self._ownership_blocked = True
                raise VoiceBrokerError(
                    "native player owner failed to stop"
                ) from exc
            thread = self._thread
            if thread is not None and thread.is_alive():
                thread.join(timeout=max(0.0, deadline - self._monotonic()))

            with self._ownership_condition:
                while (
                    self._active_syntheses
                    or self._synthesis_futures
                    or self._interrupt_watchers
                ):
                    remaining = deadline - self._monotonic()
                    if remaining <= 0:
                        break
                    self._ownership_condition.wait(timeout=remaining)
                thread_alive = self._thread is not None and self._thread.is_alive()
                quiescent = (
                    not thread_alive
                    and self._active_syntheses == 0
                    and not self._synthesis_futures
                    and not self._interrupt_watchers
                )
                if not quiescent:
                    self._ownership_blocked = True
                    _LOGGER.warning(
                        "Voice broker shutdown timed out; retaining broker, "
                        "executor, synthesis, and watcher ownership."
                    )
                    raise VoiceBrokerShutdownTimeout(
                        "voice broker did not reach synthesis quiescence within "
                        f"{timeout:g} seconds"
                    )

                executor = self._executor
                self._thread = None
                self._ownership_blocked = False
            if executor is not None:
                executor.shutdown(wait=True, cancel_futures=True)
            self._executor_stopped = True

    def pause(self) -> None:
        """Stop claiming new queue rows; the active chunk plays to its end."""

        self._paused.set()

    def resume(self) -> None:
        self._paused.clear()

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    def stop_playback(self) -> None:
        self._player.stop()

    def _run(self) -> None:
        try:
            self._with_queue(lambda queue: queue.recover_orphans())
            backoff = self._poll_interval
            while not self._stop.is_set():
                try:
                    played = self.drain_all(recover=False)
                except Exception:
                    _LOGGER.exception("Voice broker drain failed; retrying after backoff.")
                    self._stop.wait(backoff)
                    backoff = min(backoff * 2, 5.0)
                    continue
                backoff = self._poll_interval
                if played == 0:
                    self._stop.wait(self._poll_interval)
        finally:
            with self._ownership_condition:
                self._ownership_condition.notify_all()

    def drain_all(self, *, recover: bool = True) -> int:
        with self._drain_lock:
            if recover:
                self._with_queue(lambda queue: queue.recover_orphans())
            return self._drain_claimed()

    def _drain_claimed(self) -> int:
        played = 0
        if self._stop.is_set():
            return played
        current = self._claim()
        prefetched: Future[SynthesizedChunk] | None = None
        while current is not None:
            try:
                if self._stop.is_set():
                    return played
                try:
                    chunk = (
                        prefetched.result()
                        if prefetched is not None
                        else self._synthesize(current)
                    )
                except Exception as exc:
                    self._mark_failed(current, str(exc))
                    current = self._claim()
                    prefetched = None
                    continue

                self._mark_synthesis_complete(current)
                if not self._is_synthesizing(current):
                    current = self._claim()
                    prefetched = None
                    continue

                if self._stop.is_set():
                    return played
                next_request = self._claim()
                prefetched = (
                    self._submit_synthesis(next_request)
                    if next_request is not None
                    else None
                )

                if self._stop.is_set():
                    # A synthesis finished after stop() must not start playing.
                    return played

                watcher = self._start_interrupt_watcher(current)
                try:
                    if self._stop.is_set():
                        return played
                    playback_request = current
                    self._player.play(
                        chunk,
                        # The player re-checks the predicate after on_started
                        # (row already 'speaking'), so it must stay true for
                        # the whole active playback cycle and turn false only
                        # after barge-in/cancel ('cancelled'/'failed').
                        should_play=lambda request=playback_request: (
                            not self._stop.is_set() and self._is_playable(request)
                        ),
                        on_started=lambda request=playback_request: (
                            self._mark_playback_started(request)
                        ),
                    )
                except PlaybackCancelled:
                    # A pre-schedule skip may leave the row 'synthesizing';
                    # close it out so it never hangs until the next restart.
                    if self._is_active(current):
                        self._with_queue(
                            lambda queue: queue.cancel_request(
                                current.id,
                                reason="playback cancelled before schedule",
                            )
                        )
                except Exception as exc:
                    if self._is_active(current):
                        self._mark_failed(current, f"playback failed: {exc}")
                else:
                    if self._is_speaking(current):
                        self._mark_done(current)
                        played += 1
                finally:
                    self._stop_interrupt_watcher(watcher)
                current = next_request
            except Exception:
                _LOGGER.exception("Voice broker failed request %s", current.id)
                if self._is_active(current):
                    self._mark_failed(current, "broker loop failed")
                current = self._claim()
                prefetched = None
            if self._stop.is_set():
                return played
        return played

    def _synthesize(self, request: VoiceRequest) -> SynthesizedChunk:
        with self._ownership_condition:
            self._active_syntheses += 1
        try:
            snapshot = request.render_snapshot
            if snapshot is None:
                raise RuntimeError("legacy-unresolved voice request is not playable")
            return self._engine.synthesize(request.text, snapshot)
        finally:
            with self._ownership_condition:
                self._active_syntheses = max(0, self._active_syntheses - 1)
                self._ownership_condition.notify_all()

    def _submit_synthesis(
        self,
        request: VoiceRequest,
    ) -> Future[SynthesizedChunk] | None:
        with self._ownership_condition:
            if self._stop.is_set():
                return None
            executor = self._executor
            if executor is None:
                raise VoiceBrokerOwnershipError("synthesis executor is not available")
            future = executor.submit(self._synthesize, request)
            self._synthesis_futures.add(future)
            future.add_done_callback(self._synthesis_finished)
            return future

    def _synthesis_finished(self, future: Future[SynthesizedChunk]) -> None:
        with self._ownership_condition:
            self._synthesis_futures.discard(future)
            self._ownership_condition.notify_all()

    def _claim(self) -> VoiceRequest | None:
        # A paused broker never claims; already-claimed work finishes normally.
        if self._paused.is_set():
            return None
        return self._with_queue(lambda queue: queue.claim_next())

    def _status(self, request: VoiceRequest) -> str | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT status FROM voice_queue WHERE id = ?",
                (request.id,),
            ).fetchone()
            return str(row[0]) if row is not None else None
        finally:
            close_quietly(conn)

    def _is_synthesizing(self, request: VoiceRequest) -> bool:
        return self._status(request) == "synthesizing"

    def _is_playable(self, request: VoiceRequest) -> bool:
        """True while the row still owns playback: synthesizing or speaking."""
        return self._status(request) in {"synthesizing", "speaking"}

    def _is_speaking(self, request: VoiceRequest) -> bool:
        return self._status(request) == "speaking"

    def _is_active(self, request: VoiceRequest) -> bool:
        return self._status(request) in {"queued", "synthesizing", "speaking"}

    def _mark_synthesis_complete(self, request: VoiceRequest) -> None:
        self._with_queue(lambda queue: queue.mark_synthesis_complete(request.id))

    def _mark_playback_started(self, request: VoiceRequest) -> None:
        self._with_queue(lambda queue: queue.mark_playback_started(request.id))

    def _mark_done(self, request: VoiceRequest) -> None:
        self._with_queue(lambda queue: queue.mark_done(request.id))

    def _mark_failed(self, request: VoiceRequest, error: str) -> None:
        _LOGGER.warning("Voice request %s failed: %s", request.id, error)
        self._with_queue(lambda queue: queue.mark_failed(request.id, error=error))

    def _start_interrupt_watcher(
        self,
        request: VoiceRequest,
    ) -> tuple[threading.Event, threading.Thread] | None:
        # Every playing request gets a watcher: external cancels (DB-only)
        # must reach the live player, not just future claims.
        stop = threading.Event()
        thread = threading.Thread(
            target=self._interrupt_watcher_entry,
            args=(request, stop),
            name=f"dan-voice-interrupt-{request.id[:8]}",
            daemon=True,
        )
        with self._ownership_condition:
            self._interrupt_watchers[thread] = stop
        try:
            thread.start()
        except Exception:
            with self._ownership_condition:
                self._interrupt_watchers.pop(thread, None)
                self._ownership_condition.notify_all()
            raise
        return stop, thread

    def _interrupt_watcher_entry(
        self,
        request: VoiceRequest,
        stop: threading.Event,
    ) -> None:
        try:
            self._watch_playing_request(request, stop)
        finally:
            with self._ownership_condition:
                self._interrupt_watchers.pop(threading.current_thread(), None)
                self._ownership_condition.notify_all()

    def _stop_interrupt_watcher(
        self,
        watcher: tuple[threading.Event, threading.Thread] | None,
    ) -> None:
        if watcher is None:
            return
        stop, thread = watcher
        stop.set()
        thread.join(timeout=1)

    def _watch_playing_request(
        self,
        request: VoiceRequest,
        stop: threading.Event,
    ) -> None:
        interruptible = (
            request.interrupt_policy == "interruptible" and bool(request.session_id)
        )
        while not stop.is_set() and not self._stop.is_set():
            status = self._status(request)
            if stop.is_set() or self._stop.is_set():
                return
            if status not in {"queued", "synthesizing", "speaking"}:
                # Terminal row: only an external cancel/failure interrupts the
                # player; a normal 'done' must never stop the NEXT request.
                if status in {"cancelled", "failed"}:
                    self.stop_playback()
                return
            superseded = False
            if interruptible:
                superseded = self._same_session_noninterruptible_waiting(request)
                if stop.is_set() or self._stop.is_set():
                    return
            if superseded:
                self._with_queue(
                    lambda queue: queue.cancel_superseded_request(
                        request.id,
                        reason="superseded by non-interruptible speech",
                    )
                )
                if stop.is_set() or self._stop.is_set():
                    return
                self.stop_playback()
                return
            stop.wait(INTERRUPT_WATCH_INTERVAL_SECONDS)

    def _same_session_noninterruptible_waiting(self, request: VoiceRequest) -> bool:
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT 1 FROM voice_queue
                WHERE session_id = ? AND id != ?
                  AND status IN ('queued', 'synthesizing', 'speaking')
                  AND interrupt_policy != 'interruptible'
                LIMIT 1
                """,
                (request.session_id, request.id),
            ).fetchone()
            return row is not None
        finally:
            close_quietly(conn)

    def _with_queue(self, action: Callable[[VoiceQueue], Any]) -> Any:
        conn = self._connect()
        try:
            return action(VoiceQueue(conn, event_store=create_event_store(conn)))
        finally:
            close_quietly(conn)


__all__ = [
    "VoiceBroker",
    "VoiceBrokerError",
    "VoiceBrokerOwnershipError",
    "VoiceBrokerShutdownTimeout",
]
