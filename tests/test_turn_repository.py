"""Prompt 10 conversation and turn repository tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from dan.store.db import close_quietly, initialize_database
from dan.store.repositories import MAX_REPOSITORY_LIMIT
from dan.turns import (
    Conversation,
    ConversationRepository,
    ConversationRepositoryError,
    ConversationStatus,
    Turn,
    TurnRepository,
    TurnRepositoryError,
    TurnSource,
    TurnStatus,
)
from tests.git_guards import assert_schema_and_migrations_unchanged


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = initialize_database(tmp_path / "turns.db")
    try:
        yield connection
    finally:
        close_quietly(connection)


def fixed_now(*values: str):
    iterator = iter(values or ("2026-07-01T12:00:00+00:00",))
    last = values[-1] if values else "2026-07-01T12:00:00+00:00"

    def _now() -> str:
        nonlocal last
        try:
            last = next(iterator)
        except StopIteration:
            pass
        return last

    return _now


def create_conversation(
    conn: sqlite3.Connection,
    conversation_id: str = "conversation-1",
) -> Conversation:
    return ConversationRepository(conn, now=fixed_now()).create(
        conversation_id=conversation_id,
        title="Conversation",
    )


def create_turn(
    conn: sqlite3.Connection,
    *,
    conversation_id: str = "conversation-1",
    turn_id: str = "turn-1",
    input_text: str = "Hello",
) -> Turn:
    return TurnRepository(conn, now=fixed_now()).create(
        conversation_id,
        source="text",
        input_text=input_text,
        turn_id=turn_id,
    )


def event_count(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])


def test_conversation_create_stores_conversation(conn: sqlite3.Connection) -> None:
    repo = ConversationRepository(conn, now=fixed_now("2026-07-01T12:00:00+00:00"))

    conversation = repo.create(
        title="Main",
        metadata={"topic": "dan"},
        conversation_id="conversation-main",
    )

    assert isinstance(conversation, Conversation)
    assert conversation.id == "conversation-main"
    assert conversation.title == "Main"
    assert conversation.status == "active"
    assert conversation.metadata == {"topic": "dan"}
    assert conversation.created_at == "2026-07-01T12:00:00+00:00"
    row = conn.execute("SELECT id, title, status, metadata_json FROM conversations").fetchone()
    assert row[0] == "conversation-main"
    assert row[1] == "Main"
    assert row[2] == "active"
    assert '"topic": "dan"' in row[3]


def test_conversation_get_returns_stored_conversation(conn: sqlite3.Connection) -> None:
    repo = ConversationRepository(conn, now=fixed_now())
    created = repo.create(conversation_id="conversation-get", title="Get me")

    fetched = repo.get("conversation-get")

    assert fetched == created


def test_conversation_get_or_create_creates_when_missing(conn: sqlite3.Connection) -> None:
    repo = ConversationRepository(conn, now=fixed_now())

    conversation = repo.get_or_create("new-conversation", title="New")

    assert conversation.id == "new-conversation"
    assert conversation.title == "New"


def test_conversation_get_or_create_returns_existing_when_id_exists(conn: sqlite3.Connection) -> None:
    repo = ConversationRepository(conn, now=fixed_now())
    existing = repo.create(conversation_id="existing", title="Existing")

    fetched = repo.get_or_create("existing", title="Ignored")

    assert fetched == existing


def test_conversation_list_recent_returns_newest_first(conn: sqlite3.Connection) -> None:
    repo = ConversationRepository(
        conn,
        now=fixed_now(
            "2026-07-01T12:00:00+00:00",
            "2026-07-01T12:01:00+00:00",
            "2026-07-01T12:02:00+00:00",
        ),
    )
    repo.create(conversation_id="first")
    repo.create(conversation_id="second")
    repo.create(conversation_id="third")

    conversations = repo.list_recent()

    assert [conversation.id for conversation in conversations] == ["third", "second", "first"]


def test_list_recent_with_stats_orders_by_latest_activity(conn: sqlite3.Connection) -> None:
    conversations = ConversationRepository(
        conn,
        now=fixed_now(
            "2026-07-01T12:00:00+00:00",
            "2026-07-01T12:01:00+00:00",
            "2026-07-01T12:02:00+00:00",
            "2026-07-01T12:04:00+00:00",
        ),
    )
    conversations.create(conversation_id="conversation-a")
    conversations.create(conversation_id="conversation-b")
    conversations.create(conversation_id="conversation-empty")
    TurnRepository(conn, now=fixed_now("2026-07-01T12:00:30+00:00")).create(
        "conversation-a",
        source="text",
        turn_id="turn-a-old",
    )
    TurnRepository(conn, now=fixed_now("2026-07-01T12:03:00+00:00")).create(
        "conversation-b",
        source="text",
        turn_id="turn-b-newer",
    )
    conversations.update("conversation-a", title="Renamed after B turn")

    summaries = conversations.list_recent_with_stats()

    assert [summary["id"] for summary in summaries] == [
        "conversation-a",
        "conversation-b",
        "conversation-empty",
    ]
    by_id = {summary["id"]: summary for summary in summaries}
    assert by_id["conversation-a"]["title"] == "Renamed after B turn"
    assert by_id["conversation-a"]["latest_turn_at"] == "2026-07-01T12:00:30+00:00"
    assert by_id["conversation-b"]["latest_turn_at"] == "2026-07-01T12:03:00+00:00"
    assert by_id["conversation-empty"]["turn_count"] == 0
    assert by_id["conversation-empty"]["latest_turn_at"] is None


def test_list_recent_with_stats_uses_turn_insert_order_for_same_second_activity(
    conn: sqlite3.Connection,
) -> None:
    conversations = ConversationRepository(conn, now=fixed_now("2026-07-01T12:00:00+00:00"))
    conversations.create(conversation_id="conversation-a")
    conversations.create(conversation_id="conversation-z")
    turns = TurnRepository(conn, now=fixed_now("2026-07-01T12:01:00+00:00"))
    turns.create("conversation-a", source="text", turn_id="turn-a")
    turns.create("conversation-z", source="text", turn_id="turn-z")

    summaries = conversations.list_recent_with_stats(limit=1)

    assert [summary["id"] for summary in summaries] == ["conversation-z"]


def test_conversation_update_updates_title_status_metadata(conn: sqlite3.Connection) -> None:
    repo = ConversationRepository(
        conn,
        now=fixed_now("2026-07-01T12:00:00+00:00", "2026-07-01T12:05:00+00:00"),
    )
    repo.create(conversation_id="conversation-update", title="Old", metadata={"old": True})

    updated = repo.update(
        "conversation-update",
        title="New",
        status="archived",
        metadata={"new": True},
    )

    assert updated.title == "New"
    assert updated.status == "archived"
    assert updated.metadata == {"new": True}
    assert updated.updated_at == "2026-07-01T12:05:00+00:00"


def test_conversation_archive_sets_status_archived(conn: sqlite3.Connection) -> None:
    repo = ConversationRepository(conn, now=fixed_now())
    repo.create(conversation_id="conversation-archive")

    archived = repo.archive("conversation-archive")

    assert archived.status == "archived"


def test_invalid_conversation_status_raises(conn: sqlite3.Connection) -> None:
    repo = ConversationRepository(conn, now=fixed_now())
    repo.create(conversation_id="conversation-invalid-status")

    with pytest.raises(ConversationRepositoryError, match="Invalid conversation status"):
        repo.update("conversation-invalid-status", status="deleted")


def test_non_json_conversation_metadata_raises(conn: sqlite3.Connection) -> None:
    repo = ConversationRepository(conn, now=fixed_now())

    with pytest.raises(ConversationRepositoryError, match="JSON serializable"):
        repo.create(metadata={"bad": {1, 2}})


def test_empty_conversation_id_raises(conn: sqlite3.Connection) -> None:
    repo = ConversationRepository(conn, now=fixed_now())

    with pytest.raises(ConversationRepositoryError, match="conversation_id"):
        repo.create(conversation_id="  ")


def test_turn_create_stores_turn_for_existing_conversation(conn: sqlite3.Connection) -> None:
    create_conversation(conn)
    repo = TurnRepository(conn, now=fixed_now("2026-07-01T12:00:00+00:00"))

    turn = repo.create(
        "conversation-1",
        source="text",
        input_text="Hello DAN",
        metadata={"channel": "test"},
        turn_id="turn-create",
    )

    assert isinstance(turn, Turn)
    assert turn.id == "turn-create"
    assert turn.conversation_id == "conversation-1"
    assert turn.source == "text"
    assert turn.status == "received"
    assert turn.input_text == "Hello DAN"
    assert turn.metadata == {"channel": "test"}
    assert turn.created_at == "2026-07-01T12:00:00+00:00"


def test_creating_turn_for_missing_conversation_fails_clearly(conn: sqlite3.Connection) -> None:
    repo = TurnRepository(conn, now=fixed_now())

    with pytest.raises(TurnRepositoryError, match="Could not create turn"):
        repo.create("missing-conversation", source="text", input_text="No parent")


def test_turn_get_returns_stored_turn(conn: sqlite3.Connection) -> None:
    create_conversation(conn)
    created = create_turn(conn, turn_id="turn-get")

    fetched = TurnRepository(conn).get("turn-get")

    assert fetched == created


def test_turn_update_status_updates_status_and_updated_at(conn: sqlite3.Connection) -> None:
    create_conversation(conn)
    create_turn(conn, turn_id="turn-status")
    repo = TurnRepository(conn, now=fixed_now("2026-07-01T12:05:00+00:00"))

    updated = repo.update_status("turn-status", "started", error="not an error")

    assert updated.status == "started"
    assert updated.error == "not an error"
    assert updated.updated_at == "2026-07-01T12:05:00+00:00"


def test_invalid_turn_status_raises(conn: sqlite3.Connection) -> None:
    create_conversation(conn)
    create_turn(conn, turn_id="turn-invalid-status")

    with pytest.raises(TurnRepositoryError, match="Invalid turn status"):
        TurnRepository(conn).update_status("turn-invalid-status", "working")


def test_invalid_turn_source_raises(conn: sqlite3.Connection) -> None:
    create_conversation(conn)

    with pytest.raises(TurnRepositoryError, match="Invalid turn source"):
        TurnRepository(conn).create("conversation-1", source="email")


def test_empty_turn_ids_raise(conn: sqlite3.Connection) -> None:
    create_conversation(conn)
    repo = TurnRepository(conn)

    with pytest.raises(TurnRepositoryError, match="conversation_id"):
        repo.create("  ", source="text")
    with pytest.raises(TurnRepositoryError, match="turn_id"):
        repo.create("conversation-1", source="text", turn_id="  ")


def test_attach_context_snapshot_stores_json_and_sets_context_built(
    conn: sqlite3.Connection,
) -> None:
    create_conversation(conn)
    create_turn(conn, turn_id="turn-context")

    turn = TurnRepository(conn).attach_context_snapshot(
        "turn-context",
        {"message_count": 3, "provider_sessions_are_memory": False},
    )

    assert turn.status == "context_built"
    assert turn.context_snapshot == {
        "message_count": 3,
        "provider_sessions_are_memory": False,
    }


def test_non_json_context_snapshot_raises(conn: sqlite3.Connection) -> None:
    create_conversation(conn)
    create_turn(conn, turn_id="turn-bad-context")

    with pytest.raises(TurnRepositoryError, match="JSON serializable"):
        TurnRepository(conn).attach_context_snapshot("turn-bad-context", {"bad": {1, 2}})


def test_attach_brain_request_stores_adapter_model_and_sets_brain_requested(
    conn: sqlite3.Connection,
) -> None:
    create_conversation(conn)
    create_turn(conn, turn_id="turn-brain")

    turn = TurnRepository(conn).attach_brain_request(
        "turn-brain",
        adapter="mock",
        model="mock-local",
    )

    assert turn.status == "brain_requested"
    assert turn.brain_adapter == "mock"
    assert turn.brain_model == "mock-local"


def test_finish_stores_final_text_brain_fields_and_status_finished(
    conn: sqlite3.Connection,
) -> None:
    create_conversation(conn)
    create_turn(conn, turn_id="turn-finish")

    turn = TurnRepository(conn).finish(
        "turn-finish",
        final_text="Done",
        brain_adapter="mock",
        brain_model="mock-local",
        metadata={"finish_reason": "stop"},
    )

    assert turn.status == "finished"
    assert turn.final_text == "Done"
    assert turn.brain_adapter == "mock"
    assert turn.brain_model == "mock-local"
    assert turn.metadata == {"finish_reason": "stop"}


def test_await_approval_stores_final_text_brain_fields_and_status(
    conn: sqlite3.Connection,
) -> None:
    create_conversation(conn)
    create_turn(conn, turn_id="turn-awaiting-approval")

    turn = TurnRepository(conn).await_approval(
        "turn-awaiting-approval",
        final_text="Tool requires approval",
        brain_adapter="mock",
        brain_model="mock-local",
        metadata={"approval_count": 1},
    )

    assert turn.status == "awaiting_approval"
    assert turn.final_text == "Tool requires approval"
    assert turn.brain_adapter == "mock"
    assert turn.brain_model == "mock-local"
    assert turn.error is None
    assert turn.metadata == {"approval_count": 1}


def test_fail_stores_error_and_status_failed(conn: sqlite3.Connection) -> None:
    create_conversation(conn)
    create_turn(conn, turn_id="turn-fail")

    turn = TurnRepository(conn).fail(
        "turn-fail",
        error="adapter unavailable",
        metadata={"kind": "adapter_unavailable"},
    )

    assert turn.status == "failed"
    assert turn.error == "adapter unavailable"
    assert turn.metadata == {"kind": "adapter_unavailable"}


def test_cancel_stores_cancelled_status_and_reason(conn: sqlite3.Connection) -> None:
    create_conversation(conn)
    create_turn(conn, turn_id="turn-cancel")

    turn = TurnRepository(conn).cancel("turn-cancel", reason="user interrupted")

    assert turn.status == "cancelled"
    assert turn.error == "user interrupted"


def test_list_for_conversation_returns_chronological_by_default(conn: sqlite3.Connection) -> None:
    create_conversation(conn)
    repo = TurnRepository(
        conn,
        now=fixed_now(
            "2026-07-01T12:00:00+00:00",
            "2026-07-01T12:01:00+00:00",
            "2026-07-01T12:02:00+00:00",
        ),
    )
    repo.create("conversation-1", source="text", turn_id="turn-1")
    repo.create("conversation-1", source="text", turn_id="turn-2")
    repo.create("conversation-1", source="text", turn_id="turn-3")

    turns = repo.list_for_conversation("conversation-1")

    assert [turn.id for turn in turns] == ["turn-1", "turn-2", "turn-3"]


def test_list_for_conversation_preserves_insert_order_for_same_second_turns(
    conn: sqlite3.Connection,
) -> None:
    create_conversation(conn)
    repo = TurnRepository(conn, now=fixed_now("2026-07-01T12:00:00+00:00"))
    repo.create("conversation-1", source="text", turn_id="turn-b", input_text="First")
    repo.create("conversation-1", source="text", turn_id="turn-a", input_text="Second")

    turns = repo.list_for_conversation("conversation-1")
    newest_first = repo.list_for_conversation("conversation-1", newest_first=True)

    assert [turn.input_text for turn in turns] == ["First", "Second"]
    assert [turn.input_text for turn in newest_first] == ["Second", "First"]


def test_list_for_conversation_newest_first_returns_newest_first(conn: sqlite3.Connection) -> None:
    create_conversation(conn)
    repo = TurnRepository(
        conn,
        now=fixed_now(
            "2026-07-01T12:00:00+00:00",
            "2026-07-01T12:01:00+00:00",
        ),
    )
    repo.create("conversation-1", source="text", turn_id="turn-old")
    repo.create("conversation-1", source="text", turn_id="turn-new")

    turns = repo.list_for_conversation("conversation-1", newest_first=True)

    assert [turn.id for turn in turns] == ["turn-new", "turn-old"]


def test_recent_for_context_returns_newest_n_ordered_chronological(conn: sqlite3.Connection) -> None:
    create_conversation(conn)
    repo = TurnRepository(
        conn,
        now=fixed_now(
            "2026-07-01T12:00:00+00:00",
            "2026-07-01T12:01:00+00:00",
            "2026-07-01T12:02:00+00:00",
        ),
    )
    repo.create("conversation-1", source="text", turn_id="turn-1")
    repo.create("conversation-1", source="text", turn_id="turn-2")
    repo.create("conversation-1", source="text", turn_id="turn-3")

    turns = repo.recent_for_context("conversation-1", limit=2)

    assert [turn.id for turn in turns] == ["turn-2", "turn-3"]


def test_list_for_conversation_isolates_conversations(conn: sqlite3.Connection) -> None:
    create_conversation(conn, "conversation-1")
    create_conversation(conn, "conversation-2")
    repo = TurnRepository(conn, now=fixed_now())
    repo.create("conversation-1", source="text", turn_id="turn-a")
    repo.create("conversation-2", source="text", turn_id="turn-b")

    turns = repo.list_for_conversation("conversation-1")

    assert [turn.id for turn in turns] == ["turn-a"]


@pytest.mark.parametrize("method_name", ["list_for_conversation", "recent_for_context"])
def test_limit_lte_zero_raises(conn: sqlite3.Connection, method_name: str) -> None:
    create_conversation(conn)
    repo = TurnRepository(conn)

    with pytest.raises(TurnRepositoryError, match="limit must be positive"):
        getattr(repo, method_name)("conversation-1", limit=0)


def test_over_large_limit_raises(conn: sqlite3.Connection) -> None:
    create_conversation(conn)
    repo = TurnRepository(conn)

    with pytest.raises(TurnRepositoryError, match=f"limit must be at most {MAX_REPOSITORY_LIMIT}"):
        repo.list_for_conversation("conversation-1", limit=MAX_REPOSITORY_LIMIT + 1)


def test_metadata_merge_behavior_works_for_finish_fail_cancel(conn: sqlite3.Connection) -> None:
    create_conversation(conn)
    repo = TurnRepository(conn, now=fixed_now())
    repo.create("conversation-1", source="text", turn_id="turn-merge", metadata={"initial": True})

    finished = repo.finish("turn-merge", final_text="Done", metadata={"finish": True})
    failed = repo.fail("turn-merge", error="Retried failure", metadata={"failed": True})
    cancelled = repo.cancel("turn-merge", reason="Then cancelled")

    assert finished.metadata == {"initial": True, "finish": True}
    assert failed.metadata == {"initial": True, "finish": True, "failed": True}
    assert cancelled.metadata == {"initial": True, "finish": True, "failed": True}


def test_repositories_do_not_append_events(conn: sqlite3.Connection) -> None:
    conversations = ConversationRepository(conn)
    turns = TurnRepository(conn)

    conversation = conversations.create(conversation_id="conversation-events")
    turn = turns.create(conversation.id, source="text", turn_id="turn-events")
    turns.update_status(turn.id, "started")
    turns.attach_context_snapshot(turn.id, {"ok": True})
    turns.attach_brain_request(turn.id, adapter="mock", model="mock-local")
    turns.finish(turn.id, final_text="Done")
    conversations.archive(conversation.id)

    assert event_count(conn) == 0


def test_repositories_do_not_call_brain_context_voice_tools_or_workers() -> None:
    forbidden_fragments = (
        "BrainManager",
        "ContextBuilder",
        "dan.voice",
        "dan.tools",
        "dan.workers",
        "voice_queue",
        "tool_runs",
        "worker_jobs",
        "EventStore",
        ".append(",
        "import subprocess",
        "import urllib",
    )
    for relative in ("dan/turns/repository.py", "dan/store/repositories.py"):
        source = (ROOT / relative).read_text(encoding="utf-8")
        offenders = [fragment for fragment in forbidden_fragments if fragment in source]
        assert offenders == [], f"{relative} has forbidden fragments: {offenders}"


def test_turn_contract_enums_have_prompt_10_values() -> None:
    assert {status.value for status in ConversationStatus} == {"active", "archived"}
    assert {source.value for source in TurnSource} == {"text", "voice", "panel", "cli", "api"}
    assert {status.value for status in TurnStatus} == {
        "received",
        "started",
        "context_built",
        "brain_requested",
        "brain_responded",
        "awaiting_approval",
        "finished",
        "failed",
        "cancelled",
    }


def test_sqlite_schema_and_migrations_are_not_modified() -> None:
    assert_schema_and_migrations_unchanged(ROOT)


def test_runtime_files_do_not_contain_forbidden_legacy_strings() -> None:
    forbidden = (
        "/Users/" "n1_ozzy" "/Documents/dev/dan",
        "/tmp/dan",
        "afplay",
        "--dangerously-skip-permissions",
    )
    scanned = (
        ROOT / "dan" / "turns" / "models.py",
        ROOT / "dan" / "turns" / "repository.py",
        ROOT / "dan" / "store" / "repositories.py",
    )
    offenders: list[tuple[str, str]] = []

    for path in scanned:
        source = path.read_text(encoding="utf-8")
        for snippet in forbidden:
            if snippet in source:
                offenders.append((str(path.relative_to(ROOT)), snippet))

    assert offenders == []
