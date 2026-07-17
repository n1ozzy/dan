"""Shared local memory archive contracts."""

from __future__ import annotations

import sqlite3
from importlib import import_module
from pathlib import Path

from dan.store.db import close_quietly, initialize_database, table_names
from dan.store.migrations import LATEST_SCHEMA_VERSION, apply_migrations


def test_database_initializes_separate_memory_archive_and_fts5_tables(
    tmp_path: Path,
) -> None:
    conn = initialize_database(tmp_path / "dan.db")
    try:
        tables = table_names(conn)

        assert "memory_archive_documents" in tables
        assert "memory_archive_sync_state" in tables
        assert "memory_archive_fts" in tables

        fts_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
            ("memory_archive_fts",),
        ).fetchone()[0]
        assert "USING fts5" in fts_sql
    finally:
        close_quietly(conn)


def test_migration_adds_memory_archive_without_replacing_existing_data() -> None:
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE schema_version (
          version INTEGER PRIMARY KEY,
          applied_at TEXT NOT NULL,
          description TEXT NOT NULL
        );
        INSERT INTO schema_version VALUES (2, '2026-07-16T00:00:00Z', 'existing v2');
        CREATE TABLE existing_user_data (value TEXT NOT NULL);
        INSERT INTO existing_user_data VALUES ('keep me');
        """
    )

    apply_migrations(conn)

    assert LATEST_SCHEMA_VERSION == 4
    assert "memory_archive_documents" in table_names(conn)
    assert "memory_archive_sync_state" in table_names(conn)
    assert conn.execute("SELECT value FROM existing_user_data").fetchone()[0] == "keep me"


def test_migration_rejects_non_fts_table_conflict_before_recording_v3() -> None:
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE schema_version (
          version INTEGER PRIMARY KEY,
          applied_at TEXT NOT NULL,
          description TEXT NOT NULL
        );
        INSERT INTO schema_version VALUES (2, '2026-07-16T00:00:00Z', 'existing v2');
        CREATE TABLE memory_archive_fts (fake TEXT);
        """
    )

    try:
        apply_migrations(conn)
    except RuntimeError:
        pass
    else:
        raise AssertionError("accepted a normal table in place of the FTS5 index")

    assert conn.execute("SELECT COUNT(*) FROM schema_version WHERE version = 3").fetchone()[0] == 0


def test_canonical_memory_id_is_stable_and_source_scoped() -> None:
    archive = import_module("dan.memory.archive")

    first = archive.canonical_memory_id("claude_jsonl", "/tmp/session.jsonl", "line:7")
    repeated = archive.canonical_memory_id("claude_jsonl", "/tmp/session.jsonl", "line:7")
    other_source = archive.canonical_memory_id("codex_session", "/tmp/session.jsonl", "line:7")

    assert first == repeated
    assert first.startswith("mem_")
    assert len(first) == 36
    assert other_source != first


def test_canonical_memory_id_has_no_delimiter_collisions() -> None:
    archive = import_module("dan.memory.archive")

    left = archive.canonical_memory_id("a\0b", "c", "d")
    right = archive.canonical_memory_id("a", "b\0c", "d")

    assert left != right


def test_canonical_memory_id_rejects_empty_identity_parts() -> None:
    archive = import_module("dan.memory.archive")

    for parts in (("", "uri", "item"), ("kind", " ", "item"), ("kind", "uri", "")):
        try:
            archive.canonical_memory_id(*parts)
        except ValueError:
            continue
        raise AssertionError(f"accepted empty identity part: {parts!r}")


def test_archive_redacts_document_before_persisting_or_indexing(tmp_path: Path) -> None:
    archive_module = import_module("dan.memory.archive")
    conn = initialize_database(tmp_path / "dan.db")
    archive = archive_module.MemoryArchive(conn, now=lambda: "2026-07-16T12:00:00Z")
    raw_secret = "sk-ant-never-store-this"

    archived = archive.upsert(
        archive_module.ArchiveDocument(
            source_type="claude_jsonl",
            source_uri="/tmp/session.jsonl",
            source_item_id="message:7",
            title=f"credential {raw_secret}",
            content=f"ordinary searchable fact beside {raw_secret}",
            metadata={"authorization": raw_secret, "safe": "visible"},
        )
    )

    stored = conn.execute(
        "SELECT title, content, metadata_json FROM memory_archive_documents"
    ).fetchone()
    indexed = conn.execute("SELECT title, content FROM memory_archive_fts").fetchone()
    assert archived.changed is True
    assert raw_secret not in " ".join(stored)
    assert raw_secret not in " ".join(indexed)
    assert "ordinary searchable fact" in stored[1]
    assert '"safe":"visible"' in stored[2]
    assert archive.recall(raw_secret).results == ()


def test_archive_upsert_is_idempotent_for_unchanged_source_item(tmp_path: Path) -> None:
    archive_module = import_module("dan.memory.archive")
    conn = initialize_database(tmp_path / "dan.db")
    timestamps = iter(("2026-07-16T12:00:00Z", "2026-07-16T13:00:00Z"))
    archive = archive_module.MemoryArchive(conn, now=lambda: next(timestamps))
    document = archive_module.ArchiveDocument(
        source_type="codex_session",
        source_uri="/tmp/rollout.jsonl",
        source_item_id="response:9",
        content="one durable fact",
    )

    first = archive.upsert(document)
    repeated = archive.upsert(document)

    assert first.changed is True
    assert repeated.changed is False
    assert repeated.document == first.document
    assert conn.execute("SELECT COUNT(*) FROM memory_archive_documents").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM memory_archive_fts").fetchone()[0] == 1


def test_archive_rejects_untrusted_non_iso_source_timestamp(tmp_path: Path) -> None:
    archive_module = import_module("dan.memory.archive")
    conn = initialize_database(tmp_path / "dan.db")
    archive = archive_module.MemoryArchive(conn)

    try:
        archive.upsert(
            archive_module.ArchiveDocument(
                source_type="claude_jsonl",
                source_uri="claude_jsonl:session:one",
                source_item_id="user:one",
                content="safe content",
                source_updated_at="sk-ant-timestamp-secret",
            )
        )
    except ValueError:
        pass
    else:
        raise AssertionError("accepted a non-ISO source timestamp")

    assert conn.execute("SELECT COUNT(*) FROM memory_archive_documents").fetchone()[0] == 0


def test_recall_returns_deterministic_canonical_results(tmp_path: Path) -> None:
    archive_module = import_module("dan.memory.archive")
    conn = initialize_database(tmp_path / "dan.db")
    archive = archive_module.MemoryArchive(conn, now=lambda: "2026-07-16T12:00:00Z")
    for source_item_id in ("message:b", "message:a"):
        archive.upsert(
            archive_module.ArchiveDocument(
                source_type="claude_jsonl",
                source_uri="/tmp/session.jsonl",
                source_item_id=source_item_id,
                title="Persistent brain",
                content="shared local memory survives restarts",
            )
        )

    response = archive.recall("local memory", limit=10)

    assert response.query == "local memory"
    assert [hit.canonical_id for hit in response.results] == sorted(
        hit.canonical_id for hit in response.results
    )
    assert [hit.source_item_id for hit in response.results] in [
        ["message:a", "message:b"],
        ["message:b", "message:a"],
    ]
    assert len(response.results) == 2


def test_recall_serializes_one_canonical_transport_payload(tmp_path: Path) -> None:
    archive_module = import_module("dan.memory.archive")
    conn = initialize_database(tmp_path / "dan.db")
    archive = archive_module.MemoryArchive(conn, now=lambda: "2026-07-16T12:00:00Z")
    archive.upsert(
        archive_module.ArchiveDocument(
            source_type="dan_turn",
            source_uri="dan.db",
            source_item_id="turn:1:assistant",
            content="the recall payload is shared",
            metadata={"role": "assistant"},
        )
    )

    payload = archive_module.memory_recall_to_dict(archive.recall("shared", limit=4))

    assert payload == {
        "query": "shared",
        "limit": 4,
        "count": 1,
        "results": [
            {
                "canonical_id": payload["results"][0]["canonical_id"],
                "source_type": "dan_turn",
                "source_uri": "dan.db",
                "source_item_id": "turn:1:assistant",
                "title": None,
                "content": "the recall payload is shared",
                "source_updated_at": None,
                "metadata": {"role": "assistant"},
                "score": payload["results"][0]["score"],
            }
        ],
    }


def test_sync_source_is_incremental_idempotent_and_records_cursor(tmp_path: Path) -> None:
    archive_module = import_module("dan.memory.archive")
    conn = initialize_database(tmp_path / "dan.db")
    timestamps = iter(("2026-07-16T12:00:00Z", "2026-07-16T13:00:00Z"))
    archive = archive_module.MemoryArchive(conn, now=lambda: next(timestamps))
    document = archive_module.ArchiveDocument(
        source_type="codex_memory",
        source_uri="codex-memory:project",
        source_item_id="MEMORY.md",
        content="stable local memory",
    )

    first = archive.sync_source(
        "codex_memory",
        "codex-memory:project",
        [document],
        cursor="123",
        fingerprint="sha256:first",
    )
    repeated = archive.sync_source(
        "codex_memory",
        "codex-memory:project",
        [document],
        cursor="123",
        fingerprint="sha256:first",
    )

    assert (first.imported, first.updated, first.unchanged) == (1, 0, 0)
    assert (repeated.imported, repeated.updated, repeated.unchanged) == (0, 0, 1)
    assert conn.execute("SELECT COUNT(*) FROM memory_archive_documents").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM memory_archive_fts").fetchone()[0] == 1
    assert conn.execute(
        "SELECT cursor, fingerprint FROM memory_archive_sync_state"
    ).fetchone() == ("123", "sha256:first")
