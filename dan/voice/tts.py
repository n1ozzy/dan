"""Pluggable TTS engines (G3, decree §7.3).

Decreed engine set: Supertonic (fast) + Chatterbox (voice-clone), with the
mock engine for every test and smoke. edgeTTS, piper and XTTS are BANNED by
decree — asking for them is an explicit error, never a silent fallback.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from dan.audio.execution import AudioExecutionDisabled, assert_audio_execution_allowed
from dan.voice.assets import (
    AssetVerificationError,
    VoiceAsset,
    load_asset_manifest,
    sha256_file,
    verify_assets,
)
from dan.voice.models import RenderSnapshot
from dan.voice.supertonic_seeded import (
    SEED_PROTOCOL_HEADER,
    SEED_PROTOCOL_VERSION,
    SYNTHESIS_SEED_HEADER,
    seeded_supertonic_argv,
)

_LOGGER = logging.getLogger(__name__)


def _response_header(response: Any, name: str) -> str | None:
    """Read a response header from HTTPMessage or a small test double."""

    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    getter = getattr(headers, "get", None)
    if callable(getter):
        value = getter(name)
        if value is not None:
            return str(value)
    target = name.lower()
    try:
        for key, value in headers.items():
            if str(key).lower() == target:
                return str(value)
    except (AttributeError, TypeError):
        return None
    return None

BANNED_ENGINES = ("edgetts", "piper", "xtts")
# Decreed but not yet implemented; each arrives in its own stage.
RESERVED_ENGINES = {
    "chatterbox": "Chatterbox MLX voice-clone lands in G5.",
}


class TTSEngineError(Exception):
    """Raised when an engine is unknown, reserved, or fails to synthesize."""


class PlaybackCancelled(Exception):
    """Raised by play() when the should_play re-check fails under the player
    lock (FIX-09): the row was cancelled in the check->spawn gap, so no player
    is started. Deliberately NOT a TTSEngineError — it is a clean skip, not a
    failure, and the broker treats it as such (no mark_failed, no error log)."""


class BannedEngineError(TTSEngineError):
    """Raised when a decree-banned engine is requested."""


@dataclass(frozen=True)
class SynthesizedChunk:
    text: str
    audio: bytes


class TTSEngine(Protocol):
    def synthesize(self, text: str, snapshot: RenderSnapshot) -> SynthesizedChunk: ...


@dataclass(frozen=True)
class SynthesisCall:
    text: str
    snapshot: RenderSnapshot


class MockTTSEngine:
    """Deterministic snapshot-only synthesis double; it never owns playback."""

    name = "mock"

    def __init__(
        self,
        *,
        explode_on: str | None = None,
    ) -> None:
        self.log: list[tuple[str, str]] = []
        self.synth_calls: list[SynthesisCall] = []
        self._explode_on = explode_on
        self._lock = threading.Lock()

    def synthesize(self, text: str, snapshot: RenderSnapshot) -> SynthesizedChunk:
        snapshot.validate_complete()
        with self._lock:
            self.log.append(("synth", text))
            self.synth_calls.append(SynthesisCall(text=text, snapshot=snapshot))
        if self._explode_on and self._explode_on in text:
            raise TTSEngineError(f"mock synthesis failure for {text!r}")
        return SynthesizedChunk(text=text, audio=text.encode("utf-8"))


# Empirical fact (live inventory, docs/reviews/2026-07-02-voice-tools-inventory.md):
# typographic quotes crash the supertonic CLI, so they are stripped before
# synthesis. The original text stays canonical everywhere else.
_SUPERTONIC_STRIP = dict.fromkeys(map(ord, "„”“‚’‘«»"))


def apply_pronunciations(text: str, pronunciations: dict[str, str]) -> str:
    """Rewrite anglicisms to phonetic spellings before synthesis (data-driven).

    Case-insensitive token match, longest keys first. Token boundaries stop a
    short entry such as "api" from corrupting Polish words like "zapisana";
    apostrophe-inflected forms ("runtime'ie") still keep their endings.
    """

    rewritten = text
    for key in sorted(pronunciations, key=len, reverse=True):
        if key:
            pattern = rf"(?<!\w){re.escape(key)}(?!\w)"
            rewritten = re.sub(
                pattern,
                pronunciations[key],
                rewritten,
                flags=re.IGNORECASE,
            )
    return rewritten


# One public mastering route. Historical timbre profiles were unverified,
# changed character identity and repeatedly returned as false acting presets.
# ``default`` leaves timbre alone and only keeps the live loudness tail.
# ``none`` remains an internal candidate-render bypass.
_MASTER_TAIL = ",loudnorm=I=-14:TP=-2.0:LRA=7,aresample=44100"
MASTERING_PROFILES = frozenset({"default"})

_SUPERTONIC_BUILTIN_VOICES = frozenset(
    {f"M{index}" for index in range(1, 6)}
    | {f"F{index}" for index in range(1, 6)}
)


def _wav_duration_seconds(audio: bytes) -> float:
    """Return the duration of one complete Supertonic render."""

    try:
        with wave.open(io.BytesIO(audio), "rb") as reader:
            frames = reader.getnframes()
            rate = reader.getframerate()
    except (EOFError, wave.Error) as exc:
        raise TTSEngineError(f"Supertonic produced an invalid WAV: {exc}") from exc
    if frames <= 0 or rate <= 0:
        raise TTSEngineError("Supertonic produced an empty WAV")
    return frames / rate


_TONE_FILTERS = {
    "neutral": (),
    "dark": (
        "bass=g=1.4:f=180",
        "treble=g=-1.1:f=3200",
    ),
    "hard": (
        "highpass=f=58",
        "equalizer=f=2350:t=q:w=1.35:g=1.8",
    ),
    "bright": (
        "bass=g=-0.4:f=180",
        "treble=g=1.1:f=3000",
    ),
}


def _dynamic_tempo_filter(snapshot: RenderSnapshot, duration_seconds: float) -> str:
    """Build a continuous tempo ramp over the full rendered utterance.

    Supertonic receives the starting pace. ffmpeg then moves gradually to the
    requested ending pace without cutting the text into clips, so the model
    keeps the complete linguistic context. Nothing here inspects punctuation.
    """

    ratio = snapshot.tempo_end / snapshot.tempo_start
    if abs(ratio - 1.0) < 1e-6:
        return ""
    # The authored contour spans the whole utterance. Control points are a
    # technical interpolation detail, not an invented "slow final words" rule.
    ramp_duration = duration_seconds
    # Very wide legal contours need two atempo stages: each stage remains in
    # the high-quality 0.5-2.0 band while their product is the desired ratio.
    stage_count = 2 if ratio < 0.5 or ratio > 2.0 else 1
    labels = ["live"] if stage_count == 1 else ["live_1", "live_2"]
    commands: list[str] = []
    for index in range(9):
        fraction = index / 8
        when = ramp_duration * fraction
        target = 1.0 + ((ratio - 1.0) * fraction)
        stage_value = target ** (1 / stage_count)
        commands.extend(
            f"{when:.3f} {label} tempo {stage_value:.6f}"
            for label in labels
        )
    stages = ",".join(f"atempo@{label}=1.000000" for label in labels)
    return f"asendcmd=c='{';'.join(commands)}',{stages}"


def _emotion_filters(emotion: str, duration_seconds: float) -> tuple[str, ...]:
    duration = max(duration_seconds, 0.001)
    if emotion == "anger":
        return (
            "acompressor=threshold=-17dB:ratio=2.8:attack=4:release=65:makeup=1.12",
            f"volume='0.97+0.07*t/{duration:.3f}':eval=frame",
            "alimiter=limit=0.96:level=false",
        )
    if emotion == "contempt":
        return (
            "acompressor=threshold=-20dB:ratio=2.0:attack=12:release=120:makeup=1.04",
            f"volume='1.01-0.04*t/{duration:.3f}':eval=frame",
        )
    if emotion == "mockery":
        return (
            f"volume='1+0.025*sin(PI*t/{duration:.3f})':eval=frame",
        )
    if emotion == "cold":
        return (
            "acompressor=threshold=-21dB:ratio=1.8:attack=16:release=140:makeup=1.02",
            f"volume='1.01-0.02*t/{duration:.3f}':eval=frame",
        )
    return ()


def _live_prosody_components(
    snapshot: RenderSnapshot,
    *,
    duration_seconds: float,
) -> tuple[list[str], str]:
    """Return full-utterance filters and the explicit trailing pause."""

    if not isinstance(duration_seconds, (int, float)) or duration_seconds <= 0:
        raise TTSEngineError("live prosody requires a positive WAV duration")
    body: list[str] = []
    tempo_filter = _dynamic_tempo_filter(snapshot, float(duration_seconds))
    if tempo_filter:
        body.append(tempo_filter)
    body.extend(_TONE_FILTERS[snapshot.tone])
    body.extend(_emotion_filters(snapshot.emotion, float(duration_seconds)))
    pause = (
        f"apad=pad_dur={snapshot.pause_after:.3f}"
        if snapshot.pause_after > 0
        else ""
    )
    return body, pause


def live_prosody_filter(snapshot: RenderSnapshot, *, duration_seconds: float) -> str:
    """Render an explicit prosody plan without interpreting the spoken text."""

    snapshot.validate_complete()
    body, pause = _live_prosody_components(
        snapshot,
        duration_seconds=duration_seconds,
    )
    return ",".join([*body, *([pause] if pause else [])])


def mastering_filter(profile: str, *, include_loudnorm: bool = True) -> str:
    """Return the ffmpeg chain for one mastering profile.

    Live speech keeps the historical per-utterance loudnorm behavior by using
    the default ``include_loudnorm=True``. The offline prosody renderer selects
    a take first and then calls this with ``False`` so calibrated fixed gains
    can preserve scene dynamics instead of normalizing every line separately.
    """
    normalized = str(profile or "").strip().lower()
    tail = _MASTER_TAIL if include_loudnorm else ""
    if normalized == "default":
        return tail.lstrip(",")
    return ""


class SupertonicEngine:
    """Snapshot-only Supertonic synthesis; this class never owns playback."""

    name = "supertonic"

    def __init__(
        self,
        *,
        config: Any,
        serve_autostart: bool | None = None,
    ) -> None:
        voice_cfg = config.voice
        self._binary = _resolve_supertonic_binary(str(voice_cfg.supertonic_binary or ""))
        repository_root = Path(__file__).resolve().parents[2]
        manifest_setting = str(
            getattr(
                voice_cfg,
                "supertonic_custom_styles_manifest",
                "config/voice/custom_styles/manifest.json",
            )
            or "config/voice/custom_styles/manifest.json"
        )
        manifest_path = Path(os.path.expanduser(manifest_setting))
        if not manifest_path.is_absolute():
            manifest_path = repository_root / manifest_path
        try:
            custom_manifest = load_asset_manifest(manifest_path)
            verify_assets(custom_manifest, repo_root=repository_root)
        except AssetVerificationError as exc:
            raise TTSEngineError(f"invalid Supertonic custom-style manifest: {exc}") from exc
        self._custom_voice_assets: dict[str, VoiceAsset] = {
            asset.name: asset for asset in custom_manifest.assets
        }
        self._custom_voice_assets_by_path: dict[Path, VoiceAsset] = {
            asset.path.resolve(): asset for asset in custom_manifest.assets
        }
        self._lang = str(voice_cfg.supertonic_lang or "pl")
        self._steps = max(1, int(voice_cfg.supertonic_steps or 14))
        self._timeout = max(1, int(voice_cfg.tts_timeout_seconds or 120))
        workdir = Path(os.path.expanduser(str(config.runtime.runtime_dir))) / "voice"
        workdir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(workdir, 0o700)
        self.workdir = str(workdir)
        self._mastering_binary = str(getattr(voice_cfg, "mastering_binary", "ffmpeg") or "ffmpeg")
        self._mastering_enabled = bool(shutil.which(self._mastering_binary))
        # Warm serve (ported from DAN): reuse an existing server, optionally
        # autostart one, else stay None -> CLI-only. The model reloads per CLI
        # chunk (~0.64s); serve loads it once. Fallback to CLI on any failure.
        self._serve_url = str(getattr(voice_cfg, "supertonic_serve_url", "") or "").rstrip("/")
        self._serve_model = str(
            getattr(voice_cfg, "supertonic_serve_model", "supertonic-3")
            or "supertonic-3"
        )
        # Task 9: when the daemon's ChildSupervisor owns `supertonic serve`,
        # it passes serve_autostart=False so this engine only REUSES the
        # supervised server and never spawns a competing one. None keeps the
        # config behavior (standalone engine use, e.g. CLI tools).
        self._serve_autostart = (
            bool(getattr(voice_cfg, "supertonic_serve_autostart", False))
            if serve_autostart is None
            else bool(serve_autostart)
        )
        self._serve_max_chunk = max(
            1,
            int(getattr(voice_cfg, "supertonic_serve_max_chunk_length", 400) or 400),
        )
        self._serve: str | None = None
        self._serve_proc: subprocess.Popen[str] | None = None
        self._seeded_renderer_argv = seeded_supertonic_argv()
        # Lazily probed real binary version; the snapshot's engine_version
        # is only trustworthy once it matches the binary that will render it.
        self._engine_semver: str | None = None
        if self._serve_url:
            self._ensure_serve()

    def _detect_engine_semver(self) -> str:
        """`supertonic version` -> cached semver; any probe failure is loud."""
        if self._engine_semver is not None:
            return self._engine_semver
        assert_audio_execution_allowed(operation="supertonic version probe")
        try:
            proc = subprocess.run(
                [self._binary, "version"],
                capture_output=True, text=True, timeout=10, check=False,
            )
        except AudioExecutionDisabled:
            raise
        except Exception as exc:
            raise TTSEngineError(f"supertonic version probe failed: {exc}") from exc
        if proc.returncode != 0:
            raise TTSEngineError(
                f"supertonic version exited {proc.returncode}: "
                f"{(proc.stderr or '').strip()[:200]}"
            )
        output = f"{proc.stdout or ''} {proc.stderr or ''}"
        match = re.search(r"\d+\.\d+\.\d+", output)
        if match is None:
            raise TTSEngineError(
                "supertonic version output has no parsable semver: "
                f"{output.strip()[:200]!r}"
            )
        self._engine_semver = match.group(0)
        return self._engine_semver

    def _verify_engine_version(self, snapshot: RenderSnapshot) -> None:
        expected = str(snapshot.engine_version).split("+", 1)[0]
        actual = self._detect_engine_semver()
        if expected != actual:
            raise TTSEngineError(
                f"snapshot engine_version {snapshot.engine_version!r} does not "
                f"match the real supertonic binary version {actual!r}"
            )

    # -- warm serve ----------------------------------------------------------

    def _serve_alive(self) -> bool:
        try:
            with urllib.request.urlopen(self._serve_url + "/v1/health", timeout=2) as r:
                return (
                    r.status == 200
                    and _response_header(r, SEED_PROTOCOL_HEADER)
                    == SEED_PROTOCOL_VERSION
                )
        except Exception:
            return False

    def _ensure_serve(self) -> None:
        """Reuse a running server, else (autostart) spawn one, else stay CLI."""
        assert_audio_execution_allowed(operation="supertonic warm-server initialization")
        if self._serve_alive():
            self._serve = self._serve_url
            _LOGGER.info("supertonic serve: reusing %s (warm model)", self._serve_url)
            return
        if not self._serve_autostart:
            _LOGGER.info(
                "supertonic serve %s not answering and autostart off; using CLI.",
                self._serve_url,
            )
            return
        try:
            port = self._serve_url.rsplit(":", 1)[-1]
            self._serve_proc = subprocess.Popen(
                [*self._seeded_renderer_argv, "serve", "--model", self._serve_model,
                 "--port", port, "--log-level", "warning"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                text=True, start_new_session=True,
            )
            for _ in range(40):
                if self._serve_alive():
                    self._serve = self._serve_url
                    _LOGGER.info("supertonic serve: started %s (warm model)", self._serve_url)
                    return
                time.sleep(0.5)
            _LOGGER.warning("supertonic serve did not come up in 20s; using CLI.")
        except AudioExecutionDisabled:
            raise
        except Exception:
            _LOGGER.exception("supertonic serve failed to start; using CLI.")

    def _synth_serve(self, clean: str, speed: float, voice: str, seed: int) -> bytes:
        """POST to the warm server -> WAV bytes. Field is `lang` NOT `language`
        (the server's pydantic model silently ignores unknown fields)."""
        assert_audio_execution_allowed(operation="supertonic warm-server synthesis")
        body = json.dumps({
            "text": clean,
            "voice": voice,
            "lang": self._lang,
            "speed": float(speed),
            "steps": self._steps,
            "max_chunk_length": self._serve_max_chunk,
            "silence_duration": 0.0,
            "seed": seed,
        }).encode("utf-8")
        req = urllib.request.Request(
            self._serve + "/v1/tts", data=body,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=self._timeout) as r:
            protocol = _response_header(r, SEED_PROTOCOL_HEADER)
            rendered_seed = _response_header(r, SYNTHESIS_SEED_HEADER)
            audio = r.read()
        if protocol != SEED_PROTOCOL_VERSION:
            raise TTSEngineError(
                "warm Supertonic does not implement DAN's deterministic seed protocol"
            )
        if rendered_seed != str(seed):
            raise TTSEngineError(
                f"warm Supertonic rendered seed {rendered_seed!r}, expected {seed}"
            )
        if len(audio) < 1000:
            raise TTSEngineError("supertonic serve returned no usable audio.")
        return audio

    def _synthesis_voice(
        self,
        snapshot: RenderSnapshot,
    ) -> tuple[str, str | None]:
        voice_or_style = snapshot.voice_or_style
        candidate_path = Path(voice_or_style).expanduser()
        asset = (
            self._custom_voice_assets_by_path.get(candidate_path.resolve())
            if candidate_path.is_absolute()
            else None
        )
        if asset is not None:
            actual = sha256_file(asset.path)
            snapshot_hash = snapshot.asset_sha256.get(
                f"engine.{self.name}.voice:{asset.name}"
            )
            if actual != asset.sha256 or snapshot_hash != asset.sha256:
                raise TTSEngineError(
                    f"SHA-256 mismatch for Supertonic custom style {asset.name}: "
                    f"manifest={asset.sha256}, snapshot={snapshot_hash}, actual={actual}"
                )
            return asset.name, str(asset.path)
        if voice_or_style in _SUPERTONIC_BUILTIN_VOICES:
            return voice_or_style, None
        raise TTSEngineError(
            f"unverified Supertonic custom style: {voice_or_style!r}"
        )

    def _apply_mastering(self, audio: bytes, filter_chain: str) -> bytes:
        """Run the required mastering chain or fail without raw fallback."""
        if not filter_chain:
            return audio
        assert_audio_execution_allowed(operation="audio mastering")
        if not self._mastering_enabled:
            raise TTSEngineError(
                f"mastering requires executable {self._mastering_binary!r}"
            )
        src = Path(self.workdir) / f"master-in-{uuid.uuid4().hex}.wav"
        dst = Path(self.workdir) / f"master-out-{uuid.uuid4().hex}.wav"
        try:
            src.write_bytes(audio)
            proc = subprocess.run(
                [self._mastering_binary, "-y", "-i", str(src),
                 "-af", filter_chain, str(dst)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=20, check=False,
            )
            if proc.returncode == 0 and dst.is_file() and dst.stat().st_size > 44:
                return dst.read_bytes()
            raise TTSEngineError(f"mastering failed with exit code {proc.returncode}")
        except (AudioExecutionDisabled, TTSEngineError):
            raise
        except Exception as exc:
            raise TTSEngineError(f"mastering failed: {exc}") from exc
        finally:
            src.unlink(missing_ok=True)
            dst.unlink(missing_ok=True)

    def _synth_cli(
        self,
        clean: str,
        speed: float,
        voice: str,
        custom_style_path: str | None = None,
        seed: int = 0,
    ) -> bytes:
        assert_audio_execution_allowed(operation="supertonic CLI synthesis")
        out = Path(self.workdir) / f"tts-{uuid.uuid4().hex}.wav"
        cmd = [
            *self._seeded_renderer_argv, "render", clean, "-o", str(out),
            "--model", self._serve_model,
            "--voice", voice, "--lang", self._lang,
            "--steps", str(self._steps), "--speed", repr(float(speed)),
            "--max-chunk-length", str(self._serve_max_chunk),
            "--silence-duration", "0.0",
            "--seed", str(seed),
        ]
        if custom_style_path is not None:
            cmd.extend(["--custom-style-path", custom_style_path])
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=self._timeout, check=False
            )
            if proc.returncode != 0:
                raise TTSEngineError(
                    f"supertonic exited {proc.returncode}: {(proc.stderr or '').strip()[:200]}"
                )
            if not out.is_file() or out.stat().st_size < 1000:
                raise TTSEngineError("supertonic produced no usable audio output.")
            return out.read_bytes()
        except subprocess.TimeoutExpired as exc:
            raise TTSEngineError(f"supertonic timed out after {self._timeout}s.") from exc
        finally:
            out.unlink(missing_ok=True)

    def _synth_one(
        self,
        clean: str,
        speed: float,
        voice: str,
        custom_style_path: str | None,
        seed: int,
    ) -> bytes:
        """Render one sentence: warm serve first (no per-chunk model reload),
        any failure falls back to the CLI so warm-serve never regresses to
        silence."""

        if (
            custom_style_path is None
            and self._serve is None
            and self._serve_url
            and not self._serve_autostart
        ):
            self._ensure_serve()
        if self._serve and custom_style_path is None:
            try:
                return self._synth_serve(clean, speed, voice, seed)
            except AudioExecutionDisabled:
                raise
            except Exception:
                _LOGGER.warning("supertonic serve synth failed; CLI fallback.", exc_info=True)
                if not self._serve_alive():
                    self._serve = None  # server died -> stop trying, go CLI
        return self._synth_cli(clean, speed, voice, custom_style_path, seed)

    def synthesize(self, text: str, snapshot: RenderSnapshot) -> SynthesizedChunk:
        assert_audio_execution_allowed(operation="supertonic synthesis")
        snapshot.validate_complete()
        if snapshot.engine != self.name:
            raise TTSEngineError(
                f"snapshot engine {snapshot.engine!r} cannot run on {self.name!r}"
            )
        self._verify_engine_version(snapshot)
        spoken = apply_pronunciations(str(text or ""), dict(snapshot.pronunciations))
        clean = spoken.translate(_SUPERTONIC_STRIP).strip()
        if not clean:
            raise TTSEngineError(f"Nothing speakable left after sanitizing {text!r}.")
        speed = snapshot.speed
        voice, custom_style_path = self._synthesis_voice(snapshot)
        # Supertonic sees the complete utterance exactly once. Tempo, tone,
        # emotion and the trailing pause are explicit snapshot controls; they
        # never come from commas, full stops or any other text classifier.
        audio = self._synth_one(
            clean,
            speed,
            voice,
            custom_style_path,
            snapshot.seed,
        )
        _LOGGER.info(
            "supertonic raw render seed=%d text_sha256=%s wav_sha256=%s",
            snapshot.seed,
            hashlib.sha256(clean.encode("utf-8")).hexdigest(),
            hashlib.sha256(audio).hexdigest(),
        )
        # Static tone and an explicit tail do not need signal timing. Parsing
        # duration is reserved for time-varying tempo/emotional envelopes.
        duration_seconds = (
            _wav_duration_seconds(audio)
            if (
                abs(snapshot.tempo_end - snapshot.tempo_start) >= 1e-6
                or snapshot.emotion != "neutral"
            )
            else 1.0
        )
        prosody_body, trailing_pause = _live_prosody_components(
            snapshot,
            duration_seconds=duration_seconds,
        )
        mastering_profile = snapshot.mastering_profile.strip().lower()
        if mastering_profile == "none":
            filter_chain = ""
        else:
            mastering_chain = mastering_filter(mastering_profile)
            if not mastering_chain:
                raise TTSEngineError(
                    f"unknown resolved mastering profile: {snapshot.mastering_profile!r}"
                )
            filter_chain = mastering_chain
        dsp_chain = snapshot.dsp.strip()
        if dsp_chain and dsp_chain.lower() != "none":
            filter_chain = ",".join(part for part in (dsp_chain, filter_chain) if part)
        # Tone/emotional dynamics happen before loudness normalization. The
        # explicit pause is appended last, after mastering, so it remains true
        # silence and does not affect loudness measurement.
        filter_chain = ",".join(
            part
            for part in (
                ",".join(prosody_body),
                filter_chain,
                trailing_pause,
            )
            if part
        )
        return SynthesizedChunk(
            text=text,
            audio=self._apply_mastering(audio, filter_chain),
        )

    def close(self) -> None:
        """Terminate an autostarted warm-serve server (no-op if reused/absent).

        A server we did NOT start (reuse) is left running so the next engine
        keeps the warm model — same reuse behavior as DAN's broker.
        """
        proc = self._serve_proc
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                # Server ignored SIGTERM — escalate to SIGKILL so no orphan is
                # left holding the serve port for the next autostart.
                proc.kill()
                proc.wait(timeout=5)
        except Exception:
            pass
        finally:
            self._serve_proc = None

def _resolve_supertonic_binary(explicit: str) -> str:
    """Explicit config path, else the venv bin next to python, else PATH.

    If explicit is a command name (no path separator), resolve via PATH first.
    If explicit is a path (contains separator), it must exist as-is - no fallback.
    """

    if explicit:
        if "/" not in explicit:
            resolved = shutil.which(explicit)
            if resolved:
                return resolved
        else:
            if Path(explicit).is_file() and os.access(explicit, os.X_OK):
                return explicit
            raise TTSEngineError(
                f"supertonic binary not found at explicit path: {explicit}"
            )

    candidates = [
        shutil.which("supertonic") or "",
        str(Path(sys.executable).parent / "supertonic"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise TTSEngineError(
        "supertonic binary not found (set voice.supertonic_binary or install "
        "the decreed package into the daemon's venv)."
    )


def build_tts_engine(
    name: str,
    *,
    config: Any | None = None,
    serve_autostart: bool | None = None,
) -> Any:
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
    if normalized == "supertonic":
        if config is None:
            raise TTSEngineError(
                "TTS engine 'supertonic' needs the daemon config "
                "(voice.supertonic_* and runtime.runtime_dir)."
            )
        return SupertonicEngine(config=config, serve_autostart=serve_autostart)
    raise TTSEngineError(f"Unknown TTS engine {name!r}.")


__all__ = [
    "BANNED_ENGINES",
    "BannedEngineError",
    "MockTTSEngine",
    "PlaybackCancelled",
    "SupertonicEngine",
    "SynthesizedChunk",
    "SynthesisCall",
    "TTSEngine",
    "TTSEngineError",
    "build_tts_engine",
    "live_prosody_filter",
    "mastering_filter",
]
