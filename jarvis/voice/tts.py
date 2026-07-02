"""Pluggable TTS engines (G3, decree §7.3).

Decreed engine set: Supertonic (fast) + Chatterbox (voice-clone), with the
mock engine for every test and smoke. edgeTTS, piper and XTTS are BANNED by
decree — asking for them is an explicit error, never a silent fallback.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any


BANNED_ENGINES = ("edgetts", "piper", "xtts")
# Decreed but not yet implemented; each arrives in its own stage.
RESERVED_ENGINES = {
    "supertonic": "Supertonic lands with the first real engine step of G3.",
    "chatterbox": "Chatterbox MLX voice-clone lands in G5.",
}


class TTSEngineError(Exception):
    """Raised when an engine is unknown, reserved, or fails to synthesize."""


class BannedEngineError(TTSEngineError):
    """Raised when a decree-banned engine is requested."""


@dataclass(frozen=True)
class SynthesizedChunk:
    text: str
    audio: bytes


class MockTTSEngine:
    """Deterministic engine double: logs synth/play, produces no sound.

    `play_gate` lets tests block playback to prove the broker prefetches the
    next chunk while the previous one plays; `explode_on` triggers a
    synthesis failure for error-path tests.
    """

    name = "mock"

    def __init__(
        self,
        *,
        play_gate: threading.Event | None = None,
        explode_on: str | None = None,
    ) -> None:
        self.log: list[tuple[str, str]] = []
        self._play_gate = play_gate
        self._explode_on = explode_on
        self._lock = threading.Lock()

    def synthesize(self, text: str) -> SynthesizedChunk:
        with self._lock:
            self.log.append(("synth", text))
        if self._explode_on and self._explode_on in text:
            raise TTSEngineError(f"mock synthesis failure for {text!r}")
        return SynthesizedChunk(text=text, audio=text.encode("utf-8"))

    def play(self, chunk: SynthesizedChunk) -> None:
        if self._play_gate is not None:
            self._play_gate.wait(timeout=30)
        with self._lock:
            self.log.append(("play", chunk.text))


def build_tts_engine(name: str) -> Any:
    normalized = str(name or "").strip().lower()
    if normalized in BANNED_ENGINES:
        raise BannedEngineError(
            f"TTS engine {name!r} is banned by decree (MASTER_PLAN §7.3)."
        )
    if normalized in RESERVED_ENGINES:
        raise TTSEngineError(
            f"TTS engine {name!r} is decreed but not implemented yet: "
            f"{RESERVED_ENGINES[normalized]}"
        )
    if normalized == "mock":
        return MockTTSEngine()
    raise TTSEngineError(f"Unknown TTS engine {name!r}.")


__all__ = [
    "BANNED_ENGINES",
    "BannedEngineError",
    "MockTTSEngine",
    "SynthesizedChunk",
    "TTSEngineError",
    "build_tts_engine",
]
