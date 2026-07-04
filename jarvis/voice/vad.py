"""Energy/VAD capture gate (G4b — the mandatory filter BEFORE whisper).

Empirical fact (live inventory 2026-07-02, docs/reviews/…voice-tools-
inventory.md): whisper hallucinates on silence — 3 s of digital silence
transcribed as „Dziękuję." despite no_speech_threshold=0.6. The model can
therefore never be trusted to detect silence itself; this gate decides
from the raw PCM whether a capture contains enough voiced audio to be
worth transcribing at all. Pure and deterministic: bytes in, decision out.
"""

from __future__ import annotations

import math
from array import array
from dataclasses import dataclass
from typing import Any

from jarvis.voice.capture_policy import min_capture_ms


DEFAULT_SAMPLE_RATE = 16000
FRAME_MS = 30


@dataclass(frozen=True)
class CaptureStats:
    duration_seconds: float
    rms: int
    voiced_seconds: float
    voiced_ratio: float


@dataclass(frozen=True)
class GateDecision:
    accepted: bool
    reason: str
    stats: CaptureStats


def pcm_from_wav(audio: bytes) -> bytes:
    """Payload after the `data` chunk marker; raw input passes through.

    Deliberately ignores the declared RIFF/data sizes: a recorder killed
    with SIGKILL leaves bogus lengths in the header, but the samples after
    the marker are still real audio.
    """

    if audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
        return audio
    marker = audio.find(b"data", 12)
    if marker == -1 or marker + 8 > len(audio):
        return b""
    return audio[marker + 8 :]


def _frame_rms(samples: array, start: int, end: int) -> float:
    total = 0
    for index in range(start, end):
        value = samples[index]
        total += value * value
    return math.sqrt(total / (end - start))


def analyze_capture(
    audio: bytes,
    *,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    frame_ms: int = FRAME_MS,
    voiced_rms_threshold: int = 300,
) -> CaptureStats:
    """Frame-level stats of a 16-bit mono capture (WAV or raw PCM)."""

    pcm = pcm_from_wav(audio)
    if len(pcm) % 2:
        pcm = pcm[:-1]
    samples = array("h")
    samples.frombytes(pcm)
    if not samples:
        return CaptureStats(0.0, 0, 0.0, 0.0)

    duration = len(samples) / sample_rate
    frame_size = max(1, int(sample_rate * frame_ms / 1000))
    frames = max(1, len(samples) // frame_size)
    voiced = 0
    total_square = 0
    for value in samples:
        total_square += value * value
    for index in range(frames):
        start = index * frame_size
        end = min(start + frame_size, len(samples))
        if _frame_rms(samples, start, end) >= voiced_rms_threshold:
            voiced += 1
    frame_seconds = frame_size / sample_rate
    return CaptureStats(
        duration_seconds=round(duration, 3),
        rms=int(math.sqrt(total_square / len(samples))),
        voiced_seconds=round(voiced * frame_seconds, 3),
        voiced_ratio=round(voiced / frames, 4),
    )


class CaptureGate:
    """Accepts a capture only when it plausibly contains an utterance."""

    def __init__(self, *, config: Any) -> None:
        self._sample_rate = int(getattr(config, "recorder_sample_rate", DEFAULT_SAMPLE_RATE) or DEFAULT_SAMPLE_RATE)
        self._min_rms = int(getattr(config, "stt_min_rms", 300) or 0)
        self._min_voiced_seconds = float(getattr(config, "stt_min_voiced_seconds", 0.3) or 0.0)
        self._min_voiced_ratio = float(getattr(config, "stt_min_voiced_ratio", 0.05) or 0.0)
        self._min_capture_seconds = min_capture_ms(config) / 1000.0

    def evaluate(self, audio: bytes) -> GateDecision:
        stats = analyze_capture(
            audio,
            sample_rate=self._sample_rate,
            voiced_rms_threshold=self._min_rms,
        )
        if stats.duration_seconds <= 0.0:
            return GateDecision(False, "empty", stats)
        if stats.duration_seconds < self._min_capture_seconds:
            return GateDecision(False, "too_short", stats)
        if stats.voiced_seconds < self._min_voiced_seconds:
            return GateDecision(False, "too_quiet", stats)
        if stats.voiced_ratio < self._min_voiced_ratio:
            return GateDecision(False, "sparse_voice", stats)
        return GateDecision(True, "ok", stats)


__all__ = [
    "CaptureGate",
    "CaptureStats",
    "GateDecision",
    "analyze_capture",
    "pcm_from_wav",
]
