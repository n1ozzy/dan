"""Recorder backends for the listening pipeline (G2 interface, G4a real sox).

The recorder is a dumb sink: leases decide WHEN it runs (CONTRACTS §8), the
AudioDeviceManager decides WHICH input it uses (ADR-012) — the device
arrives through a provider callable, the recorder never reads audio policy
itself. Captured audio is handed to `on_capture` as bytes in RAM; the only
disk artifact is a transient 0600 WAV in the private runtime workdir,
unlinked as soon as the capture ends (transport, not truth — D4 precedent).
"""

from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import threading
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from dan.voice.capture_policy import min_capture_ms


logger = logging.getLogger(__name__)

# A header-sized WAV is not an utterance: below this the capture is dropped
# without waking any consumer. Proper energy/VAD filtering is G4b's job.
MIN_CAPTURE_BYTES = 1024


class RecorderBackendError(Exception):
    """Raised when the recorder backend is unknown or cannot be built."""


class MockRecorder:
    """Deterministic recorder double: counts starts/stops, captures nothing."""

    name = "mock"

    def __init__(self) -> None:
        self.started = 0
        self.stopped = 0
        self.discarded = 0
        self.recording = False

    def start(self) -> None:
        if not self.recording:
            self.started += 1
            self.recording = True

    def stop(self) -> None:
        if self.recording:
            self.stopped += 1
            self.recording = False

    def discard_current_capture(self) -> None:
        self.discarded += 1


class SoxRecorder:
    """Real capture through the sox CLI (decreed stack, MASTER_PLAN §7.4).

    One subprocess per listening session: leases call start()/stop(), the
    process records 16 kHz / mono / 16-bit (the STT stack's native shape)
    from the policy-selected input device. Effect chain per the §4a facts:
    highpass 80 Hz against hum; a configured gain comes after highpass and
    would have to precede any future `silence` effect (there is none —
    leases end captures, not VAD; VAD belongs to G4b on the STT side).
    """

    name = "sox"

    def __init__(
        self,
        *,
        config: Any,
        input_device_provider: Callable[[], str | None],
        on_capture: Callable[[bytes], None] | None = None,
    ) -> None:
        voice_cfg = config.voice
        self._binary = _resolve_sox_binary(str(getattr(voice_cfg, "recorder_binary", "") or ""))
        self._sample_rate = int(getattr(voice_cfg, "recorder_sample_rate", 16000) or 16000)
        self._min_capture_ms = min_capture_ms(voice_cfg)
        self._highpass_hz = int(getattr(voice_cfg, "recorder_highpass_hz", 80) or 0)
        self._gain_db = float(getattr(voice_cfg, "recorder_gain_db", 0.0) or 0.0)
        self._device_provider = input_device_provider
        self._on_capture = on_capture
        # Locked-mode segmentation (FIX-09): > 0 rotates the capture every N
        # seconds so transcripts flow during a long lease instead of only when
        # it ends. 0 keeps the old single-capture behaviour (hold mode).
        self._segment_seconds = max(
            0.0, float(getattr(voice_cfg, "recorder_segment_seconds", 0.0) or 0.0)
        )
        self._lock = threading.Lock()
        self._proc: subprocess.Popen[bytes] | None = None
        self._capture_path: Path | None = None
        self._discard_current_capture = False
        # True between start() and stop(): a listening session is meant to be
        # capturing. rotate() uses it to recover from a device flap (respawn)
        # without turning into a "start recording" on a stopped recorder.
        self._active = False
        self._rotation_stop = threading.Event()
        self._rotation_thread: threading.Thread | None = None
        workdir = Path(os.path.expanduser(str(config.runtime.runtime_dir))) / "voice"
        workdir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(workdir, 0o700)
        self.workdir = str(workdir)

    @property
    def recording(self) -> bool:
        proc = self._proc
        return proc is not None and proc.poll() is None

    def start(self) -> None:
        detached = None
        with self._lock:
            if self._proc is not None:
                if self._proc.poll() is None:
                    return
                # The previous session died on its own (device yanked, sox
                # crash): detach what it captured (deliver below, off-lock, so
                # whisper does not run under the lock), then start fresh.
                detached = self._detach_current_locked()
            self._start_locked()
            self._active = True
        if detached is not None:
            proc, path, discard = detached
            self._deliver_segment(proc, path, discard=discard)
        self._arm_rotation()

    def _start_locked(self) -> None:
        device = self._device_provider()
        if not device:
            # Policy said "no usable input": recording from a disallowed
            # device is worse than not recording — fail closed, no spawn.
            logger.warning(
                "sox recorder not started: audio policy offers no usable input device."
            )
            return

        path = Path(self.workdir) / f"rec-{uuid.uuid4().hex}.wav"
        path.touch(mode=0o600)
        cmd = [
            self._binary,
            "-q",
            "-t", "coreaudio", device,
            "-r", str(self._sample_rate),
            "-c", "1",
            "-b", "16",
            str(path),
        ]
        if self._highpass_hz > 0:
            cmd += ["highpass", str(self._highpass_hz)]
        if self._gain_db:
            # §4a: gain must precede any future `silence` effect.
            cmd += ["gain", f"{self._gain_db:g}"]
        try:
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        except OSError as exc:
            path.unlink(missing_ok=True)
            raise RecorderBackendError(f"Failed to spawn sox recorder: {exc}") from exc
        self._capture_path = path

    def rotate(self) -> None:
        """Close the current segment and start a fresh capture (FIX-09).

        Capture-first: the new sox is spawned under the lock so the mic gap is
        minimal, and the closed segment is delivered OUTSIDE the lock (on_capture
        runs whisper — it must not block a concurrent stop()). No-op when
        segmentation is disabled.

        Recovery: if the capture is not currently running (a previous rotation
        hit a device flap and could not spawn), still attempt _start_locked so a
        transient device loss does not silently kill listening for the rest of
        the lease — the rotation thread retries every segment interval."""

        if self._segment_seconds <= 0:
            return
        detached = None
        with self._lock:
            proc, path = self._proc, self._capture_path
            if proc is not None or path is not None:
                # Healthy or dead capture with bytes to salvage — detach it for
                # delivery below (off-lock). A None proc with a set path means a
                # crashed sox; either way we hand its bytes to on_capture.
                detached = self._detach_current_locked()
            # (Re)start within an active session only: a healthy rotation, a
            # crashed sox, or a prior device-flap that couldn't spawn. On a
            # stopped/never-started recorder rotate stays a no-op.
            if detached is not None or self._active:
                self._start_locked()
        if detached is not None:
            proc, path, discard = detached
            self._deliver_segment(proc, path, discard=discard)

    def stop(self) -> None:
        self._disarm_rotation()
        with self._lock:
            self._active = False
            detached = self._detach_current_locked()
        # Deliver OUTSIDE the lock: on_capture runs whisper and must not hold the
        # recorder lock (would serialize a concurrent start()/rotate()).
        proc, path, discard = detached
        self._deliver_segment(proc, path, discard=discard)

    def discard_current_capture(self) -> None:
        """Drop the current segment when it closes."""

        with self._lock:
            if self._proc is not None or self._capture_path is not None:
                self._discard_current_capture = True

    # -- rotation thread ----------------------------------------------------

    def _arm_rotation(self) -> None:
        if self._segment_seconds <= 0:
            return
        if self._rotation_thread is not None and self._rotation_thread.is_alive():
            return
        self._rotation_stop.clear()
        self._rotation_thread = threading.Thread(
            target=self._run_rotation, name="dan-recorder-rotate", daemon=True
        )
        self._rotation_thread.start()

    def _disarm_rotation(self) -> None:
        self._rotation_stop.set()
        thread = self._rotation_thread
        if thread is not None:
            thread.join(timeout=5)
            self._rotation_thread = None

    def _run_rotation(self) -> None:
        while not self._rotation_stop.wait(self._segment_seconds):
            try:
                self.rotate()
            except Exception:  # noqa: BLE001 — a rotation hiccup must not kill listening
                logger.exception("recorder segment rotation failed; continuing.")

    # -- internals ---------------------------------------------------------

    def _detach_current_locked(
        self,
    ) -> tuple[subprocess.Popen[bytes] | None, Path | None, bool]:
        """Take ownership of the current capture and clear the shared slot, under
        the lock. The caller delivers the returned segment OUTSIDE the lock so
        whisper (on_capture) never runs while the recorder lock is held."""

        proc, path = self._proc, self._capture_path
        discard = self._discard_current_capture
        self._proc, self._capture_path = None, None
        self._discard_current_capture = False
        return proc, path, discard

    def _deliver_segment(
        self,
        proc: subprocess.Popen[bytes] | None,
        path: Path | None,
        *,
        discard: bool = False,
    ) -> None:
        """Stop one capture proc, read its WAV, and hand the bytes to on_capture.

        Takes proc/path explicitly and touches no shared state, so rotate() can
        call it OUTSIDE the lock while a fresh capture is already running."""

        if proc is not None and proc.poll() is None:
            # SIGINT is sox's documented graceful stop (it finalizes the WAV
            # header); escalate only if it ignores us.
            for sig, grace in ((signal.SIGINT, 5.0), (signal.SIGTERM, 2.0)):
                try:
                    proc.send_signal(sig)
                    proc.wait(timeout=grace)
                    break
                except subprocess.TimeoutExpired:
                    continue
                except ProcessLookupError:
                    break
            else:
                proc.kill()
                proc.wait(timeout=5.0)

        if path is None:
            return
        try:
            audio = path.read_bytes() if path.is_file() else b""
        finally:
            path.unlink(missing_ok=True)
        if len(audio) < MIN_CAPTURE_BYTES or self._on_capture is None:
            return
        if discard and _capture_duration_ms(audio, sample_rate=self._sample_rate) < self._min_capture_ms:
            return
        try:
            self._on_capture(audio)
        except Exception:  # noqa: BLE001 — a consumer bug must not kill listening
            logger.exception("voice capture consumer raised; capture dropped.")


def _resolve_sox_binary(explicit: str) -> str:
    """Explicit config path, else sox from PATH (brew install — inventory)."""

    candidates = [explicit] if explicit else [shutil.which("sox") or ""]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise RecorderBackendError(
        "sox recorder binary not found (set voice.recorder_binary or install "
        "sox — decreed stack, MASTER_PLAN §7.4)."
    )


def _capture_duration_ms(audio: bytes, *, sample_rate: int) -> float:
    pcm = audio
    if audio[:4] == b"RIFF" and audio[8:12] == b"WAVE":
        marker = audio.find(b"data", 12)
        if marker == -1 or marker + 8 > len(audio):
            pcm = b""
        else:
            pcm = audio[marker + 8 :]
    if len(pcm) % 2:
        pcm = pcm[:-1]
    return (len(pcm) / 2) / max(1, sample_rate) * 1000.0


def build_recorder(
    backend: str,
    *,
    config: Any | None = None,
    input_device_provider: Callable[[], str | None] | None = None,
    on_capture: Callable[[bytes], None] | None = None,
) -> MockRecorder | SoxRecorder:
    normalized = str(backend or "").strip().lower()
    if normalized == "mock":
        return MockRecorder()
    if normalized == "sox":
        if config is None:
            raise RecorderBackendError(
                "Recorder backend 'sox' needs the daemon config "
                "(voice.recorder_* and runtime.runtime_dir)."
            )
        if input_device_provider is None:
            raise RecorderBackendError(
                "Recorder backend 'sox' needs an input device provider "
                "(the AudioDeviceManager decides which input — ADR-012)."
            )
        return SoxRecorder(
            config=config,
            input_device_provider=input_device_provider,
            on_capture=on_capture,
        )
    raise RecorderBackendError(
        f"Unknown recorder backend {backend!r}; expected 'mock' or 'sox'."
    )


__all__ = [
    "MIN_CAPTURE_BYTES",
    "MockRecorder",
    "RecorderBackendError",
    "SoxRecorder",
    "build_recorder",
]
