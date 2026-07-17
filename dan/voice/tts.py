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
import signal
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dan.voice.models import SpeechIntent
from dan.voice.resolver import VoiceResolver


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


class MockTTSEngine:
    """Deterministic engine double: logs synth/play, produces no sound.

    `play_gate` lets tests block playback to prove the broker prefetches the
    next chunk while the previous one plays; `explode_on` triggers a
    synthesis failure for error-path tests. `stop_playback()` mirrors the
    real engines' barge-in leg 3: it interrupts only the CURRENT playback,
    which then raises like a killed player process would.
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
        self._current_interrupt: threading.Event | None = None

    def synthesize(self, text: str) -> SynthesizedChunk:
        with self._lock:
            self.log.append(("synth", text))
        if self._explode_on and self._explode_on in text:
            raise TTSEngineError(f"mock synthesis failure for {text!r}")
        return SynthesizedChunk(text=text, audio=text.encode("utf-8"))

    def play(
        self,
        chunk: SynthesizedChunk,
        should_play: Any = None,
        on_started: Any = None,
    ) -> None:
        interrupt = threading.Event()
        with self._lock:
            # Same lock stop_playback uses: the should_play re-check and the
            # commit-to-play are atomic, closing the barge-in TOCTOU (FIX-09).
            if should_play is not None and not should_play():
                raise PlaybackCancelled(f"playback skipped for {chunk.text!r}")
            self._current_interrupt = interrupt
        # Committed to play (past the should_play gate) — mark spoken now, like
        # the real engine does right after spawning its player.
        if on_started is not None:
            on_started()
        try:
            if self._play_gate is not None:
                deadline = time.monotonic() + 30
                while not self._play_gate.is_set() and time.monotonic() < deadline:
                    if interrupt.wait(0.005):
                        with self._lock:
                            self.log.append(("play_interrupted", chunk.text))
                        raise TTSEngineError(
                            f"mock playback interrupted for {chunk.text!r}"
                        )
            with self._lock:
                self.log.append(("play", chunk.text))
        finally:
            with self._lock:
                self._current_interrupt = None

    def stop_playback(self) -> None:
        with self._lock:
            interrupt = self._current_interrupt
        if interrupt is not None:
            interrupt.set()


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
# loudnorm tail evens out loudness. Raw supertonic sounds thin/robotic; this is
# what makes DAN a ziomek, not a text-to-speech readout.
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
}


def mastering_filter(profile: str) -> str:
    """ffmpeg -af chain for a profile, or '' when the profile is unknown/empty."""
    chain = _MASTER_PROFILES.get(str(profile or "").strip().lower())
    return (chain + _MASTER_TAIL) if chain else ""


class SupertonicEngine:
    """First real TTS engine (decree §7.3): shells out to the supertonic CLI.

    One subprocess per chunk keeps the daemon insulated from ONNX crashes
    (the D4 subprocess precedent) and makes cancellation a plain kill: the
    barge-in playback leg (G4c) is `stop_playback()`, which kills the
    current player process. Synthesized audio lives in RAM; the only disk
    artifacts are transient WAVs in a private runtime workdir, unlinked in
    finally blocks. Playback happens here and only here — the broker is the
    sole caller (ADR-005), so this does not add a second speaker path.
    """

    name = "supertonic"

    def __init__(
        self,
        *,
        config: Any,
        persona_provider: Any = None,
        resolver: VoiceResolver | None = None,
    ) -> None:
        voice_cfg = config.voice
        self._voice_config = voice_cfg
        if resolver is None:
            raise TTSEngineError(
                "SupertonicEngine requires a caller-supplied VoiceResolver"
            )
        warnings.warn(
            "SupertonicEngine is a compatibility caller; use snapshot-only TTS in Task 7",
            DeprecationWarning,
            stacklevel=2,
        )
        self._resolver = resolver
        # Persona binding (2026-07-08): resolve the current persona.profile per
        # chunk so a live persona switch (panel dropdown) changes voice +
        # mastering on the next spoken chunk, no daemon restart. None -> the
        # global voice/mastering, exactly as before.
        self._persona_provider = persona_provider if callable(persona_provider) else None
        self._binary = _resolve_supertonic_binary(str(voice_cfg.supertonic_binary or ""))
        player = str(voice_cfg.playback_binary or "")
        if player and "/" not in player:
            player = shutil.which(player) or player
        if not (player and Path(player).is_file() and os.access(player, os.X_OK)):
            raise TTSEngineError(
                f"Supertonic player {player!r} is not an executable file "
                "(set voice.playback_binary)."
            )
        self._player = player
        self._lang = str(voice_cfg.supertonic_lang or "pl")
        self._steps = max(1, int(voice_cfg.supertonic_steps or 14))
        self._short_chars = max(
            0, int(getattr(voice_cfg, "supertonic_short_sentence_chars", 0) or 0)
        )
        self._short_speed = float(
            getattr(voice_cfg, "supertonic_short_sentence_speed", 1.0) or 1.0
        )
        self._timeout = max(1, int(voice_cfg.tts_timeout_seconds or 120))
        self._pad_start = max(0.0, float(getattr(voice_cfg, "playback_pad_start_seconds", 0.0) or 0.0))
        self._pad_end = max(0.0, float(getattr(voice_cfg, "playback_pad_end_seconds", 0.0) or 0.0))
        self._player_lock = threading.Lock()
        self._player_proc: subprocess.Popen[str] | None = None
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
        self._serve_model = str(getattr(voice_cfg, "supertonic_serve_model", "supertonic-3") or "supertonic-3")
        self._serve_autostart = bool(getattr(voice_cfg, "supertonic_serve_autostart", False))
        self._serve_max_chunk = max(1, int(getattr(voice_cfg, "supertonic_serve_max_chunk_length", 400) or 400))
        self._serve: str | None = None
        self._serve_proc: subprocess.Popen[str] | None = None
        if self._serve_url:
            self._ensure_serve()

    # -- warm serve ----------------------------------------------------------

    def _serve_alive(self) -> bool:
        try:
            with urllib.request.urlopen(self._serve_url + "/v1/health", timeout=2) as r:
                return r.status == 200
        except Exception:
            return False

    def _ensure_serve(self) -> None:
        """Reuse a running server, else (autostart) spawn one, else stay CLI."""
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
        except Exception:
            _LOGGER.exception("supertonic serve failed to start; using CLI.")

    def _synth_serve(self, clean: str, speed: float, voice: str) -> bytes:
        """POST to the warm server -> WAV bytes. Field is `lang` NOT `language`
        (the server's pydantic model silently ignores unknown fields)."""
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

    # -- persona binding -----------------------------------------------------

    def _current_persona(self) -> str:
        """Currently selected persona.profile, or "" — fail-safe to silence-free.

        The engine must never let a provider error (settings DB hiccup) turn
        into no audio, so any failure resolves to the global voice/mastering.
        """
        if self._persona_provider is None:
            return ""
        try:
            return str(self._persona_provider() or "").strip()
        except Exception:
            _LOGGER.debug("persona provider raised; using global voice/mastering.", exc_info=True)
            return ""

    def _voice_for(self, profile: str) -> str:
        return self._render_snapshot("compatibility", profile).voice_or_style

    def _mastering_filter_for(self, profile: str) -> str:
        return mastering_filter(self._render_snapshot("compatibility", profile).mastering_profile)

    def _render_snapshot(self, text: str, profile: str):
        persona = profile or "dan"
        intent = SpeechIntent(
            text=text,
            persona=persona,
            source="dand",
            session="tts",
            participant=persona,
            priority=0,
            lane="normal",
            interrupt_policy="finish_current",
            utterance_index=0,
        )
        return self._resolver.resolve(intent)

    def _apply_mastering(self, audio: bytes, filter_chain: str) -> bytes:
        """Run the required mastering chain or fail without raw fallback."""
        if not filter_chain:
            return audio
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
        except TTSEngineError:
            raise
        except Exception as exc:
            raise TTSEngineError(f"mastering failed: {exc}") from exc
        finally:
            src.unlink(missing_ok=True)
            dst.unlink(missing_ok=True)

    def _synth_cli(self, clean: str, speed: float, voice: str) -> bytes:
        out = Path(self.workdir) / f"tts-{uuid.uuid4().hex}.wav"
        cmd = [
            self._binary, "tts", clean, "-o", str(out),
            "--voice", voice, "--lang", self._lang,
            "--steps", str(self._steps), "--speed", f"{speed:.2f}",
        ]
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

    def synthesize(self, text: str) -> SynthesizedChunk:
        profile = self._current_persona()
        snapshot = self._render_snapshot(str(text or ""), profile)
        spoken = apply_pronunciations(str(text or ""), dict(snapshot.pronunciations))
        clean = spoken.translate(_SUPERTONIC_STRIP).strip()
        if not clean:
            raise TTSEngineError(f"Nothing speakable left after sanitizing {text!r}.")
        speed = snapshot.speed
        # Resolve the persona once per chunk so a live switch takes effect on the
        # next chunk (voice + mastering both key off the same profile).
        voice = snapshot.voice_or_style
        # Warm serve first (no per-chunk model reload); any failure falls back
        # to the CLI so warm-serve never regresses to silence.
        audio: bytes | None = None
        if self._serve:
            try:
                audio = self._synth_serve(clean, speed, voice)
            except Exception:
                _LOGGER.warning("supertonic serve synth failed; CLI fallback.", exc_info=True)
                if not self._serve_alive():
                    self._serve = None  # server died -> stop trying, go CLI
        if audio is None:
            audio = self._synth_cli(clean, speed, voice)
        mastering_profile = snapshot.mastering_profile.strip().lower()
        if mastering_profile in {"raw", "none"}:
            filter_chain = ""
        else:
            filter_chain = mastering_filter(mastering_profile)
            if not filter_chain:
                raise TTSEngineError(
                    f"unknown resolved mastering profile: {snapshot.mastering_profile!r}"
                )
        return SynthesizedChunk(
            text=text,
            audio=self._apply_mastering(audio, filter_chain),
        )

    def play(
        self,
        chunk: SynthesizedChunk,
        should_play: Any = None,
        on_started: Any = None,
    ) -> None:
        path = Path(self.workdir) / f"play-{uuid.uuid4().hex}.wav"
        try:
            path.touch(mode=0o600)
            path.write_bytes(chunk.audio)
            # 44.1 kHz / 16-bit / mono is what supertonic emits; the margin
            # keeps a stuck player from hanging the broker thread forever.
            duration = len(chunk.audio) / (44100 * 2) + self._pad_start + self._pad_end
            command = [self._player, str(path)]
            if self._pad_start > 0 or self._pad_end > 0:
                command += ["pad", f"{self._pad_start:g}", f"{self._pad_end:g}"]
            with self._player_lock:
                # Re-check under the same lock stop_playback holds, right before
                # spawning: a barge-in that landed in the check->spawn gap flips
                # the row to 'cancelled', so this closes the TOCTOU (FIX-09).
                if should_play is not None and not should_play():
                    raise PlaybackCancelled(f"playback skipped for {chunk.text!r}")
                # Own process group: stop_playback() kills the player AND
                # anything it spawned, so no orphan can hold the pipes open.
                proc = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    start_new_session=True,
                )
                self._player_proc = proc
            # Player is live — audio is going out. Signal "spoken" now (outside
            # the player lock so a DB write can't block stop_playback). A
            # barge-in raising PlaybackCancelled above never reaches here, so
            # only truly-audible chunks are marked (FIX-09 anti-echo truth).
            if on_started is not None:
                on_started()
            try:
                _, stderr = proc.communicate(timeout=duration + 30)
            except subprocess.TimeoutExpired as exc:
                proc.kill()
                proc.communicate()
                raise TTSEngineError("supertonic player timed out.") from exc
            finally:
                with self._player_lock:
                    self._player_proc = None
            if proc.returncode != 0:
                raise TTSEngineError(
                    f"supertonic player exited {proc.returncode}: "
                    f"{(stderr or '').strip()[:200]}"
                )
        finally:
            path.unlink(missing_ok=True)

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

    def stop_playback(self) -> None:
        """Barge-in leg 3: kill the current player process (and only it)."""

        with self._player_lock:
            proc = self._player_proc
        if proc is None or proc.poll() is not None:
            return
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


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
    persona_provider: Any = None,
    resolver: VoiceResolver | None = None,
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
        return SupertonicEngine(
            config=config,
            persona_provider=persona_provider,
            resolver=resolver,
        )
    raise TTSEngineError(f"Unknown TTS engine {name!r}.")


__all__ = [
    "BANNED_ENGINES",
    "BannedEngineError",
    "MockTTSEngine",
    "PlaybackCancelled",
    "SupertonicEngine",
    "SynthesizedChunk",
    "TTSEngineError",
    "build_tts_engine",
]
