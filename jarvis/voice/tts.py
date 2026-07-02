"""Pluggable TTS engines (G3, decree §7.3).

Decreed engine set: Supertonic (fast) + Chatterbox (voice-clone), with the
mock engine for every test and smoke. edgeTTS, piper and XTTS are BANNED by
decree — asking for them is an explicit error, never a silent fallback.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


BANNED_ENGINES = ("edgetts", "piper", "xtts")
# Decreed but not yet implemented; each arrives in its own stage.
RESERVED_ENGINES = {
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

    def play(self, chunk: SynthesizedChunk) -> None:
        interrupt = threading.Event()
        with self._lock:
            self._current_interrupt = interrupt
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

    def __init__(self, *, config: Any) -> None:
        voice_cfg = config.voice
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
        self._voice = str(voice_cfg.supertonic_voice or "M1")
        self._lang = str(voice_cfg.supertonic_lang or "pl")
        self._steps = max(1, int(voice_cfg.supertonic_steps or 14))
        self._speed = float(voice_cfg.supertonic_speed or 1.35)
        self._timeout = max(1, int(voice_cfg.tts_timeout_seconds or 120))
        self._pad_start = max(0.0, float(getattr(voice_cfg, "playback_pad_start_seconds", 0.0) or 0.0))
        self._pad_end = max(0.0, float(getattr(voice_cfg, "playback_pad_end_seconds", 0.0) or 0.0))
        self._player_lock = threading.Lock()
        self._player_proc: subprocess.Popen[str] | None = None
        workdir = Path(os.path.expanduser(str(config.runtime.runtime_dir))) / "voice"
        workdir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(workdir, 0o700)
        self.workdir = str(workdir)

    def synthesize(self, text: str) -> SynthesizedChunk:
        clean = str(text or "").translate(_SUPERTONIC_STRIP).strip()
        if not clean:
            raise TTSEngineError(f"Nothing speakable left after sanitizing {text!r}.")
        out = Path(self.workdir) / f"tts-{uuid.uuid4().hex}.wav"
        cmd = [
            self._binary, "tts", clean, "-o", str(out),
            "--voice", self._voice, "--lang", self._lang,
            "--steps", str(self._steps), "--speed", f"{self._speed:.2f}",
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
            return SynthesizedChunk(text=text, audio=out.read_bytes())
        except subprocess.TimeoutExpired as exc:
            raise TTSEngineError(f"supertonic timed out after {self._timeout}s.") from exc
        finally:
            out.unlink(missing_ok=True)

    def play(self, chunk: SynthesizedChunk) -> None:
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
                # Own process group: stop_playback() kills the player AND
                # anything it spawned, so no orphan can hold the pipes open.
                proc = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    start_new_session=True,
                )
                self._player_proc = proc
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
    """Explicit config path, else the venv bin next to python, else PATH."""

    candidates = [explicit] if explicit else [
        str(Path(sys.executable).parent / "supertonic"),
        shutil.which("supertonic") or "",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise TTSEngineError(
        "supertonic binary not found (set voice.supertonic_binary or install "
        "the decreed package into the daemon's venv)."
    )


def build_tts_engine(name: str, *, config: Any | None = None) -> Any:
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
        return SupertonicEngine(config=config)
    raise TTSEngineError(f"Unknown TTS engine {name!r}.")


__all__ = [
    "BANNED_ENGINES",
    "BannedEngineError",
    "MockTTSEngine",
    "SupertonicEngine",
    "SynthesizedChunk",
    "TTSEngineError",
    "build_tts_engine",
]
