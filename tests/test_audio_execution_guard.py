"""Fail-closed execution guards for test-time audio and microphone edges."""

from __future__ import annotations

import builtins
import os
import subprocess
import sys
from pathlib import Path

import pytest

import dan.voice.player as player_module
import dan.voice.tts as tts_module
from dan.audio.execution import (
    AUDIO_EXECUTABLE_NAMES,
    AudioExecutionDisabled,
    MicrophoneExecutionDisabled,
    assert_audio_execution_allowed,
    assert_microphone_execution_allowed,
)
from dan.voice.player import CoreAudioPlayer
from tests.test_audio_player import FakeCoreAudioBackend, chunks
from tests.test_voice_recorder import build_sox, spawn_lines
from tests.test_voice_tts_supertonic import build_engine, snapshot

ROOT = Path(__file__).resolve().parents[1]


def test_disable_audio_blocks_coreaudio_and_supertonic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DAN_DISABLE_AUDIO", "1")
    backend = FakeCoreAudioBackend()

    with pytest.raises(AudioExecutionDisabled, match="coreaudio playback"):
        CoreAudioPlayer(backend=backend).play(
            chunks()[0],
            should_play=lambda: True,
            on_started=lambda: None,
        )

    assert backend.started == 0
    assert backend.audio == []

    engine, argv_file = build_engine(tmp_path)
    version_calls = tmp_path / "supertonic-version-calls.txt"
    with pytest.raises(AudioExecutionDisabled, match="supertonic synthesis"):
        engine.synthesize("never", snapshot())

    assert not argv_file.exists()
    assert not version_calls.exists()


def test_disable_audio_blocks_default_backend_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DAN_DISABLE_AUDIO", "1")
    calls = 0

    def forbidden_backend_factory() -> object:
        nonlocal calls
        calls += 1
        raise AssertionError("native backend factory executed")

    monkeypatch.setattr(
        player_module,
        "_AVFoundationBackend",
        forbidden_backend_factory,
    )

    with pytest.raises(AudioExecutionDisabled, match="native backend construction"):
        CoreAudioPlayer()

    assert calls == 0


def test_disable_audio_blocks_private_native_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DAN_DISABLE_AUDIO", "1")
    real_import = builtins.__import__

    def guarded_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "AVFoundation":
            raise AssertionError("AVFoundation import executed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    with pytest.raises(AudioExecutionDisabled, match="native backend initialization"):
        player_module._AVFoundationBackend()

    class ForbiddenNativeObject:
        def __getattr__(self, name: str) -> object:
            raise AssertionError(f"native method executed: {name}")

    backend = object.__new__(player_module._AVFoundationBackend)
    backend._av = ForbiddenNativeObject()
    backend._engine = ForbiddenNativeObject()
    backend._node = ForbiddenNativeObject()
    backend._connected_format = None

    with pytest.raises(AudioExecutionDisabled, match="native graph initialization"):
        backend._build_graph()
    with pytest.raises(AudioExecutionDisabled, match="native route start"):
        backend.start()
    with pytest.raises(AudioExecutionDisabled, match="native buffer scheduling"):
        backend.play(object(), lambda: None)


def test_disable_audio_blocks_each_supertonic_execution_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, argv_file = build_engine(tmp_path)
    version_calls = tmp_path / "supertonic-version-calls.txt"
    monkeypatch.setenv("DAN_DISABLE_AUDIO", "1")

    with pytest.raises(AudioExecutionDisabled, match="version probe"):
        engine._detect_engine_semver()
    with pytest.raises(AudioExecutionDisabled, match="CLI synthesis"):
        engine._synth_cli("never", 1.0, "M3")

    engine._serve = "http://127.0.0.1:9"
    with pytest.raises(AudioExecutionDisabled, match="warm-server synthesis"):
        engine._synth_serve("never", 1.0, "M3")

    engine._mastering_enabled = True
    with pytest.raises(AudioExecutionDisabled, match="audio mastering"):
        engine._apply_mastering(b"not-a-wave", "volume=1")

    engine._serve = None
    engine._serve_url = "http://127.0.0.1:9"
    engine._serve_autostart = True
    monkeypatch.setattr(
        engine,
        "_serve_alive",
        lambda: pytest.fail("warm-server health probe executed"),
    )
    with pytest.raises(AudioExecutionDisabled, match="warm-server initialization"):
        engine._ensure_serve()

    assert not argv_file.exists()
    assert not version_calls.exists()
    assert list(Path(engine.workdir).iterdir()) == []


def test_supertonic_does_not_swallow_a_guard_during_serve_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, argv_file = build_engine(tmp_path)
    render_snapshot = snapshot()
    engine._engine_semver = render_snapshot.engine_version.split("+", 1)[0]
    engine._serve = "http://127.0.0.1:9"

    def staged_guard(*, operation: str) -> None:
        if operation == "supertonic warm-server synthesis":
            raise AudioExecutionDisabled(f"audio execution disabled: {operation}")

    monkeypatch.setattr(tts_module, "assert_audio_execution_allowed", staged_guard)
    monkeypatch.setattr(
        engine,
        "_serve_alive",
        lambda: pytest.fail("serve health fallback executed"),
    )
    monkeypatch.setattr(
        engine,
        "_synth_cli",
        lambda *args: pytest.fail("CLI fallback executed"),
    )

    with pytest.raises(AudioExecutionDisabled, match="warm-server synthesis"):
        engine.synthesize("fixture", render_snapshot)

    assert not argv_file.exists()


def test_supertonic_does_not_swallow_a_plugin_guard_during_serve_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, argv_file = build_engine(tmp_path)
    engine._serve_url = "http://127.0.0.1:9"
    engine._serve_autostart = True
    monkeypatch.setattr(engine, "_serve_alive", lambda: False)
    monkeypatch.setattr(
        tts_module,
        "assert_audio_execution_allowed",
        lambda *, operation: None,
    )

    def blocked_popen(*args: object, **kwargs: object) -> None:
        raise AudioExecutionDisabled("audio subprocess execution disabled: supertonic")

    monkeypatch.setattr(tts_module.subprocess, "Popen", blocked_popen)

    with pytest.raises(AudioExecutionDisabled, match="supertonic"):
        engine._ensure_serve()

    assert engine._serve_proc is None
    assert not argv_file.exists()


def test_disable_mic_blocks_sox_before_file_or_process_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DAN_DISABLE_MIC", "1")
    recorder, argv_file = build_sox(tmp_path)

    with pytest.raises(MicrophoneExecutionDisabled, match="sox capture"):
        recorder.start()

    assert spawn_lines(argv_file) == []
    assert list(Path(recorder.workdir).iterdir()) == []


def test_disable_mic_blocks_sox_rotation_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DAN_DISABLE_MIC", "1")
    fixture_root = tmp_path / "rotation"
    fixture_root.mkdir()
    recorder, argv_file = build_sox(
        fixture_root,
        recorder_segment_seconds=1.0,
    )
    recorder._active = True

    with pytest.raises(MicrophoneExecutionDisabled, match="sox capture"):
        recorder.rotate()

    assert spawn_lines(argv_file) == []
    assert list(Path(recorder.workdir).iterdir()) == []


def test_disable_mic_rotation_still_cleans_an_existing_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder, argv_file = build_sox(
        tmp_path,
        recorder_segment_seconds=1.0,
    )

    class ExistingCapture:
        def __init__(self) -> None:
            self.signals: list[int] = []

        def poll(self) -> None:
            return None

        def send_signal(self, signal_number: int) -> None:
            self.signals.append(signal_number)

        def wait(self, *, timeout: float) -> int:
            return 0

    process = ExistingCapture()
    capture_path = Path(recorder.workdir) / "existing.wav"
    capture_path.write_bytes(b"fixture")
    recorder._proc = process  # type: ignore[assignment]
    recorder._capture_path = capture_path
    recorder._active = True
    monkeypatch.setenv("DAN_DISABLE_MIC", "1")

    with pytest.raises(MicrophoneExecutionDisabled, match="sox capture"):
        recorder.rotate()

    assert process.signals
    assert recorder._proc is None
    assert recorder._capture_path is None
    assert not capture_path.exists()
    assert spawn_lines(argv_file) == []


def test_disable_mic_restart_still_cleans_a_dead_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder, argv_file = build_sox(tmp_path)

    class DeadCapture:
        def poll(self) -> int:
            return 1

    capture_path = Path(recorder.workdir) / "dead.wav"
    capture_path.write_bytes(b"fixture")
    recorder._proc = DeadCapture()  # type: ignore[assignment]
    recorder._capture_path = capture_path
    monkeypatch.setenv("DAN_DISABLE_MIC", "1")

    with pytest.raises(MicrophoneExecutionDisabled, match="sox capture"):
        recorder.start()

    assert recorder._proc is None
    assert recorder._capture_path is None
    assert not capture_path.exists()
    assert spawn_lines(argv_file) == []


@pytest.mark.parametrize("executable", sorted(AUDIO_EXECUTABLE_NAMES))
def test_audio_guard_plugin_blocks_each_known_executable_before_popen(
    executable: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DAN_DISABLE_AUDIO", "1")
    command = [f"/definitely-missing/{executable}", "fixture.wav"]

    with pytest.raises(AudioExecutionDisabled, match=executable):
        subprocess.Popen(command)


@pytest.mark.parametrize(
    ("command", "kwargs", "expected"),
    [
        ("/definitely-missing/AFPLAY fixture.wav", {}, "AFPLAY"),
        ([b"/definitely-missing/SAY", b"hello"], {}, "SAY"),
        (
            [sys.executable, "-c", "raise SystemExit(99)"],
            {"executable": "/definitely-missing/SUPERTONIC"},
            "SUPERTONIC",
        ),
    ],
)
def test_audio_guard_plugin_normalizes_string_bytes_absolute_and_override_forms(
    command: object,
    kwargs: dict[str, object],
    expected: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DAN_DISABLE_AUDIO", "1")

    with pytest.raises(AudioExecutionDisabled, match=expected.lower()):
        subprocess.Popen(command, **kwargs)  # type: ignore[arg-type]


def test_non_audio_subprocess_still_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DAN_DISABLE_AUDIO", "1")
    completed = subprocess.run(
        [sys.executable, "-c", "print('ok')"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert completed.stdout.strip() == "ok"


def test_audio_guard_plugin_is_really_loaded(pytestconfig: pytest.Config) -> None:
    from tests import audio_guard_plugin

    plugin = pytestconfig.pluginmanager.get_plugin("tests.audio_guard_plugin")
    assert plugin is audio_guard_plugin
    assert (
        getattr(pytestconfig, audio_guard_plugin.CONFIG_MARKER_ATTRIBUTE, None)
        is audio_guard_plugin.PLUGIN_LOADED_MARKER
    )


def test_disable_flags_fail_closed_when_plugin_is_intentionally_omitted(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    runtime = tmp_path / "runtime"
    home.mkdir()
    runtime.mkdir()
    environment = {
        **os.environ,
        "HOME": str(home),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "TMPDIR": str(runtime),
        "DAN_RUNTIME_DIR": str(runtime),
        "DAN_DB_PATH": str(runtime / "dan.sqlite3"),
        "DAN_DISABLE_AUDIO": "1",
        "DAN_DISABLE_MIC": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    }
    environment.pop("PYTEST_ADDOPTS", None)
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            # pyproject arms the guard for every run through addopts, and a
            # subprocess inherits that. This case is specifically "the guard
            # was left out", so blank addopts to reproduce the omission
            # instead of testing an already-armed run.
            "-o",
            "addopts=",
            "-p",
            "no:cacheprovider",
            "--basetemp",
            str(runtime / "pytest-tmp"),
            "tests/test_audio_execution_guard.py::test_non_audio_subprocess_still_runs",
        ],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "tests.audio_guard_plugin must be loaded" in (completed.stdout + completed.stderr)


@pytest.mark.parametrize("value", [None, "", "0", "true", "yes", "01"])
def test_non_exact_disable_values_leave_injected_boundaries_usable(
    value: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if value is None:
        monkeypatch.delenv("DAN_DISABLE_AUDIO", raising=False)
        monkeypatch.delenv("DAN_DISABLE_MIC", raising=False)
    else:
        monkeypatch.setenv("DAN_DISABLE_AUDIO", value)
        monkeypatch.setenv("DAN_DISABLE_MIC", value)

    assert_audio_execution_allowed(operation="injected audio fixture")
    assert_microphone_execution_allowed(operation="injected microphone fixture")


def test_exact_disable_value_only_blocks_the_matching_execution_class(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DAN_DISABLE_AUDIO", "1")
    monkeypatch.setenv("DAN_DISABLE_MIC", "0")
    with pytest.raises(AudioExecutionDisabled, match="static operation label"):
        assert_audio_execution_allowed(operation="static operation label")
    assert_microphone_execution_allowed(operation="static operation label")

    monkeypatch.setenv("DAN_DISABLE_AUDIO", "0")
    monkeypatch.setenv("DAN_DISABLE_MIC", "1")
    assert_audio_execution_allowed(operation="static operation label")
    with pytest.raises(MicrophoneExecutionDisabled, match="static operation label"):
        assert_microphone_execution_allowed(operation="static operation label")
