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

from jarvis.logging import get_logger
from jarvis.store.db import close_quietly
from jarvis.store.event_store import create_event_store
from jarvis.voice.models import VoiceRequest
from jarvis.voice.queue import VoiceQueue
from jarvis.voice.tts import SynthesizedChunk


_LOGGER = get_logger("voice.broker")

DEFAULT_POLL_INTERVAL_SECONDS = 0.25


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
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="jarvis-tts")

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        if self._executor is None or getattr(self._executor, "_shutdown", False):
            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="jarvis-tts")
        self._thread = threading.Thread(target=self._run, name="jarvis-voice-broker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        # Interrupt the current playback so a blocked play() cannot outlive
        # the join timeout (FIX-04d).
        try:
            stop_playback = getattr(self._engine, "stop_playback", None)
            if callable(stop_playback):
                stop_playback()
        except Exception:
            _LOGGER.exception("stop_playback failed during broker stop.")
        if self._thread is not None:
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                _LOGGER.warning("Voice broker thread did not stop within timeout.")
            self._thread = None
        self._executor.shutdown(cancel_futures=True)

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
            # Honour stop() between chunks: a long queue must not keep the
            # drain loop alive past shutdown (FIX-04d). The claimed row goes
            # back to 'queued' via recover_orphans on the next start.
            if self._stop.is_set():
                return played
            try:
                chunk = (
                    prefetched.result()
                    if prefetched is not None
                    else self._engine.synthesize(current.text)
                )
            except Exception as exc:
                # Not only TTSEngineError: a sqlite error or missing binary in
                # synthesis must fail the row, not the loop (FIX-04c).
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

            try:
                self._play(chunk)
            except Exception as exc:  # playback must never kill the loop
                self._mark_failed(current, f"playback failed: {exc}")
            else:
                self._mark_done(current)
                played += 1
            current = next_request
        return played

    # -- internals ------------------------------------------------------------

    def _play(self, chunk: SynthesizedChunk) -> None:
        self._engine.play(chunk)

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

    def _mark_done(self, request: VoiceRequest) -> None:
        self._with_queue(lambda queue: queue.mark_done(request.id))

    def _mark_failed(self, request: VoiceRequest, error: str) -> None:
        _LOGGER.warning("Voice request %s failed: %s", request.id, error)
        self._with_queue(lambda queue: queue.mark_failed(request.id, error=error))

    def _with_queue(self, action: Callable[[VoiceQueue], Any]) -> Any:
        conn = self._connect()
        try:
            return action(VoiceQueue(conn, event_store=create_event_store(conn)))
        finally:
            close_quietly(conn)


__all__ = ["VoiceBroker"]
