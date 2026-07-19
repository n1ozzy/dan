"""Direct model-tool execution must never create an awaiting-approval turn."""

from __future__ import annotations

import json
from pathlib import Path

from dan.brain import BrainManager, BrainToolCall, MockBrainAdapter
from dan.daemon.app import DaemonApp, create_daemon_app
from tests.git_guards import assert_schema_and_migrations_unchanged
from tests.test_api_smoke import write_config
from tests.test_cli_history import config_args, history_server, run_cli
from tests.test_model_tool_permission_policy import (
    RecordingTool,
    request_json,
    running_server,
    set_model_tool_response,
    table_count,
)


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_RUNTIME_STRINGS = (
    "/Users/" "n1_ozzy" "/Documents/dev/dan",
    "/tmp/" "dan",
    "af" "play",
    "--dangerously-" "skip-permissions",
)


def make_app(tmp_path: Path) -> DaemonApp:
    config_path = write_config(tmp_path / "dan.toml", tmp_path / "home" / "dan.db")
    return create_daemon_app(config_path)


def test_model_originated_tool_finishes_without_approval_state(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    try:
        recording = RecordingTool(name="direct_probe", risk="file_write")
        app.tool_registry.register(recording)
        set_model_tool_response(
            app,
            BrainToolCall(
                id="call-direct",
                name="direct_probe",
                arguments={"purpose": "direct"},
            ),
        )
        app.start()

        with running_server(app) as base_url:
            status, payload = request_json("POST", f"{base_url}/input/text", {"text": "Use tool"})
            state_status, state_payload = request_json("GET", f"{base_url}/state")

        assert status == 200
        assert payload["turn"]["status"] == "finished"
        assert payload["tool_calls"][0]["status"] == "finished"
        assert payload["tool_calls"][0]["output"] == {"received": {"purpose": "direct"}}
        assert "approval_id" not in payload["tool_calls"][0]
        assert "approvals" not in payload
        assert recording.calls == [{"purpose": "direct"}]
        assert table_count(app, "approvals") == 0
        assert table_count(app, "tool_runs") == 1
        assert state_status == 200
        assert state_payload["state"] == "IDLE"
        assert "pending_approval_count" not in state_payload

        turn = app.conn.execute(  # type: ignore[union-attr]
            "SELECT status, error FROM turns WHERE id = ?",
            (payload["turn_id"],),
        ).fetchone()
        assert turn == ("finished", None)
    finally:
        app.close()


def test_history_api_shows_direct_tool_turn_as_finished(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    try:
        recording = RecordingTool(name="history_probe", risk="shell_read")
        app.tool_registry.register(recording)
        set_model_tool_response(
            app,
            BrainToolCall(id="call-history", name="history_probe", arguments={}),
        )
        app.start()

        with running_server(app) as base_url:
            status, created = request_json("POST", f"{base_url}/input/text", {"text": "History"})
            assert status == 200
            turns_status, turns_payload = request_json(
                "GET",
                f"{base_url}/turns?conversation_id={created['conversation_id']}",
            )

        assert turns_status == 200
        assert turns_payload["turns"][0]["id"] == created["turn_id"]
        assert turns_payload["turns"][0]["status"] == "finished"
        assert table_count(app, "approvals") == 0
    finally:
        app.close()


def test_cli_history_prints_finished_turn_json(capsys) -> None:
    response = {
        "conversation_id": "conversation-cli",
        "turns": [{"id": "turn-cli", "status": "finished"}],
        "limit": 50,
        "newest_first": False,
    }

    with history_server(response_payload=response) as (base_url, _records):
        rc, out, err = run_cli(
            capsys,
            *config_args(),
            "turns",
            "list",
            "--conversation-id",
            "conversation-cli",
            "--url",
            base_url,
        )

    assert rc == 0
    assert err == ""
    assert json.loads(out)["turns"][0]["status"] == "finished"


def test_unknown_model_tool_fails_without_approval_state(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    try:
        set_model_tool_response(
            app,
            BrainToolCall(id="call-missing", name="missing_tool", arguments={}),
        )
        app.start()

        with running_server(app) as base_url:
            status, payload = request_json("POST", f"{base_url}/input/text", {"text": "Missing"})

        assert status == 200
        assert payload["tool_calls"][0]["status"] == "failed"
        assert "Unknown tool: missing_tool" in payload["tool_calls"][0]["error"]
        assert "approval_id" not in payload["tool_calls"][0]
        assert "approvals" not in payload
        assert payload["turn"]["status"] == "finished"
        assert table_count(app, "approvals") == 0
        assert table_count(app, "tool_runs") == 0
    finally:
        app.close()


def test_destructive_risk_model_tool_executes_directly(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    try:
        destructive = RecordingTool(name="dangerous_tool", risk="destructive")
        app.tool_registry.register(destructive)
        set_model_tool_response(
            app,
            BrainToolCall(id="call-danger", name="dangerous_tool", arguments={"confirm": True}),
        )
        app.start()

        with running_server(app) as base_url:
            status, payload = request_json("POST", f"{base_url}/input/text", {"text": "Danger"})

        assert status == 200
        assert payload["tool_calls"][0]["status"] == "finished"
        assert payload["turn"]["status"] == "finished"
        assert "approval_id" not in payload["tool_calls"][0]
        assert "approvals" not in payload
        assert destructive.calls == [{"confirm": True}]
        assert table_count(app, "approvals") == 0
        assert table_count(app, "tool_runs") == 1
    finally:
        app.close()


def test_text_only_response_remains_finished(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    try:
        set_model_tool_response(app, text="Plain response.")
        app.start()

        with running_server(app) as base_url:
            status, payload = request_json("POST", f"{base_url}/input/text", {"text": "Plain"})

        assert status == 200
        assert payload["turn"]["status"] == "finished"
        assert payload["tool_calls"] == []
        assert "approvals" not in payload
    finally:
        app.close()


def test_new_input_after_direct_tool_turn_is_allowed(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    try:
        recording = RecordingTool(name="followup_probe", risk="network")
        app.tool_registry.register(recording)
        set_model_tool_response(
            app,
            BrainToolCall(id="call-first", name="followup_probe", arguments={}),
        )
        app.start()

        with running_server(app) as base_url:
            first_status, first = request_json("POST", f"{base_url}/input/text", {"text": "Use tool"})
            app.brain_manager = BrainManager([MockBrainAdapter()], default_adapter="mock")
            second_status, second = request_json(
                "POST",
                f"{base_url}/input/text",
                {
                    "text": "Plain follow-up",
                    "conversation_id": first["conversation_id"],
                },
            )

        assert first_status == 200
        assert first["turn"]["status"] == "finished"
        assert second_status == 200
        assert second["turn"]["status"] == "finished"
        assert second["final_text"] == "DAN mock response: Plain follow-up"
        assert app.state_machine is not None
        assert app.state_machine.state.value == "IDLE"
        assert table_count(app, "approvals") == 0
    finally:
        app.close()


def test_sqlite_schema_and_migrations_are_not_modified() -> None:
    assert_schema_and_migrations_unchanged(ROOT)


def test_runtime_code_and_scripts_do_not_contain_forbidden_legacy_strings() -> None:
    allowed_contracts = {
        ("dan/voice/shared_broker.py", "/tmp/dan"),
        ("dan/migration/test_safety.py", "/tmp/dan"),
        ("dan/migration/test_safety.py", "afplay"),
    }
    findings: list[str] = []
    for root_name in ("dan", "scripts"):
        for path in (ROOT / root_name).rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            relative = str(path.relative_to(ROOT))
            for forbidden in FORBIDDEN_RUNTIME_STRINGS:
                if forbidden in text and (relative, forbidden) not in allowed_contracts:
                    findings.append(f"{relative} contains {forbidden}")

    assert findings == []
