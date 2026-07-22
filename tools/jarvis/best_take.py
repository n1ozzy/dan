#!/usr/bin/env python3
"""Recovered offline best-take selector for deterministic Supertonic renders.

The original 2026-07-13 tool generated several diffusion seeds with one loaded
model and ranked them by F0 movement, RMS dynamics, pauses, long silences and
tail loudness.  This version preserves those criteria but delegates seeding and
WAV encoding to DAN's canonical adapter.  It never plays audio and is not part
of the live broker path.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import threading
import wave
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

from dan.voice.supertonic_seeded import (
    encode_wav,
    synthesize_seeded,
    validate_seed,
)

_OFFLINE_SYNTH_LOCK = threading.Lock()


@dataclass(frozen=True)
class TakeResult:
    score: float
    seed: int
    path: Path
    metrics: dict[str, float]
    wav_sha256: str


def load_wav(path: str | Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as reader:
        sample_rate = reader.getframerate()
        frames = reader.getnframes()
        raw = reader.readframes(frames)
        channels = reader.getnchannels()
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if channels == 2:
        samples = samples.reshape(-1, 2).mean(axis=1)
    return samples, sample_rate


def frame_rms(samples: np.ndarray, sample_rate: int, window: float = 0.05) -> np.ndarray:
    width = int(sample_rate * window)
    count = len(samples) // width
    if count == 0:
        return np.zeros(1)
    framed = samples[: count * width].reshape(count, width)
    return np.sqrt((framed**2).mean(axis=1))


def f0_track(
    samples: np.ndarray,
    sample_rate: int,
    window: float = 0.04,
    fmin: int = 60,
    fmax: int = 400,
) -> np.ndarray:
    """Small autocorrelation pitch tracker retained from the recovered tool."""

    width = int(sample_rate * window)
    count = len(samples) // width
    f0s: list[float] = []
    low_lag, high_lag = int(sample_rate / fmax), int(sample_rate / fmin)
    for index in range(count):
        frame = samples[index * width : (index + 1) * width]
        if np.sqrt((frame**2).mean()) < 0.02:
            continue
        frame = frame - frame.mean()
        autocorrelation = np.correlate(frame, frame, "full")[len(frame) - 1 :]
        if high_lag >= len(autocorrelation):
            continue
        segment = autocorrelation[low_lag:high_lag]
        if len(segment) == 0 or autocorrelation[0] <= 0:
            continue
        peak = int(np.argmax(segment)) + low_lag
        if autocorrelation[peak] / autocorrelation[0] > 0.3:
            f0s.append(sample_rate / peak)
    return np.asarray(f0s)


def score_wav(path: str | Path) -> tuple[float, dict[str, float]]:
    """Recovered heuristic v1, intentionally unchanged in meaning."""

    samples, sample_rate = load_wav(path)
    if len(samples) < sample_rate // 2:
        return 0.0, {}
    rms = frame_rms(samples, sample_rate)
    peak = float(rms.max()) or 1e-9
    silence = rms < 0.06 * peak
    runs: list[int] = []
    current = 0
    for quiet in silence:
        current = current + 1 if quiet else 0
        runs.append(current)
    max_silence = max(runs) * 0.05 if runs else 0.0
    silence_ratio = float(silence.mean())
    voiced = rms[~silence]
    dynamics = (
        float(voiced.std() / (voiced.mean() + 1e-9)) if len(voiced) else 0.0
    )
    f0 = f0_track(samples, sample_rate)
    f0_std = float(f0.std()) if len(f0) > 10 else 0.0
    f0_mean = float(f0.mean()) if len(f0) > 10 else 0.0
    tail_loudness = float(rms[-3:].mean() / peak)

    score = 5.0
    score += float(np.clip((f0_std - 8) / 8, -2.0, 2.0))
    score += float(np.clip((dynamics - 0.35) * 4, -1.5, 1.5))
    score += float(np.clip((silence_ratio - 0.02) * 12, 0, 1.0))
    if max_silence > 1.2:
        score -= (max_silence - 1.2) * 2
    if tail_loudness > 0.5:
        score -= 1.0

    metrics = {
        "f0_std": round(f0_std, 1),
        "f0_mean": round(f0_mean, 1),
        "dynamics": round(dynamics, 2),
        "silence_ratio": round(silence_ratio, 3),
        "max_silence_s": round(max_silence, 2),
        "tail": round(tail_loudness, 2),
        "duration_s": round(len(samples) / sample_rate, 2),
    }
    return float(np.clip(score, 0, 10)), metrics


def render_take(
    *,
    tts: Any,
    text: str,
    style: Any,
    seed: int,
    output: str | Path,
    speed: float,
    steps: int,
    lang: str,
    max_chunk_length: int,
) -> TakeResult:
    checked_seed = validate_seed(seed)
    wav, _duration = synthesize_seeded(
        tts,
        text=text,
        voice_style=style,
        seed=checked_seed,
        lock=_OFFLINE_SYNTH_LOCK,
        total_steps=steps,
        speed=speed,
        lang=lang,
        max_chunk_length=max_chunk_length,
        silence_duration=0.0,
    )
    payload = encode_wav(np.asarray(wav, dtype=np.float32), tts.sample_rate)
    target = Path(output)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    target.write_bytes(payload)
    score, metrics = score_wav(target)
    return TakeResult(
        score=score,
        seed=checked_seed,
        path=target,
        metrics=metrics,
        wav_sha256=hashlib.sha256(payload).hexdigest(),
    )


def choose_best(takes: Iterable[TakeResult]) -> TakeResult:
    ranked = sorted(takes, key=lambda take: (-take.score, take.seed))
    if not ranked:
        raise ValueError("no successful takes")
    return ranked[0]


def make_tts(*, coreml: bool = False):
    """Load one model; CoreML remains an explicit offline-only option."""

    if coreml or os.environ.get("SUPERTONIC_COREML"):
        import supertonic.loader as loader

        loader.DEFAULT_ONNX_PROVIDERS = [
            "CoreMLExecutionProvider",
            "CPUExecutionProvider",
        ]
    from supertonic import TTS

    return TTS(model="supertonic-3")


def _seed_values(count: int, explicit: str) -> tuple[int, ...]:
    if explicit:
        values = tuple(validate_seed(int(value.strip())) for value in explicit.split(","))
    else:
        if not 1 <= count <= 64:
            raise ValueError("--seeds must be between 1 and 64")
        values = tuple(range(1, count + 1))
    if not values or len(set(values)) != len(values):
        raise ValueError("seed list must be non-empty and unique")
    return values


def _manifest(takes: list[TakeResult], best: TakeResult) -> dict[str, Any]:
    rows = []
    for take in sorted(takes, key=lambda item: item.seed):
        row = asdict(take)
        row["candidate_file"] = take.path.name
        del row["path"]
        rows.append(row)
    return {
        "schema_version": 1,
        "criteria": "recovered-best-take-v1",
        "selected_seed": best.seed,
        "selected_wav_sha256": best.wav_sha256,
        "takes": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("text")
    parser.add_argument("--voice", default="M3")
    parser.add_argument("--speed", type=float, default=1.25)
    parser.add_argument("--seeds", type=int, default=8)
    parser.add_argument(
        "--seed-values",
        default="",
        help="comma-separated explicit seeds, e.g. 17,42,91",
    )
    parser.add_argument("--steps", type=int, default=18)
    parser.add_argument("--lang", default="pl")
    parser.add_argument("--max-chunk-length", type=int, default=400)
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--keep-all", action="store_true")
    parser.add_argument("--coreml", action="store_true")
    args = parser.parse_args(argv)

    try:
        seeds = _seed_values(args.seeds, args.seed_values)
    except (TypeError, ValueError) as exc:
        parser.error(str(exc))
    tts = make_tts(coreml=args.coreml)
    style = tts.get_voice_style(args.voice)
    temporary = Path(tempfile.mkdtemp(prefix="dan-best-take-"))
    takes: list[TakeResult] = []
    candidates_kept = False
    try:
        for seed in seeds:
            try:
                take = render_take(
                    tts=tts,
                    text=args.text,
                    style=style,
                    seed=seed,
                    output=temporary / f"seed-{seed}.wav",
                    speed=args.speed,
                    steps=args.steps,
                    lang=args.lang,
                    max_chunk_length=args.max_chunk_length,
                )
            except Exception as exc:  # noqa: BLE001 - keep ranking other seeds
                print(f"seed {seed}: FAIL ({exc})", file=sys.stderr)
                continue
            takes.append(take)
            print(f"seed {seed}: {take.score:.2f} {take.metrics}")
        best = choose_best(takes)
        args.output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        shutil.copyfile(best.path, args.output)
        manifest_path = args.manifest or args.output.with_suffix(".takes.json")
        manifest_path.write_text(
            json.dumps(_manifest(takes, best), ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
        print(
            f"BEST seed={best.seed} score={best.score:.2f} "
            f"sha256={best.wav_sha256} -> {args.output}"
        )
        if args.keep_all:
            keep = args.output.with_suffix(".takes")
            if keep.exists():
                raise FileExistsError(f"candidate directory already exists: {keep}")
            temporary.replace(keep)
            temporary = keep
            candidates_kept = True
            print(f"TAKES -> {keep}")
        return 0
    finally:
        if temporary.exists() and not candidates_kept:
            shutil.rmtree(temporary)


if __name__ == "__main__":
    raise SystemExit(main())
