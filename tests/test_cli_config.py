"""Task 8: `dan config`, `dan voice hook` and `dan doctor` CLI contracts."""

from __future__ import annotations

import json
import socket
from collections.abc import Iterator
from pathlib import Path

import pytest

from dan import cli as dan_cli
from dan.config_registry import ConfigStore
from dan.daemon.app import DaemonApp

from tests.test_api_smoke import running_server, write_config
from tests.test_voice_api_contract import make_voice_app


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def voice_app(tmp_path: Path) -> Iterator[DaemonApp]:
    app = make_voice_app(tmp_path)
    try:
        yield app
    finally:
        app.close()


def run_cli(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, str]:
    rc = dan_cli.main(list(args))
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


def assert_single_json_object(out: str) -> dict[str, object]:
    stripped = out.strip()
    assert stripped
    assert "\n" not in stripped, "JSON mode must print exactly one object"
    payload = json.loads(stripped)
    assert isinstance(payload, dict)
    return payload


def unused_local_url() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        host, port = sock.getsockname()
    return f"http://{host}:{port}"


# --- dan config explain ------------------------------------------------------


def test_config_explain_names_owner_source_and_value(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = ROOT / "config" / "dan.example.toml"
    config_path = tmp_path / "dan.toml"
    config_path.write_bytes(source.read_bytes())

    rc, out, _err = run_cli(
        capsys,
        "--config",
        str(config_path),
        "config",
        "explain",
        "voice.output_gain",
        "--json",
    )

    payload = assert_single_json_object(out)
    assert rc == 0
    assert set(payload) >= {"key", "value", "owner", "source", "revision", "consumers"}
    assert payload["key"] == "voice.output_gain"
    assert payload["owner"] == "installation"
    assert payload["consumers"] == ["VoiceResolver"]


def test_config_explain_unknown_key_is_nonzero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = write_config(tmp_path / "dan.toml", tmp_path / "home" / "dan.db")
    rc, out, err = run_cli(
        capsys, "--config", str(config_path), "config", "explain", "voice.no_such_key"
    )

    assert rc != 0
    assert out == ""
    assert "voice.no_such_key" in err


# --- dan config set ----------------------------------------------------------


def test_config_set_updates_daemon_config(
    voice_app: DaemonApp, capsys: pytest.CaptureFixture[str]
) -> None:
    with running_server(voice_app) as base_url:
        rc, out, _err = run_cli(
            capsys,
            "--config",
            str(voice_app.config.source_path),
            "config",
            "set",
            "voice.hook_enabled",
            "false",
            "--json",
            "--url",
            base_url,
        )

    payload = assert_single_json_object(out)
    assert rc == 0
    assert payload["key"] == "voice.hook_enabled"
    assert payload["value"] is False
    assert ConfigStore(voice_app.config.source_path).get("voice.hook_enabled") is False


def test_config_set_rejects_dead_key(
    voice_app: DaemonApp, capsys: pytest.CaptureFixture[str]
) -> None:
    with running_server(voice_app) as base_url:
        rc, out, _err = run_cli(
            capsys,
            "--config",
            str(voice_app.config.source_path),
            "config",
            "set",
            "jarvis_speed",
            "1.2",
            "--json",
            "--url",
            base_url,
        )

    payload = assert_single_json_object(out)
    assert rc != 0
    assert "error" in payload


# --- dan voice hook ----------------------------------------------------------


def test_voice_hook_off_on_status_roundtrip(
    voice_app: DaemonApp, capsys: pytest.CaptureFixture[str]
) -> None:
    config = ["--config", str(voice_app.config.source_path)]
    with running_server(voice_app) as base_url:
        rc_off, out_off, _ = run_cli(
            capsys, *config, "voice", "hook", "off", "--json", "--url", base_url
        )
        rc_status, out_status, _ = run_cli(
            capsys, *config, "voice", "hook", "status", "--json", "--url", base_url
        )
        rc_on, out_on, _ = run_cli(
            capsys, *config, "voice", "hook", "on", "--json", "--url", base_url
        )
        rc_status2, out_status2, _ = run_cli(
            capsys, *config, "voice", "hook", "status", "--json", "--url", base_url
        )

    assert rc_off == 0
    assert assert_single_json_object(out_off)["hook_enabled"] is False
    assert rc_status == 0
    assert assert_single_json_object(out_status)["hook_enabled"] is False
    assert rc_on == 0
    assert assert_single_json_object(out_on)["hook_enabled"] is True
    assert rc_status2 == 0
    assert assert_single_json_object(out_status2)["hook_enabled"] is True


def test_voice_hook_status_unreachable_daemon_is_nonzero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = write_config(tmp_path / "dan.toml", tmp_path / "home" / "dan.db")
    rc, out, _err = run_cli(
        capsys,
        "--config",
        str(config_path),
        "voice",
        "hook",
        "status",
        "--json",
        "--url",
        unused_local_url(),
        "--timeout",
        "0.2",
    )

    payload = assert_single_json_object(out)
    assert rc != 0
    assert payload["error"] == "daemon_unreachable"


# --- dan doctor --------------------------------------------------------------


def test_doctor_json_reports_daemon_broker_and_queue(
    voice_app: DaemonApp, capsys: pytest.CaptureFixture[str]
) -> None:
    with running_server(voice_app) as base_url:
        rc, out, _err = run_cli(
            capsys,
            "--config",
            str(voice_app.config.source_path),
            "doctor",
            "--json",
            "--url",
            base_url,
        )

    payload = assert_single_json_object(out)
    assert rc == 0
    assert payload["config_ok"] is True
    assert payload["daemon"]["status"] == "ok"
    voice_runtime = payload["voice_runtime"]
    assert voice_runtime["broker_present"] is True
    assert "engine" in voice_runtime
    assert isinstance(voice_runtime["queue_counts"], dict)


def test_doctor_json_without_daemon_reports_unknown_not_fiction(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = write_config(tmp_path / "dan.toml", tmp_path / "home" / "dan.db")
    rc, out, _err = run_cli(
        capsys,
        "--config",
        str(config_path),
        "doctor",
        "--json",
        "--url",
        unused_local_url(),
        "--timeout",
        "0.2",
    )

    payload = assert_single_json_object(out)
    assert rc == 0
    assert payload["daemon"]["status"] == "unreachable"
    voice_runtime = payload["voice_runtime"]
    assert voice_runtime["broker_present"] == "unknown"
    assert voice_runtime["engine"] == "unknown"
    assert voice_runtime["queue_counts"] == "unknown"
