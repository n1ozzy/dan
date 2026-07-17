"""memory_save tool: one direct, durable, evidence-backed memory path."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from dan.daemon.app import DaemonApp, create_daemon_app
from dan.memory import (
    MemoryCandidateRepository,
    MemoryEvidenceRepository,
    MemoryItemRepository,
    MemoryManager,
)
from dan.store.db import close_quietly, initialize_database
from dan.tools.memory_tool import MAX_BODY_CHARS, MAX_TITLE_CHARS, MemorySaveTool
from dan.tools.permissions import RequestSource
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
    config_path = write_config(tmp_path / "dan.toml", tmp_path / "home" / "dan.db")
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
    assert "priority" not in tool.input_schema["properties"]
    assert "current screen" in tool.description
    assert "active app" in tool.description
    assert "running process" in tool.description


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


def test_run_without_candidate_id_creates_evidence_and_activates_directly(
    tool: MemorySaveTool,
    conn: sqlite3.Connection,
) -> None:
    result = tool.run({"kind": "fact", "title": "T", "body": "B"})

    assert result["ok"] is True
    assert result["candidate_id"]
    assert result["memory_id"]
    assert table_count(conn, "memory_candidates") == 1
    assert table_count(conn, "memory_evidence") == 1
    assert table_count(conn, "memory_items") == 1
    assert table_count(conn, "memory_blocks") == 0


# --- integration: daemon registration + direct execution ---


def test_daemon_app_registers_memory_save(app: DaemonApp) -> None:
    specs = {spec.name: spec for spec in app.tool_registry.list_specs()}
    assert "memory_save" in specs
    assert specs["memory_save"].risk == "memory_write"


def test_memory_save_request_executes_directly_without_approval_row(app: DaemonApp) -> None:
    requested = app.request_tool(
        tool_name="memory_save",
        arguments={"kind": "project", "title": "DAN v4", "body": "Repo w ~/Documents/dev."},
        requested_by="model",
        source=RequestSource.MODEL_ORIGINATED,
    )
    assert requested.status == "finished"
    assert requested.approval_id is None
    assert requested.output is not None
    assert requested.output["candidate_id"]
    assert requested.output["memory_id"]
    assert app.conn is not None
    assert table_count(app.conn, "approvals") == 0
    assert table_count(app.conn, "memory_candidates") == 1
    assert table_count(app.conn, "memory_evidence") == 1
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
    assert "kind" in result.error
    assert app.conn is not None
    assert table_count(app.conn, "approvals") == 0
    assert table_count(app.conn, "memory_candidates") == 0
    assert table_count(app.conn, "memory_evidence") == 0
    assert table_count(app.conn, "memory_items") == 0
    assert table_count(app.conn, "memory_blocks") == 0
