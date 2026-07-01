"""Prompt 13 tool permission, approval and recorder tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from jarvis.events.types import EventType
from jarvis.store.db import close_quietly, initialize_database
from jarvis.store.event_store import EventStore, create_event_store
from jarvis.tools.file_tool import FileReadPlaceholderTool, FileWritePlaceholderTool
from jarvis.tools.permissions import ToolDecision, ToolPermissionPolicy
from jarvis.tools.registry import (
    ApprovalGate,
    ApprovalProbeTool,
    Tool,
    ToolRegistry,
    ToolRegistryError,
    ToolRequest,
    ToolRunRecorder,
)
from jarvis.tools.shell_tool import ShellReadPlaceholderTool, ShellWritePlaceholderTool
from jarvis.tools.system_tool import SystemStatusTool

from tests.git_guards import assert_schema_and_migrations_unchanged


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    db_conn = initialize_database(tmp_path / "jarvis.db")
    try:
        yield db_conn
    finally:
        close_quietly(db_conn)


@pytest.fixture
def event_store(conn: sqlite3.Connection) -> EventStore:
    return create_event_store(conn)


class RecordingTool(Tool):
    name = "recording"
    description = "records execution"
    risk = "safe_read"
    input_schema = {"type": "object"}

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        payload = dict(arguments)
        self.calls.append(payload)
        return {"received": payload}


class ApprovalTool(RecordingTool):
    name = "approval"
    risk = "shell_read"


class BlockedTool(RecordingTool):
    name = "blocked"
    risk = "destructive"


class ExplodingTool(RecordingTool):
    name = "exploding"

    def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("boom")


def latest_event_types(store: EventStore) -> list[str]:
    return [event.type for event in store.list_after(0, limit=100)]


def table_count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def test_safe_read_allows() -> None:
    result = ToolPermissionPolicy().decide("safe_read", tool_name="echo", payload={})

    assert result.decision == ToolDecision.ALLOW
    assert result.approval_required is False
    assert result.blocked is False


def test_safe_status_allows() -> None:
    result = ToolPermissionPolicy().decide("safe_status", tool_name="system_status", payload={})

    assert result.decision == ToolDecision.ALLOW


def test_file_read_allows_when_approved_root_policy_is_satisfied(tmp_path: Path) -> None:
    allowed_file = tmp_path / "notes.txt"
    policy = ToolPermissionPolicy(approved_roots=[str(tmp_path)])

    result = policy.decide("file_read", tool_name="file.read", payload={"path": str(allowed_file)})

    assert result.decision == ToolDecision.ALLOW


def test_file_read_blocks_outside_approved_roots(tmp_path: Path) -> None:
    policy = ToolPermissionPolicy(approved_roots=[str(tmp_path / "allowed")])

    result = policy.decide("file_read", tool_name="file.read", payload={"path": str(tmp_path / "other.txt")})

    assert result.decision == ToolDecision.BLOCKED
    assert result.blocked is True


@pytest.mark.parametrize("risk", ["file_write", "shell_read", "shell_write", "network"])
def test_approval_required_risks_require_approval(risk: str) -> None:
    result = ToolPermissionPolicy().decide(risk, tool_name="risky", payload={})

    assert result.decision == ToolDecision.APPROVAL_REQUIRED
    assert result.approval_required is True
    assert result.blocked is False


def test_destructive_blocked_by_default() -> None:
    result = ToolPermissionPolicy().decide("destructive", tool_name="delete_everything", payload={})

    assert result.decision == ToolDecision.BLOCKED
    assert result.blocked is True


def test_destructive_requires_approval_when_enabled() -> None:
    result = ToolPermissionPolicy(destructive_tools_enabled=True).decide(
        "destructive",
        tool_name="delete_everything",
        payload={},
    )

    assert result.decision == ToolDecision.APPROVAL_REQUIRED
    assert result.approval_required is True


def test_unknown_risk_is_blocked() -> None:
    result = ToolPermissionPolicy().decide("surprise", tool_name="unknown", payload={})

    assert result.decision == ToolDecision.BLOCKED
    assert result.blocked is True


def test_registry_rejects_duplicate_tool_names() -> None:
    registry = ToolRegistry()
    registry.register(RecordingTool())

    with pytest.raises(ToolRegistryError):
        registry.register(RecordingTool())


def test_registry_lists_tool_specs() -> None:
    registry = ToolRegistry()
    registry.register(RecordingTool())

    specs = registry.list_specs()

    assert len(specs) == 1
    assert specs[0].name == "recording"
    assert specs[0].risk == "safe_read"
    assert specs[0].input_schema == {"type": "object"}


def test_approval_probe_is_approval_required_and_harmless_if_called() -> None:
    probe = ApprovalProbeTool()
    decision = ToolPermissionPolicy().decide(probe.risk, tool_name=probe.name, payload={})

    assert probe.name == "approval_probe"
    assert probe.risk == "shell_read"
    assert decision.decision == ToolDecision.APPROVAL_REQUIRED
    assert decision.approval_required is True
    assert probe.run({}) == {
        "ok": True,
        "message": "approval_probe executed safely",
    }


def test_allowed_tool_executes() -> None:
    tool = RecordingTool()
    registry = ToolRegistry()
    registry.register(tool)

    result = registry.request_tool(
        ToolRequest(id="run-1", tool_name="recording", arguments={"x": 1}, requested_by="tests"),
        permission_policy=ToolPermissionPolicy(),
    )

    assert result.status == "finished"
    assert result.output == {"received": {"x": 1}}
    assert tool.calls == [{"x": 1}]


def test_approval_required_tool_does_not_execute(conn: sqlite3.Connection) -> None:
    tool = ApprovalTool()
    registry = ToolRegistry()
    registry.register(tool)

    result = registry.request_tool(
        ToolRequest(id="run-approval", tool_name="approval", arguments={"cmd": "status"}, requested_by="tests"),
        permission_policy=ToolPermissionPolicy(),
        approval_gate=ApprovalGate(conn),
    )

    assert result.status == "approval_required"
    assert result.approval_id is not None
    assert tool.calls == []
    assert table_count(conn, "approvals") == 1
    assert table_count(conn, "worker_jobs") == 0
    assert table_count(conn, "voice_queue") == 0


def test_approval_probe_request_creates_approval_without_tool_run_or_runtime_side_effects(
    conn: sqlite3.Connection,
) -> None:
    registry = ToolRegistry()
    registry.register(ApprovalProbeTool())

    result = registry.request_tool(
        ToolRequest(id="run-probe", tool_name="approval_probe", arguments={}, requested_by="tests"),
        permission_policy=ToolPermissionPolicy(),
        approval_gate=ApprovalGate(conn),
    )

    assert result.status == "approval_required"
    assert result.approval_id is not None
    assert result.output is None
    assert table_count(conn, "approvals") == 1
    assert table_count(conn, "tool_runs") == 0
    assert table_count(conn, "worker_jobs") == 0
    assert table_count(conn, "voice_queue") == 0


def test_blocked_tool_does_not_execute() -> None:
    tool = BlockedTool()
    registry = ToolRegistry()
    registry.register(tool)

    result = registry.request_tool(
        ToolRequest(id="run-blocked", tool_name="blocked", arguments={}, requested_by="tests"),
        permission_policy=ToolPermissionPolicy(),
    )

    assert result.status == "blocked"
    assert result.error is not None
    assert tool.calls == []


def test_tool_handler_exception_returns_failed_result() -> None:
    registry = ToolRegistry()
    registry.register(ExplodingTool())

    result = registry.request_tool(
        ToolRequest(id="run-fail", tool_name="exploding", arguments={}, requested_by="tests"),
        permission_policy=ToolPermissionPolicy(),
    )

    assert result.status == "failed"
    assert result.error == "boom"


def test_approval_gate_creates_pending_approval(conn: sqlite3.Connection) -> None:
    approval = ApprovalGate(conn).create_approval(
        risk="shell_read",
        requested_by="tests",
        action_type="tool:approval",
        payload={"command": "status"},
    )

    assert approval["status"] == "pending"
    assert approval["risk"] == "shell_read"
    assert approval["requested_by"] == "tests"


def test_approval_gate_approve_updates_status_and_event(
    conn: sqlite3.Connection,
    event_store: EventStore,
) -> None:
    gate = ApprovalGate(conn, event_store=event_store, now=lambda: "2026-07-01T12:00:00Z")
    approval = gate.create_approval(
        risk="shell_read",
        requested_by="tests",
        action_type="tool:approval",
        payload={"token": "secret-value"},
    )

    decided = gate.decide(str(approval["id"]), "approved", reason="ok")

    assert decided["status"] == "approved"
    assert decided["decided_at"] == "2026-07-01T12:00:00Z"
    assert decided["decision_reason"] == "ok"
    assert latest_event_types(event_store) == [
        EventType.APPROVAL_CREATED,
        EventType.APPROVAL_APPROVED,
    ]
    created_payload = event_store.list_after(0, limit=10)[0].payload
    assert created_payload["payload"]["token"] == "[REDACTED]"


def test_approval_gate_reject_updates_status_and_event(
    conn: sqlite3.Connection,
    event_store: EventStore,
) -> None:
    gate = ApprovalGate(conn, event_store=event_store)
    approval = gate.create_approval(
        risk="network",
        requested_by="tests",
        action_type="tool:network",
        payload={},
    )

    decided = gate.decide(str(approval["id"]), "rejected", reason="no")

    assert decided["status"] == "rejected"
    assert decided["decision_reason"] == "no"
    assert EventType.APPROVAL_REJECTED in latest_event_types(event_store)


def test_cannot_decide_non_pending_approval_twice(conn: sqlite3.Connection) -> None:
    gate = ApprovalGate(conn)
    approval = gate.create_approval(
        risk="network",
        requested_by="tests",
        action_type="tool:network",
        payload={},
    )
    gate.decide(str(approval["id"]), "approved")

    with pytest.raises(ToolRegistryError):
        gate.decide(str(approval["id"]), "rejected")


def test_list_pending_returns_only_pending(conn: sqlite3.Connection) -> None:
    gate = ApprovalGate(conn)
    pending = gate.create_approval(risk="network", requested_by="tests", action_type="network", payload={})
    approved = gate.create_approval(risk="shell_read", requested_by="tests", action_type="shell", payload={})
    gate.decide(str(approved["id"]), "approved")

    listed = gate.list_pending()

    assert [approval["id"] for approval in listed] == [pending["id"]]


def test_tool_run_recorder_records_requested_finished_failed(
    conn: sqlite3.Connection,
    event_store: EventStore,
) -> None:
    recorder = ToolRunRecorder(conn, event_store=event_store, now=lambda: "2026-07-01T12:00:00Z")

    requested = recorder.record_requested(
        run_id="run-ok",
        tool_name="echo",
        risk="safe_read",
        input={"text": "hello"},
        turn_id="turn-1",
    )
    finished = recorder.record_finished("run-ok", output={"ok": True})
    recorder.record_requested(run_id="run-failed", tool_name="echo", risk="safe_read", input={})
    failed = recorder.record_failed("run-failed", error="bad")

    assert requested["status"] == "requested"
    assert finished["status"] == "finished"
    assert failed["status"] == "failed"
    assert recorder.get("run-ok")["output"] == {"ok": True}
    assert [row["id"] for row in recorder.list_recent()] == ["run-failed", "run-ok"]
    assert latest_event_types(event_store) == [
        EventType.TOOL_REQUESTED,
        EventType.TOOL_FINISHED,
        EventType.TOOL_REQUESTED,
        EventType.TOOL_FAILED,
    ]


def test_tool_run_recorder_records_started_event_with_approval_payload(
    conn: sqlite3.Connection,
    event_store: EventStore,
) -> None:
    approval = ApprovalGate(conn).create_approval(
        risk="shell_read",
        requested_by="tests",
        action_type="tool:approval",
        payload={"tool_name": "approval", "arguments": {"command": "status"}, "requested_by": "tests"},
    )
    recorder = ToolRunRecorder(conn, event_store=event_store, now=lambda: "2026-07-01T12:00:00Z")
    recorder.record_requested(
        run_id="run-approved",
        tool_name="approval",
        risk="shell_read",
        input={"command": "status"},
        turn_id="turn-approval",
        approval_id=str(approval["id"]),
    )

    started = recorder.record_started("run-approved")

    assert started["status"] == "started"
    assert recorder.get_by_approval_id(str(approval["id"]))["id"] == "run-approved"
    event = event_store.list_after(0, limit=10)[-1]
    assert event.type == EventType.TOOL_STARTED
    assert event.payload == {
        "tool_name": "approval",
        "approval_id": str(approval["id"]),
        "turn_id": "turn-approval",
        "risk": "shell_read",
        "run_id": "run-approved",
        "tool_run_id": "run-approved",
        "status": "started",
    }


def test_placeholder_tools_do_not_mutate_shell_file_or_system_state(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"

    assert ShellReadPlaceholderTool().run({"command": "echo unsafe"})["ok"] is False
    assert ShellWritePlaceholderTool().run({"command": "touch target.txt"})["ok"] is False
    assert FileReadPlaceholderTool().run({"path": str(target)})["ok"] is False
    assert FileWritePlaceholderTool().run({"path": str(target), "content": "x"})["ok"] is False
    assert SystemStatusTool().run({}) == {
        "ok": True,
        "message": "Jarvis system status placeholder",
    }
    assert not target.exists()


def test_sqlite_schema_and_migrations_are_not_modified() -> None:
    assert_schema_and_migrations_unchanged(ROOT)
