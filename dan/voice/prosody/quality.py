"""Conservative technical gating for stochastic Supertonic takes.

This is intentionally not sold as an automatic judge of human naturalness.
It rejects obvious failures and avoids preferring exaggerated F0 movement. The
human ear remains the final authority, while deterministic candidates and a
manifest make that decision reproducible.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from .audio import AudioProcessingError, read_wav_bytes
from .models import TakeCandidate, TakeMetrics


class TakeSelectionError(RuntimeError):
    """No candidate is technically safe enough to select automatically."""


def analyze_take(
    wav_payload: bytes,
    *,
    text: str,
) -> TakeMetrics:
    buffer = read_wav_bytes(wav_payload, mono=True)
    samples = np.asarray(buffer.samples, dtype=np.float32)
    duration = buffer.duration_seconds
    peak = float(np.max(np.abs(samples)))
    rms = float(np.sqrt(np.mean(samples * samples) + 1e-12))
    clipping_ratio = float(np.mean(np.abs(samples) >= 0.995))

    frame_seconds = 0.020
    hop_seconds = 0.010
    frame = max(32, int(round(buffer.sample_rate * frame_seconds)))
    hop = max(16, int(round(buffer.sample_rate * hop_seconds)))
    frame_rms = _frame_rms(samples, frame=frame, hop=hop)
    silence_threshold = max(10.0 ** (-48.0 / 20.0), min(0.025, rms * 0.20))
    voiced = frame_rms >= silence_threshold
    voiced_ratio = float(np.mean(voiced)) if voiced.size else 0.0
    silence_ratio = 1.0 - voiced_ratio
    leading = _edge_silence_seconds(voiced, hop_seconds, from_end=False)
    trailing = _edge_silence_seconds(voiced, hop_seconds, from_end=True)
    max_internal = _max_internal_silence_seconds(voiced, hop_seconds)

    tail_count = max(1, int(round(buffer.sample_rate * 0.060)))
    tail_rms = float(np.sqrt(np.mean(samples[-tail_count:] ** 2) + 1e-12))
    tail_energy_ratio = tail_rms / max(rms, 1e-9)
    spoken_chars = _spoken_character_count(text)
    speech_rate = spoken_chars / max(duration, 1e-9)

    f0_values = _estimate_f0_track(
        samples,
        sample_rate=buffer.sample_rate,
        activity_threshold=silence_threshold,
    )
    if f0_values.size:
        f0_mean = float(np.mean(f0_values))
        f0_std = float(np.std(f0_values))
        f0_tail_slope = _tail_slope(f0_values)
    else:
        f0_mean = 0.0
        f0_std = 0.0
        f0_tail_slope = 0.0

    hard: list[str] = []
    warnings: list[str] = []
    if duration < 0.16:
        hard.append("audio_too_short")
    if peak < 0.006 or rms < 0.0012:
        hard.append("near_silence")
    if clipping_ratio > 0.03:
        hard.append("severe_clipping")
    if voiced_ratio < 0.10:
        hard.append("insufficient_voiced_audio")
    if speech_rate > 36.0:
        hard.append("implausibly_fast")
    elif speech_rate < 1.8:
        hard.append("implausibly_slow")
    if max_internal > 2.0:
        hard.append("extreme_internal_silence")

    abrupt_end = trailing < 0.015 and tail_energy_ratio > 0.42
    if abrupt_end:
        warnings.append("possible_cut_ending")
    if leading > 0.35:
        warnings.append("long_leading_silence")
    if trailing > 0.45:
        warnings.append("long_trailing_silence")
    if max_internal > 0.85:
        warnings.append("long_internal_silence")
    if f0_values.size >= 5 and f0_std > 75.0:
        warnings.append("excessive_pitch_motion")
    if f0_values.size >= 5 and f0_std < 3.5:
        warnings.append("very_flat_pitch")

    score = _score_take(
        clipping_ratio=clipping_ratio,
        silence_ratio=silence_ratio,
        leading_silence=leading,
        trailing_silence=trailing,
        max_internal_silence=max_internal,
        f0_count=int(f0_values.size),
        f0_std=f0_std,
        abrupt_end=abrupt_end,
        hard_failure_count=len(hard),
    )
    return TakeMetrics(
        duration_seconds=round(duration, 6),
        peak=round(peak, 8),
        rms=round(rms, 8),
        clipping_ratio=round(clipping_ratio, 8),
        voiced_ratio=round(voiced_ratio, 6),
        silence_ratio=round(silence_ratio, 6),
        leading_silence_seconds=round(leading, 6),
        trailing_silence_seconds=round(trailing, 6),
        max_internal_silence_seconds=round(max_internal, 6),
        tail_energy_ratio=round(tail_energy_ratio, 6),
        speech_rate_chars_per_second=round(speech_rate, 6),
        f0_mean_hz=round(f0_mean, 4),
        f0_std_hz=round(f0_std, 4),
        f0_tail_slope=round(f0_tail_slope, 5),
        score=round(score, 6),
        hard_failures=tuple(hard),
        warnings=tuple(warnings),
    )


def select_best_take(
    candidates: Sequence[TakeCandidate],
    *,
    preferred_seed: int | None = None,
) -> tuple[TakeCandidate, str]:
    if not candidates:
        raise TakeSelectionError("no take candidates were rendered")
    by_seed = {candidate.seed: candidate for candidate in candidates}
    if preferred_seed is not None:
        preferred = by_seed.get(preferred_seed)
        if preferred is None:
            raise TakeSelectionError(
                f"manual seed {preferred_seed} is not among rendered candidates"
            )
        if preferred.metrics.hard_failures:
            failures = ", ".join(preferred.metrics.hard_failures)
            raise TakeSelectionError(
                f"manual seed {preferred_seed} failed hard gates: {failures}"
            )
        return preferred, "manual seed override"

    passing = [candidate for candidate in candidates if not candidate.metrics.hard_failures]
    if not passing:
        summary = "; ".join(
            f"seed {candidate.seed}: {','.join(candidate.metrics.hard_failures) or 'failed'}"
            for candidate in candidates
        )
        raise TakeSelectionError(f"all deterministic takes failed hard gates: {summary}")
    # Tie-breaking by seed is deterministic. Crucially, the score mostly
    # penalizes pathological output; it does not reward the wildest pitch.
    selected = max(passing, key=lambda candidate: (candidate.metrics.score, -candidate.seed))
    return selected, "highest conservative quality score among hard-gate passes"


def _spoken_character_count(text: str) -> int:
    count = sum(1 for char in text if char.isalnum())
    return max(1, count)


def _frame_rms(samples: np.ndarray, *, frame: int, hop: int) -> np.ndarray:
    if samples.size <= frame:
        return np.array([float(np.sqrt(np.mean(samples * samples) + 1e-12))])
    count = 1 + ((samples.size - frame) // hop)
    values = np.empty(count, dtype=np.float32)
    for index in range(count):
        window = samples[index * hop : (index * hop) + frame]
        values[index] = np.sqrt(np.mean(window * window) + 1e-12)
    return values


def _edge_silence_seconds(mask: np.ndarray, hop_seconds: float, *, from_end: bool) -> float:
    if mask.size == 0:
        return 0.0
    indices = np.flatnonzero(mask)
    if indices.size == 0:
        return float(mask.size * hop_seconds)
    frames = (mask.size - 1 - indices[-1]) if from_end else indices[0]
    return float(frames * hop_seconds)


def _max_internal_silence_seconds(mask: np.ndarray, hop_seconds: float) -> float:
    active = np.flatnonzero(mask)
    if active.size < 2:
        return 0.0
    interior = ~mask[active[0] : active[-1] + 1]
    best = current = 0
    for is_silent in interior:
        if is_silent:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return float(best * hop_seconds)


def _estimate_f0_track(
    samples: np.ndarray,
    *,
    sample_rate: int,
    activity_threshold: float,
) -> np.ndarray:
    frame = max(256, int(round(sample_rate * 0.040)))
    hop = max(128, int(round(sample_rate * 0.012)))
    min_lag = max(1, int(sample_rate / 350.0))
    max_lag = min(frame - 2, int(sample_rate / 65.0))
    if samples.size < frame or max_lag <= min_lag:
        return np.empty(0, dtype=np.float32)

    window_fn = np.hanning(frame).astype(np.float32)
    values: list[float] = []
    for start in range(0, samples.size - frame + 1, hop):
        window = samples[start : start + frame].astype(np.float32, copy=False)
        local_rms = float(np.sqrt(np.mean(window * window) + 1e-12))
        if local_rms < activity_threshold:
            continue
        centered = (window - float(np.mean(window))) * window_fn
        correlation = np.correlate(centered, centered, mode="full")[frame - 1 :]
        energy = float(correlation[0])
        if energy <= 1e-8:
            continue
        region = correlation[min_lag : max_lag + 1]
        if region.size == 0:
            continue
        relative = int(np.argmax(region))
        lag = min_lag + relative
        confidence = float(region[relative] / energy)
        if confidence < 0.28:
            continue
        values.append(float(sample_rate / lag))
    return np.asarray(values, dtype=np.float32)


def _tail_slope(values: np.ndarray) -> float:
    count = min(8, values.size)
    if count < 3:
        return 0.0
    tail = values[-count:].astype(np.float64)
    x = np.arange(count, dtype=np.float64)
    slope, _ = np.polyfit(x, tail, 1)
    return float(slope)


def _score_take(
    *,
    clipping_ratio: float,
    silence_ratio: float,
    leading_silence: float,
    trailing_silence: float,
    max_internal_silence: float,
    f0_count: int,
    f0_std: float,
    abrupt_end: bool,
    hard_failure_count: int,
) -> float:
    score = 100.0
    score -= min(30.0, clipping_ratio * 1000.0)
    if silence_ratio > 0.38:
        score -= (silence_ratio - 0.38) * 75.0
    score -= max(0.0, leading_silence - 0.20) * 22.0
    score -= max(0.0, trailing_silence - 0.25) * 18.0
    score -= max(0.0, max_internal_silence - 0.55) * 25.0
    if f0_count >= 5:
        if f0_std > 65.0:
            score -= min(18.0, (f0_std - 65.0) * 0.25)
        elif f0_std < 3.5:
            score -= (3.5 - f0_std) * 1.5
    if abrupt_end:
        score -= 12.0
    score -= hard_failure_count * 100.0
    return score


__all__ = [
    "AudioProcessingError",
    "TakeSelectionError",
    "analyze_take",
    "select_best_take",
]
