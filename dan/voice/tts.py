"""Pluggable TTS engines (G3, decree §7.3).

Decreed engine set: Supertonic (fast) + Chatterbox (voice-clone), with the
mock engine for every test and smoke. edgeTTS, piper and XTTS are BANNED by
decree — asking for them is an explicit error, never a silent fallback.
"""

from __future__ import annotations

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

_LOGGER = logging.getLogger(__name__)


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

    Case-insensitive substring match, longest keys first, so inflected forms
    ("runtime'ie") keep their endings and longer entries win over prefixes.
    """

    rewritten = text
    for key in sorted(pronunciations, key=len, reverse=True):
        if key:
            rewritten = re.sub(re.escape(key), pronunciations[key], rewritten, flags=re.IGNORECASE)
    return rewritten


# Per-persona mastering chains, ported 1:1 from DAN's voice_broker
# (2026-07-08). asetrate*k + atempo=1/k pitches DOWN without changing tempo;
# then EQ (bass/presence), aexciter + crystalizer (transient sparkle so it
# reads as "recorded", not "synthetic"), deesser, compressor, limiter. The
# loudnorm tail evens out loudness.
#
# WHO ACTUALLY USES THESE (config/voice/personas.toml, the authority): only
# danusia -> "clean". DAN and his alias jarvis are mastering = "raw" — untouched
# timbre, but since 2026-07-22 (Ozzy: "loudnorm to norma") raw still gets the
# loudnorm tail, so no persona plays quieter than the mastered ones. "bastard"
# was judged over-driven and dropped on 2026-07-10; do not "restore" it for DAN
# on the strength of this table. "none" is the only true bypass.
_MASTER_TAIL = ",loudnorm=I=-14:TP=-2.0:LRA=7,aresample=44100"
_MASTER_PROFILES = {
    # DAN: slightly LESS bass (Ozzy 2026-07-08) — pitch 0.91->0.93, bass +3dB.
    "bastard": ("asetrate=44100*0.93,aresample=44100,atempo=1.0753,"
                "equalizer=f=105:t=q:w=1:g=3,equalizer=f=300:t=q:w=1.2:g=-2,"
                "equalizer=f=2200:t=q:w=1.5:g=3.5,aexciter=amount=2.5:drive=7:blend=0.4:freq=3500,"
                "crystalizer=i=1.8,deesser=i=0.4,"
                "acompressor=threshold=-19dB:ratio=3:attack=8:release=120:makeup=3:knee=4,"
                "alimiter=limit=0.96,aresample=44100"),
    "gritty": ("asetrate=44100*0.92,aresample=44100,atempo=1.087,"
               "equalizer=f=110:t=q:w=1:g=4,equalizer=f=1800:t=q:w=2:g=2.5,"
               "aexciter=amount=3.5:drive=8:blend=0.6:freq=3500,crystalizer=i=2.0,deesser=i=0.4,"
               "acompressor=threshold=-24dB:ratio=4:attack=3:release=60:makeup=3,"
               "alimiter=limit=0.97,aresample=44100"),
    "clean": ("asetrate=44100*0.96,aresample=44100,atempo=1.0417,"
              "equalizer=f=120:t=q:w=1:g=2.5,equalizer=f=2200:t=q:w=1.5:g=2,"
              "aexciter=amount=1.5:drive=5:blend=0.25:freq=3500,crystalizer=i=1.4,deesser=i=0.3,"
              "acompressor=threshold=-18dB:ratio=2.5:attack=6:release=90,"
              "alimiter=limit=0.95,aresample=44100"),
    "raport": ("asetrate=44100*1.015,aresample=44100,atempo=0.9852,"
               "equalizer=f=130:t=q:w=1:g=2,equalizer=f=2400:t=q:w=1.5:g=2,"
               "aexciter=amount=1.5:drive=5:blend=0.25:freq=3500,"
               "crystalizer=i=1.2,deesser=i=0.3,"
               "acompressor=threshold=-18dB:ratio=2.5:attack=6:release=90,"
               "alimiter=limit=0.95,aresample=44100"),
}

# Every mastering value a persona route may carry: the ffmpeg chains above
# plus "raw" (untouched timbre, loudnorm tail only).
MASTERING_PROFILES = frozenset(_MASTER_PROFILES) | {"raw"}

_SUPERTONIC_BUILTIN_VOICES = frozenset(
    {f"M{index}" for index in range(1, 6)}
    | {f"F{index}" for index in range(1, 6)}
)


def mastering_filter(profile: str) -> str:
    """ffmpeg -af chain for a profile, or '' when the profile is unknown/empty.

    "raw" is NOT a bypass: it keeps the timbre untouched but still returns the
    loudnorm tail, so every persona lands at the same loudness (-14 LUFS).
    """
    normalized = str(profile or "").strip().lower()
    if normalized == "raw":
        return _MASTER_TAIL.lstrip(",")
    chain = _MASTER_PROFILES.get(normalized)
    return (chain + _MASTER_TAIL) if chain else ""


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
                return r.status == 200
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
                [self._binary, "serve", "--model", self._serve_model,
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

    def _synth_serve(self, clean: str, speed: float, voice: str) -> bytes:
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
        }).encode("utf-8")
        req = urllib.request.Request(
            self._serve + "/v1/tts", data=body,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=self._timeout) as r:
            audio = r.read()
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
    ) -> bytes:
        assert_audio_execution_allowed(operation="supertonic CLI synthesis")
        out = Path(self.workdir) / f"tts-{uuid.uuid4().hex}.wav"
        cmd = [
            self._binary, "tts", clean, "-o", str(out),
            "--voice", voice, "--lang", self._lang,
            "--steps", str(self._steps), "--speed", f"{speed:.2f}",
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
        # Warm serve first (no per-chunk model reload); any failure falls back
        # to the CLI so warm-serve never regresses to silence.
        audio: bytes | None = None
        if (
            custom_style_path is None
            and self._serve is None
            and self._serve_url
            and not self._serve_autostart
        ):
            self._ensure_serve()
        if self._serve and custom_style_path is None:
            try:
                audio = self._synth_serve(clean, speed, voice)
            except AudioExecutionDisabled:
                raise
            except Exception:
                _LOGGER.warning("supertonic serve synth failed; CLI fallback.", exc_info=True)
                if not self._serve_alive():
                    self._serve = None  # server died -> stop trying, go CLI
        if audio is None:
            audio = self._synth_cli(clean, speed, voice, custom_style_path)
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
]
