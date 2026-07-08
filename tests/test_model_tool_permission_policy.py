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
from jarvis.security.redaction import REDACTION_PLACEHOLDER
from jarvis.store.db import close_quietly, initialize_database
from jarvis.store.event_store import create_event_store
from jarvis.tools import RequestSource, ToolPermissionPolicy, ToolRegistry
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


def memory_save_call(
    *,
    call_id: str = "call-memory-save",
    title: str = "Remembered fact",
    body: str = "This fact should enter context only after approved execution.",
) -> BrainToolCall:
    return BrainToolCall(
        id=call_id,
        name="memory_save",
        arguments={
            "kind": "fact",
            "title": title,
            "body": body,
            "priority": 4,
        },
    )


def event_types_for_turn(app: DaemonApp, turn_id: str) -> list[str]:
    assert app.event_store is not None
    return [event.type for event in app.event_store.list_by_turn_id(turn_id, limit=100)]


def table_count(app: DaemonApp, table: str) -> int:
    assert app.conn is not None
    return int(app.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def event_type_count(app: DaemonApp, event_type: str) -> int:
    assert app.conn is not None
    return int(app.conn.execute("SELECT COUNT(*) FROM events WHERE type = ?", (event_type,)).fetchone()[0])


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


def test_approve_and_execute_runs_the_tool_in_one_call(app: DaemonApp) -> None:
    # Ozzy 2026-07-08: one click must both approve AND execute — no second
    # "execute" step.
    set_model_tool_response(
        app,
        BrainToolCall(id="call-atomic", name="approval_probe", arguments={"purpose": "atomic"}),
    )
    app.start()
    payload = post_text(app)
    approval_id = str(payload["tool_calls"][0]["approval_id"])
    assert table_count(app, "tool_runs") == 0

    result = app.approve_and_execute_tool(approval_id, reason="one click")

    assert result["ok"] is True
    assert result["result"] == {"ok": True, "message": "approval_probe executed safely"}
    assert table_count(app, "tool_runs") == 1
    approval = app.approval_gate.get_approval(approval_id)  # type: ignore[union-attr]
    assert approval is not None
    assert approval["status"] == "approved"


def test_approve_and_execute_is_idempotent_on_double_click(app: DaemonApp) -> None:
    # A second click (or a stray retry) must conflict, not run the tool twice.
    set_model_tool_response(
        app,
        BrainToolCall(id="call-atomic-2", name="approval_probe", arguments={}),
    )
    app.start()
    approval_id = str(post_text(app)["tool_calls"][0]["approval_id"])

    app.approve_and_execute_tool(approval_id, reason="first")
    with pytest.raises(DaemonAppConflictError):
        app.approve_and_execute_tool(approval_id, reason="second")
    assert table_count(app, "tool_runs") == 1


def test_approve_and_execute_retries_an_approved_but_unexecuted_approval(app: DaemonApp) -> None:
    # If a prior execute failed, the approval is already "approved" but has no
    # tool_run. approve_and_execute must still run it (retry), not choke on the
    # non-pending status.
    set_model_tool_response(
        app,
        BrainToolCall(id="call-retry", name="approval_probe", arguments={}),
    )
    app.start()
    approval_id = str(post_text(app)["tool_calls"][0]["approval_id"])
    app.approve(approval_id, reason="approved, not executed")
    assert table_count(app, "tool_runs") == 0

    result = app.approve_and_execute_tool(approval_id, reason="retry")

    assert result["ok"] is True
    assert table_count(app, "tool_runs") == 1


def test_actionable_approvals_include_approved_but_not_executed(app: DaemonApp) -> None:
    # "Nothing disappears silently": an approved-but-not-yet-executed approval
    # must remain visible in the actionable list (server truth), so the panel
    # never loses it just because a client-side map forgot it.
    set_model_tool_response(
        app,
        BrainToolCall(id="call-visible", name="approval_probe", arguments={}),
    )
    app.start()
    approval_id = str(post_text(app)["tool_calls"][0]["approval_id"])

    # pending is actionable
    pending = app.list_actionable_approvals()
    assert [entry["id"] for entry in pending] == [approval_id]
    assert pending[0]["status"] == "pending"

    # approved-but-not-executed stays actionable
    app.approve(approval_id, reason="ok")
    approved = app.list_actionable_approvals()
    assert [entry["id"] for entry in approved] == [approval_id]
    assert approved[0]["status"] == "approved"

    # once executed it drops off (it is done, not silently gone)
    app.execute_approved_tool(approval_id)
    assert app.list_actionable_approvals() == []


def test_http_approve_and_execute_endpoint_runs_tool_in_one_request(app: DaemonApp) -> None:
    set_model_tool_response(
        app,
        BrainToolCall(id="call-http-atomic", name="approval_probe", arguments={}),
    )
    app.start()
    with running_server(app) as base_url:
        _, created = request_json("POST", f"{base_url}/input/text", {"text": "go"})
        approval_id = str(created["tool_calls"][0]["approval_id"])
        status, result = request_json(
            "POST", f"{base_url}/approvals/{approval_id}/approve-and-execute"
        )

    assert status == 200
    assert result["ok"] is True
    assert table_count(app, "tool_runs") == 1


def test_model_originated_memory_save_waits_for_approval_and_execute_promotes_once(
    app: DaemonApp,
) -> None:
    set_model_tool_response(app, memory_save_call())
    app.start()

    payload = post_text(app, text="Remember this through memory_save")
    turn_id = str(payload["turn_id"])
    conversation_id = str(payload["conversation_id"])
    tool_call = payload["tool_calls"][0]
    approval_id = str(tool_call["approval_id"])

    assert tool_call["tool_name"] == "memory_save"
    assert tool_call["status"] == "approval_required"
    assert payload["approvals"][0]["status"] == "pending"
    assert payload["approvals"][0]["risk"] == "memory_write"
    assert table_count(app, "approvals") == 1
    assert table_count(app, "tool_runs") == 0
    assert table_count(app, "memory_candidates") == 1
    assert table_count(app, "memory_evidence") == 1
    assert table_count(app, "memory_items") == 0
    assert table_count(app, "memory_blocks") == 0

    approval = app.approval_gate.get_approval(approval_id)  # type: ignore[union-attr]
    assert approval is not None
    assert approval["payload"]["tool_name"] == "memory_save"
    assert approval["payload"]["turn_id"] == turn_id
    candidate_id = approval["payload"]["arguments"]["candidate_id"]
    assert isinstance(candidate_id, str)
    assert approval["metadata"]["origin"] == "model"
    assert approval["metadata"]["tool_call_id"] == "call-memory-save"

    assert app.conn is not None
    candidate = app.conn.execute(
        """
        SELECT candidate_kind, title, claim, status
        FROM memory_candidates
        WHERE id = ?
        """,
        (candidate_id,),
    ).fetchone()
    assert candidate == (
        "fact",
        "Remembered fact",
        "This fact should enter context only after approved execution.",
        "needs_review",
    )
    evidence = app.conn.execute(
        """
        SELECT o.source_type, o.source_id, e.conversation_id, e.turn_id, e.quote, e.memory_id
        FROM memory_evidence AS e
        JOIN memory_observations AS o ON o.id = e.observation_id
        WHERE e.candidate_id = ?
        """,
        (candidate_id,),
    ).fetchone()
    assert evidence == (
        "explicit_memory_save",
        "call-memory-save",
        conversation_id,
        turn_id,
        "This fact should enter context only after approved execution.",
        None,
    )

    assert app.event_store is not None
    created_events = [
        event
        for event in app.event_store.list_by_turn_id(turn_id, limit=100)
        if event.type == EventType.APPROVAL_CREATED
    ]
    assert len(created_events) == 1
    assert created_events[0].turn_id == turn_id
    assert created_events[0].correlation_id == turn_id

    approved = app.approve(approval_id, reason="ok")
    assert approved["status"] == "approved"
    assert table_count(app, "memory_items") == 0
    assert table_count(app, "memory_blocks") == 0
    assert table_count(app, "tool_runs") == 0

    executed = app.execute_approved_tool(approval_id)
    assert executed["ok"] is True
    assert executed["result"]["candidate_id"] == candidate_id
    memory_id = executed["result"]["memory_id"]
    assert isinstance(memory_id, str)
    assert table_count(app, "memory_items") == 1
    assert table_count(app, "memory_blocks") == 0
    assert table_count(app, "tool_runs") == 1

    with pytest.raises(DaemonAppConflictError):
        app.execute_approved_tool(approval_id)
    assert table_count(app, "memory_items") == 1
    assert table_count(app, "memory_blocks") == 0
    assert table_count(app, "tool_runs") == 1
    assert event_type_count(app, "memory.activated") == 1

    linked_memory_id = app.conn.execute(
        "SELECT memory_id FROM memory_evidence WHERE candidate_id = ?",
        (candidate_id,),
    ).fetchone()[0]
    assert linked_memory_id == memory_id

    assert app.memory_item_repository is not None
    items = app.memory_item_repository.list_items()
    assert len(items) == 1
    assert items[0].id == memory_id
    assert items[0].title == "Remembered fact"
    assert items[0].claim == "This fact should enter context only after approved execution."
    assert items[0].status == "active"

    assert app.context_builder is not None
    future = app.context_builder.build_request(
        turn_id="future-turn",
        conversation_id=conversation_id,
        input_text="Use saved memory later",
    )
    assert future.request.memory_blocks == []


def test_malformed_model_originated_memory_save_is_captured_as_failed_without_memory(
    app: DaemonApp,
) -> None:
    set_model_tool_response(
        app,
        BrainToolCall(
            id="call-bad-memory-save",
            name="memory_save",
            arguments={"key": "value"},
        ),
    )
    app.start()

    with running_server(app) as base_url:
        status, payload = request_json(
            "POST",
            f"{base_url}/input/text",
            {"text": "Try malformed memory_save"},
        )

    assert status == 200
    assert "Internal server error" not in json.dumps(payload, sort_keys=True)
    tool_call = payload["tool_calls"][0]
    assert tool_call["tool_name"] == "memory_save"
    assert tool_call["status"] == "failed"
    assert tool_call["approval_required"] is False
    assert tool_call["approval_id"] is None
    assert "memory_save requires a non-empty string kind" in tool_call["error"]
    assert payload["approvals"] == []
    assert table_count(app, "approvals") == 0
    assert table_count(app, "memory_candidates") == 0
    assert table_count(app, "memory_evidence") == 0
    assert table_count(app, "memory_items") == 0
    assert table_count(app, "memory_blocks") == 0
    assert "tool.failed" in event_types_for_turn(app, str(payload["turn_id"]))


def test_model_originated_memory_save_rejects_model_supplied_candidate_id(
    app: DaemonApp,
) -> None:
    set_model_tool_response(
        app,
        BrainToolCall(
            id="call-candidate-id-memory-save",
            name="memory_save",
            arguments={"candidate_id": "bogus"},
        ),
    )
    app.start()

    with running_server(app) as base_url:
        status, payload = request_json(
            "POST",
            f"{base_url}/input/text",
            {"text": "Try candidate_id memory_save"},
        )

    assert status == 200
    tool_call = payload["tool_calls"][0]
    assert tool_call["tool_name"] == "memory_save"
    assert tool_call["status"] == "failed"
    assert tool_call["approval_required"] is False
    assert tool_call["approval_id"] is None
    assert "candidate_id" in tool_call["error"]
    assert "model proposal" in tool_call["error"]
    assert payload["approvals"] == []
    assert table_count(app, "approvals") == 0
    assert table_count(app, "memory_candidates") == 0
    assert table_count(app, "memory_evidence") == 0
    assert table_count(app, "memory_items") == 0
    assert table_count(app, "memory_blocks") == 0
    assert "tool.failed" in event_types_for_turn(app, str(payload["turn_id"]))


def test_rejected_model_originated_memory_save_creates_no_memory(app: DaemonApp) -> None:
    set_model_tool_response(
        app,
        memory_save_call(
            call_id="call-rejected-memory-save",
            title="Rejected fact",
            body="This rejected memory must never persist.",
        ),
    )
    app.start()

    payload = post_text(app, text="Try rejected memory_save")
    approval_id = str(payload["tool_calls"][0]["approval_id"])
    approval = app.approval_gate.get_approval(approval_id)  # type: ignore[union-attr]
    assert approval is not None
    candidate_id = approval["payload"]["arguments"]["candidate_id"]

    rejected = app.reject(approval_id, reason="no")
    assert rejected["status"] == "rejected"
    with pytest.raises(DaemonAppConflictError):
        app.execute_approved_tool(approval_id)

    assert app.conn is not None
    candidate_status = app.conn.execute(
        "SELECT status FROM memory_candidates WHERE id = ?",
        (candidate_id,),
    ).fetchone()[0]
    assert candidate_status == "rejected"
    assert table_count(app, "memory_items") == 0
    assert table_count(app, "memory_blocks") == 0
    assert table_count(app, "tool_runs") == 0


def test_model_originated_memory_save_redacts_secrets_before_persistence(
    app: DaemonApp,
) -> None:
    raw_secret = "sk-ant-memorysavev201"
    set_model_tool_response(
        app,
        memory_save_call(
            title=f"Remember {raw_secret}",
            body=f"Authorization: Bearer {raw_secret}",
        ),
    )
    app.start()

    post_text(app, text="Try secret memory_save")

    assert app.conn is not None
    persisted = {
        "candidates": app.conn.execute(
            "SELECT title, claim FROM memory_candidates"
        ).fetchall(),
        "evidence": app.conn.execute(
            "SELECT quote FROM memory_evidence"
        ).fetchall(),
        "observations": app.conn.execute(
            "SELECT observed_text FROM memory_observations"
        ).fetchall(),
    }
    rendered_persisted = json.dumps(persisted, sort_keys=True)
    assert raw_secret not in rendered_persisted
    assert REDACTION_PLACEHOLDER in rendered_persisted

    assert app.event_store is not None
    rendered_events = json.dumps(
        [event.payload for event in app.event_store.list_after(0, limit=100)],
        sort_keys=True,
    )
    assert raw_secret not in rendered_events
    assert REDACTION_PLACEHOLDER in rendered_events


def test_plain_text_turn_does_not_create_automatic_memory_candidates(
    app: DaemonApp,
) -> None:
    set_model_tool_response(app, text="Plain response with no tool calls.")
    app.start()

    post_text(app, text="Remembering is not requested here.")

    assert table_count(app, "memory_candidates") == 0
    assert table_count(app, "memory_evidence") == 0
    assert table_count(app, "memory_items") == 0
    assert table_count(app, "memory_blocks") == 0


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
            source=RequestSource.DIRECT_USER_COMMAND,
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
