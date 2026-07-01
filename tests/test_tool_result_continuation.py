"""Prompt 19D-mini approved tool result continuation tests."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from jarvis.brain import BrainAdapterError, BrainManager, BrainRequest, BrainResponse, BrainToolCall
from jarvis.daemon.app import DaemonApp, DaemonAppConflictError, create_daemon_app
from jarvis.events.types import EventType
from jarvis.tools.registry import Tool
from jarvis.turns.models import TurnStatus
from jarvis.turns.repository import TurnRepository
from tests.git_guards import assert_schema_and_migrations_unchanged
from tests.test_api_smoke import write_config
from tests.test_model_tool_permission_policy import table_count


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_RUNTIME_STRINGS = (
    "/Users/n1_ozzy/Documents/dev/" "dan",
    "/tmp/" "dan",
    "af" "play",
    "--dangerously-" "skip-permissions",
)


class SequenceBrainAdapter:
    name = "sequence"
    default_model = "sequence-model"

    def __init__(self, *responses: BrainResponse | Exception) -> None:
        self._responses = list(responses)
        self.requests: list[BrainRequest] = []

    def available_models(self) -> list[str]:
        return [self.default_model]

    def generate(self, request: BrainRequest) -> BrainResponse:
        self.requests.append(request)
        if not self._responses:
            raise BrainAdapterError("unexpected extra brain call")
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class RecordingContinuationTool(Tool):
    description = "records continuation tool execution"
    risk = "shell_read"
    input_schema = {"type": "object"}

    def __init__(self, *, name: str = "continuation_probe", output: Mapping[str, Any] | None = None) -> None:
        self.name = name
        self.output = dict(output or {"answer": "tool says yes"})
        self.calls: list[dict[str, Any]] = []

    def run(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        payload = dict(arguments)
        self.calls.append(payload)
        return dict(self.output)


def make_app(tmp_path: Path, adapter: SequenceBrainAdapter | None = None) -> DaemonApp:
    config_path = write_config(tmp_path / "jarvis.toml", tmp_path / "home" / "jarvis.db")
    app = create_daemon_app(config_path)
    if adapter is not None:
        app.brain_manager = BrainManager([adapter], default_adapter=adapter.name)
    return app


def set_sequence_brain(app: DaemonApp, adapter: SequenceBrainAdapter) -> None:
    app.brain_manager = BrainManager([adapter], default_adapter=adapter.name)


def model_tool_response(tool_name: str, arguments: Mapping[str, Any] | None = None) -> BrainResponse:
    return BrainResponse(
        text="Need a tool.",
        model="sequence-model",
        tool_calls=[
            BrainToolCall(
                id=f"call-{tool_name}",
                name=tool_name,
                arguments=dict(arguments or {}),
            )
        ],
    )


def turn_row(app: DaemonApp, turn_id: str) -> dict[str, Any]:
    assert app.conn is not None
    turn = TurnRepository(app.conn).get(turn_id)
    assert turn is not None
    return {
        "status": turn.status,
        "final_text": turn.final_text,
        "metadata": turn.metadata,
    }


def event_types_for_turn(app: DaemonApp, turn_id: str) -> list[str]:
    assert app.event_store is not None
    return [event.type for event in app.event_store.list_by_turn_id(turn_id, limit=200)]


def test_execute_approved_one_shot_tool_continues_original_awaiting_turn(tmp_path: Path) -> None:
    tool = RecordingContinuationTool()
    adapter = SequenceBrainAdapter(
        model_tool_response(tool.name, {"question": "status"}),
        BrainResponse(
            text="Continuation answer from tool result.",
            model="sequence-model",
            tool_calls=[BrainToolCall(id="ignored-repeat", name=tool.name, arguments={})],
        ),
    )
    app = make_app(tmp_path, adapter)
    app.tool_registry.register(tool)
    try:
        app.start()

        first = app.handle_text_input(text="Use the continuation tool")
        approval_id = str(first.approvals[0]["id"])
        approved = app.approve(approval_id, reason="ok")
        executed = app.execute_approved_tool(approval_id)

        assert approved["status"] == "approved"
        assert tool.calls == [{"question": "status"}]
        assert table_count(app, "tool_runs") == 1
        assert len(adapter.requests) == 2
        assert executed["ok"] is True
        assert executed["continuation"]["applied"] is True
        assert executed["continuation"]["status"] == "finished"

        stored = turn_row(app, first.turn_id)
        continuation = stored["metadata"]["tool_result_continuation"]
        assert stored["status"] == TurnStatus.FINISHED
        assert stored["final_text"] == "Continuation answer from tool result."
        assert continuation["approval_id"] == approval_id
        assert continuation["tool_name"] == tool.name
        assert continuation["tool_run_id"] == executed["tool_run"]["id"]
        assert continuation["previous_status"] == TurnStatus.AWAITING_APPROVAL
        assert continuation["continuation_eligible"] is True

        continuation_request = adapter.requests[1]
        assert "Continuation after approved tool execution" in continuation_request.input_text
        assert "Use the continuation tool" in continuation_request.input_text
        assert tool.name in continuation_request.input_text
        assert '"answer": "tool says yes"' in continuation_request.input_text
        assert continuation_request.metadata["tool_result_continuation"]["approval_id"] == approval_id
        assert table_count(app, "approvals") == 1
        assert table_count(app, "tool_runs") == 1
    finally:
        app.close()


def test_duplicate_execute_does_not_duplicate_tool_run_or_continuation(tmp_path: Path) -> None:
    tool = RecordingContinuationTool()
    adapter = SequenceBrainAdapter(
        model_tool_response(tool.name, {"n": 1}),
        BrainResponse(text="continued once", model="sequence-model"),
    )
    app = make_app(tmp_path, adapter)
    app.tool_registry.register(tool)
    try:
        app.start()
        first = app.handle_text_input(text="Run once")
        approval_id = str(first.approvals[0]["id"])
        app.approve(approval_id)

        app.execute_approved_tool(approval_id)
        with pytest.raises(DaemonAppConflictError):
            app.execute_approved_tool(approval_id)

        assert tool.calls == [{"n": 1}]
        assert table_count(app, "tool_runs") == 1
        assert len(adapter.requests) == 2
    finally:
        app.close()


def test_rejected_approval_cannot_execute_or_continue(tmp_path: Path) -> None:
    tool = RecordingContinuationTool()
    adapter = SequenceBrainAdapter(
        model_tool_response(tool.name),
        BrainResponse(text="should not be used", model="sequence-model"),
    )
    app = make_app(tmp_path, adapter)
    app.tool_registry.register(tool)
    try:
        app.start()
        first = app.handle_text_input(text="Reject it")
        approval_id = str(first.approvals[0]["id"])
        app.reject(approval_id, reason="no")

        with pytest.raises(DaemonAppConflictError):
            app.execute_approved_tool(approval_id)

        assert tool.calls == []
        assert table_count(app, "tool_runs") == 0
        assert len(adapter.requests) == 1
    finally:
        app.close()


def test_approval_without_turn_id_executes_without_forcing_continuation(tmp_path: Path) -> None:
    tool = RecordingContinuationTool()
    adapter = SequenceBrainAdapter(BrainResponse(text="should not be called", model="sequence-model"))
    app = make_app(tmp_path, adapter)
    app.tool_registry.register(tool)
    try:
        app.start()
        requested = app.request_tool(
            tool_name=tool.name,
            arguments={"direct": True},
            requested_by="api",
        )
        app.approve(str(requested.approval_id))

        executed = app.execute_approved_tool(str(requested.approval_id))

        assert executed["ok"] is True
        assert "continuation" not in executed
        assert tool.calls == [{"direct": True}]
        assert len(adapter.requests) == 0
    finally:
        app.close()


def test_approval_tied_to_non_awaiting_turn_executes_without_forcing_continuation(tmp_path: Path) -> None:
    tool = RecordingContinuationTool()
    adapter = SequenceBrainAdapter(BrainResponse(text="plain answer", model="sequence-model"))
    app = make_app(tmp_path, adapter)
    app.tool_registry.register(tool)
    try:
        app.start()
        finished_turn = app.handle_text_input(text="No pending approval")
        requested = app.request_tool(
            tool_name=tool.name,
            arguments={"after": "finished"},
            requested_by="api",
            turn_id=finished_turn.turn_id,
        )
        app.approve(str(requested.approval_id))

        executed = app.execute_approved_tool(str(requested.approval_id))

        assert executed["ok"] is True
        assert "continuation" not in executed
        assert tool.calls == [{"after": "finished"}]
        assert len(adapter.requests) == 1
        assert turn_row(app, finished_turn.turn_id)["status"] == TurnStatus.FINISHED
    finally:
        app.close()


def test_unknown_and_blocked_model_tools_create_no_approval_and_do_not_continue(tmp_path: Path) -> None:
    blocked = RecordingContinuationTool(name="blocked_continuation")
    blocked.risk = "destructive"
    adapter = SequenceBrainAdapter(
        BrainResponse(
            text="Need tools.",
            model="sequence-model",
            tool_calls=[
                BrainToolCall(id="call-missing", name="missing_tool", arguments={}),
                BrainToolCall(id="call-blocked", name=blocked.name, arguments={}),
            ],
        )
    )
    app = make_app(tmp_path, adapter)
    app.tool_registry.register(blocked)
    try:
        app.start()

        result = app.handle_text_input(text="Try unsafe tools")

        assert result.approvals == []
        assert [call["status"] for call in result.tool_calls] == ["unknown", "blocked"]
        assert table_count(app, "approvals") == 0
        assert table_count(app, "tool_runs") == 0
        assert len(adapter.requests) == 1
        assert blocked.calls == []
    finally:
        app.close()


def test_new_input_after_pending_approval_remains_allowed(tmp_path: Path) -> None:
    tool = RecordingContinuationTool()
    adapter = SequenceBrainAdapter(
        model_tool_response(tool.name),
        BrainResponse(text="plain follow-up", model="sequence-model"),
    )
    app = make_app(tmp_path, adapter)
    app.tool_registry.register(tool)
    try:
        app.start()

        first = app.handle_text_input(text="Needs approval")
        second = app.handle_text_input(
            text="Plain follow-up",
            conversation_id=first.conversation_id,
        )

        assert first.turn.status == TurnStatus.AWAITING_APPROVAL
        assert second.turn.status == TurnStatus.FINISHED
        assert second.final_text == "plain follow-up"
        assert len(adapter.requests) == 2
    finally:
        app.close()


def test_continuation_failure_keeps_tool_run_and_records_predictable_metadata(tmp_path: Path) -> None:
    tool = RecordingContinuationTool()
    adapter = SequenceBrainAdapter(
        model_tool_response(tool.name, {"n": 1}),
        BrainAdapterError("continuation brain failed"),
    )
    app = make_app(tmp_path, adapter)
    app.tool_registry.register(tool)
    try:
        app.start()
        first = app.handle_text_input(text="Continue but fail")
        approval_id = str(first.approvals[0]["id"])
        app.approve(approval_id)

        executed = app.execute_approved_tool(approval_id)
        with pytest.raises(DaemonAppConflictError):
            app.execute_approved_tool(approval_id)

        stored = turn_row(app, first.turn_id)
        continuation = stored["metadata"]["tool_result_continuation"]
        assert executed["ok"] is True
        assert executed["continuation"]["status"] == "failed"
        assert stored["status"] == TurnStatus.AWAITING_APPROVAL
        assert continuation["status"] == "failed"
        assert continuation["continuation_eligible"] is True
        assert "continuation brain failed" in continuation["error"]
        assert tool.calls == [{"n": 1}]
        assert table_count(app, "tool_runs") == 1
        assert len(adapter.requests) == 2
        assert EventType.BRAIN_FAILED in event_types_for_turn(app, first.turn_id)
        assert EventType.ERROR_RAISED in event_types_for_turn(app, first.turn_id)
    finally:
        app.close()


def test_event_store_redacts_continuation_payloads(tmp_path: Path) -> None:
    raw_secret = "sk-ant-continuation123"
    tool = RecordingContinuationTool(
        output={
            "stdout": f"tool returned {raw_secret}",
            "api_key": raw_secret,
        }
    )
    adapter = SequenceBrainAdapter(
        model_tool_response(tool.name, {"token": raw_secret, "note": f"Bearer {raw_secret}"}),
        BrainResponse(text="redacted continuation answer", model="sequence-model"),
    )
    app = make_app(tmp_path, adapter)
    app.tool_registry.register(tool)
    try:
        app.start()
        first = app.handle_text_input(text="Use secret-shaped data")
        approval_id = str(first.approvals[0]["id"])
        app.approve(approval_id)

        app.execute_approved_tool(approval_id)

        assert app.event_store is not None
        rendered_events = json.dumps(
            [event.payload for event in app.event_store.list_by_turn_id(first.turn_id, limit=200)],
            sort_keys=True,
        )
        rendered_request = json.dumps(adapter.requests[1].metadata, sort_keys=True)
        assert raw_secret not in rendered_events
        assert raw_secret not in rendered_request
    finally:
        app.close()


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
