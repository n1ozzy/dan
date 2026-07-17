"""VoiceBroker: the ONLY speaker in the system (G3, ADR-005).

Claims queued VoiceRequests, synthesizes and plays them in order, and
synthesizes the NEXT chunk while the current one plays (§4a smoothness
requirement). Nothing else ever calls a player; cancellation flips queue
rows, and the broker simply never plays a cancelled row.
"""

from __future__ import annotations

import threading
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


class VoiceBroker:
    def __init__(
        self,
        connection_factory: Callable[[], Any],
        *,
        config: Any,
        engine: Any,
        poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
    ) -> None:
        self._connect = connection_factory
        self._config = config
        self._engine = engine
        self._poll_interval = poll_interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # One worker: the single prefetch slot of "synthesize next while
        # the current chunk plays". More would reorder synthesis.
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dan-tts")

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        if self._executor is None or getattr(self._executor, "_shutdown", False):
            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dan-tts")
        self._thread = threading.Thread(target=self._run, name="dan-voice-broker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        # Interrupt the current playback so a blocked play() cannot outlive
        # the join timeout (FIX-04d).
        self._stop_playback("broker stop")
        if self._thread is not None:
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                _LOGGER.warning("Voice broker thread did not stop within timeout.")
            self._thread = None
        # Do NOT wait on a synthesis that's mid-flight: _engine.synthesize can be
        # a subprocess with a timeout up to voice.tts_timeout (~120s), and
        # _stop_playback only kills the PLAYER, not the synth. wait=True here made
        # daemon shutdown hang for that whole timeout. cancel_futures drops the
        # queued prefetch; a running one finishes on its own worker thread while
        # shutdown returns immediately.
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _run(self) -> None:
        # A non-TTS exception (sqlite "database is locked", vanished binary)
        # must never kill the only speaker in the system (FIX-04c): log,
        # back off, retry.
        backoff = self._poll_interval
        while not self._stop.is_set():
            try:
                self._with_queue(lambda queue: queue.recover_orphans())
                played = self.drain_all()
            except Exception:
                _LOGGER.exception("Voice broker drain failed; retrying after backoff.")
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 5.0)
                continue
            backoff = self._poll_interval
            if played == 0:
                self._stop.wait(self._poll_interval)

    # -- draining -----------------------------------------------------------

    def drain_all(self) -> int:
        """Play everything queued right now; returns the played count."""

        self._with_queue(lambda queue: queue.recover_orphans())
        played = 0
        current = self._claim()
        prefetched: Future | None = None
        while current is not None:
            try:
                # Keep one request's failure from killing the worker; mark it
                # failed and continue draining the rest of the queue.
                try:
                    chunk = (
                        prefetched.result()
                        if prefetched is not None
                        else self._engine.synthesize(current.text)
                    )
                except Exception as exc:
                    self._mark_failed(current, str(exc))
                    current = self._claim()
                    prefetched = None
                    continue

                # Claim and start synthesizing the NEXT chunk before playback,
                # so audio for it is ready the moment this one finishes.
                next_request = self._claim()
                prefetched = (
                    self._executor.submit(self._engine.synthesize, next_request.text)
                    if next_request is not None
                    else None
                )

                # Barge-in (G4c): a claimed row may have been flipped to
                # 'cancelled' while the previous chunk was playing — re-check DB
                # truth so a cancelled row is never played.
                if not self._still_speaking(current):
                    current = next_request
                    continue

                # Play this chunk. spoken_at is stamped via on_started, which the
                # engine calls the instant the player actually spawns (under the
                # player lock, AFTER the should_play re-check). A barge-in that
                # raises PlaybackCancelled BEFORE the spawn therefore never marks
                # spoken_at — only audio that truly went out counts toward the
                # anti-echo corpus, so a cancelled-before-sound chunk no longer
                # causes false echo rejections of the user's next turn (FIX-09).
                watcher = self._start_interrupt_watcher(current)
                interrupted = False
                try:
                    # should_play is re-checked inside the engine under its player
                    # lock, right before spawning — closes the barge-in TOCTOU the
                    # pre-play check above cannot (FIX-09).
                    self._play(
                        chunk,
                        should_play=lambda: self._still_speaking(current),
                        on_started=lambda: self._mark_spoken(current),
                    )
                except PlaybackCancelled:
                    # Cancelled in the check->spawn gap: the row is already
                    # 'cancelled' (leg 2), so skip cleanly — no done, no failure.
                    interrupted = True
                except Exception as exc:  # playback must never kill the loop
                    if self._still_speaking(current):
                        self._mark_failed(current, f"playback failed: {exc}")
                    else:
                        interrupted = True
                else:
                    if self._still_speaking(current):
                        self._mark_done(current)
                        played += 1
                    else:
                        interrupted = True
                finally:
                    self._stop_interrupt_watcher(watcher)
                if interrupted and next_request is None:
                    next_request = self._claim()
                current = next_request
            except Exception:
                _LOGGER.exception("Voice broker failed processing request %s; moving on", current.id if current else "unknown")
                if current is not None:
                    self._mark_failed(current, "broker loop failed")
                    current = self._claim()
                    prefetched = None
                else:
                    current = None
                    prefetched = None
            # Honour stop() between chunks: a long queue must not keep the
            # drain loop alive past shutdown (FIX-04d). The claimed row goes
            # back to 'queued' via recover_orphans on the next start.
            if self._stop.is_set():
                return played
        return played

    # -- internals ------------------------------------------------------------

    def _play(
        self,
        chunk: SynthesizedChunk,
        should_play: Callable[[], bool] | None = None,
        on_started: Callable[[], None] | None = None,
    ) -> None:
        self._engine.play(chunk, should_play=should_play, on_started=on_started)

    def _claim(self) -> VoiceRequest | None:
        return self._with_queue(lambda queue: queue.claim_next())

    def _still_speaking(self, request: VoiceRequest) -> bool:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT status FROM voice_queue WHERE id = ?", (request.id,)
            ).fetchone()
            return row is not None and str(row[0]) == "speaking"
        finally:
            close_quietly(conn)

    def _mark_spoken(self, request: VoiceRequest) -> None:
        self._with_queue(lambda queue: queue.mark_spoken(request.id))

    def _mark_done(self, request: VoiceRequest) -> None:
        self._with_queue(lambda queue: queue.mark_done(request.id))

    def _mark_failed(self, request: VoiceRequest, error: str) -> None:
        _LOGGER.warning("Voice request %s failed: %s", request.id, error)
        self._with_queue(lambda queue: queue.mark_failed(request.id, error=error))

    def _cancel_request(self, request: VoiceRequest) -> bool:
        return bool(self._with_queue(lambda queue: queue.cancel_request(request.id)))

    def _start_interrupt_watcher(
        self, request: VoiceRequest
    ) -> tuple[threading.Event, threading.Thread] | None:
        if request.interrupt_policy != "interruptible" or not request.turn_id:
            return None
        stop = threading.Event()
        thread = threading.Thread(
            target=self._watch_interruptible_request,
            args=(request, stop),
            name=f"dan-voice-interrupt-{request.id[:8]}",
            daemon=True,
        )
        thread.start()
        return stop, thread

    def _stop_interrupt_watcher(
        self, watcher: tuple[threading.Event, threading.Thread] | None
    ) -> None:
        if watcher is None:
            return
        stop, thread = watcher
        stop.set()
        thread.join(timeout=1)

    def _watch_interruptible_request(
        self, request: VoiceRequest, stop: threading.Event
    ) -> None:
        while not stop.is_set() and not self._stop.is_set():
            if not self._still_speaking(request):
                return
            if self._same_turn_sentence_waiting(request):
                if self._cancel_request(request):
                    self._stop_playback("interruptible filler")
                return
            stop.wait(INTERRUPT_WATCH_INTERVAL_SECONDS)

    def _same_turn_sentence_waiting(self, request: VoiceRequest) -> bool:
        if not request.turn_id:
            return False
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT 1 FROM voice_queue
                WHERE turn_id = ?
                  AND id != ?
                  AND status IN ('queued', 'speaking')
                  AND json_extract(metadata_json, '$.kind') = 'sentence'
                LIMIT 1
                """,
                (request.turn_id, request.id),
            ).fetchone()
            return row is not None
        finally:
            close_quietly(conn)

    def _stop_playback(self, reason: str) -> None:
        try:
            stop_playback = getattr(self._engine, "stop_playback", None)
            if callable(stop_playback):
                stop_playback()
        except Exception:
            _LOGGER.exception("stop_playback failed during %s.", reason)

    def _with_queue(self, action: Callable[[VoiceQueue], Any]) -> Any:
        conn = self._connect()
        try:
            return action(VoiceQueue(conn, event_store=create_event_store(conn)))
        finally:
            close_quietly(conn)


__all__ = ["VoiceBroker"]
