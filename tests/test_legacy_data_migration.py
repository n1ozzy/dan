"""Contracts for explicit, idempotent import of the real legacy memory schema."""

from __future__ import annotations

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

    assert rendered["memory"] == {"imported": 6, "merged": 1, "rejected": 0}
    assert "/private/secret" not in str(rendered)
    assert "fixture user turn" not in str(rendered)
