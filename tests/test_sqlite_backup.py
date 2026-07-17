"""Contracts for lossless, WAL-safe SQLite backups."""

from __future__ import annotations

import os
import sqlite3
import stat
import subprocess
from pathlib import Path

import pytest


def _create_wal_database(path: Path, *, rows: int) -> Path:
    connection = sqlite3.connect(path)
    try:
        assert connection.execute("PRAGMA journal_mode = WAL").fetchone()[0] == "wal"
        connection.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, body TEXT NOT NULL)")
        connection.executemany(
            "INSERT INTO events (body) VALUES (?)",
            [(f"committed-{index}",) for index in range(rows)],
        )
        connection.commit()
    finally:
        connection.close()
    return path


def _create_live_wal_database(path: Path, *, rows: int) -> tuple[Path, sqlite3.Connection]:
    connection = sqlite3.connect(path)
    assert connection.execute("PRAGMA journal_mode = WAL").fetchone()[0] == "wal"
    connection.execute("PRAGMA wal_autocheckpoint = 0")
    connection.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, body TEXT NOT NULL)")
    connection.executemany(
        "INSERT INTO events (body) VALUES (?)",
        [(f"committed-{index}",) for index in range(rows)],
    )
    connection.commit()
    wal = Path(f"{path}-wal")
    assert wal.is_file() and wal.stat().st_size > 0
    return path, connection


def _rows(path: Path, table: str) -> int:
    connection = sqlite3.connect(path)
    try:
        return int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
    finally:
        connection.close()


def test_backup_preserves_committed_wal_rows_and_is_owner_only(tmp_path: Path) -> None:
    from dan.migration.sqlite_backup import backup_database

    source, writer = _create_live_wal_database(tmp_path / "source.db", rows=3)
    destination = tmp_path / "backup.db"
    try:
        report = backup_database(source, destination, approved_pids={os.getpid()})
    finally:
        writer.close()

    assert report.integrity == "ok"
    assert report.source_counts == report.destination_counts == {"events": 3}
    assert _rows(destination, "events") == 3
    assert len(report.sha256) == 64
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600


def test_backup_refuses_unapproved_writer(tmp_path: Path) -> None:
    from dan.migration.sqlite_backup import (
        ActiveWriterError,
        DatabaseHandle,
        assert_quiescent_database,
    )

    source = _create_wal_database(tmp_path / "source.db", rows=0)
    with pytest.raises(ActiveWriterError, match="777:python"):
        assert_quiescent_database(source, handles=[DatabaseHandle(pid=777, command="python")])


def test_quiescence_runs_concrete_lsof_contract_and_parses_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dan.migration.sqlite_backup as sqlite_backup

    source = _create_wal_database(tmp_path / "source.db", rows=0)
    observed: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        observed.append(args)
        assert kwargs == {"capture_output": True, "check": False, "text": True}
        return subprocess.CompletedProcess(args, 0, "p777\ncpython\n", "")

    monkeypatch.setattr(sqlite_backup.subprocess, "run", fake_run)
    with pytest.raises(sqlite_backup.ActiveWriterError, match="777:python"):
        sqlite_backup.assert_quiescent_database(source)

    assert observed == [["lsof", "-Fpc", "--", str(source), f"{source}-wal", f"{source}-shm"]]


def test_backup_failure_does_not_leave_destination_or_sidecars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dan.migration.sqlite_backup as sqlite_backup

    source = _create_wal_database(tmp_path / "source.db", rows=1)
    destination = tmp_path / "backup.db"
    real_connect = sqlite_backup.sqlite3.connect

    class FailingBackupConnection:
        def __init__(self, connection: sqlite3.Connection) -> None:
            self._connection = connection

        def execute(self, *args: object, **kwargs: object) -> sqlite3.Cursor:
            return self._connection.execute(*args, **kwargs)

        def backup(self, destination_connection: sqlite3.Connection) -> None:
            del destination_connection
            raise sqlite3.OperationalError("forced backup failure")

        def close(self) -> None:
            self._connection.close()

    def failing_connect(database: object, *args: object, **kwargs: object) -> object:
        connection = real_connect(database, *args, **kwargs)
        return FailingBackupConnection(connection) if database == source else connection

    monkeypatch.setattr(sqlite_backup.sqlite3, "connect", failing_connect)
    with pytest.raises(sqlite3.OperationalError, match="forced backup failure"):
        sqlite_backup.backup_database(source, destination)

    assert not destination.exists()
    assert list(tmp_path.glob(".backup.db.*.tmp*")) == []
