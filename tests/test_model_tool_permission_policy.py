"""Prompt 19B model-originated tool permission policy tests."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from jarvis.brain import BrainManager, BrainRequest, BrainResponse, BrainToolCall, MockBrainAdapter
from jarvis.brain.context_builder import ContextBuilder
from jarvis.daemon.app import DaemonApp, DaemonAppConflictError, create_daemon_app
from jarvis.daemon.lifecycle import build_server
from jarvis.daemon.state_machine import RuntimeState, RuntimeStateMachine
from jarvis.events.types import EventType
from jarvis.store.db import close_quietly, initialize_database
from jarvis.store.event_store import create_event_store
from jarvis.tools import ToolPermissionPolicy, ToolRegistry
from jarvis.tools.registry import ApprovalGate, ApprovalProbeTool, Tool, ToolRunRecorder
from jarvis.turns.orchestrator import TurnOrchestrator
from tests.git_guards import assert_schema_and_migrations_unchanged
from tests.test_api_smoke import write_config


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_RUNTIME_STRINGS = (
    "/Users/n1_ozzy/Documents/dev/" "dan",
    "/tmp/" "dan",
    "af" "play",
    "--dangerously-" "skip-permissions",
)


@pytest.fixture
def app(tmp_path: Path) -> Iterator[DaemonApp]:
    config_path = write_config(tmp_path / "jarvis.toml", tmp_path / "home" / "jarvis.db")
    daemon_app = create_daemon_app(config_path)
    try:
        yield daemon_app
    finally:
        daemon_app.close()


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    db_conn = initialize_database(tmp_path / "model-tool-policy.db")
    try:
        yield db_conn
    finally:
        close_quietly(db_conn)


class ToolCallingBrainAdapter:
    name = "tool_calling"
    default_model = "tool-calling-model"

    def __init__(self, response: BrainResponse) -> None:
        self.response = response

    def available_models(self) -> list[str]:
        return [self.default_model]

    def generate(self, request: BrainRequest) -> BrainResponse:
        return self.response


class RecordingTool(Tool):
    description = "records calls"
    input_schema = {"type": "object"}

    def __init__(self, *, name: str, risk: str) -> None:
        self.name = name
        self.risk = risk
        self.calls: list[dict[str, Any]] = []

    def run(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        payload = dict(arguments)
        self.calls.append(payload)
        return {"received": payload}


@contextmanager
def running_server(app: DaemonApp) -> Iterator[str]:
    server = build_server(app, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, name="jarvis-model-tool-policy-http", daemon=True)
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


def set_model_tool_response(app: DaemonApp, *tool_calls: BrainToolCall, text: str = "Tool requested.") -> None:
    app.brain_manager = BrainManager(
        [
            ToolCallingBrainAdapter(
                BrainResponse(
                    text=text,
                    model="tool-calling-model",
                    tool_calls=list(tool_calls),
                )
            )
        ],
        default_adapter="tool_calling",
    )


def post_text(app: DaemonApp, text: str = "Use tool") -> dict[str, object]:
    with running_server(app) as base_url:
        status, payload = request_json("POST", f"{base_url}/input/text", {"text": text})

    assert status == 200
    return payload


def event_types_for_turn(app: DaemonApp, turn_id: str) -> list[str]:
    assert app.event_store is not None
    return [event.type for event in app.event_store.list_by_turn_id(turn_id, limit=100)]


def table_count(app: DaemonApp, table: str) -> int:
    assert app.conn is not None
    return int(app.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def test_model_originated_approval_required_tool_creates_pending_approval(app: DaemonApp) -> None:
    set_model_tool_response(
        app,
        BrainToolCall(id="call-probe", name="approval_probe", arguments={"purpose": "policy"}),
    )
    app.start()

    payload = post_text(app)

    tool_call = payload["tool_calls"][0]
    assert tool_call["tool_name"] == "approval_probe"
    assert tool_call["status"] == "approval_required"
    assert tool_call["approval_required"] is True
    assert isinstance(tool_call["approval_id"], str)
    assert payload["approvals"][0]["status"] == "pending"
    assert payload["approvals"][0]["risk"] == "shell_read"
    assert table_count(app, "approvals") == 1
    assert table_count(app, "tool_runs") == 0


def test_model_originated_approval_uses_registry_risk_not_model_provided_risk(app: DaemonApp) -> None:
    set_model_tool_response(
        app,
        BrainToolCall(
            id="call-risk-spoof",
            name="approval_probe",
            arguments={"purpose": "risk"},
            risk="safe_read",
        ),
    )
    app.start()

    payload = post_text(app)

    approval_id = str(payload["tool_calls"][0]["approval_id"])
    approval = app.approval_gate.get_approval(approval_id)  # type: ignore[union-attr]
    assert approval is not None
    assert approval["risk"] == "shell_read"
    assert payload["approvals"][0]["risk"] == "shell_read"


def test_model_originated_approval_preserves_turn_and_correlation_on_events(app: DaemonApp) -> None:
    set_model_tool_response(
        app,
        BrainToolCall(id="call-correlated", name="approval_probe", arguments={}),
    )
    app.start()

    payload = post_text(app)

    turn_id = str(payload["turn_id"])
    assert app.event_store is not None
    approval_events = [
        event
        for event in app.event_store.list_by_turn_id(turn_id, limit=100)
        if event.type == EventType.APPROVAL_CREATED
    ]
    assert len(approval_events) == 1
    assert approval_events[0].turn_id == turn_id
    assert approval_events[0].correlation_id == turn_id
    assert approval_events[0].payload["metadata"]["origin"] == "model"


def test_unknown_model_originated_tool_creates_no_approval_and_is_reported_unknown(
    app: DaemonApp,
) -> None:
    set_model_tool_response(
        app,
        BrainToolCall(id="call-missing", name="missing_tool", arguments={}),
    )
    app.start()

    payload = post_text(app)

    assert payload["tool_calls"][0]["status"] == "unknown"
    assert payload["tool_calls"][0]["approval_required"] is False
    assert payload["tool_calls"][0]["approval_id"] is None
    assert "Unknown tool: missing_tool" in payload["tool_calls"][0]["error"]
    assert payload["approvals"] == []
    assert table_count(app, "approvals") == 0
    assert "tool.failed" in event_types_for_turn(app, str(payload["turn_id"]))


def test_unavailable_model_tool_registry_creates_no_approval_and_is_reported_unavailable(
    conn: sqlite3.Connection,
) -> None:
    response = BrainResponse(
        text="Need tool.",
        model="tool-calling-model",
        tool_calls=[BrainToolCall(id="call-unavailable", name="echo", arguments={})],
    )
    event_store = create_event_store(conn)
    orchestrator = TurnOrchestrator(
        conn=conn,
        event_store=event_store,
        event_bus=None,
        state_machine=RuntimeStateMachine(event_store, initial_state=RuntimeState.IDLE),
        brain_manager=BrainManager([ToolCallingBrainAdapter(response)], default_adapter="tool_calling"),
        context_builder=ContextBuilder(conn),
        tool_registry=None,
        approval_gate=ApprovalGate(conn, event_store=event_store),
        tool_permission_policy=ToolPermissionPolicy(),
    )

    result = orchestrator.handle_text(text="Unavailable")

    assert result.tool_calls[0]["status"] == "unavailable"
    assert result.tool_calls[0]["approval_required"] is False
    assert "tool registry is unavailable" in result.tool_calls[0]["error"]
    assert result.approvals == []
    assert int(conn.execute("SELECT COUNT(*) FROM approvals").fetchone()[0]) == 0


def test_destructive_model_originated_tool_is_blocked_when_destructive_tools_disabled(
    app: DaemonApp,
) -> None:
    destructive = RecordingTool(name="dangerous_tool", risk="destructive")
    app.tool_registry.register(destructive)
    set_model_tool_response(
        app,
        BrainToolCall(
            id="call-destructive",
            name="dangerous_tool",
            arguments={"confirm": True},
            risk="safe_read",
        ),
    )
    app.start()

    payload = post_text(app)

    tool_call = payload["tool_calls"][0]
    assert tool_call["tool_name"] == "dangerous_tool"
    assert tool_call["status"] == "blocked"
    assert tool_call["approval_required"] is False
    assert tool_call["approval_id"] is None
    assert "destructive tools are disabled" in tool_call["error"]
    assert payload["approvals"] == []
    assert destructive.calls == []
    assert table_count(app, "approvals") == 0
    assert table_count(app, "tool_runs") == 0
    assert "tool.rejected" in event_types_for_turn(app, str(payload["turn_id"]))


def test_safe_model_originated_tool_still_creates_conservative_approval_without_execution(
    app: DaemonApp,
) -> None:
    set_model_tool_response(
        app,
        BrainToolCall(id="call-echo", name="echo", arguments={"text": "hello"}, risk="destructive"),
    )
    app.start()

    payload = post_text(app)

    tool_call = payload["tool_calls"][0]
    assert tool_call["tool_name"] == "echo"
    assert tool_call["status"] == "approval_required"
    assert tool_call["approval_required"] is True
    assert payload["approvals"][0]["risk"] == "safe_read"
    assert table_count(app, "approvals") == 1
    assert table_count(app, "tool_runs") == 0


def test_no_model_originated_tool_call_auto_executes_during_capture(app: DaemonApp) -> None:
    recording = RecordingTool(name="safe_recorder", risk="safe_read")
    app.tool_registry.register(recording)
    set_model_tool_response(
        app,
        BrainToolCall(id="call-safe-recorder", name="safe_recorder", arguments={"x": 1}),
    )
    app.start()

    payload = post_text(app)

    assert payload["tool_calls"][0]["status"] == "approval_required"
    assert recording.calls == []
    assert table_count(app, "tool_runs") == 0


def test_explicit_execute_approved_behavior_is_unchanged_for_model_created_approval(
    app: DaemonApp,
) -> None:
    set_model_tool_response(
        app,
        BrainToolCall(id="call-execute", name="approval_probe", arguments={"purpose": "execute"}),
    )
    app.start()
    payload = post_text(app)
    approval_id = str(payload["tool_calls"][0]["approval_id"])

    app.approve(approval_id, reason="ok")
    executed = app.execute_approved_tool(approval_id)
    with pytest.raises(DaemonAppConflictError):
        app.execute_approved_tool(approval_id)

    assert executed["ok"] is True
    assert executed["result"] == {
        "ok": True,
        "message": "approval_probe executed safely",
    }
    assert table_count(app, "tool_runs") == 1


def test_prompt_19a_decision_events_still_work_for_model_created_approval(
    app: DaemonApp,
) -> None:
    set_model_tool_response(
        app,
        BrainToolCall(id="call-decision", name="approval_probe", arguments={}),
    )
    app.start()
    payload = post_text(app)
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
    assert approved_events[0].payload["tool_name"] == "approval_probe"
    assert approved_events[0].payload["requested_risk"] == "shell_read"


def test_model_tool_argument_secrets_are_redacted_in_event_payloads(app: DaemonApp) -> None:
    raw_secret = "sk-ant-modeltoolpolicy123"
    set_model_tool_response(
        app,
        BrainToolCall(
            id="call-secret",
            name="echo",
            arguments={"api_key": raw_secret, "note": f"Bearer {raw_secret}"},
        ),
    )
    app.start()

    payload = post_text(app)

    assert app.event_store is not None
    rendered_events = json.dumps(
        [event.payload for event in app.event_store.list_by_turn_id(str(payload["turn_id"]), limit=100)],
        sort_keys=True,
    )
    assert raw_secret not in rendered_events
    approval_id = str(payload["tool_calls"][0]["approval_id"])
    approval = app.approval_gate.get_approval(approval_id)  # type: ignore[union-attr]
    assert approval is not None
    assert approval["payload"]["arguments"]["api_key"] == "[REDACTED]"


def test_direct_tools_request_path_still_executes_safe_tools_without_model_gate(app: DaemonApp) -> None:
    app.start()

    result = app.request_tool(
        tool_name="echo",
        arguments={"text": "direct"},
        requested_by="api",
    )

    assert result.status == "finished"
    assert result.output == {"arguments": {"text": "direct"}}
    assert result.approval_id is None
    assert table_count(app, "approvals") == 0
    assert table_count(app, "tool_runs") == 1


def test_sqlite_schema_and_migrations_are_not_modified() -> None:
    assert_schema_and_migrations_unchanged(ROOT)


def test_runtime_code_and_scripts_do_not_contain_forbidden_legacy_strings() -> None:
    findings: list[str] = []
    for root_name in ("jarvis", "scripts"):
        for path in (ROOT / root_name).rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for forbidden in FORBIDDEN_RUNTIME_STRINGS:
                if forbidden in text:
                    findings.append(f"{path.relative_to(ROOT)} contains {forbidden}")

    assert findings == []
