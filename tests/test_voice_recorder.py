"""SoxRecorder tests (G4a — first real recorder behind the G2 interface).

The recorder stays a dumb sink: leases decide WHEN it runs (CONTRACTS §8)
and the AudioDeviceManager decides WHICH input it uses (ADR-012), so the
recorder receives the device through a provider callable and never touches
audio policy itself. Tests replace the sox binary with a fake script — no
real microphone is ever opened (tests never record, same rule as smoke).

Empirical §4a facts encoded here: highpass 80 Hz against hum, and gain must
come BEFORE any future `silence` effect (there is no silence effect yet —
leases end a capture, not VAD — but the ordering is asserted so the fact
survives until the effect exists).
"""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import pytest

from dan.voice.recorder import (
    MockRecorder,
    RecorderBackendError,
    SoxRecorder,
    build_recorder,
)


def write_script(path: Path, body: str) -> Path:
    path.write_text("#!/bin/bash\n" + body)
    path.chmod(0o700)
    return path


def fake_sox(
    tmp_path: Path,
    *,
    wav_bytes: int = 8000,
    exit_immediately: bool = False,
) -> tuple[Path, Path]:
    """Fake sox: fills the WAV target, records argv (one tab-joined line per
    spawn), then either lingers until SIGINT/SIGTERM or crashes at once.

    The WAV is written BEFORE the argv line on purpose: tests wait for the
    argv line, so once it appears the capture bytes are already complete and
    stop() cannot race the fake mid-write."""

    argv_file = tmp_path / "sox-argv.txt"
    tail = "exit 1" if exit_immediately else "trap 'exit 0' INT TERM\nsleep 30 &\nwait $!"
    script = write_script(
        tmp_path / "fake-sox",
        f"""
out=""
for arg in "$@"; do
  case "$arg" in *.wav) out="$arg";; esac
done
if [ -n "$out" ]; then head -c {wav_bytes} /dev/zero > "$out"; fi
printf '%s\\t' "$@" >> {argv_file}
printf '\\n' >> {argv_file}
{tail}
""",
    )
    return script, argv_file


def sox_config(tmp_path: Path, binary: Path, **voice_overrides) -> SimpleNamespace:
    voice = {
        "recorder": "sox",
        "recorder_binary": str(binary),
        "recorder_sample_rate": 16000,
        "recorder_highpass_hz": 80,
        "recorder_gain_db": 0.0,
    }
    voice.update(voice_overrides)
    return SimpleNamespace(
        voice=SimpleNamespace(**voice),
        runtime=SimpleNamespace(runtime_dir=str(tmp_path / "runtime")),
    )


def build_sox(
    tmp_path: Path,
    *,
    device: str | None = "FakeMic",
    wav_bytes: int = 8000,
    exit_immediately: bool = False,
    on_capture: Callable[[bytes], None] | None = None,
    **voice_overrides,
) -> tuple[SoxRecorder, Path]:
    binary, argv_file = fake_sox(
        tmp_path, wav_bytes=wav_bytes, exit_immediately=exit_immediately
    )
    recorder = build_recorder(
        "sox",
        config=sox_config(tmp_path, binary, **voice_overrides),
        input_device_provider=lambda: device,
        on_capture=on_capture,
    )
    return recorder, argv_file


def wait_for(predicate: Callable[[], bool], timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def spawn_lines(argv_file: Path) -> list[list[str]]:
    if not argv_file.exists():
        return []
    return [
        line.rstrip("\t").split("\t")
        for line in argv_file.read_text().splitlines()
        if line.strip()
    ]


# --- construction ------------------------------------------------------------


def test_build_recorder_mock_unchanged() -> None:
    assert isinstance(build_recorder("mock"), MockRecorder)


def test_build_recorder_unknown_backend_raises() -> None:
    with pytest.raises(RecorderBackendError):
        build_recorder("arecord")


def test_build_sox_without_config_raises() -> None:
    with pytest.raises(RecorderBackendError, match="config"):
        build_recorder("sox", input_device_provider=lambda: "FakeMic")


def test_build_sox_without_device_provider_raises(tmp_path: Path) -> None:
    binary, _ = fake_sox(tmp_path)
    with pytest.raises(RecorderBackendError, match="provider"):
        build_recorder("sox", config=sox_config(tmp_path, binary))


def test_build_sox_missing_binary_fails_at_startup(tmp_path: Path) -> None:
    config = sox_config(tmp_path, tmp_path / "no-such-sox")
    with pytest.raises(RecorderBackendError, match="binary"):
        build_recorder("sox", config=config, input_device_provider=lambda: "FakeMic")


# --- start -------------------------------------------------------------------


def test_start_spawns_sox_with_policy_device_and_chain(tmp_path: Path) -> None:
    recorder, argv_file = build_sox(tmp_path)
    try:
        recorder.start()
        assert wait_for(lambda: len(spawn_lines(argv_file)) == 1)
        args = spawn_lines(argv_file)[0]
        # Input: explicitly the policy-selected device, never the system default.
        assert args[args.index("-t") + 1] == "coreaudio"
        assert args[args.index("-t") + 2] == "FakeMic"
        # Output: 16 kHz / mono / 16-bit — the STT stack's native shape.
        assert args[args.index("-r") + 1] == "16000"
        assert args[args.index("-c") + 1] == "1"
        assert args[args.index("-b") + 1] == "16"
        # Capture file lives in the private runtime workdir.
        wav = next(a for a in args if a.endswith(".wav"))
        assert Path(wav).parent == Path(recorder.workdir)
        # Effect chain (§4a): highpass against hum; no gain when 0.
        assert args[args.index("highpass") + 1] == "80"
        assert "gain" not in args
        assert recorder.recording
    finally:
        recorder.stop()


def test_gain_follows_highpass_and_precedes_any_future_silence(tmp_path: Path) -> None:
    recorder, argv_file = build_sox(tmp_path, recorder_gain_db=10.0)
    try:
        recorder.start()
        assert wait_for(lambda: len(spawn_lines(argv_file)) == 1)
        args = spawn_lines(argv_file)[0]
        assert args[args.index("gain") + 1] == "10"
        # §4a: gain must precede a silence effect; today silence must be
        # absent entirely (leases end captures, not VAD).
        assert args.index("highpass") < args.index("gain")
        assert "silence" not in args
    finally:
        recorder.stop()


def test_start_is_idempotent_while_running(tmp_path: Path) -> None:
    recorder, argv_file = build_sox(tmp_path)
    try:
        recorder.start()
        assert wait_for(lambda: len(spawn_lines(argv_file)) == 1)
        recorder.start()
        time.sleep(0.1)
        assert len(spawn_lines(argv_file)) == 1
    finally:
        recorder.stop()


def test_no_usable_input_never_spawns(tmp_path: Path) -> None:
    # Policy said "no usable input" (e.g. bluetooth mic disabled): recording
    # from a disallowed device is worse than not recording — fail closed.
    recorder, argv_file = build_sox(tmp_path, device=None)
    recorder.start()
    time.sleep(0.1)
    assert spawn_lines(argv_file) == []
    assert not recorder.recording
    recorder.stop()


# --- stop / capture ----------------------------------------------------------


def test_stop_delivers_capture_and_cleans_workdir(tmp_path: Path) -> None:
    captures: list[bytes] = []
    recorder, argv_file = build_sox(tmp_path, on_capture=captures.append)
    recorder.start()
    assert wait_for(lambda: len(spawn_lines(argv_file)) == 1)

    recorder.stop()

    assert len(captures) == 1
    assert len(captures[0]) == 8000
    assert not recorder.recording
    # Transient WAV is transport, not truth: nothing stays on disk.
    assert list(Path(recorder.workdir).glob("rec-*.wav")) == []


def test_stop_without_start_is_noop(tmp_path: Path) -> None:
    captures: list[bytes] = []
    recorder, _ = build_sox(tmp_path, on_capture=captures.append)
    recorder.stop()
    assert captures == []
    assert not recorder.recording


def test_tiny_capture_is_discarded(tmp_path: Path) -> None:
    # A header-sized WAV is not an utterance; do not wake the STT stack.
    captures: list[bytes] = []
    recorder, argv_file = build_sox(tmp_path, wav_bytes=10, on_capture=captures.append)
    recorder.start()
    assert wait_for(lambda: len(spawn_lines(argv_file)) == 1)

    recorder.stop()

    assert captures == []
    assert list(Path(recorder.workdir).glob("rec-*.wav")) == []


def test_crashed_sox_stop_still_delivers_and_cleans(tmp_path: Path) -> None:
    captures: list[bytes] = []
    recorder, argv_file = build_sox(
        tmp_path, exit_immediately=True, on_capture=captures.append
    )
    recorder.start()
    assert wait_for(lambda: len(spawn_lines(argv_file)) == 1)
    assert wait_for(lambda: not recorder.recording)

    recorder.stop()

    assert len(captures) == 1
    assert list(Path(recorder.workdir).glob("rec-*.wav")) == []


def test_start_after_crash_spawns_again(tmp_path: Path) -> None:
    recorder, argv_file = build_sox(tmp_path, exit_immediately=True)
    recorder.start()
    assert wait_for(lambda: len(spawn_lines(argv_file)) == 1)
    assert wait_for(lambda: not recorder.recording)

    recorder.start()

    assert wait_for(lambda: len(spawn_lines(argv_file)) == 2)
    recorder.stop()


def test_on_capture_exception_does_not_break_recorder(tmp_path: Path) -> None:
    calls: list[bytes] = []

    def explode(audio: bytes) -> None:
        calls.append(audio)
        raise RuntimeError("consumer bug")

    recorder, argv_file = build_sox(tmp_path, on_capture=explode)
    recorder.start()
    assert wait_for(lambda: len(spawn_lines(argv_file)) == 1)
    recorder.stop()
    assert len(calls) == 1
    assert not recorder.recording

    # The next cycle still works.
    recorder.start()
    assert wait_for(lambda: len(spawn_lines(argv_file)) == 2)
    recorder.stop()
    assert len(calls) == 2


def test_capture_file_is_owner_only_while_recording(tmp_path: Path) -> None:
    import stat

    recorder, argv_file = build_sox(tmp_path)
    try:
        recorder.start()
        assert wait_for(lambda: len(spawn_lines(argv_file)) == 1)
        args = spawn_lines(argv_file)[0]
        wav = Path(next(a for a in args if a.endswith(".wav")))
        assert wait_for(wav.exists)
        assert stat.S_IMODE(wav.stat().st_mode) == 0o600
    finally:
        recorder.stop()


# --- locked-mode segmentation (FIX-09) ---------------------------------------


def test_rotate_delivers_the_segment_and_keeps_recording(tmp_path: Path) -> None:
    # FIX-09: a locked lease ran ONE ever-growing capture, so no transcript
    # flowed until the lock ended. Segmentation rotates the capture: the closed
    # segment is delivered while a fresh capture keeps the mic live.
    captures: list[bytes] = []
    recorder, argv_file = build_sox(
        tmp_path, on_capture=captures.append, recorder_segment_seconds=60
    )
    try:
        recorder.start()
        assert wait_for(lambda: len(spawn_lines(argv_file)) == 1)

        recorder.rotate()

        # The first segment was delivered mid-lease...
        assert wait_for(lambda: len(captures) == 1)
        assert len(captures[0]) == 8000
        # ...and a fresh capture is running (a second sox spawned).
        assert wait_for(lambda: len(spawn_lines(argv_file)) == 2)
        assert recorder.recording
    finally:
        recorder.stop()

    # stop() still delivers the final, in-progress segment.
    assert len(captures) == 2


def test_rotate_recovers_after_a_device_flap(tmp_path: Path) -> None:
    # FIX-#10: if the input device vanishes exactly when a rotation tries to
    # respawn, the capture must not stay dead for the rest of the lease — the
    # next rotation re-attempts the spawn once the device is back.
    captures: list[bytes] = []
    recorder, argv_file = build_sox(
        tmp_path, on_capture=captures.append, recorder_segment_seconds=60
    )
    try:
        recorder.start()
        assert wait_for(lambda: len(spawn_lines(argv_file)) == 1)

        # Device disappears; the rotation that fires now cannot spawn a capture.
        recorder._device_provider = lambda: None
        recorder.rotate()
        assert not recorder.recording  # capture went cold, session still active

        # Device returns; the next rotation recovers the capture.
        recorder._device_provider = lambda: "FakeMic"
        recorder.rotate()
        assert wait_for(lambda: recorder.recording)
        assert wait_for(lambda: len(spawn_lines(argv_file)) == 2)  # start + recovery
    finally:
        recorder.stop()


def test_rotate_is_a_noop_when_not_recording(tmp_path: Path) -> None:
    captures: list[bytes] = []
    recorder, argv_file = build_sox(
        tmp_path, on_capture=captures.append, recorder_segment_seconds=60
    )

    recorder.rotate()  # never started

    assert captures == []
    assert spawn_lines(argv_file) == []
    assert not recorder.recording


def test_segment_mode_off_by_default_keeps_one_capture(tmp_path: Path) -> None:
    # Hold mode (no segment interval) must behave exactly as before: one
    # capture, delivered only on stop().
    captures: list[bytes] = []
    recorder, argv_file = build_sox(tmp_path, on_capture=captures.append)
    recorder.start()
    assert wait_for(lambda: len(spawn_lines(argv_file)) == 1)

    recorder.rotate()  # no-op when segmentation is disabled

    assert captures == []
    assert len(spawn_lines(argv_file)) == 1
    recorder.stop()
    assert len(captures) == 1


# --- daemon wiring -------------------------------------------------------------


def _daemon_config_path(tmp_path: Path, binary: Path) -> Path:
    from tests.test_api_smoke import config_text

    text = config_text(tmp_path / "home" / "dan.db")
    text = text.replace(
        "queue_persisted = true",
        f'queue_persisted = true\nrecorder = "sox"\nrecorder_binary = "{binary}"',
    )
    # The fake audio fixture only has a bluetooth mic (proving the warning
    # path); allowing it gives the policy a usable input for the recorder.
    text = text.replace(
        "[audio]\nenabled = false",
        '[audio]\nenabled = true\nbackend = "fake"',
    ).replace(
        "allow_bluetooth_microphone = false",
        "allow_bluetooth_microphone = true",
    )
    config_path = tmp_path / "dan.toml"
    config_path.write_text(text, encoding="utf-8")
    return config_path


def test_daemon_builds_sox_recorder_wired_to_audio_policy(tmp_path: Path) -> None:
    from dan.daemon.app import create_daemon_app

    binary, argv_file = fake_sox(tmp_path)
    daemon_app = create_daemon_app(_daemon_config_path(tmp_path, binary))
    daemon_app.start()
    try:
        recorder = daemon_app.voice_recorder
        assert isinstance(recorder, SoxRecorder)
        recorder.start()
        assert wait_for(lambda: len(spawn_lines(argv_file)) == 1)
        args = spawn_lines(argv_file)[0]
        # ADR-012: the device comes from audio policy (fake fixture), not
        # from any recorder-side default.
        assert args[args.index("-t") + 2] == "Fake BT Headset"
        recorder.stop()
    finally:
        daemon_app.stop()


def test_daemon_dies_at_startup_on_missing_sox_binary(tmp_path: Path) -> None:
    from dan.daemon.app import create_daemon_app

    daemon_app = create_daemon_app(
        _daemon_config_path(tmp_path, tmp_path / "no-such-sox")
    )
    with pytest.raises(RecorderBackendError, match="binary"):
        daemon_app.start()
