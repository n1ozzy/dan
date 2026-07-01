"""Prompt 11 text turn pipeline tests."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from jarvis.brain import (
    BrainAdapterError,
    BrainManager,
    BrainRequest,
    BrainResponse,
    BrainToolCall,
    MockBrainAdapter,
)
from jarvis.brain.claude_cli_adapter import ClaudeCliAdapter
from jarvis.brain.context_builder import ContextBuilder, ContextBuilderError
from jarvis.daemon.app import DaemonApp, create_daemon_app
from jarvis.daemon.lifecycle import build_server
from jarvis.daemon.state_machine import RuntimeState, RuntimeStateMachine
from jarvis.events.bus import EventBus
from jarvis.store.db import close_quietly, initialize_database
from jarvis.store.event_store import create_event_store
from jarvis.turns.orchestrator import (
    TurnOrchestrator,
    TurnOrchestratorBusyError,
    TurnOrchestratorError,
)
from jarvis.turns.repository import ConversationRepository, TurnRepository
from tests.git_guards import assert_schema_and_migrations_unchanged
from tests.test_api_smoke import write_config


ROOT = Path(__file__).resolve().parents[1]


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
    connection = initialize_database(tmp_path / "turn-pipeline.db")
    try:
        yield connection
    finally:
        close_quietly(connection)


@contextmanager
def running_server(app: DaemonApp) -> Iterator[str]:
    server = build_server(app, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, name="jarvis-text-turn-http", daemon=True)
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


def table_count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def event_types_for_turn(app: DaemonApp, turn_id: str) -> list[str]:
    assert app.event_store is not None
    return [event.type for event in app.event_store.list_by_turn_id(turn_id, limit=100)]


def state_transitions_for_turn(app: DaemonApp, turn_id: str) -> list[tuple[str, str]]:
    assert app.event_store is not None
    events = app.event_store.list_by_turn_id(turn_id, limit=100)
    return [
        (str(event.payload["old_state"]), str(event.payload["new_state"]))
        for event in events
        if event.type == "state.changed"
    ]


def final_runtime_state(conn: sqlite3.Connection) -> str | None:
    events = create_event_store(conn).list_after(0, limit=200)
    state_changes = [event for event in events if event.type == "state.changed"]
    if not state_changes:
        return None
    return str(state_changes[-1].payload["new_state"])


def event_payload_for_turn(conn: sqlite3.Connection, turn_id: str, event_type: str) -> dict[str, object]:
    events = create_event_store(conn).list_by_turn_id(turn_id, limit=100)
    matches = [event.payload for event in events if event.type == event_type]
    assert len(matches) == 1
    return matches[0]


def assert_subsequence(actual: list[str], expected: list[str]) -> None:
    cursor = 0
    for value in actual:
        if value == expected[cursor]:
            cursor += 1
            if cursor == len(expected):
                return
    raise AssertionError(f"{expected!r} is not a subsequence of {actual!r}")


def make_orchestrator(
    conn: sqlite3.Connection,
    *,
    event_bus: EventBus | None = None,
    brain_manager: BrainManager | None = None,
    context_builder: ContextBuilder | None = None,
) -> TurnOrchestrator:
    event_store = create_event_store(conn)
    state_machine = RuntimeStateMachine(event_store, event_bus=event_bus, initial_state=RuntimeState.IDLE)
    return TurnOrchestrator(
        conn=conn,
        event_store=event_store,
        event_bus=event_bus,
        state_machine=state_machine,
        brain_manager=brain_manager or BrainManager([MockBrainAdapter()]),
        context_builder=context_builder or ContextBuilder(conn),
    )


class FailingBrainAdapter:
    name = "failing"
    default_model = "failing-model"

    def available_models(self) -> list[str]:
        return [self.default_model]

    def generate(self, request: BrainRequest) -> BrainResponse:
        raise BrainAdapterError("mock brain failure")


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
        return self.response


class FailingContextBuilder:
    def build_request(self, **kwargs: object) -> object:
        raise ContextBuilderError("mock context failure")


class RecordingCliRunner:
    def __init__(self, stdout: str = "provider reply\n") -> None:
        self.stdout = stdout
        self.calls: list[dict[str, object]] = []

    def __call__(
        self,
        command: list[str],
        input_text: str,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(
            {
                "command": list(command),
                "input_text": input_text,
                "timeout": timeout,
            }
        )
        return subprocess.CompletedProcess(command, 0, stdout=self.stdout, stderr="")


def test_post_input_text_with_mock_brain_returns_200_json(app: DaemonApp) -> None:
    app.start()

    with running_server(app) as base_url:
        status, payload = request_json(
            "POST",
            f"{base_url}/input/text",
            {"text": "Hello Jarvis", "metadata": {"client": "test"}, "extra": "ignored"},
        )

    assert status == 200
    assert payload["ok"] is True
    assert isinstance(payload["turn_id"], str)
    assert isinstance(payload["conversation_id"], str)
    assert payload["input_text"] == "Hello Jarvis"
    assert payload["final_text"] == "Jarvis mock response: Hello Jarvis"
    assert payload["brain_adapter"] == "mock"
    assert payload["brain_model"] == "mock-local"
    assert payload["state"] == "IDLE"
    assert payload["tool_calls"] == []
    assert payload["approvals"] == []
    assert payload["turn"]["metadata"] == {"client": "test"}
    assert "extra" not in payload["turn"]["metadata"]


def test_post_input_text_with_cli_source_persists_source_and_emits_events(app: DaemonApp) -> None:
    app.start()

    with running_server(app) as base_url:
        status, payload = request_json(
            "POST",
            f"{base_url}/input/text",
            {"text": "From CLI", "source": "cli"},
        )

    assert status == 200
    assert payload["turn"]["source"] == "cli"
    assert app.conn is not None
    row = app.conn.execute("SELECT source FROM turns WHERE id = ?", (payload["turn_id"],)).fetchone()
    assert row[0] == "cli"
    types = event_types_for_turn(app, str(payload["turn_id"]))
    assert_subsequence(
        types,
        [
            "input.text.received",
            "turn.started",
            "turn.context.built",
            "brain.requested",
            "brain.responded",
            "turn.finished",
        ],
    )
    assert table_count(app.conn, "voice_queue") == 0
    assert table_count(app.conn, "tool_runs") == 0
    assert table_count(app.conn, "worker_jobs") == 0


def test_post_input_text_without_source_defaults_to_api(app: DaemonApp) -> None:
    app.start()

    with running_server(app) as base_url:
        status, payload = request_json("POST", f"{base_url}/input/text", {"text": "Default source"})

    assert status == 200
    assert payload["turn"]["source"] == "api"
    assert app.conn is not None
    row = app.conn.execute("SELECT source FROM turns WHERE id = ?", (payload["turn_id"],)).fetchone()
    assert row[0] == "api"


@pytest.mark.parametrize("source", ["voice", "worker", "", 123])
def test_post_input_text_with_invalid_source_returns_400_and_creates_no_turn(
    app: DaemonApp,
    source: object,
) -> None:
    app.start()

    with running_server(app) as base_url:
        status, payload = request_json(
            "POST",
            f"{base_url}/input/text",
            {"text": "Bad source", "source": source},
        )

    assert status == 400
    assert payload["status"] == 400
    assert app.conn is not None
    assert table_count(app.conn, "turns") == 0


def test_one_input_creates_one_persisted_turn_and_final_text_survives_reload(
    app: DaemonApp,
) -> None:
    app.start()

    with running_server(app) as base_url:
        status, payload = request_json("POST", f"{base_url}/input/text", {"text": "Persist me"})

    assert status == 200
    assert app.conn is not None
    assert table_count(app.conn, "turns") == 1
    turn_id = str(payload["turn_id"])
    close_quietly(app.conn)
    app.conn = sqlite3.connect(app.paths.db_path)
    app.conn.row_factory = sqlite3.Row

    row = app.conn.execute(
        "SELECT id, final_text, brain_adapter, brain_model FROM turns WHERE id = ?",
        (turn_id,),
    ).fetchone()
    assert row["final_text"] == "Jarvis mock response: Persist me"
    assert row["brain_adapter"] == "mock"
    assert row["brain_model"] == "mock-local"


def test_conversation_is_created_when_omitted_and_existing_id_is_reused(app: DaemonApp) -> None:
    app.start()

    with running_server(app) as base_url:
        first_status, first = request_json("POST", f"{base_url}/input/text", {"text": "First"})
        conversation_id = str(first["conversation_id"])
        second_status, second = request_json(
            "POST",
            f"{base_url}/input/text",
            {"conversation_id": conversation_id, "text": "Second"},
        )

    assert first_status == 200
    assert second_status == 200
    assert second["conversation_id"] == conversation_id
    assert app.conn is not None
    assert table_count(app.conn, "conversations") == 1
    assert table_count(app.conn, "turns") == 2


@pytest.mark.parametrize("payload", [{}, {"text": ""}, {"text": "   "}, {"text": 123}])
def test_missing_blank_or_non_string_text_returns_400_and_creates_no_turn(
    app: DaemonApp,
    payload: object,
) -> None:
    app.start()

    with running_server(app) as base_url:
        status, response = request_json("POST", f"{base_url}/input/text", payload)

    assert status == 400
    assert response["status"] == 400
    assert app.conn is not None
    assert table_count(app.conn, "turns") == 0


def test_non_object_json_returns_400_and_creates_no_turn(app: DaemonApp) -> None:
    app.start()

    with running_server(app) as base_url:
        status, payload = request_json("POST", f"{base_url}/input/text", ["not", "object"])

    assert status == 400
    assert payload["status"] == 400
    assert app.conn is not None
    assert table_count(app.conn, "turns") == 0


def test_invalid_json_returns_400_and_creates_no_turn(app: DaemonApp) -> None:
    app.start()

    with running_server(app) as base_url:
        status, payload = request_json("POST", f"{base_url}/input/text", b"{not-json")

    assert status == 400
    assert payload["status"] == 400
    assert app.conn is not None
    assert table_count(app.conn, "turns") == 0


def test_not_started_app_returns_503_and_creates_no_turn(app: DaemonApp) -> None:
    with running_server(app) as base_url:
        status, payload = request_json("POST", f"{base_url}/input/text", {"text": "Wait"})

    assert status == 503
    assert payload["status"] == 503
    assert app.conn is not None
    assert table_count(app.conn, "turns") == 0


def test_held_text_turn_lock_returns_409_and_creates_no_turn(app: DaemonApp) -> None:
    app.start()
    assert app.text_turn_lock.acquire(blocking=False)
    try:
        with running_server(app) as base_url:
            status, payload = request_json("POST", f"{base_url}/input/text", {"text": "Busy"})
    finally:
        app.text_turn_lock.release()

    assert status == 409
    assert payload["status"] == 409
    assert app.conn is not None
    assert table_count(app.conn, "turns") == 0


def test_successful_turn_emits_ordered_events_and_state_transitions(app: DaemonApp) -> None:
    app.start()

    with running_server(app) as base_url:
        status, payload = request_json("POST", f"{base_url}/input/text", {"text": "Events"})

    assert status == 200
    turn_id = str(payload["turn_id"])
    types = event_types_for_turn(app, turn_id)
    assert_subsequence(
        types,
        [
            "input.text.received",
            "turn.started",
            "turn.context.built",
            "brain.requested",
            "brain.responded",
            "turn.finished",
        ],
    )
    assert state_transitions_for_turn(app, turn_id) == [
        ("IDLE", "THINKING"),
        ("THINKING", "IDLE"),
    ]


def test_event_bus_subscribers_receive_orchestrator_events(conn: sqlite3.Connection) -> None:
    bus = EventBus()
    seen: list[str] = []
    bus.subscribe(lambda event: seen.append(event.type))

    result = make_orchestrator(conn, event_bus=bus).handle_text(text="Bus")

    assert result.final_text == "Jarvis mock response: Bus"
    assert "input.text.received" in seen
    assert "turn.finished" in seen


def test_failing_event_bus_subscriber_does_not_fail_turn(conn: sqlite3.Connection) -> None:
    bus = EventBus()

    def fail(_event: object) -> None:
        raise RuntimeError("subscriber failed")

    bus.subscribe(fail)

    result = make_orchestrator(conn, event_bus=bus).handle_text(text="Still works")

    assert result.final_text == "Jarvis mock response: Still works"
    assert bus.last_errors


def test_brain_request_is_assembled_from_jarvis_owned_context(conn: sqlite3.Connection) -> None:
    conversations = ConversationRepository(conn)
    turns = TurnRepository(conn)
    conversation = conversations.create(conversation_id="conversation-owned")
    turns.create(conversation.id, source="api", input_text="Previous", turn_id="turn-prev")
    turns.finish("turn-prev", final_text="Previous answer", brain_adapter="mock", brain_model="mock-local")

    result = make_orchestrator(conn).handle_text(
        text="Use owned context",
        conversation_id=conversation.id,
    )

    turn = TurnRepository(conn).get(result.turn_id)
    assert turn is not None
    assert turn.context_snapshot is not None
    assert turn.context_snapshot["provider_sessions_are_memory"] is False
    assert turn.context_snapshot["recent_turn_count"] == 1
    assert result.brain_adapter == "mock"
    assert result.brain_model == "mock-local"


def test_mock_default_brain_model_metadata_stays_mock_local(conn: sqlite3.Connection) -> None:
    result = make_orchestrator(conn).handle_text(text="Mock metadata")

    requested = event_payload_for_turn(conn, result.turn_id, "brain.requested")
    responded = event_payload_for_turn(conn, result.turn_id, "brain.responded")
    finished = event_payload_for_turn(conn, result.turn_id, "turn.finished")

    assert requested["adapter"] == "mock"
    assert requested["model"] == "mock-local"
    assert responded["model"] == requested["model"]
    assert finished["brain_model"] == responded["model"]
    assert result.brain_model == "mock-local"


def test_fake_claude_cli_turn_uses_selected_adapter_model_metadata(
    conn: sqlite3.Connection,
) -> None:
    runner = RecordingCliRunner(stdout="claude final\n")
    manager = BrainManager(
        [
            MockBrainAdapter(),
            ClaudeCliAdapter(command="fake-claude", args=["-p"], runner=runner),
        ],
        default_adapter="claude_cli",
    )

    context_builder = ContextBuilder(
        conn,
        config=SimpleNamespace(
            brain=SimpleNamespace(
                default_adapter="claude_cli",
                default_model="mock-local",
                context_budget_chars=24000,
            ),
            memory=SimpleNamespace(max_active_blocks=50, max_context_chars=12000),
        ),
    )

    result = make_orchestrator(
        conn,
        brain_manager=manager,
        context_builder=context_builder,
    ).handle_text(text="Provider metadata")

    requested = event_payload_for_turn(conn, result.turn_id, "brain.requested")
    responded = event_payload_for_turn(conn, result.turn_id, "brain.responded")
    finished = event_payload_for_turn(conn, result.turn_id, "turn.finished")

    assert len(runner.calls) == 1
    assert runner.calls[0]["command"] == ["fake-claude", "-p"]
    assert runner.calls[0]["timeout"] == 120.0
    assert requested["adapter"] == "claude_cli"
    assert requested["model"] == "claude-cli"
    assert responded["model"] == requested["model"]
    assert finished["brain_model"] == responded["model"]
    assert result.brain_model == "claude-cli"


def test_fake_claude_cli_tool_call_block_creates_approval_without_execution(
    app: DaemonApp,
) -> None:
    runner = RecordingCliRunner(
        stdout=(
            'Need approval.\n'
            '<jarvis_tool_call>{"name":"approval_probe","arguments":{"reason":"integration"}}</jarvis_tool_call>\n'
            'Waiting.'
        )
    )
    app.brain_manager = BrainManager(
        [
            MockBrainAdapter(),
            ClaudeCliAdapter(command="fake-claude", args=["-p"], runner=runner),
        ],
        default_adapter="claude_cli",
    )
    app.start()

    with running_server(app) as base_url:
        status, payload = request_json("POST", f"{base_url}/input/text", {"text": "Probe via CLI"})

    assert status == 200
    assert len(runner.calls) == 1
    assert payload["final_text"].startswith("Need approval.\nWaiting.")
    assert "approval_probe requires approval" in payload["final_text"]
    assert len(payload["tool_calls"]) == 1
    assert payload["tool_calls"][0]["tool_name"] == "approval_probe"
    assert payload["tool_calls"][0]["status"] == "approval_required"
    assert payload["approvals"][0]["tool_name"] == "approval_probe"
    assert payload["approvals"][0]["status"] == "pending"
    assert app.conn is not None
    assert table_count(app.conn, "approvals") == 1
    assert table_count(app.conn, "tool_runs") == 0
    assert table_count(app.conn, "voice_queue") == 0
    assert table_count(app.conn, "worker_jobs") == 0


def test_no_voice_tool_or_worker_rows_are_created(app: DaemonApp) -> None:
    app.start()

    with running_server(app) as base_url:
        status, _payload = request_json("POST", f"{base_url}/input/text", {"text": "No side effects"})

    assert status == 200
    assert app.conn is not None
    assert table_count(app.conn, "voice_queue") == 0
    assert table_count(app.conn, "tool_runs") == 0
    assert table_count(app.conn, "worker_jobs") == 0


def test_model_originated_safe_echo_tool_call_creates_approval_without_execution(
    app: DaemonApp,
) -> None:
    response = BrainResponse(
        text="I need echo.",
        model="tool-calling-model",
        tool_calls=[
            BrainToolCall(
                id="call-echo",
                name="echo",
                arguments={"text": "hello"},
            )
        ],
    )
    app.brain_manager = BrainManager(
        [ToolCallingBrainAdapter(response)],
        default_adapter="tool_calling",
    )
    app.start()

    with running_server(app) as base_url:
        status, payload = request_json("POST", f"{base_url}/input/text", {"text": "Use echo"})

    assert status == 200
    assert payload["final_text"].startswith("I need echo.")
    assert "Tool requests captured" in payload["final_text"]
    assert "echo requires approval" in payload["final_text"]
    assert payload["state"] == "IDLE"
    assert len(payload["tool_calls"]) == 1
    tool_call = payload["tool_calls"][0]
    assert tool_call["id"] == "call-echo"
    assert tool_call["tool_name"] == "echo"
    assert tool_call["status"] == "approval_required"
    assert tool_call["approval_required"] is True
    assert isinstance(tool_call["approval_id"], str)
    assert len(payload["approvals"]) == 1
    assert payload["approvals"][0] == {
        "id": tool_call["approval_id"],
        "tool_call_id": "call-echo",
        "tool_name": "echo",
        "status": "pending",
        "risk": "safe_read",
    }
    assert app.conn is not None
    assert table_count(app.conn, "approvals") == 1
    assert table_count(app.conn, "tool_runs") == 0
    assert table_count(app.conn, "voice_queue") == 0
    assert table_count(app.conn, "worker_jobs") == 0

    approval = app.approval_gate.get_approval(str(tool_call["approval_id"]))  # type: ignore[union-attr]
    assert approval is not None
    assert approval["requested_by"] == "model"
    assert approval["payload"]["tool_name"] == "echo"
    assert approval["payload"]["arguments"] == {"text": "hello"}
    assert approval["payload"]["turn_id"] == payload["turn_id"]
    assert approval["metadata"]["origin"] == "model"
    assert approval["metadata"]["tool_call_id"] == "call-echo"

    assert app.event_store is not None
    approval_events = [
        event
        for event in app.event_store.list_by_turn_id(str(payload["turn_id"]), limit=100)
        if event.type == "approval.created"
    ]
    assert len(approval_events) == 1
    approval_event = approval_events[0]
    assert approval_event.turn_id == payload["turn_id"]
    assert approval_event.correlation_id == payload["turn_id"]
    assert approval_event.payload["approval_id"] == tool_call["approval_id"]
    assert approval_event.payload["risk"] == "safe_read"
    assert approval_event.payload["requested_by"] == "model"
    assert approval_event.payload["payload"]["tool_name"] == "echo"
    assert approval_event.payload["metadata"]["origin"] == "model"
    assert approval_event.payload["metadata"]["tool_call_id"] == "call-echo"

    capture = payload["turn"]["metadata"]["tool_call_capture"]
    assert capture["origin"] == "model"
    assert capture["total"] == 1
    assert capture["approval_count"] == 1
    assert capture["error_count"] == 0

    event_types = event_types_for_turn(app, str(payload["turn_id"]))
    assert "brain.responded" in event_types
    assert "tool.requested" in event_types
    assert "tool.approval.required" in event_types
    assert event_types.index("brain.responded") < event_types.index("tool.requested")
    assert event_types.index("tool.requested") < event_types.index("tool.approval.required")


def test_model_originated_approval_probe_creates_approval_without_execution(
    app: DaemonApp,
) -> None:
    response = BrainResponse(
        text="Probe requested.",
        model="tool-calling-model",
        tool_calls=[
            BrainToolCall(
                id="call-probe",
                name="approval_probe",
                arguments={"purpose": "model"},
            )
        ],
    )
    app.brain_manager = BrainManager(
        [ToolCallingBrainAdapter(response)],
        default_adapter="tool_calling",
    )
    app.start()

    with running_server(app) as base_url:
        status, payload = request_json("POST", f"{base_url}/input/text", {"text": "Probe"})

    assert status == 200
    assert payload["tool_calls"][0]["tool_name"] == "approval_probe"
    assert payload["tool_calls"][0]["status"] == "approval_required"
    assert payload["approvals"][0]["tool_name"] == "approval_probe"
    assert payload["approvals"][0]["status"] == "pending"
    assert app.conn is not None
    assert table_count(app.conn, "approvals") == 1
    assert table_count(app.conn, "tool_runs") == 0
    assert table_count(app.conn, "voice_queue") == 0
    assert table_count(app.conn, "worker_jobs") == 0


def test_unknown_model_tool_call_is_reported_without_execution_or_approval(
    app: DaemonApp,
) -> None:
    response = BrainResponse(
        text="Unknown tool please.",
        model="tool-calling-model",
        tool_calls=[BrainToolCall(id="call-missing", name="missing_tool", arguments={})],
    )
    app.brain_manager = BrainManager(
        [ToolCallingBrainAdapter(response)],
        default_adapter="tool_calling",
    )
    app.start()

    with running_server(app) as base_url:
        status, payload = request_json("POST", f"{base_url}/input/text", {"text": "Missing"})

    assert status == 200
    assert payload["state"] == "IDLE"
    assert payload["tool_calls"] == [
        {
            "id": "call-missing",
            "tool_name": "missing_tool",
            "status": "unknown",
            "approval_required": False,
            "approval_id": None,
            "error": "Unknown tool: missing_tool",
        }
    ]
    assert payload["approvals"] == []
    assert "missing_tool unknown" in payload["final_text"]
    assert payload["turn"]["metadata"]["tool_call_capture"]["error_count"] == 1
    assert app.conn is not None
    assert table_count(app.conn, "approvals") == 0
    assert table_count(app.conn, "tool_runs") == 0
    assert table_count(app.conn, "voice_queue") == 0
    assert table_count(app.conn, "worker_jobs") == 0
    event_types = event_types_for_turn(app, str(payload["turn_id"]))
    assert "tool.failed" in event_types
    assert "error.raised" in event_types


def test_model_tool_call_with_non_json_safe_arguments_is_reported_without_execution(
    app: DaemonApp,
) -> None:
    response = BrainResponse(
        text="Bad args.",
        model="tool-calling-model",
        tool_calls=[
            BrainToolCall(
                id="call-bad-args",
                name="echo",
                arguments={"bad": object()},
            )
        ],
    )
    app.brain_manager = BrainManager(
        [ToolCallingBrainAdapter(response)],
        default_adapter="tool_calling",
    )
    app.start()

    with running_server(app) as base_url:
        status, payload = request_json("POST", f"{base_url}/input/text", {"text": "Bad args"})

    assert status == 200
    assert payload["state"] == "IDLE"
    assert payload["tool_calls"] == [
        {
            "id": "call-bad-args",
            "tool_name": "echo",
            "status": "failed",
            "approval_required": False,
            "approval_id": None,
            "error": "tool arguments must be JSON serializable",
        }
    ]
    assert payload["approvals"] == []
    assert "echo failed" in payload["final_text"]
    assert payload["turn"]["metadata"]["tool_call_capture"]["error_count"] == 1
    assert app.conn is not None
    assert table_count(app.conn, "approvals") == 0
    assert table_count(app.conn, "tool_runs") == 0
    assert table_count(app.conn, "voice_queue") == 0
    assert table_count(app.conn, "worker_jobs") == 0
    event_types = event_types_for_turn(app, str(payload["turn_id"]))
    assert "tool.failed" in event_types
    assert "error.raised" in event_types


def test_brain_failure_marks_turn_failed_and_records_events(conn: sqlite3.Connection) -> None:
    manager = BrainManager([FailingBrainAdapter()], default_adapter="failing")
    orchestrator = make_orchestrator(conn, brain_manager=manager)

    with pytest.raises(TurnOrchestratorError, match="brain"):
        orchestrator.handle_text(text="Fail brain")

    turn = conn.execute("SELECT status, error FROM turns").fetchone()
    assert turn[0] == "failed"
    assert "mock brain failure" in turn[1]
    event_types = [event.type for event in create_event_store(conn).list_after(0, limit=100)]
    assert "brain.failed" in event_types
    assert "turn.failed" in event_types
    assert "error.raised" in event_types
    assert final_runtime_state(conn) == "IDLE"


def test_context_build_failure_marks_turn_failed_and_records_error(conn: sqlite3.Connection) -> None:
    orchestrator = make_orchestrator(conn, context_builder=FailingContextBuilder())  # type: ignore[arg-type]

    with pytest.raises(TurnOrchestratorError, match="context"):
        orchestrator.handle_text(text="Fail context")

    turn = conn.execute("SELECT status, error FROM turns").fetchone()
    assert turn[0] == "failed"
    assert "mock context failure" in turn[1]
    event_types = [event.type for event in create_event_store(conn).list_after(0, limit=100)]
    assert "turn.failed" in event_types
    assert "error.raised" in event_types
    assert final_runtime_state(conn) == "IDLE"


def test_non_idle_runtime_returns_409_and_creates_no_turn(app: DaemonApp) -> None:
    app.start()
    assert app.state_machine is not None
    # Move the runtime out of IDLE without holding the text-turn lock, so the 409
    # is produced by the orchestrator precondition rather than the DaemonApp lock.
    app.state_machine.transition(RuntimeState.THINKING, reason="test busy runtime")

    with running_server(app) as base_url:
        status, payload = request_json("POST", f"{base_url}/input/text", {"text": "Busy runtime"})

    assert status == 409
    assert payload["status"] == 409
    assert app.conn is not None
    assert table_count(app.conn, "turns") == 0


def test_handle_text_on_non_idle_runtime_raises_busy_error_without_turn(
    conn: sqlite3.Connection,
) -> None:
    event_store = create_event_store(conn)
    state_machine = RuntimeStateMachine(event_store, initial_state=RuntimeState.THINKING)
    orchestrator = TurnOrchestrator(
        conn=conn,
        event_store=event_store,
        event_bus=None,
        state_machine=state_machine,
        brain_manager=BrainManager([MockBrainAdapter()]),
        context_builder=ContextBuilder(conn),
    )

    with pytest.raises(TurnOrchestratorBusyError):
        orchestrator.handle_text(text="Busy")

    # Busy stays a TurnOrchestratorError subclass so existing callers keep working.
    assert issubclass(TurnOrchestratorBusyError, TurnOrchestratorError)
    assert table_count(conn, "turns") == 0


def test_text_turn_lock_released_after_failure(app: DaemonApp) -> None:
    app.start()
    app.brain_manager = BrainManager([FailingBrainAdapter()], default_adapter="failing")

    with pytest.raises(TurnOrchestratorError, match="brain"):
        app.handle_text_input(text="Fail then release")

    # The lock must be free again and the runtime must not be stranded in THINKING.
    assert app.text_turn_lock.acquire(blocking=False)
    app.text_turn_lock.release()
    assert app.state_machine is not None
    assert app.state_machine.state is RuntimeState.IDLE


@pytest.mark.parametrize(
    "payload",
    [
        {"text": "hi", "metadata": "not-an-object"},
        {"text": "hi", "metadata": ["nope"]},
        {"text": "hi", "conversation_id": 123},
        {"text": "hi", "conversation_id": "   "},
    ],
)
def test_invalid_metadata_or_conversation_id_returns_400_and_creates_no_turn(
    app: DaemonApp,
    payload: object,
) -> None:
    app.start()

    with running_server(app) as base_url:
        status, response = request_json("POST", f"{base_url}/input/text", payload)

    assert status == 400
    assert response["status"] == 400
    assert app.conn is not None
    assert table_count(app.conn, "turns") == 0


def test_get_input_text_returns_json_405_or_501(app: DaemonApp) -> None:
    with running_server(app) as base_url:
        status, payload = request_json("GET", f"{base_url}/input/text")

    assert status in {405, 501}
    assert payload["status"] == status


def test_runtime_files_do_not_contain_forbidden_legacy_strings() -> None:
    forbidden = (
        "/Users/n1_ozzy/Documents/dev/dan",
        "/tmp/dan",
        "afplay",
        "--dangerously-skip-permissions",
    )
    scanned = (
        ROOT / "jarvis" / "turns" / "orchestrator.py",
        ROOT / "jarvis" / "api" / "routes_input.py",
        ROOT / "jarvis" / "daemon" / "app.py",
        ROOT / "jarvis" / "daemon" / "lifecycle.py",
    )
    offenders: list[tuple[str, str]] = []

    for path in scanned:
        source = path.read_text(encoding="utf-8")
        for snippet in forbidden:
            if snippet in source:
                offenders.append((str(path.relative_to(ROOT)), snippet))

    assert offenders == []


def test_sqlite_schema_and_migrations_are_not_modified() -> None:
    assert_schema_and_migrations_unchanged(ROOT)
