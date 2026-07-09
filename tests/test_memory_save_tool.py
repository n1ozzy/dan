"""memory_save tool: the model curates its own memory through the approval gate.

ADR-009 keeps promotion human-sanctioned; ADR-010 routes every mutation through
ApprovalGate. memory_save composes both: the model proposes a block in-turn,
the human approves it in the existing approvals panel, and only the approved
execution promotes it into brain context.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from jarvis.daemon.app import DaemonApp, create_daemon_app
from jarvis.memory import (
    MemoryCandidateRepository,
    MemoryEvidenceRepository,
    MemoryItemRepository,
    MemoryManager,
)
from jarvis.store.db import close_quietly, initialize_database
from jarvis.tools.memory_tool import MAX_BODY_CHARS, MAX_TITLE_CHARS, MemorySaveTool
from jarvis.tools.permissions import (
    RequestSource,
    ToolDecision,
    ToolPermissionPolicy,
)
from tests.test_api_smoke import write_config


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = initialize_database(tmp_path / "memory.db")
    try:
        yield connection
    finally:
        close_quietly(connection)


def table_count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


@pytest.fixture
def manager(conn: sqlite3.Connection) -> MemoryManager:
    return MemoryManager(conn)


@pytest.fixture
def tool(conn: sqlite3.Connection) -> MemorySaveTool:
    return MemorySaveTool(
        candidate_repository=MemoryCandidateRepository(conn),
        evidence_repository=MemoryEvidenceRepository(conn),
        item_repository=MemoryItemRepository(conn),
    )


@pytest.fixture
def app(tmp_path: Path) -> DaemonApp:
    config_path = write_config(tmp_path / "jarvis.toml", tmp_path / "home" / "jarvis.db")
    daemon_app = create_daemon_app(config_path)
    daemon_app.start()
    try:
        yield daemon_app
    finally:
        daemon_app.stop(reason="test teardown")
        daemon_app.close()


# --- spec ---


def test_spec_declares_memory_write_risk(tool: MemorySaveTool) -> None:
    assert tool.name == "memory_save"
    assert tool.risk == "memory_write"
    assert set(tool.input_schema["required"]) == {"kind", "title", "body"}


# --- unit: propose() / run() ---


def test_propose_creates_candidate_and_evidence_without_active_memory(
    tool: MemorySaveTool,
    conn: sqlite3.Connection,
) -> None:
    output = tool.propose(
        {
            "kind": "user_preference",
            "title": "Ozzy prefers headphones",
            "body": "Speakers echo without AEC; voice chats should use headphones.",
        },
        source_id="call-memory-save",
        turn_id="turn-memory-save",
    )

    assert output["ok"] is True
    assert table_count(conn, "memory_candidates") == 1
    assert table_count(conn, "memory_evidence") == 1
    assert table_count(conn, "memory_items") == 0
    assert table_count(conn, "memory_blocks") == 0

    candidate = conn.execute(
        """
        SELECT candidate_kind, scope, namespace, title, claim, status
        FROM memory_candidates
        WHERE id = ?
        """,
        (output["candidate_id"],),
    ).fetchone()
    assert candidate == (
        "user_preference",
        "user",
        "user/default",
        "Ozzy prefers headphones",
        "Speakers echo without AEC; voice chats should use headphones.",
        "needs_review",
    )
    evidence = conn.execute(
        """
        SELECT o.source_type, o.source_id, e.turn_id, e.quote
        FROM memory_evidence AS e
        JOIN memory_observations AS o ON o.id = e.observation_id
        WHERE e.candidate_id = ?
        """,
        (output["candidate_id"],),
    ).fetchone()
    assert evidence == (
        "explicit_memory_save",
        "call-memory-save",
        "turn-memory-save",
        "Speakers echo without AEC; voice chats should use headphones.",
    )


def test_run_activates_candidate_into_memory_items_without_memory_blocks(
    tool: MemorySaveTool,
    conn: sqlite3.Connection,
) -> None:
    proposed = tool.propose(
        {"kind": "fact", "title": "Fact", "body": "Approved execution activates this."},
        source_id="call-memory-save",
    )

    output = tool.run(
        {
            "candidate_id": proposed["candidate_id"],
            "kind": "fact",
            "title": "Fact",
            "body": "Approved execution activates this.",
        }
    )

    assert output["ok"] is True
    assert output["candidate_id"] == proposed["candidate_id"]
    assert isinstance(output["memory_id"], str)
    assert table_count(conn, "memory_items") == 1
    assert table_count(conn, "memory_blocks") == 0
    assert (
        conn.execute(
            "SELECT memory_id FROM memory_evidence WHERE candidate_id = ?",
            (proposed["candidate_id"],),
        ).fetchone()[0]
        == output["memory_id"]
    )


def test_run_activates_candidate_whose_body_looks_like_a_secret(
    tool: MemorySaveTool,
    conn: sqlite3.Connection,
) -> None:
    # Regression: create_candidate stores claim/title redacted. run() must compare
    # the payload through the same redaction, else a secret-shaped body makes the
    # stored (redacted) claim differ from the raw payload and strands the
    # candidate forever. A real save with a key-shaped string must still persist.
    secret_body = "The deploy key is AKIAIOSFODNN7EXAMPLE do not lose it."
    proposed = tool.propose(
        {"kind": "fact", "title": "Deploy note", "body": secret_body},
        source_id="call-secret-save",
    )

    output = tool.run(
        {
            "candidate_id": proposed["candidate_id"],
            "kind": "fact",
            "title": "Deploy note",
            "body": secret_body,
        }
    )

    assert output["ok"] is True
    assert table_count(conn, "memory_items") == 1


def test_run_rejects_invalid_kind(tool: MemorySaveTool, manager: MemoryManager) -> None:
    with pytest.raises(ValueError, match="kind"):
        tool.propose({"kind": "nope", "title": "T", "body": "B"})
    assert manager.list_blocks() == []


def test_run_caps_title_and_body(tool: MemorySaveTool, manager: MemoryManager) -> None:
    with pytest.raises(ValueError, match="body"):
        tool.propose({"kind": "fact", "title": "T", "body": "x" * (MAX_BODY_CHARS + 1)})
    with pytest.raises(ValueError, match="title"):
        tool.propose({"kind": "fact", "title": "t" * (MAX_TITLE_CHARS + 1), "body": "B"})
    assert manager.list_blocks() == []


def test_run_requires_candidate_id_from_approval_proposal(
    tool: MemorySaveTool,
    conn: sqlite3.Connection,
) -> None:
    with pytest.raises(ValueError, match="candidate_id"):
        tool.run({"kind": "fact", "title": "T", "body": "B", "priority": 3})

    assert table_count(conn, "memory_items") == 0
    assert table_count(conn, "memory_blocks") == 0


# --- permission matrix: memory_write | user AP | model AP | auto B ---


@pytest.mark.parametrize(
    ("source", "decision"),
    [
        (RequestSource.PANEL_COMMAND, ToolDecision.APPROVAL_REQUIRED),
        (RequestSource.VOICE_COMMAND, ToolDecision.APPROVAL_REQUIRED),
        (RequestSource.MODEL_ORIGINATED, ToolDecision.APPROVAL_REQUIRED),
        (RequestSource.SCHEDULED_WORKER, ToolDecision.BLOCKED),
        (RequestSource.HOOK_TRIGGERED, ToolDecision.BLOCKED),
    ],
)
def test_memory_write_permission_matrix(source: RequestSource, decision: ToolDecision) -> None:
    policy = ToolPermissionPolicy()

    result = policy.decide("memory_write", source=source, tool_name="memory_save")

    assert result.decision == decision


# --- integration: daemon app registration + full approval lifecycle ---


def test_daemon_app_registers_memory_save(app: DaemonApp) -> None:
    specs = {spec.name: spec for spec in app.tool_registry.list_specs()}
    assert "memory_save" in specs
    assert specs["memory_save"].risk == "memory_write"


def test_memory_save_full_approval_lifecycle(app: DaemonApp) -> None:
    requested = app.request_tool(
        tool_name="memory_save",
        arguments={"kind": "project", "title": "Jarvis v4", "body": "Repo w ~/Documents/dev."},
        requested_by="model",
        source=RequestSource.MODEL_ORIGINATED,
    )
    assert requested.status == "approval_required"
    assert requested.approval_id is not None
    assert app.conn is not None
    assert table_count(app.conn, "memory_candidates") == 1
    assert table_count(app.conn, "memory_evidence") == 1
    assert table_count(app.conn, "memory_items") == 0
    assert table_count(app.conn, "memory_blocks") == 0
    assert app.memory_manager.list_blocks() == []  # nic przed zgodą

    app.approve(str(requested.approval_id), reason="ok")
    assert table_count(app.conn, "memory_items") == 0  # approve sam nie wykonuje
    assert table_count(app.conn, "memory_blocks") == 0

    response = app.execute_approved_tool(str(requested.approval_id))

    assert response["ok"] is True
    assert response["result"]["candidate_id"]
    assert response["result"]["memory_id"]
    assert table_count(app.conn, "memory_items") == 1
    assert table_count(app.conn, "memory_blocks") == 0
    assert app.memory_manager.active_blocks_for_context() == []


def test_model_originated_request_tool_rejects_model_supplied_candidate_id(
    app: DaemonApp,
) -> None:
    result = app.request_tool(
        tool_name="memory_save",
        arguments={"candidate_id": "bogus"},
        requested_by="model",
        source=RequestSource.MODEL_ORIGINATED,
    )

    assert result.status == "failed"
    assert result.approval_id is None
    assert result.error is not None
    assert "candidate_id" in result.error
    assert "model proposal" in result.error
    assert app.conn is not None
    assert table_count(app.conn, "approvals") == 0
    assert table_count(app.conn, "memory_candidates") == 0
    assert table_count(app.conn, "memory_evidence") == 0
    assert table_count(app.conn, "memory_items") == 0
    assert table_count(app.conn, "memory_blocks") == 0
