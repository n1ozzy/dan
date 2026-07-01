"""Prompt 06 daemon app and local HTTP API smoke tests."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pytest

from jarvis.daemon.app import DaemonApp, create_daemon_app
from jarvis.daemon.lifecycle import DaemonServer, build_server
from jarvis.daemon.state_machine import RuntimeState
from jarvis.store.db import close_quietly


ROOT = Path(__file__).resolve().parents[1]


def config_text(db_path: Path, *, port: int = 41741) -> str:
    runtime_home = db_path.parent
    return f"""
[daemon]
name = "jarvisd"
host = "127.0.0.1"
port = {port}
log_level = "INFO"

[database]
path = "{db_path}"
migrations = "manual"
destroy_existing = false

[brain]
default_adapter = "mock"
default_model = "mock-local"
timeout_seconds = 60
context_budget_chars = 24000
provider_sessions_are_memory = false

[memory]
enabled = true
max_active_blocks = 50
max_context_chars = 12000
worker_candidates_require_promotion = true

[voice]
enabled = false
speak_responses = false
broker_enabled = false
default_tts = "mock"
default_stt = "mock"
ptt_mode = "hold"
queue_persisted = true

[audio]
enabled = false
input_policy = "pin_builtin_mic"
preferred_input = "Mikrofon (MacBook Air)"
output_policy = "follow_system_default"
allow_bluetooth_microphone = false
always_listen_enabled = false

[panel]
enabled = false
api_base_url = "http://127.0.0.1:{port}"
width = 420
height = 620

[security]
localhost_only = true
require_approval_for_shell = true
require_approval_for_file_write = true
require_approval_for_network = true
destructive_tools_enabled = false

[runtime]
home = "{runtime_home}"
logs_dir = "{runtime_home / "logs"}"
runtime_dir = "{runtime_home / "runtime"}"
pid_file = "{runtime_home / "runtime" / "jarvisd.pid"}"
legacy_detection = "report_only"

[launchd]
enabled = false
label = "com.ozzy.jarvisd"
install_automatically = false
"""


def write_config(path: Path, db_path: Path, *, port: int = 41741) -> Path:
    path.write_text(config_text(db_path, port=port), encoding="utf-8")
    return path


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    return write_config(tmp_path / "jarvis.toml", tmp_path / "home" / "jarvis.db")


@pytest.fixture
def app(config_path: Path) -> Iterator[DaemonApp]:
    daemon_app = create_daemon_app(config_path)
    try:
        yield daemon_app
    finally:
        daemon_app.close()


@contextmanager
def running_server(app: DaemonApp) -> Iterator[str]:
    server = build_server(app, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, name="jarvis-test-http", daemon=True)
    thread.start()
    try:
        yield server.base_url
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
        assert not thread.is_alive()


def request_json(
    method: str,
    url: str,
    payload: object | bytes | None = None,
) -> tuple[int, dict[str, object]]:
    data: bytes | None
    headers = {"Accept": "application/json"}
    if isinstance(payload, bytes):
        data = payload
        headers["Content-Type"] = "application/json"
    elif payload is None:
        data = None
    else:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def run_cli(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
    merged_env.pop("JARVIS_CONFIG", None)
    merged_env["PYTHONPATH"] = str(ROOT)
    if env:
        merged_env.update(env)
    return subprocess.run(
        [sys.executable, "-m", "jarvis.cli", *args],
        cwd=ROOT,
        env=merged_env,
        text=True,
        capture_output=True,
        check=False,
    )


def event_types(app: DaemonApp) -> list[str]:
    assert app.event_store is not None
    return [event.type for event in app.event_store.list_after(0, limit=100)]


def test_create_daemon_app_with_temp_config_initializes_temp_db_only(config_path: Path) -> None:
    daemon_app = create_daemon_app(config_path)
    try:
        assert daemon_app.paths.db_path.is_file()
        assert daemon_app.paths.db_path.parent == config_path.parent / "home"
        assert daemon_app.paths.home == config_path.parent / "home"
    finally:
        daemon_app.close()


def test_create_daemon_app_initialize_false_does_not_create_db(tmp_path: Path) -> None:
    db_path = tmp_path / "home" / "jarvis.db"
    config = write_config(tmp_path / "jarvis.toml", db_path)

    daemon_app = create_daemon_app(config, initialize=False)
    try:
        assert daemon_app.conn is None
        assert daemon_app.event_store is None
        assert not db_path.exists()
        assert not db_path.parent.exists()
    finally:
        daemon_app.close()


def test_app_start_appends_daemon_started(app: DaemonApp) -> None:
    app.start()

    assert "daemon.started" in event_types(app)
    assert app.started is True


def test_app_start_transitions_booting_to_idle_and_appends_state_changed(
    app: DaemonApp,
) -> None:
    app.start()

    assert app.state_machine is not None
    assert app.state_machine.state is RuntimeState.IDLE
    assert event_types(app) == ["daemon.started", "state.changed"]


def test_app_start_is_idempotent(app: DaemonApp) -> None:
    app.start()
    app.start()

    assert event_types(app) == ["daemon.started", "state.changed"]


def test_snapshot_state_returns_required_keys(app: DaemonApp) -> None:
    app.start()
    expected = {
        "service",
        "ok",
        "started",
        "state",
        "schema_version",
        "latest_event_id",
        "host",
        "port",
        "voice_enabled",
        "brain_adapter",
        "launchd_label",
    }

    snapshot = app.snapshot_state()

    assert set(snapshot) == expected
    assert snapshot["service"] == "jarvisd"
    assert snapshot["ok"] is True
    assert snapshot["started"] is True
    assert snapshot["state"] == "IDLE"
    assert snapshot["latest_event_id"] == 2


def test_app_stop_transitions_to_stopping_and_appends_daemon_stopped(app: DaemonApp) -> None:
    app.start()

    app.stop(reason="test shutdown")

    assert app.state_machine is not None
    assert app.state_machine.state is RuntimeState.STOPPING
    assert app.started is False
    assert event_types(app) == [
        "daemon.started",
        "state.changed",
        "daemon.stopped",
        "state.changed",
    ]


def test_get_health_returns_200_json_and_expected_fields(app: DaemonApp) -> None:
    app.start()
    with running_server(app) as base_url:
        status, payload = request_json("GET", f"{base_url}/health")

    assert status == 200
    assert payload["ok"] is True
    assert payload["service"] == "jarvisd"
    assert payload["state"] == "IDLE"
    assert payload["started"] is True
    assert payload["schema_version"] == 1


def test_get_state_returns_current_state_and_allowed_targets(app: DaemonApp) -> None:
    app.start()
    with running_server(app) as base_url:
        status, payload = request_json("GET", f"{base_url}/state")

    assert status == 200
    assert payload["state"] == "IDLE"
    assert set(payload["allowed_state_targets"]) == {"LISTENING", "THINKING", "ERROR", "STOPPING"}


def test_get_events_returns_ascending_events_after_after_id(app: DaemonApp) -> None:
    app.start()
    app.stop(reason="done")

    with running_server(app) as base_url:
        status, payload = request_json("GET", f"{base_url}/events?after_id=1&limit=10")

    assert status == 200
    ids = [event["id"] for event in payload["events"]]
    assert ids == sorted(ids)
    assert ids == [2, 3, 4]
    assert payload["after_id"] == 1
    assert payload["limit"] == 10
    assert payload["latest_event_id"] == 4


@pytest.mark.parametrize("query", ["after_id=bad", "limit=bad", "limit=0", "limit=1001"])
def test_get_events_rejects_invalid_query_values_with_json_400(
    app: DaemonApp,
    query: str,
) -> None:
    app.start()
    with running_server(app) as base_url:
        status, payload = request_json("GET", f"{base_url}/events?{query}")

    assert status == 400
    assert payload["status"] == 400
    assert "error" in payload


def test_get_settings_returns_empty_settings_initially(app: DaemonApp) -> None:
    with running_server(app) as base_url:
        status, payload = request_json("GET", f"{base_url}/settings")

    assert status == 200
    assert payload == {"settings": {}}


def test_post_settings_upserts_single_key(app: DaemonApp) -> None:
    with running_server(app) as base_url:
        status, payload = request_json(
            "POST",
            f"{base_url}/settings",
            {"key": "ui.theme", "value": {"mode": "dark"}},
        )

    assert status == 200
    assert payload["settings"]["ui.theme"] == {"mode": "dark"}
    row = app.conn.execute("SELECT value_json, source FROM settings WHERE key = ?", ("ui.theme",)).fetchone()
    assert json.loads(row[0]) == {"mode": "dark"}
    assert row[1] == "api"


def test_post_settings_upserts_multiple_keys(app: DaemonApp) -> None:
    with running_server(app) as base_url:
        status, payload = request_json(
            "POST",
            f"{base_url}/settings",
            {"settings": {"ui.theme": "dark", "voice.enabled": False}},
        )

    assert status == 200
    assert payload["settings"] == {"ui.theme": "dark", "voice.enabled": False}


def test_post_settings_rejects_malformed_json(app: DaemonApp) -> None:
    with running_server(app) as base_url:
        status, payload = request_json("POST", f"{base_url}/settings", b"{not-json")

    assert status == 400
    assert payload["status"] == 400
    assert "JSON" in payload["error"]


def test_post_settings_rejects_non_object_json(app: DaemonApp) -> None:
    with running_server(app) as base_url:
        status, payload = request_json("POST", f"{base_url}/settings", ["not", "object"])

    assert status == 400
    assert payload["status"] == 400


def test_post_input_text_returns_501_and_does_not_create_turns_or_brain_events(
    app: DaemonApp,
) -> None:
    app.start()
    with running_server(app) as base_url:
        status, payload = request_json("POST", f"{base_url}/input/text", {"text": "hello"})

    assert status == 501
    assert payload["status"] == 501
    assert "not implemented" in payload["error"]
    turn_count = app.conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
    assert turn_count == 0
    assert not any(event_type.startswith("brain.") for event_type in event_types(app))


def test_get_input_text_returns_not_implemented_json(app: DaemonApp) -> None:
    with running_server(app) as base_url:
        status, payload = request_json("GET", f"{base_url}/input/text")

    assert status == 501
    assert payload["status"] == 501


def test_unknown_route_returns_404_json(app: DaemonApp) -> None:
    with running_server(app) as base_url:
        status, payload = request_json("GET", f"{base_url}/missing")

    assert status == 404
    assert payload == {"error": "Not found", "status": 404}


def test_cli_health_state_and_events_can_query_ephemeral_server(
    app: DaemonApp,
    config_path: Path,
) -> None:
    app.start()
    with running_server(app) as base_url:
        health = run_cli("--config", str(config_path), "health", "--url", base_url)
        state = run_cli("--config", str(config_path), "state", "--url", base_url)
        events = run_cli(
            "--config",
            str(config_path),
            "events",
            "after",
            "--id",
            "0",
            "--limit",
            "100",
            "--url",
            base_url,
        )

    assert health.returncode == 0, health.stderr
    assert state.returncode == 0, state.stderr
    assert events.returncode == 0, events.stderr
    assert json.loads(health.stdout)["state"] == "IDLE"
    assert json.loads(state.stdout)["allowed_state_targets"]
    assert [event["type"] for event in json.loads(events.stdout)["events"]] == [
        "daemon.started",
        "state.changed",
    ]


def test_health_cli_exits_nonzero_when_daemon_is_unreachable(config_path: Path) -> None:
    result = run_cli("--config", str(config_path), "health", "--url", "http://127.0.0.1:9")

    assert result.returncode != 0
    assert "unreachable" in result.stderr.lower()


def test_no_real_home_is_touched_by_temp_config(tmp_path: Path) -> None:
    db_path = tmp_path / "home" / "jarvis.db"
    config = write_config(tmp_path / "jarvis.toml", db_path)

    daemon_app = create_daemon_app(config)
    try:
        assert str(daemon_app.paths.home).startswith(str(tmp_path))
        assert str(daemon_app.paths.db_path).startswith(str(tmp_path))
    finally:
        daemon_app.close()


def test_sqlite_schema_and_migrations_are_not_modified() -> None:
    result = subprocess.run(
        ["git", "diff", "--name-only", "--", "jarvis/store/schema.sql", "jarvis/store/migrations.py"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_runtime_files_do_not_contain_forbidden_legacy_strings() -> None:
    forbidden = (
        "/Users/n1_ozzy/Documents/dev/dan",
        "/tmp/dan",
        "afplay",
        "--dangerously-skip-permissions",
    )
    roots = (
        ROOT / "jarvis",
        ROOT / "config",
        ROOT / "scripts",
        ROOT / "launchd",
    )
    text_suffixes = {".py", ".sql", ".toml", ".md", ".sh", ".example", ".html", ".js", ".css"}
    offenders: list[tuple[str, str]] = []

    for root in roots:
        files = [path for path in root.rglob("*") if path.is_file()]
        for path in files:
            if "__pycache__" in path.parts or path.suffix not in text_suffixes:
                continue
            text = path.read_text(encoding="utf-8")
            for snippet in forbidden:
                if snippet in text:
                    offenders.append((str(path.relative_to(ROOT)), snippet))

    assert offenders == []
