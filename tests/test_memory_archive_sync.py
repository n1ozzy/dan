"""Incremental explicit import contracts for the shared memory archive."""

from __future__ import annotations

import json
from importlib import import_module
from pathlib import Path

import pytest

from jarvis import cli as jarvis_cli
from jarvis.memory.archive import MemoryArchive
from jarvis.store.db import close_quietly, initialize_database
from tests.test_api_smoke import write_config


def test_claude_jsonl_sync_imports_only_new_visible_messages(tmp_path: Path) -> None:
    sync_module = import_module("jarvis.memory.sync")
    conn = initialize_database(tmp_path / "jarvis.db")
    synchronizer = sync_module.MemorySourceSynchronizer(MemoryArchive(conn), conn)
    source = tmp_path / "claude-session.jsonl"
    source.write_text(
        json.dumps(
            {
                "type": "user",
                "uuid": "user-1",
                "sessionId": "session-1",
                "message": {"role": "user", "content": "remember the local archive"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    with source.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "type": "user",
                    "uuid": "meta-1",
                    "sessionId": "session-1",
                    "isMeta": True,
                    "message": {"role": "user", "content": "hidden hook instructions"},
                }
            )
            + "\n"
        )

    first = synchronizer.sync_path("claude_jsonl", source)
    with source.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "type": "assistant",
                    "uuid": "assistant-1",
                    "sessionId": "session-1",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": "the second durable fact"},
                            {"type": "tool_use", "input": {"secret": "never index"}},
                        ],
                    },
                }
            )
            + "\n"
        )

    second = synchronizer.sync_path("claude_jsonl", source)

    assert (first.imported, second.imported) == (1, 1)
    assert conn.execute("SELECT COUNT(*) FROM memory_archive_documents").fetchone()[0] == 2
    contents = [
        row[0]
        for row in conn.execute(
            "SELECT content FROM memory_archive_documents ORDER BY source_item_id"
        )
    ]
    assert contents == ["the second durable fact", "remember the local archive"]
    assert "never index" not in " ".join(contents)
    assert "hidden hook instructions" not in " ".join(contents)


def test_jsonl_sync_detects_rewritten_prefix_and_reconciles_stale_records(
    tmp_path: Path,
) -> None:
    sync_module = import_module("jarvis.memory.sync")
    conn = initialize_database(tmp_path / "jarvis.db")
    synchronizer = sync_module.MemorySourceSynchronizer(MemoryArchive(conn), conn)
    source = tmp_path / "rewritten.jsonl"

    def row(uuid: str, content: str) -> str:
        return json.dumps(
            {
                "type": "user",
                "uuid": uuid,
                "sessionId": "session-1",
                "message": {"role": "user", "content": content},
            }
        ) + "\n"

    source.write_text(row("old-id", "old durable fact"), encoding="utf-8")
    synchronizer.sync_path("claude_jsonl", source)
    source.write_text(row("new-id", "new durable fact"), encoding="utf-8")

    result = synchronizer.sync_path("claude_jsonl", source)

    assert (result.imported, result.deleted) == (1, 1)
    assert conn.execute("SELECT COUNT(*) FROM memory_archive_documents").fetchone()[0] == 1
    assert conn.execute("SELECT content FROM memory_archive_documents").fetchone()[0] == (
        "new durable fact"
    )


def test_jsonl_sync_detects_same_size_rewrite_outside_sampled_edges(
    tmp_path: Path,
) -> None:
    sync_module = import_module("jarvis.memory.sync")
    conn = initialize_database(tmp_path / "jarvis.db")
    synchronizer = sync_module.MemorySourceSynchronizer(MemoryArchive(conn), conn)
    source = tmp_path / "large-rewritten.jsonl"

    padding = "x" * (70 * 1024)
    ignored = json.dumps({"type": "progress", "payload": padding}) + "\n"

    def visible(content: str) -> str:
        return json.dumps(
            {
                "type": "user",
                "uuid": "message-1",
                "sessionId": "session-1",
                "message": {"role": "user", "content": content},
            }
        ) + "\n"

    source.write_text(
        ignored + visible("OLD-MIDDLE") + ignored,
        encoding="utf-8",
    )
    synchronizer.sync_path("claude_jsonl", source)
    source.write_text(
        ignored + visible("NEW-MIDDLE") + ignored,
        encoding="utf-8",
    )

    result = synchronizer.sync_path("claude_jsonl", source)

    assert result.updated == 1
    assert conn.execute("SELECT content FROM memory_archive_documents").fetchone()[0] == (
        "NEW-MIDDLE"
    )


def test_jsonl_rewrite_waits_for_complete_line_before_reconciliation(tmp_path: Path) -> None:
    sync_module = import_module("jarvis.memory.sync")
    conn = initialize_database(tmp_path / "jarvis.db")
    synchronizer = sync_module.MemorySourceSynchronizer(MemoryArchive(conn), conn)
    source = tmp_path / "rotating.jsonl"
    old_row = json.dumps(
        {
            "type": "user",
            "uuid": "old-id",
            "sessionId": "session-1",
            "message": {"role": "user", "content": "old complete fact"},
        }
    )
    new_row = json.dumps(
        {
            "type": "user",
            "uuid": "new-id",
            "sessionId": "session-1",
            "message": {"role": "user", "content": "new complete fact"},
        }
    )
    source.write_text(old_row + "\n", encoding="utf-8")
    synchronizer.sync_path("claude_jsonl", source)
    source.write_text(new_row, encoding="utf-8")

    incomplete = synchronizer.sync_path("claude_jsonl", source)

    assert incomplete.deleted == 0
    assert conn.execute("SELECT content FROM memory_archive_documents").fetchone()[0] == (
        "old complete fact"
    )


def test_jsonl_move_keeps_logical_session_identity(tmp_path: Path) -> None:
    sync_module = import_module("jarvis.memory.sync")
    conn = initialize_database(tmp_path / "jarvis.db")
    synchronizer = sync_module.MemorySourceSynchronizer(MemoryArchive(conn), conn)
    source = tmp_path / "active.jsonl"
    source.write_text(
        json.dumps(
            {
                "type": "user",
                "uuid": "message-1",
                "sessionId": "stable-session",
                "message": {"role": "user", "content": "stable across moves"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    synchronizer.sync_path("claude_jsonl", source)
    moved = tmp_path / "archived.jsonl"
    source.rename(moved)

    synchronizer.sync_path("claude_jsonl", moved)

    assert conn.execute("SELECT COUNT(*) FROM memory_archive_documents").fetchone()[0] == 1


def test_explicit_directory_sync_discovers_supported_session_files(tmp_path: Path) -> None:
    sync_module = import_module("jarvis.memory.sync")
    conn = initialize_database(tmp_path / "jarvis.db")
    synchronizer = sync_module.MemorySourceSynchronizer(MemoryArchive(conn), conn)
    source_root = tmp_path / "claude-projects"
    source_root.mkdir()
    for index in (1, 2):
        (source_root / f"session-{index}.jsonl").write_text(
            json.dumps(
                {
                    "type": "user",
                    "uuid": f"message-{index}",
                    "sessionId": f"session-{index}",
                    "message": {"role": "user", "content": f"fact number {index}"},
                }
            )
            + "\n",
            encoding="utf-8",
        )

    first = synchronizer.sync_path("claude_jsonl", source_root)
    repeated = synchronizer.sync_path("claude_jsonl", source_root)

    assert (first.imported, repeated.imported) == (2, 0)
    assert conn.execute("SELECT COUNT(*) FROM memory_archive_documents").fetchone()[0] == 2


def test_directory_sync_reconciles_sources_removed_from_disk(tmp_path: Path) -> None:
    sync_module = import_module("jarvis.memory.sync")
    conn = initialize_database(tmp_path / "jarvis.db")
    synchronizer = sync_module.MemorySourceSynchronizer(MemoryArchive(conn), conn)
    source_root = tmp_path / "codex-memory"
    source_root.mkdir()
    kept = source_root / "kept.md"
    removed = source_root / "removed.md"
    kept.write_text("kept fact\n", encoding="utf-8")
    removed.write_text("removed fact\n", encoding="utf-8")
    synchronizer.sync_path("codex_memory", source_root)
    removed.unlink()

    result = synchronizer.sync_path("codex_memory", source_root)

    assert result.deleted == 1
    assert conn.execute(
        "SELECT content FROM memory_archive_documents ORDER BY content"
    ).fetchall() == [("kept fact",)]
    assert conn.execute(
        """
        SELECT COUNT(*) FROM memory_archive_sync_state
        WHERE source_type = 'codex_memory' AND source_uri = ?
        """,
        (removed.resolve().as_uri(),),
    ).fetchone()[0] == 0


def test_directory_sync_rolls_back_documents_when_any_candidate_fails(
    tmp_path: Path,
) -> None:
    sync_module = import_module("jarvis.memory.sync")
    conn = initialize_database(tmp_path / "jarvis.db")
    synchronizer = sync_module.MemorySourceSynchronizer(MemoryArchive(conn), conn)
    source_root = tmp_path / "codex-memory"
    source_root.mkdir()
    (source_root / "a-good.md").write_text("must roll back\n", encoding="utf-8")
    (source_root / "z-bad.md").write_bytes(b"\xff\xfe\xfd")

    with pytest.raises(UnicodeDecodeError):
        synchronizer.sync_path("codex_memory", source_root)

    assert conn.execute("SELECT COUNT(*) FROM memory_archive_documents").fetchone()[0] == 0
    assert conn.execute(
        """
        SELECT COUNT(*) FROM memory_archive_sync_state
        WHERE source_type IN ('codex_memory', 'directory:codex_memory')
        """
    ).fetchone()[0] == 0


def test_codex_session_sync_indexes_messages_not_reasoning_or_tools(tmp_path: Path) -> None:
    sync_module = import_module("jarvis.memory.sync")
    conn = initialize_database(tmp_path / "jarvis.db")
    synchronizer = sync_module.MemorySourceSynchronizer(MemoryArchive(conn), conn)
    source = tmp_path / "codex-rollout.jsonl"
    rows = [
        {
            "type": "response_item",
            "payload": {
                "id": "msg-1",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "visible codex answer"}],
            },
        },
        {
            "type": "response_item",
            "payload": {
                "id": "wrong-role-content",
                "type": "message",
                "role": "user",
                "content": [{"type": "output_text", "text": "must not cross roles"}],
            },
        },
        {
            "type": "response_item",
            "payload": {
                "id": "reason-1",
                "type": "reasoning",
                "summary": [{"text": "private chain of thought"}],
                "encrypted_content": "ciphertext",
            },
        },
        {
            "type": "response_item",
            "payload": {"type": "function_call", "arguments": "tool secret"},
        },
    ]
    source.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    result = synchronizer.sync_path("codex_session", source)

    assert result.imported == 1
    content = conn.execute("SELECT content FROM memory_archive_documents").fetchone()[0]
    assert content == "visible codex answer"


def test_codex_fallback_id_is_stable_when_ignored_records_move_offsets(
    tmp_path: Path,
) -> None:
    sync_module = import_module("jarvis.memory.sync")
    conn = initialize_database(tmp_path / "jarvis.db")
    synchronizer = sync_module.MemorySourceSynchronizer(MemoryArchive(conn), conn)
    source = tmp_path / "codex-rollout.jsonl"
    session = {
        "type": "session_meta",
        "payload": {"id": "stable-session"},
    }
    message = {
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "stable answer"}],
        },
    }
    source.write_text(
        json.dumps(session) + "\n" + json.dumps(message) + "\n",
        encoding="utf-8",
    )
    synchronizer.sync_path("codex_session", source)
    first_id = conn.execute(
        "SELECT canonical_id FROM memory_archive_documents"
    ).fetchone()[0]
    ignored = {"type": "event_msg", "payload": {"type": "token_count"}}
    source.write_text(
        json.dumps(session)
        + "\n"
        + json.dumps(ignored)
        + "\n"
        + json.dumps(message)
        + "\n",
        encoding="utf-8",
    )

    synchronizer.sync_path("codex_session", source)

    assert conn.execute(
        "SELECT canonical_id FROM memory_archive_documents"
    ).fetchone()[0] == first_id


def test_codex_fallback_ids_preserve_identical_message_occurrences(tmp_path: Path) -> None:
    sync_module = import_module("jarvis.memory.sync")
    conn = initialize_database(tmp_path / "jarvis.db")
    synchronizer = sync_module.MemorySourceSynchronizer(MemoryArchive(conn), conn)
    source = tmp_path / "codex-rollout.jsonl"
    rows = [
        {"type": "session_meta", "payload": {"id": "stable-session"}},
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "same answer"}],
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "same answer"}],
            },
        },
    ]
    source.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    result = synchronizer.sync_path("codex_session", source)

    assert result.imported == 2
    stored = conn.execute(
        "SELECT canonical_id FROM memory_archive_documents ORDER BY source_item_id"
    ).fetchall()
    assert len(stored) == 2
    assert stored[0][0] != stored[1][0]


@pytest.mark.parametrize("source_type", ["claude_memory", "codex_memory", "gpt_transcript"])
def test_markdown_memory_sources_update_one_stable_document(
    tmp_path: Path,
    source_type: str,
) -> None:
    sync_module = import_module("jarvis.memory.sync")
    conn = initialize_database(tmp_path / "jarvis.db")
    synchronizer = sync_module.MemorySourceSynchronizer(MemoryArchive(conn), conn)
    source = tmp_path / f"{source_type}.md"
    source.write_text("# Memory\n\nfirst durable paragraph\n", encoding="utf-8")

    first = synchronizer.sync_path(source_type, source)
    unchanged = synchronizer.sync_path(source_type, source)
    canonical_id = conn.execute(
        "SELECT canonical_id FROM memory_archive_documents"
    ).fetchone()[0]
    source.write_text("# Memory\n\nrevised durable paragraph\n", encoding="utf-8")
    revised = synchronizer.sync_path(source_type, source)

    assert (first.imported, unchanged.unchanged, revised.updated) == (1, 0, 1)
    assert conn.execute("SELECT COUNT(*) FROM memory_archive_documents").fetchone()[0] == 1
    assert conn.execute(
        "SELECT canonical_id, content FROM memory_archive_documents"
    ).fetchone() == (canonical_id, "# Memory\n\nrevised durable paragraph")
    source.write_text("", encoding="utf-8")
    cleared = synchronizer.sync_path(source_type, source)
    assert cleared.deleted == 1
    assert conn.execute("SELECT COUNT(*) FROM memory_archive_documents").fetchone()[0] == 0


def test_jarvis_turn_sync_reprocesses_updated_turn_without_duplicate_ids(tmp_path: Path) -> None:
    sync_module = import_module("jarvis.memory.sync")
    conn = initialize_database(tmp_path / "jarvis.db")
    conn.execute(
        """
        INSERT INTO conversations (id, created_at, updated_at, status, metadata_json)
        VALUES ('conversation-1', '2026-07-16T10:00:00Z', '2026-07-16T10:00:00Z', 'active', '{}')
        """
    )
    conn.execute(
        """
        INSERT INTO turns (
          id, conversation_id, created_at, updated_at, source, status,
          input_text, final_text, metadata_json
        ) VALUES (
          'turn-1', 'conversation-1', '2026-07-16T10:00:00Z',
          '2026-07-16T10:00:00Z', 'text', 'finished',
          'original user fact', 'original assistant fact', '{}'
        )
        """
    )
    conn.commit()
    synchronizer = sync_module.MemorySourceSynchronizer(MemoryArchive(conn), conn)

    first = synchronizer.sync_jarvis_turns()
    conn.execute(
        """
        UPDATE turns
        SET final_text = 'revised assistant fact', updated_at = '2026-07-16T11:00:00Z'
        WHERE id = 'turn-1'
        """
    )
    conn.commit()
    revised = synchronizer.sync_jarvis_turns()

    assert first.imported == 2
    assert (revised.updated, revised.unchanged) == (1, 1)
    assert conn.execute("SELECT COUNT(*) FROM memory_archive_documents").fetchone()[0] == 2
    assert conn.execute(
        """
        SELECT content FROM memory_archive_documents
        WHERE source_item_id = 'turn-1:assistant'
        """
    ).fetchone()[0] == "revised assistant fact"
    conn.execute(
        """
        UPDATE turns
        SET final_text = NULL, updated_at = '2026-07-16T12:00:00Z'
        WHERE id = 'turn-1'
        """
    )
    conn.commit()
    cleared = synchronizer.sync_jarvis_turns()
    assert cleared.deleted == 1
    assert conn.execute("SELECT COUNT(*) FROM memory_archive_documents").fetchone()[0] == 1


def test_cli_runs_explicit_local_source_sync(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path = tmp_path / "home" / "jarvis.db"
    config_path = write_config(tmp_path / "jarvis.toml", db_path)
    source = tmp_path / "memory.md"
    source.write_text("local explicit sync fact\n", encoding="utf-8")

    exit_code = jarvis_cli.main(
        [
            "--config",
            str(config_path),
            "memory",
            "sync",
            "codex_memory",
            str(source),
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    conn = initialize_database(db_path)
    try:
        stored = conn.execute(
            "SELECT source_type, content FROM memory_archive_documents"
        ).fetchone()
    finally:
        close_quietly(conn)

    assert exit_code == 0
    assert payload["imported"] == 1
    assert stored == ("codex_memory", "local explicit sync fact")
