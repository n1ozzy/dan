"""Prompt 09 DAN-owned memory manager tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from dan.brain import BrainMemoryBlock
from dan.memory import MEMORY_KINDS, MemoryBlock, MemoryError, MemoryManager
from dan.memory.policies import estimate_memory_chars, select_memory_for_budget
from dan.store.db import close_quietly, initialize_database
from dan.store.event_store import EventStore


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = initialize_database(tmp_path / "memory.db")
    try:
        yield connection
    finally:
        close_quietly(connection)


def fixed_now(value: str = "2026-07-01T12:00:00+00:00"):
    return lambda: value


def test_create_block_stores_memory_block(conn: sqlite3.Connection) -> None:
    manager = MemoryManager(conn, now=fixed_now())

    block = manager.create_block(
        "identity",
        "Name",
        "DAN is the durable assistant identity.",
        priority=7,
        metadata={"source": "test"},
    )

    assert block.id
    assert block.kind == "identity"
    assert block.priority == 7
    assert block.active is True
    assert block.created_at == "2026-07-01T12:00:00+00:00"
    row = conn.execute("SELECT title, body, metadata_json FROM memory_blocks").fetchone()
    assert row[0] == "Name"
    assert row[1] == "DAN is the durable assistant identity."
    assert '"source": "test"' in row[2]


def test_get_block_returns_stored_block(conn: sqlite3.Connection) -> None:
    manager = MemoryManager(conn, now=fixed_now())
    created = manager.create_block("fact", "Fact", "SQLite owns memory facts.")

    fetched = manager.get_block(created.id)

    assert fetched == created


def test_list_blocks_active_only_excludes_disabled_blocks(conn: sqlite3.Connection) -> None:
    manager = MemoryManager(conn, now=fixed_now())
    active = manager.create_block("fact", "Active", "Keep me")
    manager.create_block("fact", "Disabled", "Do not keep me", active=False)

    blocks = manager.list_blocks(active_only=True)

    assert [block.id for block in blocks] == [active.id]


def test_update_block_updates_fields_and_timestamp(conn: sqlite3.Connection) -> None:
    times = iter(["2026-07-01T12:00:00+00:00", "2026-07-01T12:01:00+00:00"])
    manager = MemoryManager(conn, now=lambda: next(times))
    block = manager.create_block("project", "Old", "Old body", priority=1)

    updated = manager.update_block(
        block.id,
        title="New",
        body="New body",
        priority=9,
        metadata={"updated": True},
    )

    assert updated.title == "New"
    assert updated.body == "New body"
    assert updated.priority == 9
    assert updated.metadata == {"updated": True}
    assert updated.created_at == block.created_at
    assert updated.updated_at == "2026-07-01T12:01:00+00:00"


def test_disable_block_sets_active_false(conn: sqlite3.Connection) -> None:
    manager = MemoryManager(conn, now=fixed_now())
    block = manager.create_block("temporary", "Temp", "Short-lived detail")

    disabled = manager.disable_block(block.id)

    assert disabled.active is False
    assert manager.get_block(block.id).active is False  # type: ignore[union-attr]


def test_invalid_kind_raises_memory_error(conn: sqlite3.Connection) -> None:
    manager = MemoryManager(conn)

    with pytest.raises(MemoryError, match="Invalid memory kind"):
        manager.create_block("provider_session", "Bad", "No hidden provider memory")


@pytest.mark.parametrize(("title", "body"), [("", "body"), ("title", ""), ("  ", "body")])
def test_empty_title_or_body_raises_memory_error(
    conn: sqlite3.Connection,
    title: str,
    body: str,
) -> None:
    manager = MemoryManager(conn)

    with pytest.raises(MemoryError, match="non-empty"):
        manager.create_block("fact", title, body)


def test_non_json_metadata_raises_memory_error(conn: sqlite3.Connection) -> None:
    manager = MemoryManager(conn)

    with pytest.raises(MemoryError, match="JSON serializable"):
        manager.create_block("fact", "Bad metadata", "No sets", metadata={"bad": {1, 2}})


def test_active_blocks_for_context_sorts_priority_desc(conn: sqlite3.Connection) -> None:
    manager = MemoryManager(conn, now=fixed_now())
    low = manager.create_block("fact", "Low", "Low priority", priority=1)
    high = manager.create_block("fact", "High", "High priority", priority=10)
    mid = manager.create_block("fact", "Mid", "Middle priority", priority=5)

    blocks = manager.active_blocks_for_context()

    assert [block.id for block in blocks] == [high.id, mid.id, low.id]


def test_active_blocks_for_context_respects_max_blocks(conn: sqlite3.Connection) -> None:
    manager = MemoryManager(conn, now=fixed_now())
    manager.create_block("fact", "One", "First", priority=1)
    top = manager.create_block("fact", "Top", "Best", priority=99)
    manager.create_block("fact", "Two", "Second", priority=2)

    blocks = manager.active_blocks_for_context(max_blocks=1)

    assert [block.id for block in blocks] == [top.id]


def test_active_blocks_for_context_respects_max_chars(conn: sqlite3.Connection) -> None:
    manager = MemoryManager(conn, now=fixed_now())
    first = manager.create_block("fact", "Short", "Tiny", priority=10)
    manager.create_block("fact", "Long", "x" * 200, priority=1)

    blocks = manager.active_blocks_for_context(max_chars=estimate_memory_chars(first))

    assert [block.id for block in blocks] == [first.id]


# --- FIX-10 #2: the limit is bound in SQL, not sliced in Python ---------------


class _RecordingConn:
    """Delegating sqlite3 proxy that records executed SQL statements."""

    def __init__(self, inner: sqlite3.Connection) -> None:
        self._inner = inner
        self.sql: list[str] = []

    def execute(self, sql: str, *args: object, **kwargs: object) -> sqlite3.Cursor:
        self.sql.append(sql)
        return self._inner.execute(sql, *args, **kwargs)

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


def test_list_blocks_binds_limit_in_sql(conn: sqlite3.Connection) -> None:
    seed = MemoryManager(conn, now=fixed_now())
    for index in range(5):
        seed.create_block("fact", f"Title {index}", "body")
    recording = _RecordingConn(conn)
    manager = MemoryManager(recording, now=fixed_now())

    result = manager.list_blocks(limit=2)

    assert len(result) == 2
    selects = [statement for statement in recording.sql if "FROM memory_blocks" in statement]
    assert selects, "no memory_blocks SELECT was captured"
    assert all("LIMIT" in statement for statement in selects), (
        "list_blocks must bind LIMIT in SQL, not load the whole table and slice"
    )


def test_list_blocks_without_limit_does_not_emit_sql_limit(
    conn: sqlite3.Connection,
) -> None:
    recording = _RecordingConn(conn)
    manager = MemoryManager(recording, now=fixed_now())

    manager.list_blocks()

    selects = [statement for statement in recording.sql if "FROM memory_blocks" in statement]
    assert selects
    assert all("LIMIT" not in statement for statement in selects)


# --- FIX-10 #3: state mutation and its audit event share one transaction ------


def test_block_insert_rolls_back_when_audit_event_fails(
    conn: sqlite3.Connection,
) -> None:
    # FIX-03 DoD, guarded here: the row insert and its audit event are one
    # transaction, so a failed event append must roll the insert back — no
    # orphan block persists without its event.
    class _FailingEventStore:
        def append(self, *args: object, **kwargs: object) -> None:
            raise sqlite3.OperationalError("audit event append boom")

    manager = MemoryManager(conn, event_store=_FailingEventStore(), now=fixed_now())

    with pytest.raises(MemoryError):
        manager.create_block("fact", "Atomic", "body")

    reader = MemoryManager(conn, now=fixed_now())
    assert reader.list_blocks() == []


def test_to_brain_memory_blocks_converts_to_brain_memory_block(conn: sqlite3.Connection) -> None:
    manager = MemoryManager(conn, now=fixed_now())
    block = manager.create_block("summary", "Summary", "Useful summary", priority=4)

    converted = manager.to_brain_memory_blocks([block])

    assert converted == [
        BrainMemoryBlock(
            id=block.id,
            kind="summary",
            title="Summary",
            body="Useful summary",
            priority=4,
            metadata={},
        )
    ]


def test_event_store_receives_memory_updated_events(conn: sqlite3.Connection) -> None:
    event_store = EventStore(conn)
    manager = MemoryManager(conn, event_store=event_store, now=fixed_now())

    block = manager.create_block("fact", "Create", "Created")
    manager.update_block(block.id, body="Updated")
    manager.disable_block(block.id)

    events = event_store.latest(limit=3)
    assert [event.type for event in events] == ["memory.updated"] * 3
    assert [event.payload["action"] for event in reversed(events)] == [
        "created",
        "updated",
        "disabled",
    ]


def test_disabled_blocks_are_never_returned_for_context(conn: sqlite3.Connection) -> None:
    manager = MemoryManager(conn, now=fixed_now())
    manager.create_block("fact", "Disabled", "Do not include", active=False, priority=100)
    active = manager.create_block("fact", "Active", "Include", priority=1)

    blocks = manager.active_blocks_for_context()

    assert [block.id for block in blocks] == [active.id]


def test_memory_policies_expose_required_kinds() -> None:
    assert MEMORY_KINDS == {
        "identity",
        "user_preference",
        "project",
        "fact",
        "summary",
        "temporary",
    }


def test_select_memory_for_budget_is_deterministic() -> None:
    blocks = [
        MemoryBlock(
            id="b",
            kind="fact",
            title="B",
            body="Second",
            priority=5,
            active=True,
            created_at="2026-07-01T12:00:00+00:00",
            updated_at="2026-07-01T12:00:00+00:00",
        ),
        MemoryBlock(
            id="a",
            kind="fact",
            title="A",
            body="First",
            priority=5,
            active=True,
            created_at="2026-07-01T12:00:00+00:00",
            updated_at="2026-07-01T12:00:00+00:00",
        ),
    ]

    selected = select_memory_for_budget(blocks, max_blocks=None, max_chars=None)

    assert [block.id for block in selected] == ["a", "b"]


def test_memory_manager_has_no_provider_network_or_subprocess_dependencies() -> None:
    forbidden_fragments = (
        "import subprocess",
        "from subprocess",
        "import socket",
        "import urllib",
        "from urllib",
        "claude_cli_adapter",
        "codex_cli_adapter",
        "openai_adapter",
        "groq",
        "ollama",
    )
    for relative in (
        "dan/memory/manager.py",
        "dan/memory/policies.py",
        "dan/memory/retrieval.py",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        offenders = [fragment for fragment in forbidden_fragments if fragment in source]
        assert offenders == [], f"{relative} has forbidden dependency fragments: {offenders}"
