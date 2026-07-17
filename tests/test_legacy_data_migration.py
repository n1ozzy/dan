"""Contracts for explicit, idempotent import of the real legacy memory schema."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from jarvis.migration.sqlite_backup import BackupReport

ROOT = Path(__file__).resolve().parents[1]
MEMORY_SCHEMA_SQL = ROOT / "tests" / "fixtures" / "memory_v1.sql"


def _create_jarvis_fixture(path: Path) -> Path:
    from jarvis.store.db import initialize_database

    connection = initialize_database(path)
    try:
        connection.execute(
            """
            INSERT INTO memory_blocks (
              id, kind, title, body, priority, active, created_at, updated_at, metadata_json
            ) VALUES ('jarvis-existing', 'fact', 'Duplicate', 'shared target memory', 0, 1,
                      '2026-06-30T00:00:00Z', '2026-06-30T00:00:00Z', '{}')
            """
        )
        connection.execute(
            "INSERT INTO events (created_at, type, source, payload_json) VALUES (?, ?, ?, ?)",
            ("2026-06-30T00:00:00Z", "fixture", "task3", "{}"),
        )
        connection.commit()
    finally:
        connection.close()
    return path


def _create_memory_fixture(path: Path) -> Path:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(MEMORY_SCHEMA_SQL.read_text(encoding="utf-8"))
        connection.commit()
    finally:
        connection.close()
    return path


def _table_rows(path: Path, table: str) -> int:
    connection = sqlite3.connect(path)
    try:
        return int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
    finally:
        connection.close()


def test_dan_db_evolves_from_jarvis_snapshot_and_maps_every_real_memory_table(
    tmp_path: Path,
) -> None:
    from jarvis.migration.legacy_data import migrate_databases

    jarvis = _create_jarvis_fixture(tmp_path / "jarvis.db")
    memory = _create_memory_fixture(tmp_path / "memory.db")
    target, report = migrate_databases(jarvis, memory, tmp_path / "dan.db")

    assert report.backup is not None
    assert report.backup.source_counts == report.backup.destination_counts
    assert report.jarvis_rows_preserved is True
    assert (report.memory.imported, report.memory.merged, report.memory.rejected) == (6, 1, 0)
    assert _table_rows(target, "conversations") == 1
    assert _table_rows(target, "turns") == 2
    assert _table_rows(target, "memory_blocks") == 4

    connection = sqlite3.connect(target)
    try:
        rows = connection.execute(
            "SELECT source_table, outcome, COUNT(*) FROM migration_record_map "
            "GROUP BY source_table, outcome ORDER BY source_table, outcome"
        ).fetchall()
        assert rows == [
            ("compiled_contexts", "imported", 1),
            ("conversations", "imported", 1),
            ("memory_blocks", "imported", 1),
            ("memory_blocks", "merged", 1),
            ("memory_inbox", "imported", 1),
            ("turns", "imported", 2),
        ]
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        connection.close()

    from jarvis.migration.db_report import render_database_migration_report

    assert render_database_migration_report(report)["memory"]["classes"] == [
        {
            "source_table": "compiled_contexts",
            "outcome": "imported",
            "reason": None,
            "count": 1,
        },
        {"source_table": "conversations", "outcome": "imported", "reason": None, "count": 1},
        {"source_table": "memory_blocks", "outcome": "imported", "reason": None, "count": 1},
        {
            "source_table": "memory_blocks",
            "outcome": "merged",
            "reason": "equivalent Jarvis memory block already exists",
            "count": 1,
        },
        {"source_table": "memory_inbox", "outcome": "imported", "reason": None, "count": 1},
        {"source_table": "turns", "outcome": "imported", "reason": None, "count": 2},
    ]


def test_reimporting_the_same_backup_snapshot_is_idempotent(tmp_path: Path) -> None:
    from jarvis.migration.legacy_data import migrate_databases

    jarvis = _create_jarvis_fixture(tmp_path / "jarvis.db")
    memory = _create_memory_fixture(tmp_path / "memory.db")
    target, first = migrate_databases(jarvis, memory, tmp_path / "dan.db")
    _, second = migrate_databases(jarvis, memory, target)

    assert (first.memory.imported, first.memory.merged, first.memory.rejected) == (6, 1, 0)
    assert (second.memory.imported, second.memory.merged, second.memory.rejected) == (0, 0, 0)
    assert _table_rows(target, "migration_sources") == 2
    assert _table_rows(target, "migration_record_map") == 7


@pytest.mark.parametrize(
    ("column", "value", "expected"),
    [
        ("kind", "preference", "preference"),
        ("priority", 7, 7),
        ("active", 0, 0),
        ("metadata", '{"scope":"legacy"}', '{"scope":"legacy"}'),
    ],
)
def test_same_title_and_body_only_merge_when_all_semantics_match(
    tmp_path: Path,
    column: str,
    value: object,
    expected: object,
) -> None:
    from jarvis.migration.legacy_data import migrate_databases

    jarvis = _create_jarvis_fixture(tmp_path / "jarvis.db")
    memory = _create_memory_fixture(tmp_path / "memory.db")
    connection = sqlite3.connect(memory)
    try:
        connection.execute(f'UPDATE memory_blocks SET "{column}" = ? WHERE id = ?', (value, "memory-2"))
        connection.commit()
    finally:
        connection.close()

    target, report = migrate_databases(jarvis, memory, tmp_path / "dan.db")

    assert (report.memory.imported, report.memory.merged, report.memory.rejected) == (7, 0, 0)
    connection = sqlite3.connect(target)
    try:
        imported = connection.execute(
            "SELECT kind, priority, active, metadata_json FROM memory_blocks "
            "WHERE metadata_json LIKE ?",
            ('%\"legacy_record_id\":\"memory-2\"%',),
        ).fetchone()
        mapping = connection.execute(
            "SELECT outcome FROM migration_record_map "
            "WHERE source_table = 'memory_blocks' AND source_record_id = 'memory-2'"
        ).fetchone()
    finally:
        connection.close()

    assert imported is not None
    imported_values = {
        "kind": imported[0],
        "priority": imported[1],
        "active": imported[2],
        "metadata": json.loads(imported[3])["legacy_metadata"],
    }
    assert imported_values[column] == expected
    assert mapping == ("imported",)


def test_fractional_source_timestamps_preserve_microseconds(tmp_path: Path) -> None:
    from jarvis.migration.legacy_data import migrate_databases

    memory = _create_memory_fixture(tmp_path / "memory.db")
    connection = sqlite3.connect(memory)
    try:
        connection.execute(
            "UPDATE memory_blocks SET created_at = ?, updated_at = ? WHERE id = 'memory-1'",
            (1720051203.123456, 1720051204.654321),
        )
        connection.commit()
    finally:
        connection.close()

    target, _ = migrate_databases(
        _create_jarvis_fixture(tmp_path / "jarvis.db"), memory, tmp_path / "dan.db"
    )
    connection = sqlite3.connect(target)
    try:
        timestamps = connection.execute(
            "SELECT created_at, updated_at FROM memory_blocks WHERE metadata_json LIKE ?",
            ('%\"legacy_record_id\":\"memory-1\"%',),
        ).fetchone()
    finally:
        connection.close()

    assert timestamps == ("2024-07-04T00:00:03.123456Z", "2024-07-04T00:00:04.654321Z")


def test_existing_target_with_active_immediate_writer_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import jarvis.migration.sqlite_backup as sqlite_backup
    from jarvis.migration.legacy_data import migrate_databases

    jarvis = _create_jarvis_fixture(tmp_path / "jarvis.db")
    memory = _create_memory_fixture(tmp_path / "memory.db")
    target, _ = migrate_databases(jarvis, memory, tmp_path / "dan.db")
    writer = sqlite3.connect(target)
    writer.execute("BEGIN IMMEDIATE")

    def fake_handles(path: Path) -> list[sqlite_backup.DatabaseHandle]:
        if Path(path).resolve() == target.resolve():
            return [sqlite_backup.DatabaseHandle(pid=777, command="python")]
        return []

    monkeypatch.setattr(sqlite_backup, "_lsof_handles", fake_handles)
    try:
        with pytest.raises(sqlite_backup.ActiveWriterError, match="777:python"):
            migrate_databases(jarvis, memory, target)
    finally:
        writer.rollback()
        writer.close()


def test_orphaned_compiled_context_is_rejected_with_auditable_reason(tmp_path: Path) -> None:
    from jarvis.migration.legacy_data import migrate_databases

    memory = _create_memory_fixture(tmp_path / "memory.db")
    connection = sqlite3.connect(memory)
    try:
        connection.execute(
            "UPDATE compiled_contexts SET conversation_id = 'missing-conversation' "
            "WHERE id = 'context-1'"
        )
        connection.commit()
    finally:
        connection.close()

    target, report = migrate_databases(
        _create_jarvis_fixture(tmp_path / "jarvis.db"), memory, tmp_path / "dan.db"
    )
    connection = sqlite3.connect(target)
    try:
        mapping = connection.execute(
            "SELECT target_table, target_record_id, outcome, reason "
            "FROM migration_record_map "
            "WHERE source_table = 'compiled_contexts' AND source_record_id = 'context-1'"
        ).fetchone()
    finally:
        connection.close()

    assert (report.memory.imported, report.memory.merged, report.memory.rejected) == (5, 1, 1)
    assert mapping == (None, None, "rejected", "missing legacy conversation")


def test_unknown_or_drifted_memory_schema_fails_before_creating_target(tmp_path: Path) -> None:
    from jarvis.migration.legacy_data import migrate_databases

    memory = _create_memory_fixture(tmp_path / "memory.db")
    connection = sqlite3.connect(memory)
    try:
        connection.execute("ALTER TABLE memory_inbox ADD COLUMN surprise TEXT")
        connection.commit()
    finally:
        connection.close()

    target = tmp_path / "dan.db"
    with pytest.raises(ValueError, match="unsupported legacy memory schema"):
        migrate_databases(_create_jarvis_fixture(tmp_path / "jarvis.db"), memory, target)
    assert not target.exists()


def test_migration_refuses_target_equal_to_a_source(tmp_path: Path) -> None:
    from jarvis.migration.legacy_data import migrate_databases

    jarvis = _create_jarvis_fixture(tmp_path / "jarvis.db")
    with pytest.raises(ValueError, match="distinct database paths"):
        migrate_databases(jarvis, _create_memory_fixture(tmp_path / "memory.db"), jarvis)


def test_sanitized_report_excludes_paths_and_imported_private_text() -> None:
    from jarvis.migration.db_report import render_database_migration_report
    from jarvis.migration.legacy_data import DatabaseMigrationReport, MemoryMigrationReport

    report = DatabaseMigrationReport(
        backup=BackupReport(
            source="/private/secret/jarvis.db",
            destination="/private/secret/dan.db",
            checkpoint=(0, 0, 0),
            integrity="ok",
            source_counts={"events": 1},
            destination_counts={"events": 1},
            sha256="a" * 64,
        ),
        jarvis_rows_preserved=True,
        memory=MemoryMigrationReport(imported=6, merged=1, rejected=0),
    )
    rendered = render_database_migration_report(report)

    assert rendered["memory"] == {
        "imported": 6,
        "merged": 1,
        "rejected": 0,
        "classes": [],
    }
    assert "/private/secret" not in str(rendered)
    assert "fixture user turn" not in str(rendered)
