"""TranscriptionPipeline (G4b): capture → gate → STT → junk filter → event.

The hallucination firewall around whisper, both sides mandatory (decree +
live-confirmed fact: silence transcribes as „Dziękuję."):

- BEFORE the model: the energy/VAD CaptureGate drops silence and noise
  pickup, so most junk never costs an inference.
- AFTER the model: the junk-phrase filter drops the known silence
  hallucinations that slip through anyway. The list is config data.

A surviving transcript becomes exactly one `input.voice.transcribed` event
(the type reserved in CONTRACTS since day one) and is handed to the
optional consumer callback. Turning transcripts into turns is NOT this
module's job — that wiring arrives with the anti-echo gate (G4c), so an
echo can never become a turn by construction.

Processing runs on one worker thread: the recorder's stop() (an HTTP
request thread) must never wait for a model.
"""

from __future__ import annotations

import threading
import unicodedata
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from jarvis.events.types import EventType
from jarvis.logging import get_logger
from jarvis.store.db import close_quietly
from jarvis.store.event_store import create_event_store
from jarvis.voice.vad import CaptureGate


_LOGGER = get_logger("voice.stt")


def normalize_phrase(text: str) -> str:
    """Casefold, drop punctuation, collapse whitespace — junk matching key."""

    cleaned: list[str] = []
    for char in unicodedata.normalize("NFC", str(text or "")).casefold():
        if char.isalnum():
            cleaned.append(char)
        elif char.isspace():
            cleaned.append(" ")
    return " ".join("".join(cleaned).split())


class TranscriptionPipeline:
    def __init__(
        self,
        connection_factory: Callable[[], Any],
        *,
        config: Any,
        engine: Any,
        on_transcript: Callable[[str], None] | None = None,
    ) -> None:
        self._connect = connection_factory
        self._engine = engine
        self._on_transcript = on_transcript
        self._gate = CaptureGate(config=config)
        self._junk = {
            normalize_phrase(phrase)
            for phrase in tuple(getattr(config, "stt_junk_phrases", ()) or ())
            if normalize_phrase(phrase)
        }
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="jarvis-stt")

    # -- public surface ------------------------------------------------------

    def accept_capture(self, audio: bytes) -> None:
        """Recorder handoff; returns immediately, work happens on the worker."""

        self._executor.submit(self._process, audio)

    def flush(self, timeout: float = 30.0) -> bool:
        """Wait until everything accepted so far is processed (tests/stop)."""

        done = threading.Event()
        self._executor.submit(done.set)
        return done.wait(timeout)

    def stop(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
        stop_engine = getattr(self._engine, "stop", None)
        if callable(stop_engine):
            stop_engine()

    # -- worker thread ---------------------------------------------------------

    def _process(self, audio: bytes) -> None:
        try:
            decision = self._gate.evaluate(audio)
            if not decision.accepted:
                _LOGGER.debug(
                    "capture rejected by gate: %s %s", decision.reason, decision.stats
                )
                return
            try:
                text = str(self._engine.transcribe(audio) or "").strip()
            except Exception:  # noqa: BLE001 — a model failure must not kill listening
                _LOGGER.exception("STT engine failed; capture dropped.")
                return
            if not text:
                return
            if normalize_phrase(text) in self._junk:
                _LOGGER.info("junk transcript dropped: %r", text)
                return
            if not self._append_event(text, decision):
                return
            if self._on_transcript is not None:
                try:
                    self._on_transcript(text)
                except Exception:  # noqa: BLE001
                    _LOGGER.exception("transcript consumer raised; transcript persisted.")
        except Exception:  # noqa: BLE001 — the worker loop must survive anything
            _LOGGER.exception("transcription pipeline failed on a capture.")

    def _append_event(self, text: str, decision: Any) -> bool:
        conn = None
        try:
            conn = self._connect()
            create_event_store(conn).append(
                EventType.INPUT_VOICE_TRANSCRIBED,
                "voice",
                {
                    "text": text,
                    "engine": str(getattr(self._engine, "name", "unknown")),
                    "duration_seconds": decision.stats.duration_seconds,
                    "voiced_seconds": decision.stats.voiced_seconds,
                    "rms": decision.stats.rms,
                },
            )
            return True
        except Exception:  # noqa: BLE001
            # jarvisd owns truth: an unpersisted transcript must not act.
            _LOGGER.exception("failed to persist voice transcript; dropping it.")
            return False
        finally:
            if conn is not None:
                close_quietly(conn)


__all__ = ["TranscriptionPipeline", "normalize_phrase"]
