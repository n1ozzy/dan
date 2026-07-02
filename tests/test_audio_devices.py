"""G1 AudioDeviceManager tests (CONTRACTS §9, ADR-012).

The manager OWNS device state: it reads devices (native/fake backend),
applies policy (pin builtin mic, output follows system default, bluetooth
microphone warning), persists point-in-time snapshots, and never mutates
system audio settings.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.audio.devices import (
    AudioBackendError,
    AudioDeviceManager,
    parse_system_profiler_payload,
)
from jarvis.audio.models import AudioDevice, AudioDeviceState
from jarvis.audio.policy import apply_policy
from jarvis.store.db import close_quietly, initialize_database
from tests.git_guards import assert_schema_and_migrations_unchanged


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = initialize_database(tmp_path / "audio.db")
    try:
        yield connection
    finally:
        close_quietly(connection)


def audio_config(**overrides) -> SimpleNamespace:
    values = {
        "enabled": False,
        "backend": "fake",
        "input_policy": "pin_builtin_mic",
        "preferred_input": "Mikrofon (MacBook Air)",
        "output_policy": "follow_system_default",
        "allow_bluetooth_microphone": False,
        "always_listen_enabled": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def device(
    name: str,
    *,
    transport: str = "builtin",
    is_input: bool = False,
    is_output: bool = False,
    default_input: bool = False,
    default_output: bool = False,
) -> AudioDevice:
    return AudioDevice(
        name=name,
        transport=transport,
        is_input=is_input,
        is_output=is_output,
        default_input=default_input,
        default_output=default_output,
    )


BUILTIN_MIC = device(
    "Mikrofon (MacBook Air)", is_input=True, default_input=True
)
BUILTIN_SPEAKERS = device(
    "Głośniki (MacBook Air)", is_output=True, default_output=True
)
BT_SPEAKER = device(
    "Bose Revolve", transport="bluetooth", is_output=True, default_output=True
)
BT_MIC_DEFAULT = device(
    "Bose Revolve", transport="bluetooth", is_input=True, default_input=True
)


# --- policy -----------------------------------------------------------------


def test_policy_pins_preferred_builtin_input_when_present() -> None:
    state = apply_policy(
        (BUILTIN_MIC, BT_MIC_DEFAULT, BUILTIN_SPEAKERS), audio_config()
    )

    assert state.input_device == "Mikrofon (MacBook Air)"
    assert state.input_transport == "builtin"


def test_policy_output_follows_system_default_even_bluetooth() -> None:
    state = apply_policy((BUILTIN_MIC, BT_SPEAKER), audio_config())

    assert state.output_device == "Bose Revolve"
    assert state.output_transport == "bluetooth"
    # A bluetooth SPEAKER is fine; only the microphone degrades quality.
    assert not any("output" in warning.lower() for warning in state.warnings)


def test_policy_warns_when_only_bluetooth_microphone_is_available() -> None:
    state = apply_policy((BT_MIC_DEFAULT, BUILTIN_SPEAKERS), audio_config())

    assert state.input_device is None
    assert any("bluetooth" in warning.lower() for warning in state.warnings)


def test_policy_allows_bluetooth_microphone_when_config_says_so() -> None:
    state = apply_policy(
        (BT_MIC_DEFAULT, BUILTIN_SPEAKERS),
        audio_config(allow_bluetooth_microphone=True),
    )

    assert state.input_device == "Bose Revolve"
    assert any("bluetooth" in warning.lower() for warning in state.warnings)


def test_policy_warns_when_preferred_input_is_missing() -> None:
    other_mic = device("USB Mic", transport="usb", is_input=True, default_input=True)
    state = apply_policy((other_mic, BUILTIN_SPEAKERS), audio_config())

    assert state.input_device == "USB Mic"
    assert any("preferred" in warning.lower() for warning in state.warnings)


# --- system_profiler parser --------------------------------------------------


def test_parser_reads_real_system_profiler_shape() -> None:
    payload = {
        "SPAudioDataType": [
            {
                "_items": [
                    {
                        "_name": "Bose Revolve+ II SoundLink",
                        "coreaudio_device_input": 1,
                        "coreaudio_device_transport": "coreaudio_device_type_bluetooth",
                    },
                    {
                        "_name": "Bose Revolve+ II SoundLink",
                        "coreaudio_default_audio_output_device": "spaudio_yes",
                        "coreaudio_device_output": 2,
                        "coreaudio_device_transport": "coreaudio_device_type_bluetooth",
                    },
                    {
                        "_name": "Mikrofon (MacBook Air)",
                        "coreaudio_default_audio_input_device": "spaudio_yes",
                        "coreaudio_device_input": 1,
                        "coreaudio_device_transport": "coreaudio_device_type_builtin",
                    },
                    {
                        "_name": "Głośniki (MacBook Air)",
                        "coreaudio_device_output": 2,
                        "coreaudio_device_transport": "coreaudio_device_type_builtin",
                    },
                ],
                "_name": "coreaudio_device",
            }
        ]
    }

    devices = parse_system_profiler_payload(payload)

    by_key = {(d.name, d.is_input, d.is_output) for d in devices}
    assert ("Mikrofon (MacBook Air)", True, False) in by_key
    assert ("Bose Revolve+ II SoundLink", False, True) in by_key
    default_input = [d for d in devices if d.default_input]
    default_output = [d for d in devices if d.default_output]
    assert [d.name for d in default_input] == ["Mikrofon (MacBook Air)"]
    assert [d.name for d in default_output] == ["Bose Revolve+ II SoundLink"]
    bt = [d for d in devices if d.transport == "bluetooth"]
    assert len(bt) == 2


def test_parser_handles_empty_payload() -> None:
    assert parse_system_profiler_payload({}) == ()


# --- manager (fake backend, persistence, dedup) -------------------------------


def test_unknown_backend_fails_at_construction(conn: sqlite3.Connection) -> None:
    with pytest.raises(AudioBackendError):
        AudioDeviceManager(conn, config=audio_config(backend="nope"))


def test_fake_backend_snapshot_persists_and_warns(conn: sqlite3.Connection) -> None:
    manager = AudioDeviceManager(conn, config=audio_config())

    state = manager.current()

    # The fake fixture always contains a bluetooth default microphone so
    # every smoke proves the warning path (pattern: fake fixtures carry the
    # risky case).
    assert isinstance(state, AudioDeviceState)
    assert any("bluetooth" in warning.lower() for warning in state.warnings)
    rows = conn.execute(
        "SELECT preferred_input, output_policy, warning FROM audio_device_snapshots"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "Mikrofon (MacBook Air)"
    assert rows[0][1] == "follow_system_default"
    assert rows[0][2]


def test_repeated_current_does_not_duplicate_snapshots(conn: sqlite3.Connection) -> None:
    manager = AudioDeviceManager(conn, config=audio_config())

    manager.current()
    manager.current()
    manager.current()

    count = conn.execute("SELECT COUNT(*) FROM audio_device_snapshots").fetchone()[0]
    assert count == 1


def test_snapshot_event_emitted_once_per_change(conn: sqlite3.Connection) -> None:
    events: list[tuple[str, dict]] = []

    class FakeEventStore:
        def append(self, event_type, source, payload):
            events.append((getattr(event_type, "value", str(event_type)), payload))

    manager = AudioDeviceManager(
        conn, config=audio_config(), event_store=FakeEventStore()
    )
    manager.current()
    manager.current()

    assert len(events) == 1
    assert events[0][0] == "audio.devices.snapshot"


# --- daemon integration + API -------------------------------------------------


def test_daemon_exposes_audio_devices_endpoint(tmp_path: Path) -> None:
    from jarvis.daemon.app import create_daemon_app
    from tests.test_api_smoke import config_text, request_json, running_server

    config_path = tmp_path / "jarvis.toml"
    config_path.write_text(
        config_text(tmp_path / "home" / "jarvis.db").replace(
            "[audio]\nenabled = false",
            '[audio]\nenabled = false\nbackend = "fake"',
        ),
        encoding="utf-8",
    )
    daemon_app = create_daemon_app(config_path)
    daemon_app.start()
    try:
        with running_server(daemon_app) as base_url:
            status, payload = request_json("GET", f"{base_url}/audio/devices")
        assert status == 200
        audio = payload.get("audio") or {}
        assert audio.get("output_device") == "Głośniki (MacBook Air)"
        assert audio.get("input_device") is None
        assert any("bluetooth" in w.lower() for w in audio.get("warnings", []))
        assert audio.get("preferred_input")
    finally:
        daemon_app.close()


def test_daemon_refuses_unknown_audio_backend(tmp_path: Path) -> None:
    from jarvis.daemon.app import create_daemon_app
    from tests.test_api_smoke import config_text

    config_path = tmp_path / "jarvis.toml"
    config_path.write_text(
        config_text(tmp_path / "home" / "jarvis.db").replace(
            "[audio]\nenabled = false",
            '[audio]\nenabled = false\nbackend = "bogus"',
        ),
        encoding="utf-8",
    )
    daemon_app = create_daemon_app(config_path)
    with pytest.raises(Exception):
        daemon_app.start()


def test_manager_never_calls_system_mutation_commands() -> None:
    source = (ROOT / "jarvis" / "audio" / "devices.py").read_text(encoding="utf-8")

    # Read-only manager: it inspects devices, it never switches them.
    for forbidden in ("SwitchAudioSource", "osascript", "set volume"):
        assert forbidden not in source


def test_schema_and_migrations_are_unchanged() -> None:
    assert_schema_and_migrations_unchanged(ROOT)
