"""Prompt 19C awaiting-approval turn status tests."""

from __future__ import annotations

import json
from pathlib import Path

from dan.brain import BrainManager, BrainResponse, BrainToolCall, MockBrainAdapter
from dan.daemon.app import DaemonApp, DaemonAppConflictError, create_daemon_app
from dan.events.types import EventType
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


def test_model_originated_approval_marks_turn_awaiting_approval(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    try:
        set_model_tool_response(
            app,
            BrainToolCall(id="call-probe", name="approval_probe", arguments={"purpose": "19c"}),
        )
        app.start()

        with running_server(app) as base_url:
            status, payload = request_json("POST", f"{base_url}/input/text", {"text": "Use tool"})
            state_status, state_payload = request_json("GET", f"{base_url}/state")

        assert status == 200
        assert payload["turn"]["status"] == "awaiting_approval"
        assert payload["turn"]["error"] is None
        assert payload["approvals"][0]["status"] == "pending"
        assert payload["state"] == "IDLE"
        assert state_status == 200
        assert state_payload["state"] == "IDLE"
        assert state_payload["pending_approval_count"] == 1

        turn = app.conn.execute(  # type: ignore[union-attr]
            "SELECT status, final_text, error FROM turns WHERE id = ?",
            (payload["turn_id"],),
        ).fetchone()
        assert turn[0] == "awaiting_approval"
        assert "approval_probe requires approval" in turn[1]
        assert turn[2] is None
    finally:
        app.close()


def test_history_api_shows_awaiting_approval_turn(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    try:
        set_model_tool_response(
            app,
            BrainToolCall(id="call-history", name="approval_probe", arguments={}),
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
        assert turns_payload["turns"][0]["status"] == "awaiting_approval"
    finally:
        app.close()


def test_cli_history_prints_awaiting_approval_turn_json(
    capsys,
) -> None:
    response = {
        "conversation_id": "conversation-cli",
        "turns": [{"id": "turn-cli", "status": "awaiting_approval"}],
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
    assert json.loads(out)["turns"][0]["status"] == "awaiting_approval"


def test_unknown_model_tool_does_not_mark_turn_awaiting_approval(tmp_path: Path) -> None:
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
        assert payload["tool_calls"][0]["status"] == "unknown"
        assert payload["approvals"] == []
        assert payload["turn"]["status"] == "finished"
        assert "missing_tool unknown" in payload["final_text"]
    finally:
        app.close()


def test_blocked_model_tool_does_not_mark_turn_awaiting_approval(tmp_path: Path) -> None:
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
        assert payload["tool_calls"][0]["status"] == "blocked"
        assert payload["approvals"] == []
        assert payload["turn"]["status"] == "finished"
        assert destructive.calls == []
        assert table_count(app, "approvals") == 0
    finally:
        app.close()


def test_text_only_response_remains_finished(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    try:
        app.start()

        with running_server(app) as base_url:
            status, payload = request_json("POST", f"{base_url}/input/text", {"text": "Plain"})

        assert status == 200
        assert payload["turn"]["status"] == "finished"
        assert payload["tool_calls"] == []
        assert payload["approvals"] == []
    finally:
        app.close()


def test_new_input_after_pending_approval_is_allowed(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    try:
        set_model_tool_response(
            app,
            BrainToolCall(id="call-pending", name="approval_probe", arguments={}),
        )
        app.start()

        with running_server(app) as base_url:
            first_status, first = request_json("POST", f"{base_url}/input/text", {"text": "Needs tool"})
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
        assert first["turn"]["status"] == "awaiting_approval"
        assert second_status == 200
        assert second["turn"]["status"] == "finished"
        assert second["final_text"] == "DAN mock response: Plain follow-up"
        assert app.state_machine is not None
        assert app.state_machine.state.value == "IDLE"
    finally:
        app.close()


def test_approve_and_reject_do_not_execute_tools(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    try:
        set_model_tool_response(
            app,
            BrainToolCall(id="call-approve", name="approval_probe", arguments={"decision": "approve"}),
        )
        app.start()
        with running_server(app) as base_url:
            first_status, first = request_json("POST", f"{base_url}/input/text", {"text": "Approve"})
            assert first_status == 200
            set_model_tool_response(
                app,
                BrainToolCall(
                    id="call-reject",
                    name="approval_probe",
                    arguments={"decision": "reject"},
                ),
            )
            second_status, second = request_json("POST", f"{base_url}/input/text", {"text": "Reject"})
            assert second_status == 200

        app.approve(str(first["tool_calls"][0]["approval_id"]), reason="ok")
        app.reject(str(second["tool_calls"][0]["approval_id"]), reason="no")

        assert table_count(app, "tool_runs") == 0
    finally:
        app.close()


def test_execute_approved_remains_explicit_and_duplicate_execution_conflicts(
    tmp_path: Path,
) -> None:
    app = make_app(tmp_path)
    try:
        set_model_tool_response(
            app,
            BrainToolCall(id="call-execute", name="approval_probe", arguments={"purpose": "execute"}),
        )
        app.start()
        with running_server(app) as base_url:
            status, payload = request_json("POST", f"{base_url}/input/text", {"text": "Execute"})

        assert status == 200
        approval_id = str(payload["tool_calls"][0]["approval_id"])
        app.approve(approval_id, reason="ok")
        assert table_count(app, "tool_runs") == 0

        executed = app.execute_approved_tool(approval_id)

        assert executed["ok"] is True
        assert table_count(app, "tool_runs") == 1
        try:
            app.execute_approved_tool(approval_id)
        except DaemonAppConflictError as exc:
            assert "already executed" in str(exc)
        else:
            raise AssertionError("duplicate execute should conflict")
    finally:
        app.close()


def test_decision_events_still_work_for_awaiting_approval_turn(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    try:
        set_model_tool_response(
            app,
            BrainToolCall(id="call-decision", name="approval_probe", arguments={}),
        )
        app.start()
        with running_server(app) as base_url:
            status, payload = request_json("POST", f"{base_url}/input/text", {"text": "Decide"})

        assert status == 200
        turn_id = str(payload["turn_id"])
        approval_id = str(payload["tool_calls"][0]["approval_id"])
        app.approve(approval_id, reason="ok")

        assert app.event_store is not None
        approved_events = [
            event
            for event in app.event_store.list_by_turn_id(turn_id, limit=100)
            if event.type == EventType.APPROVAL_APPROVED
        ]
        assert len(approved_events) == 1
        assert approved_events[0].correlation_id == turn_id
        assert approved_events[0].payload["approval_id"] == approval_id
    finally:
        app.close()


def test_runtime_state_remains_idle_after_awaiting_approval_turn(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    try:
        set_model_tool_response(
            app,
            BrainToolCall(id="call-state", name="approval_probe", arguments={}),
        )
        app.start()

        with running_server(app) as base_url:
            status, payload = request_json("POST", f"{base_url}/input/text", {"text": "State"})
            state_status, state_payload = request_json("GET", f"{base_url}/state")

        assert status == 200
        assert payload["turn"]["status"] == "awaiting_approval"
        assert state_status == 200
        assert state_payload["state"] == "IDLE"
        assert state_payload["pending_approval_count"] == 1
        assert "WAITING_APPROVAL" not in state_payload["allowed_state_targets"]
    finally:
        app.close()


def test_sqlite_schema_and_migrations_are_not_modified() -> None:
    assert_schema_and_migrations_unchanged(ROOT)


def test_runtime_code_and_scripts_do_not_contain_forbidden_legacy_strings() -> None:
    allowed_contracts = {("dan/voice/shared_broker.py", "/tmp/dan")}
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
