"""Model-originated tools execute directly under the owner's runtime contract."""

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

from dan.brain import BrainManager, BrainRequest, BrainResponse, BrainToolCall
from dan.brain.context_builder import ContextBuilder
from dan.daemon.app import DaemonApp, create_daemon_app
from dan.daemon.lifecycle import build_server
from dan.daemon.state_machine import RuntimeState, RuntimeStateMachine
from dan.events.types import EventType
from dan.security.redaction import REDACTION_PLACEHOLDER
from dan.store.db import close_quietly, initialize_database
from dan.store.event_store import create_event_store
from dan.tools import RequestSource, ToolPermissionPolicy
from dan.tools.registry import Tool
from dan.turns.orchestrator import TurnOrchestrator
from tests.git_guards import assert_schema_and_migrations_unchanged
from tests.test_api_smoke import write_config


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_RUNTIME_STRINGS = (
    "/Users/" "n1_ozzy" "/Documents/dev/dan",
    "/tmp/" "dan",
    "af" "play",
    "--dangerously-" "skip-permissions",
)


@pytest.fixture
def app(tmp_path: Path) -> Iterator[DaemonApp]:
    config_path = write_config(tmp_path / "dan.toml", tmp_path / "home" / "dan.db")
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
        self.requests: list[BrainRequest] = []

    def available_models(self) -> list[str]:
        return [self.default_model]

    def generate(self, request: BrainRequest) -> BrainResponse:
        self.requests.append(request)
        if len(self.requests) == 1 or not self.response.tool_calls:
            return self.response
        return BrainResponse(
            text="Tool execution complete.",
            model=self.response.model,
        )


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
    thread = threading.Thread(target=server.serve_forever, name="dan-model-tool-policy-http", daemon=True)
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
    body: str = "This fact should be saved exactly once by direct execution.",
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


def single_tool_run(app: DaemonApp) -> dict[str, Any]:
    assert app.tool_run_recorder is not None
    runs = app.tool_run_recorder.list_recent()
    assert len(runs) == 1
    return runs[0]


def assert_direct_turn(payload: Mapping[str, Any], app: DaemonApp) -> None:
    assert "approvals" not in payload
    assert payload["turn"]["status"] == "finished"
    assert table_count(app, "approvals") == 0


@pytest.mark.parametrize(
    "tool_name",
    ["ui_click", "ui_type", "ui_focus_app", "terminal_paste"],
)
def test_direct_tool_descriptions_do_not_claim_approval_gate(
    app: DaemonApp,
    tool_name: str,
) -> None:
    app.start()

    tool = app.tool_registry.get(tool_name)

    assert "approval-gated" not in tool.description.lower()
    assert "requires approval" not in tool.description.lower()


def test_model_originated_tool_executes_without_creating_an_approval(app: DaemonApp) -> None:
    set_model_tool_response(
        app,
        BrainToolCall(id="call-echo", name="echo", arguments={"purpose": "policy"}),
    )
    app.start()

    payload = post_text(app)

    tool_call = payload["tool_calls"][0]
    assert tool_call["tool_name"] == "echo"
    assert tool_call["status"] == "finished"
    assert "approval_required" not in tool_call
    assert "approval_id" not in tool_call
    assert tool_call["output"] == {"arguments": {"purpose": "policy"}}
    assert_direct_turn(payload, app)
    assert table_count(app, "tool_runs") == 1
    run = single_tool_run(app)
    assert run["status"] == "finished"
    assert run["risk"] == "safe_read"
    assert run["turn_id"] == payload["turn_id"]
    assert run["approval_id"] is None


@pytest.mark.parametrize(
    ("registry_risk", "model_risk"),
    [
        ("destructive", "safe_read"),
        ("safe_read", "destructive"),
    ],
)
def test_model_originated_tool_risk_does_not_change_direct_execution(
    app: DaemonApp,
    monkeypatch: pytest.MonkeyPatch,
    registry_risk: str,
    model_risk: str,
) -> None:
    recording = RecordingTool(name="risk_recorder", risk=registry_risk)
    app.tool_registry.register(recording)

    def fail_if_permission_policy_is_consulted(*args: object, **kwargs: object) -> None:
        raise AssertionError("model direct execution must bypass permission policy")

    monkeypatch.setattr(ToolPermissionPolicy, "decide", fail_if_permission_policy_is_consulted)
    set_model_tool_response(
        app,
        BrainToolCall(
            id="call-risk-spoof",
            name="risk_recorder",
            arguments={"purpose": "direct"},
            risk=model_risk,
        ),
    )
    app.start()

    payload = post_text(app)

    tool_call = payload["tool_calls"][0]
    assert tool_call["status"] == "finished"
    assert "approval_required" not in tool_call
    assert "approval_id" not in tool_call
    assert tool_call["output"] == {"received": {"purpose": "direct"}}
    assert recording.calls == [{"purpose": "direct"}]
    assert single_tool_run(app)["risk"] == registry_risk
    assert_direct_turn(payload, app)


def test_model_originated_direct_tool_events_preserve_turn_and_correlation(
    app: DaemonApp,
) -> None:
    set_model_tool_response(
        app,
        BrainToolCall(id="call-correlated", name="echo", arguments={}),
    )
    app.start()

    payload = post_text(app)

    turn_id = str(payload["turn_id"])
    assert app.event_store is not None
    tool_events = [
        event
        for event in app.event_store.list_by_turn_id(turn_id, limit=100)
        if event.type in {EventType.TOOL_REQUESTED, EventType.TOOL_FINISHED}
    ]
    assert [event.type for event in tool_events] == [
        EventType.TOOL_REQUESTED,
        EventType.TOOL_FINISHED,
    ]
    assert all(event.turn_id == turn_id for event in tool_events)
    assert all(event.correlation_id == turn_id for event in tool_events)
    turn_event_types = event_types_for_turn(app, turn_id)
    assert EventType.APPROVAL_CREATED not in turn_event_types
    assert EventType.TOOL_APPROVAL_REQUIRED not in turn_event_types
    assert_direct_turn(payload, app)


def test_unknown_model_originated_tool_fails_without_creating_an_approval(
    app: DaemonApp,
) -> None:
    set_model_tool_response(
        app,
        BrainToolCall(id="call-missing", name="missing_tool", arguments={}),
    )
    app.start()

    payload = post_text(app)

    assert payload["tool_calls"][0]["status"] == "failed"
    assert "approval_required" not in payload["tool_calls"][0]
    assert "approval_id" not in payload["tool_calls"][0]
    assert "Unknown tool: missing_tool" in payload["tool_calls"][0]["error"]
    assert_direct_turn(payload, app)
    assert table_count(app, "tool_runs") == 0
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
    )

    result = orchestrator.handle_text(text="Unavailable")

    assert result.tool_calls[0]["status"] == "unavailable"
    assert "approval_required" not in result.tool_calls[0]
    assert "approval_id" not in result.tool_calls[0]
    assert "tool registry is unavailable" in result.tool_calls[0]["error"]
    assert int(conn.execute("SELECT COUNT(*) FROM approvals").fetchone()[0]) == 0


def test_model_originated_memory_save_executes_once_without_approval(
    app: DaemonApp,
) -> None:
    set_model_tool_response(app, memory_save_call())
    app.start()

    payload = post_text(app, text="Remember this through memory_save")
    turn_id = str(payload["turn_id"])
    conversation_id = str(payload["conversation_id"])
    tool_call = payload["tool_calls"][0]

    assert tool_call["tool_name"] == "memory_save"
    assert tool_call["status"] == "finished"
    assert "approval_required" not in tool_call
    assert "approval_id" not in tool_call
    assert tool_call["output"]["ok"] is True
    candidate_id = tool_call["output"]["candidate_id"]
    memory_id = tool_call["output"]["memory_id"]
    assert isinstance(candidate_id, str)
    assert isinstance(memory_id, str)
    assert_direct_turn(payload, app)
    assert table_count(app, "tool_runs") == 1
    assert table_count(app, "memory_candidates") == 1
    assert table_count(app, "memory_evidence") == 1
    assert table_count(app, "memory_items") == 1
    assert table_count(app, "memory_blocks") == 0
    assert event_type_count(app, "memory.activated") == 1

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
        "This fact should be saved exactly once by direct execution.",
        "approved",
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
        "model_tool_call",
        "call-memory-save",
        conversation_id,
        turn_id,
        "This fact should be saved exactly once by direct execution.",
        memory_id,
    )

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
    assert items[0].claim == "This fact should be saved exactly once by direct execution."
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
    assert "approval_required" not in tool_call
    assert "approval_id" not in tool_call
    assert "memory_save requires a non-empty string kind" in tool_call["error"]
    assert_direct_turn(payload, app)
    assert table_count(app, "tool_runs") == 0
    assert table_count(app, "memory_candidates") == 0
    assert table_count(app, "memory_evidence") == 0
    assert table_count(app, "memory_items") == 0
    assert table_count(app, "memory_blocks") == 0
    assert "tool.failed" in event_types_for_turn(app, str(payload["turn_id"]))


def test_mixed_invalid_memory_save_and_valid_tool_finishes_without_approval_crash(
    app: DaemonApp,
) -> None:
    set_model_tool_response(
        app,
        BrainToolCall(
            id="call-bad-memory-save",
            name="memory_save",
            arguments={"key": "value"},
        ),
        BrainToolCall(
            id="call-valid-echo",
            name="echo",
            arguments={"purpose": "mixed-batch"},
        ),
    )
    app.start()

    with running_server(app) as base_url:
        status, payload = request_json(
            "POST",
            f"{base_url}/input/text",
            {"text": "Try malformed memory_save and still run the valid probe"},
        )

    assert status == 200
    assert payload["final_text"] == "Tool execution complete."
    assert "approvals" not in payload
    assert [call["status"] for call in payload["tool_calls"]] == ["failed", "finished"]
    assert [call["tool_name"] for call in payload["tool_calls"]] == [
        "memory_save",
        "echo",
    ]
    assert table_count(app, "approvals") == 0
    assert table_count(app, "tool_runs") == 1
    run = single_tool_run(app)
    assert run["tool_name"] == "echo"
    assert run["status"] == "finished"
    assert run["approval_id"] is None
    assert event_types_for_turn(app, str(payload["turn_id"])).count("turn.finished") == 1


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
    assert "approval_required" not in tool_call
    assert "approval_id" not in tool_call
    assert "candidate_id" in tool_call["error"]
    assert "model proposal" in tool_call["error"]
    assert_direct_turn(payload, app)
    assert table_count(app, "tool_runs") == 0
    assert table_count(app, "memory_candidates") == 0
    assert table_count(app, "memory_evidence") == 0
    assert table_count(app, "memory_items") == 0
    assert table_count(app, "memory_blocks") == 0
    assert "tool.failed" in event_types_for_turn(app, str(payload["turn_id"]))


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

    payload = post_text(app, text="Try secret memory_save")

    assert payload["tool_calls"][0]["status"] == "finished"
    assert raw_secret not in json.dumps(payload, sort_keys=True)
    assert_direct_turn(payload, app)
    assert table_count(app, "tool_runs") == 1
    assert table_count(app, "memory_candidates") == 1
    assert table_count(app, "memory_evidence") == 1
    assert table_count(app, "memory_items") == 1

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


def test_model_tool_argument_secrets_are_redacted_on_direct_execution(app: DaemonApp) -> None:
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

    tool_call = payload["tool_calls"][0]
    assert tool_call["status"] == "finished"
    assert "approval_required" not in tool_call
    assert "approval_id" not in tool_call
    rendered_payload = json.dumps(payload, sort_keys=True)
    assert raw_secret not in rendered_payload
    assert REDACTION_PLACEHOLDER in rendered_payload
    assert_direct_turn(payload, app)

    rendered_run = json.dumps(single_tool_run(app), sort_keys=True)
    assert raw_secret not in rendered_run
    assert REDACTION_PLACEHOLDER in rendered_run

    assert app.event_store is not None
    rendered_events = json.dumps(
        [event.payload for event in app.event_store.list_by_turn_id(str(payload["turn_id"]), limit=100)],
        sort_keys=True,
    )
    assert raw_secret not in rendered_events
    assert REDACTION_PLACEHOLDER in rendered_events


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
    allowed_contracts = {
        ("dan/voice/shared_broker.py", "/tmp/dan"),
        ("dan/migration/test_safety.py", "/tmp/dan"),
        ("dan/migration/test_safety.py", "afplay"),
    }
    for root_name in ("dan", "scripts"):
        for path in (ROOT / root_name).rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for forbidden in FORBIDDEN_RUNTIME_STRINGS:
                relative = str(path.relative_to(ROOT))
                if (relative, forbidden) in allowed_contracts:
                    continue
                if forbidden in text:
                    findings.append(f"{relative} contains {forbidden}")

    assert findings == []
