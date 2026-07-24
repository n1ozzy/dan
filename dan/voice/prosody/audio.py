"""WAV processing and deterministic scene assembly for offline prosody."""

from __future__ import annotations

import hashlib
import io
import math
import os
import shutil
import subprocess
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np

from dan.voice.tts import mastering_filter


class AudioProcessingError(RuntimeError):
    """A rendered candidate cannot be converted into safe scene audio."""


@dataclass(frozen=True)
class PCMBuffer:
    sample_rate: int
    samples: np.ndarray

    @property
    def duration_seconds(self) -> float:
        if self.sample_rate <= 0:
            return 0.0
        return float(self.samples.shape[0]) / float(self.sample_rate)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def read_wav_bytes(payload: bytes, *, mono: bool = True) -> PCMBuffer:
    try:
        with wave.open(io.BytesIO(payload), "rb") as reader:
            channels = reader.getnchannels()
            sample_width = reader.getsampwidth()
            sample_rate = reader.getframerate()
            frames = reader.getnframes()
            compression = reader.getcomptype()
            raw = reader.readframes(frames)
    except (EOFError, wave.Error) as exc:
        raise AudioProcessingError(f"invalid WAV: {exc}") from exc

    if compression != "NONE":
        raise AudioProcessingError(f"compressed WAV is unsupported: {compression}")
    if channels <= 0 or sample_rate <= 0 or frames <= 0:
        raise AudioProcessingError("WAV has no usable audio frames")

    decoded = _decode_pcm(raw, sample_width)
    expected = frames * channels
    if decoded.size < expected:
        raise AudioProcessingError(
            f"truncated WAV PCM: expected {expected} samples, got {decoded.size}"
        )
    decoded = decoded[:expected].reshape(frames, channels)
    if mono and channels > 1:
        decoded = np.mean(decoded, axis=1, keepdims=True, dtype=np.float64).astype(np.float32)
    if mono:
        decoded = decoded[:, 0]
    return PCMBuffer(sample_rate=sample_rate, samples=np.asarray(decoded, dtype=np.float32))


def write_wav_bytes(buffer: PCMBuffer) -> bytes:
    samples = np.asarray(buffer.samples, dtype=np.float32)
    if samples.ndim == 1:
        samples = samples[:, None]
    if samples.ndim != 2 or samples.shape[0] <= 0:
        raise AudioProcessingError("cannot write an empty PCM buffer")
    if buffer.sample_rate <= 0:
        raise AudioProcessingError("sample rate must be positive")
    samples = np.nan_to_num(samples, nan=0.0, posinf=0.999, neginf=-0.999)
    pcm = np.clip(samples, -1.0, 0.9999695)
    pcm16 = np.rint(pcm * 32767.0).astype("<i2")
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(samples.shape[1])
        writer.setsampwidth(2)
        writer.setframerate(buffer.sample_rate)
        writer.writeframes(pcm16.tobytes(order="C"))
    return output.getvalue()


def postprocess_selected_wav(
    raw_wav: bytes,
    *,
    mastering_profile: str,
    gain_db: float,
    dsp_chain: str = "none",
    ffmpeg_binary: str = "ffmpeg",
    target_sample_rate: int = 44_100,
    trim_threshold_db: float = -52.0,
    preserve_leading_seconds: float = 0.030,
    preserve_trailing_seconds: float = 0.080,
    fade_seconds: float = 0.006,
) -> bytes:
    """Apply accepted mastering without per-utterance loudnorm.

    The selected take is mastered first, then conservatively trimmed, then
    micro-faded. This preserves word attacks and endings better than trimming
    before dynamics processing.
    """

    profile = str(mastering_profile or "default").strip().lower()
    if not math.isfinite(float(gain_db)):
        raise AudioProcessingError("gain_db must be finite")

    ffmpeg = shutil.which(ffmpeg_binary)
    if ffmpeg:
        processed = _ffmpeg_master(
            raw_wav,
            ffmpeg=ffmpeg,
            profile=profile,
            gain_db=float(gain_db),
            dsp_chain=dsp_chain,
            sample_rate=target_sample_rate,
        )
    else:
        if profile not in {"", "default", "none"} or str(
            dsp_chain or "none"
        ).strip().lower() != "none":
            raise AudioProcessingError(
                f"ffmpeg is required for mastering profile {profile!r} or persona DSP"
            )
        buffer = read_wav_bytes(raw_wav, mono=True)
        if buffer.sample_rate != target_sample_rate:
            raise AudioProcessingError(
                f"ffmpeg is unavailable and raw WAV is {buffer.sample_rate} Hz; "
                f"expected {target_sample_rate} Hz"
            )
        processed = write_wav_bytes(_safe_fixed_gain(buffer, float(gain_db)))

    buffer = read_wav_bytes(processed, mono=True)
    trimmed = conservative_trim(
        buffer,
        threshold_db=trim_threshold_db,
        preserve_leading_seconds=preserve_leading_seconds,
        preserve_trailing_seconds=preserve_trailing_seconds,
    )
    faded = apply_micro_fades(trimmed, fade_seconds=fade_seconds)
    return write_wav_bytes(faded)


def conservative_trim(
    buffer: PCMBuffer,
    *,
    threshold_db: float = -52.0,
    preserve_leading_seconds: float = 0.030,
    preserve_trailing_seconds: float = 0.080,
) -> PCMBuffer:
    samples = np.asarray(buffer.samples, dtype=np.float32)
    if samples.ndim != 1 or samples.size == 0:
        raise AudioProcessingError("conservative_trim expects non-empty mono audio")
    threshold = 10.0 ** (threshold_db / 20.0)
    frame = max(1, int(round(buffer.sample_rate * 0.010)))
    padded = np.pad(samples, (0, (-samples.size) % frame))
    rms = np.sqrt(np.mean(padded.reshape(-1, frame) ** 2, axis=1) + 1e-12)
    active = np.flatnonzero(rms >= threshold)
    if active.size == 0:
        return buffer

    first = int(active[0] * frame)
    last = min(samples.size, int((active[-1] + 1) * frame))
    preserve_before = int(round(preserve_leading_seconds * buffer.sample_rate))
    preserve_after = int(round(preserve_trailing_seconds * buffer.sample_rate))
    start = max(0, first - preserve_before)
    end = min(samples.size, last + preserve_after)
    # Never turn a usable candidate into a tiny click because the detector met
    # an isolated high-energy sample.
    if end - start < int(0.12 * buffer.sample_rate):
        return buffer
    return PCMBuffer(sample_rate=buffer.sample_rate, samples=samples[start:end].copy())


def apply_micro_fades(buffer: PCMBuffer, *, fade_seconds: float = 0.006) -> PCMBuffer:
    samples = np.asarray(buffer.samples, dtype=np.float32).copy()
    fade = min(samples.size // 2, int(round(buffer.sample_rate * fade_seconds)))
    if fade <= 1:
        return PCMBuffer(sample_rate=buffer.sample_rate, samples=samples)
    # Half-cosine is smooth at both ends and does not audibly reshape attacks
    # over a six-millisecond safety window.
    phase = np.linspace(0.0, math.pi / 2.0, fade, endpoint=True, dtype=np.float32)
    ramp = np.sin(phase) ** 2
    samples[:fade] *= ramp
    samples[-fade:] *= ramp[::-1]
    return PCMBuffer(sample_rate=buffer.sample_rate, samples=samples)


def concatenate_wavs(
    parts: Iterable[tuple[bytes, float]],
    *,
    leading_silence_seconds: float = 0.0,
    sample_rate: int = 44_100,
) -> bytes:
    arrays: list[np.ndarray] = []
    if leading_silence_seconds > 0:
        arrays.append(
            np.zeros(
                _seconds_to_frames(leading_silence_seconds, sample_rate),
                dtype=np.float32,
            )
        )

    saw_audio = False
    for payload, silence_after in parts:
        buffer = read_wav_bytes(payload, mono=True)
        if buffer.sample_rate != sample_rate:
            raise AudioProcessingError(
                f"cannot concatenate {buffer.sample_rate} Hz with {sample_rate} Hz"
            )
        arrays.append(np.asarray(buffer.samples, dtype=np.float32))
        saw_audio = True
        if silence_after > 0:
            arrays.append(
                np.zeros(
                    _seconds_to_frames(silence_after, sample_rate),
                    dtype=np.float32,
                )
            )
    if not saw_audio:
        raise AudioProcessingError("cannot concatenate an empty audio list")
    return write_wav_bytes(
        PCMBuffer(sample_rate=sample_rate, samples=np.concatenate(arrays))
    )


def atomic_write(path: str | Path, payload: bytes) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=target.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def _ffmpeg_master(
    raw_wav: bytes,
    *,
    ffmpeg: str,
    profile: str,
    gain_db: float,
    dsp_chain: str,
    sample_rate: int,
) -> bytes:
    profile_chain = mastering_filter(profile, include_loudnorm=False)
    if profile not in {"", "default", "none"} and not profile_chain:
        raise AudioProcessingError(f"unknown mastering profile: {profile!r}")
    normalized_dsp = str(dsp_chain or "none").strip()
    filters = [
        part
        for part in (
            normalized_dsp if normalized_dsp.lower() != "none" else "",
            profile_chain,
            f"volume={gain_db:.6f}dB",
        )
        if part
    ]
    # A fixed calibrated gain is stable between utterances. The limiter only
    # catches peaks; it does not normalize each clip to a new loudness target.
    filters.extend(("alimiter=limit=0.97:level=false", f"aresample={sample_rate}"))

    with tempfile.TemporaryDirectory(prefix="dan-prosody-") as directory:
        root = Path(directory)
        source = root / "input.wav"
        output = root / "output.wav"
        source.write_bytes(raw_wav)
        command = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-i",
            str(source),
            "-af",
            ",".join(filters),
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            "-c:a",
            "pcm_s16le",
            str(output),
        ]
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=120,
            check=False,
        )
        if completed.returncode != 0 or not output.is_file():
            detail = (completed.stderr or completed.stdout or "unknown ffmpeg error").strip()
            raise AudioProcessingError(f"offline mastering failed: {detail[:500]}")
        return output.read_bytes()


def _safe_fixed_gain(buffer: PCMBuffer, gain_db: float) -> PCMBuffer:
    samples = np.asarray(buffer.samples, dtype=np.float32)
    multiplier = 10.0 ** (gain_db / 20.0)
    gained = samples * multiplier
    peak = float(np.max(np.abs(gained))) if gained.size else 0.0
    if peak > 0.97:
        gained *= 0.97 / peak
    return PCMBuffer(sample_rate=buffer.sample_rate, samples=gained.astype(np.float32))


def _decode_pcm(raw: bytes, sample_width: int) -> np.ndarray:
    if sample_width == 1:
        return ((np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0)
    if sample_width == 2:
        return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if sample_width == 3:
        packed = np.frombuffer(raw, dtype=np.uint8)
        if packed.size % 3:
            raise AudioProcessingError("invalid 24-bit PCM byte count")
        triples = packed.reshape(-1, 3).astype(np.int32)
        values = triples[:, 0] | (triples[:, 1] << 8) | (triples[:, 2] << 16)
        values = np.where(values & 0x800000, values - 0x1000000, values)
        return values.astype(np.float32) / 8_388_608.0
    if sample_width == 4:
        return np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2_147_483_648.0
    raise AudioProcessingError(f"unsupported PCM sample width: {sample_width}")


def _seconds_to_frames(seconds: float, sample_rate: int) -> int:
    if not math.isfinite(float(seconds)) or seconds < 0:
        raise AudioProcessingError("silence duration must be finite and non-negative")
    return int(round(float(seconds) * sample_rate))


__all__ = [
    "AudioProcessingError",
    "PCMBuffer",
    "apply_micro_fades",
    "atomic_write",
    "concatenate_wavs",
    "conservative_trim",
    "postprocess_selected_wav",
    "read_wav_bytes",
    "sha256_bytes",
    "write_wav_bytes",
]
