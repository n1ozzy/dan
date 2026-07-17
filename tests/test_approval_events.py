"""Prompt 19A approval decision event semantics tests."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from dan.daemon.app import DaemonApp, DaemonAppConflictError, create_daemon_app
from dan.events.types import EventType
from dan.security.redaction import REDACTION_PLACEHOLDER
from dan.store.db import close_quietly, initialize_database
from dan.store.event_store import EventStore, create_event_store
from dan.tools.permissions import RequestSource, ToolPermissionPolicy
from dan.tools.registry import (
    ApprovalGate,
    Tool,
    ToolRegistry,
    ToolRegistryError,
    ToolRequest,
)
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
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    db_conn = initialize_database(tmp_path / "dan.db")
    try:
        yield db_conn
    finally:
        close_quietly(db_conn)


@pytest.fixture
def event_store(conn: sqlite3.Connection) -> EventStore:
    return create_event_store(conn)


@pytest.fixture
def app(tmp_path: Path) -> Iterator[DaemonApp]:
    config_path = write_config(tmp_path / "dan.toml", tmp_path / "home" / "dan.db")
    daemon_app = create_daemon_app(config_path)
    try:
        yield daemon_app
    finally:
        daemon_app.close()


class RecordingApprovalTool(Tool):
    name = "approval_event_tool"
    description = "records whether an approval-gated tool executed"
    risk = "shell_read"
    input_schema = {"type": "object"}

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        payload = dict(arguments)
        self.calls.append(payload)
        return {"received": payload}


def decision_events(store: EventStore, event_type: EventType) -> list[Any]:
    return [event for event in store.list_after(0, limit=100) if event.type == event_type]


def create_tool_approval(
    gate: ApprovalGate,
    *,
    tool_name: str = "approval_event_tool",
    risk: str = "shell_read",
    turn_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return gate.create_approval(
        risk=risk,
        requested_by="tests",
        action_type=f"tool:{tool_name}",
        payload={
            "tool_name": tool_name,
            "arguments": {"command": "status"},
            "requested_by": "tests",
            "source": str(RequestSource.DIRECT_USER_COMMAND),
            "turn_id": turn_id,
        },
        metadata=metadata or {},
        turn_id=turn_id,
        correlation_id=turn_id,
    )


def test_approving_pending_approval_emits_exactly_one_approved_decision_event(
    conn: sqlite3.Connection,
    event_store: EventStore,
) -> None:
    gate = ApprovalGate(conn, event_store=event_store, now=lambda: "2026-07-01T12:00:00Z")
    approval = create_tool_approval(gate)

    decided = gate.decide(str(approval["id"]), "approved", reason="ok")

    approved_events = decision_events(event_store, EventType.APPROVAL_APPROVED)
    assert decided["status"] == "approved"
    assert len(approved_events) == 1
    assert approved_events[0].payload["approval_id"] == approval["id"]


def test_rejecting_pending_approval_emits_exactly_one_rejected_decision_event(
    conn: sqlite3.Connection,
    event_store: EventStore,
) -> None:
    gate = ApprovalGate(conn, event_store=event_store, now=lambda: "2026-07-01T12:00:00Z")
    approval = create_tool_approval(gate)

    decided = gate.decide(str(approval["id"]), "rejected", reason="no")

    rejected_events = decision_events(event_store, EventType.APPROVAL_REJECTED)
    assert decided["status"] == "rejected"
    assert len(rejected_events) == 1
    assert rejected_events[0].payload["approval_id"] == approval["id"]


def test_decision_event_payload_includes_required_approval_decision_fields(
    conn: sqlite3.Connection,
    event_store: EventStore,
) -> None:
    gate = ApprovalGate(conn, event_store=event_store, now=lambda: "2026-07-01T12:00:00Z")
    approval = create_tool_approval(gate)

    gate.decide(str(approval["id"]), "approved", reason="ok")

    event = decision_events(event_store, EventType.APPROVAL_APPROVED)[0]
    assert event.payload == {
        "approval_id": approval["id"],
        "tool_name": "approval_event_tool",
        "requested_risk": "shell_read",
        "status": "approved",
        "decision": "approved",
        "decided_at": "2026-07-01T12:00:00Z",
    }


def test_decision_event_preserves_turn_id_and_correlation_id_from_approval_request(
    conn: sqlite3.Connection,
    event_store: EventStore,
) -> None:
    registry = ToolRegistry()
    registry.register(RecordingApprovalTool())
    gate = ApprovalGate(conn, event_store=event_store, now=lambda: "2026-07-01T12:00:00Z")
    result = registry.request_tool(
        ToolRequest(
            id="run-correlated",
            tool_name="approval_event_tool",
            arguments={"command": "status"},
            requested_by="tests",
            turn_id="turn-approval-events",
        ),
        permission_policy=ToolPermissionPolicy(),
        source=RequestSource.DIRECT_USER_COMMAND,
        approval_gate=gate,
    )

    gate.decide(str(result.approval_id), "approved")

    event = decision_events(event_store, EventType.APPROVAL_APPROVED)[0]
    assert event.turn_id == "turn-approval-events"
    assert event.correlation_id == "turn-approval-events"
    assert event.payload["turn_id"] == "turn-approval-events"
    assert event.payload["correlation_id"] == "turn-approval-events"


def test_duplicate_approve_does_not_emit_second_approved_event(
    conn: sqlite3.Connection,
    event_store: EventStore,
) -> None:
    gate = ApprovalGate(conn, event_store=event_store)
    approval = create_tool_approval(gate)
    gate.decide(str(approval["id"]), "approved")

    with pytest.raises(ToolRegistryError):
        gate.decide(str(approval["id"]), "approved")

    assert len(decision_events(event_store, EventType.APPROVAL_APPROVED)) == 1


def test_duplicate_reject_does_not_emit_second_rejected_event(
    conn: sqlite3.Connection,
    event_store: EventStore,
) -> None:
    gate = ApprovalGate(conn, event_store=event_store)
    approval = create_tool_approval(gate)
    gate.decide(str(approval["id"]), "rejected")

    with pytest.raises(ToolRegistryError):
        gate.decide(str(approval["id"]), "rejected")

    assert len(decision_events(event_store, EventType.APPROVAL_REJECTED)) == 1


def test_approve_after_reject_fails_without_approved_event(
    conn: sqlite3.Connection,
    event_store: EventStore,
) -> None:
    gate = ApprovalGate(conn, event_store=event_store)
    approval = create_tool_approval(gate)
    gate.decide(str(approval["id"]), "rejected")

    with pytest.raises(ToolRegistryError):
        gate.decide(str(approval["id"]), "approved")

    assert decision_events(event_store, EventType.APPROVAL_APPROVED) == []
    assert len(decision_events(event_store, EventType.APPROVAL_REJECTED)) == 1


def test_reject_after_approve_fails_without_rejected_event(
    conn: sqlite3.Connection,
    event_store: EventStore,
) -> None:
    gate = ApprovalGate(conn, event_store=event_store)
    approval = create_tool_approval(gate)
    gate.decide(str(approval["id"]), "approved")

    with pytest.raises(ToolRegistryError):
        gate.decide(str(approval["id"]), "rejected")

    assert decision_events(event_store, EventType.APPROVAL_REJECTED) == []
    assert len(decision_events(event_store, EventType.APPROVAL_APPROVED)) == 1


def test_approve_does_not_execute_tool(app: DaemonApp) -> None:
    tool = RecordingApprovalTool()
    app.tool_registry.register(tool)
    app.start()

    requested = app.request_tool(
        tool_name=tool.name,
        arguments={"command": "status"},
        requested_by="api",
        source=RequestSource.DIRECT_USER_COMMAND,
    )
    approved = app.approve(str(requested.approval_id), reason="ok")

    assert approved["status"] == "approved"
    assert tool.calls == []
    assert table_count(app, "tool_runs") == 0


def test_rejection_does_not_execute_tool(app: DaemonApp) -> None:
    tool = RecordingApprovalTool()
    app.tool_registry.register(tool)
    app.start()

    requested = app.request_tool(
        tool_name=tool.name,
        arguments={"command": "status"},
        requested_by="api",
        source=RequestSource.DIRECT_USER_COMMAND,
    )
    rejected = app.reject(str(requested.approval_id), reason="no")

    assert rejected["status"] == "rejected"
    assert tool.calls == []
    assert table_count(app, "tool_runs") == 0


def test_execute_approved_behavior_remains_explicit_and_duplicate_execute_conflicts(
    app: DaemonApp,
) -> None:
    tool = RecordingApprovalTool()
    app.tool_registry.register(tool)
    app.start()

    requested = app.request_tool(
        tool_name=tool.name,
        arguments={"command": "status"},
        requested_by="api",
        source=RequestSource.DIRECT_USER_COMMAND,
        turn_id="turn-execute-approved",
    )
    app.approve(str(requested.approval_id), reason="ok")

    executed = app.execute_approved_tool(str(requested.approval_id))
    with pytest.raises(DaemonAppConflictError):
        app.execute_approved_tool(str(requested.approval_id))

    assert executed["ok"] is True
    assert executed["result"] == {"received": {"command": "status"}}
    assert tool.calls == [{"command": "status"}]
    assert table_count(app, "tool_runs") == 1
    assert event_types(app).count(EventType.TOOL_STARTED) == 1
    assert event_types(app).count(EventType.TOOL_FINISHED) == 1


def test_decision_event_payload_is_redacted_by_event_store(
    conn: sqlite3.Connection,
    event_store: EventStore,
) -> None:
    raw_secret = "sk-ant-approvalevents123"
    gate = ApprovalGate(conn, event_store=event_store, now=lambda: "2026-07-01T12:00:00Z")
    approval = create_tool_approval(gate)

    gate.decide(str(approval["id"]), "rejected", reason=f"no because {raw_secret}")

    event = decision_events(event_store, EventType.APPROVAL_REJECTED)[0]
    rendered = json.dumps(event.payload, sort_keys=True)
    assert raw_secret not in rendered
    assert event.payload["reason"] == f"no because {REDACTION_PLACEHOLDER}"


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


def table_count(app: DaemonApp, table: str) -> int:
    assert app.conn is not None
    return int(app.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def event_types(app: DaemonApp) -> list[str]:
    assert app.event_store is not None
    return [event.type for event in app.event_store.list_after(0, limit=100)]
