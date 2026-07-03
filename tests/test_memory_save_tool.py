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
from jarvis.memory import MemoryManager
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


@pytest.fixture
def manager(conn: sqlite3.Connection) -> MemoryManager:
    return MemoryManager(conn)


@pytest.fixture
def tool(manager: MemoryManager) -> MemorySaveTool:
    return MemorySaveTool(manager)


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


# --- unit: run() ---


def test_run_creates_promoted_block(tool: MemorySaveTool, manager: MemoryManager) -> None:
    output = tool.run(
        {
            "kind": "user_preference",
            "title": "Ozzy woli słuchawki",
            "body": "Głośniki dają echo bez AEC; do rozmów głosowych używa słuchawek.",
        }
    )

    assert output["ok"] is True
    block = manager.get_block(output["block_id"])
    assert block is not None
    assert block.active is True
    assert block.kind == "user_preference"
    assert block.metadata["candidate"] is False
    assert block.metadata["proposed_by"] == "model"
    assert block.metadata["promoted_by"] == "approval"


def test_run_promoted_block_enters_brain_context(
    tool: MemorySaveTool, manager: MemoryManager
) -> None:
    tool.run({"kind": "fact", "title": "Fakt", "body": "Treść faktu."})

    contexts = manager.active_blocks_for_context()
    assert [block.title for block in contexts] == ["Fakt"]


def test_run_rejects_invalid_kind(tool: MemorySaveTool, manager: MemoryManager) -> None:
    with pytest.raises(ValueError, match="kind"):
        tool.run({"kind": "nope", "title": "T", "body": "B"})
    assert manager.list_blocks() == []


def test_run_caps_title_and_body(tool: MemorySaveTool, manager: MemoryManager) -> None:
    with pytest.raises(ValueError, match="body"):
        tool.run({"kind": "fact", "title": "T", "body": "x" * (MAX_BODY_CHARS + 1)})
    with pytest.raises(ValueError, match="title"):
        tool.run({"kind": "fact", "title": "t" * (MAX_TITLE_CHARS + 1), "body": "B"})
    assert manager.list_blocks() == []


def test_run_clamps_priority(tool: MemorySaveTool, manager: MemoryManager) -> None:
    output = tool.run({"kind": "fact", "title": "T", "body": "B", "priority": 3})

    block = manager.get_block(output["block_id"])
    assert block is not None
    assert block.priority == 3


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
    assert app.memory_manager.list_blocks() == []  # nic przed zgodą

    app.approve(str(requested.approval_id), reason="ok")
    assert app.memory_manager.list_blocks() == []  # approve sam nie wykonuje

    response = app.execute_approved_tool(str(requested.approval_id))

    assert response["ok"] is True
    active = app.memory_manager.active_blocks_for_context()
    assert [block.title for block in active] == ["Jarvis v4"]
