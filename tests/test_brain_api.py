"""FAZA E1 brain switch API tests: /brain/adapters, /brain/current, /brain/switch."""

from __future__ import annotations

import json
import stat
from collections.abc import Iterator
from pathlib import Path

import pytest

from dan.daemon.app import (
    BRAIN_ADAPTER_SETTING_KEY,
    DaemonApp,
    create_daemon_app,
)
from tests.git_guards import assert_schema_and_migrations_unchanged
from tests.test_api_smoke import ROOT, config_text, request_json, running_server


def config_text_with_cli(db_path: Path, command: Path, *, port: int = 41741) -> str:
    return config_text(db_path, port=port) + f"""
[brain.claude_cli]
enabled = true
command = "{command}"
args = []
model = "fake-brain"
timeout_seconds = 30
"""


def write_fake_cli(tmp_path: Path, *, answer: str = "fake cli answer") -> tuple[Path, Path]:
    """Deterministic local fake CLI (pattern from smoke-tool-continuation.sh).

    Dumps the full stdin prompt to a file so tests can prove what context the
    adapter actually received, then prints a fixed answer.
    """

    prompt_dump = tmp_path / "fake-cli-prompt.txt"
    script = tmp_path / "fake-cli.sh"
    script.write_text(
        f"#!/bin/sh\ntee '{prompt_dump}' >/dev/null\nprintf '{answer}\\n'\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script, prompt_dump


@pytest.fixture
def app(tmp_path: Path) -> Iterator[DaemonApp]:
    config_path = tmp_path / "dan.toml"
    config_path.write_text(config_text(tmp_path / "home" / "dan.db"), encoding="utf-8")
    daemon_app = create_daemon_app(config_path)
    daemon_app.start()
    try:
        yield daemon_app
    finally:
        daemon_app.close()


@pytest.fixture
def cli_app(tmp_path: Path) -> Iterator[DaemonApp]:
    script, prompt_dump = write_fake_cli(tmp_path)
    config_path = tmp_path / "dan.toml"
    config_path.write_text(
        config_text_with_cli(tmp_path / "home" / "dan.db", script),
        encoding="utf-8",
    )
    daemon_app = create_daemon_app(config_path)
    daemon_app.start()
    daemon_app.fake_cli_prompt_dump = prompt_dump  # type: ignore[attr-defined]
    try:
        yield daemon_app
    finally:
        daemon_app.close()


def _settings_row(app: DaemonApp, key: str) -> object | None:
    row = app.conn.execute(
        "SELECT value_json FROM settings WHERE key = ?", (key,)
    ).fetchone()
    if row is None:
        return None
    return json.loads(row[0])


def _event_types(app: DaemonApp) -> list[str]:
    rows = app.conn.execute("SELECT type FROM events ORDER BY id").fetchall()
    return [str(row[0]) for row in rows]


def test_get_brain_adapters_lists_registered_adapters(app: DaemonApp) -> None:
    with running_server(app) as base_url:
        status, payload = request_json("GET", f"{base_url}/brain/adapters")
    assert status == 200
    assert payload["current"] == "test"
    assert payload["default"] == "test"
    adapters = payload["adapters"]
    assert [adapter["name"] for adapter in adapters] == ["test"]
    assert adapters[0]["current"] is True
    assert adapters[0]["models"] == ["test-model"]


def test_get_brain_adapters_includes_enabled_cli_adapter(cli_app: DaemonApp) -> None:
    with running_server(cli_app) as base_url:
        status, payload = request_json("GET", f"{base_url}/brain/adapters")
    assert status == 200
    names = [adapter["name"] for adapter in payload["adapters"]]
    assert names == ["claude_cli", "test"]
    assert payload["current"] == "test"


def test_get_brain_current_returns_current_adapter(app: DaemonApp) -> None:
    with running_server(app) as base_url:
        status, payload = request_json("GET", f"{base_url}/brain/current")
    assert status == 200
    assert payload == {"adapter": "test", "default": "test"}


def test_post_brain_switch_switches_persists_and_emits_event(cli_app: DaemonApp) -> None:
    with running_server(cli_app) as base_url:
        status, payload = request_json(
            "POST", f"{base_url}/brain/switch", {"adapter": "claude_cli"}
        )
        assert status == 200
        assert payload["ok"] is True
        assert payload["previous"] == "test"
        assert payload["adapter"] == "claude_cli"
        assert payload["changed"] is True

        status, current = request_json("GET", f"{base_url}/brain/current")
        assert status == 200
        assert current["adapter"] == "claude_cli"

        status, state = request_json("GET", f"{base_url}/state")
        assert status == 200
        assert state["brain_adapter"] == "claude_cli"

    assert _settings_row(cli_app, BRAIN_ADAPTER_SETTING_KEY) == "claude_cli"
    assert "brain.switched" in _event_types(cli_app)
    row = cli_app.conn.execute(
        "SELECT payload_json FROM events WHERE type = 'brain.switched'"
    ).fetchone()
    event_payload = json.loads(row[0])
    assert event_payload["from"] == "test"
    assert event_payload["to"] == "claude_cli"


def test_runtime_settings_apply_switches_registered_brain_provider(cli_app: DaemonApp) -> None:
    with running_server(cli_app) as base_url:
        status, payload = request_json(
            "POST",
            f"{base_url}/runtime/settings/apply",
            {"settings": {"brain.provider": "claude_cli"}},
        )
        assert status == 200
        assert payload["applied"] == ["brain.provider"]
        assert payload["status"] == "applied"

        status, current = request_json("GET", f"{base_url}/brain/current")
        assert status == 200
        assert current["adapter"] == "claude_cli"

    assert _settings_row(cli_app, BRAIN_ADAPTER_SETTING_KEY) == "claude_cli"
    assert "brain.switched" in _event_types(cli_app)


def test_post_brain_switch_unknown_adapter_is_404_and_keeps_state(app: DaemonApp) -> None:
    with running_server(app) as base_url:
        status, payload = request_json(
            "POST", f"{base_url}/brain/switch", {"adapter": "nope"}
        )
        assert status == 404
        assert "nope" in str(payload["error"])

        status, current = request_json("GET", f"{base_url}/brain/current")
        assert current["adapter"] == "test"

    assert _settings_row(app, BRAIN_ADAPTER_SETTING_KEY) is None
    assert "brain.switched" not in _event_types(app)


@pytest.mark.parametrize(
    "body",
    [{}, {"adapter": ""}, {"adapter": "   "}, {"adapter": 42}, ["claude_cli"]],
)
def test_post_brain_switch_rejects_invalid_payload(app: DaemonApp, body: object) -> None:
    with running_server(app) as base_url:
        status, payload = request_json("POST", f"{base_url}/brain/switch", body)
    assert status == 400
    assert "error" in payload


def test_post_brain_switch_same_adapter_is_idempotent(app: DaemonApp) -> None:
    with running_server(app) as base_url:
        status, payload = request_json(
            "POST", f"{base_url}/brain/switch", {"adapter": "test"}
        )
    assert status == 200
    assert payload["changed"] is False
    assert payload["adapter"] == "test"
    assert _settings_row(app, BRAIN_ADAPTER_SETTING_KEY) == "test"
    # A no-op switch must not spam the audit trail.
    assert "brain.switched" not in _event_types(app)


def test_persisted_adapter_survives_daemon_restart(tmp_path: Path) -> None:
    script, _ = write_fake_cli(tmp_path)
    config_path = tmp_path / "dan.toml"
    config_path.write_text(
        config_text_with_cli(tmp_path / "home" / "dan.db", script),
        encoding="utf-8",
    )

    first = create_daemon_app(config_path)
    first.start()
    first.switch_brain("claude_cli")
    first.stop()
    first.close()

    second = create_daemon_app(config_path)
    try:
        second.start()
        assert second.brain_manager is not None
        assert second.brain_manager.current_adapter_name == "claude_cli"
        assert second.snapshot_state()["brain_adapter"] == "claude_cli"
    finally:
        second.close()


def test_stale_persisted_adapter_falls_back_to_config_default(tmp_path: Path) -> None:
    script, _ = write_fake_cli(tmp_path)
    config_with_cli = tmp_path / "dan-cli.toml"
    config_with_cli.write_text(
        config_text_with_cli(tmp_path / "home" / "dan.db", script),
        encoding="utf-8",
    )
    first = create_daemon_app(config_with_cli)
    first.start()
    first.switch_brain("claude_cli")
    first.stop()
    first.close()

    # Same DB, but the config no longer registers claude_cli: the stale
    # persisted choice must not brick the daemon.
    config_without_cli = tmp_path / "dan-plain.toml"
    config_without_cli.write_text(
        config_text(tmp_path / "home" / "dan.db"), encoding="utf-8"
    )
    second = create_daemon_app(config_without_cli)
    try:
        second.start()
        assert second.brain_manager is not None
        assert second.brain_manager.current_adapter_name == "test"
    finally:
        second.close()


def test_conversation_history_survives_brain_switch(cli_app: DaemonApp) -> None:
    marker = "HISTORY_MARKER_E1_SWITCH"
    first = cli_app.handle_text_input(text=f"Remember this marker: {marker}")
    conversation_id = first.conversation_id
    assert first.brain_adapter == "test"

    cli_app.switch_brain("claude_cli")

    second = cli_app.handle_text_input(
        text="What marker did I give you?", conversation_id=conversation_id
    )
    assert second.conversation_id == conversation_id
    assert second.brain_adapter == "claude_cli"
    assert second.final_text == "fake cli answer"

    # The stateless CLI prompt must carry the pre-switch history.
    prompt = cli_app.fake_cli_prompt_dump.read_text(encoding="utf-8")  # type: ignore[attr-defined]
    assert marker in prompt

    turns = cli_app.list_turns(conversation_id)
    assert len(turns) == 2
    assert [turn.brain_adapter for turn in turns] == ["test", "claude_cli"]


def test_brain_switch_requires_transport_token(tmp_path: Path) -> None:
    db_path = tmp_path / "home" / "dan.db"
    config_path = tmp_path / "dan.toml"
    config_path.write_text(
        config_text(db_path).replace("api_token_required = false", "api_token_required = true"),
        encoding="utf-8",
    )
    daemon_app = create_daemon_app(config_path)
    daemon_app.start()
    try:
        with running_server(daemon_app) as base_url:
            status, payload = request_json(
                "POST", f"{base_url}/brain/switch", {"adapter": "mock"}
            )
            assert status == 401
            assert payload == {"error": "Unauthorized", "status": 401}

            # Reads stay tokenless (C1 contract).
            status, _ = request_json("GET", f"{base_url}/brain/current")
            assert status == 200
    finally:
        daemon_app.close()


def test_schema_and_migrations_unchanged() -> None:
    assert_schema_and_migrations_unchanged(ROOT)
