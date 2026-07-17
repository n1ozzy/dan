"""Lossless SQLite backups made through SQLite's own Backup API."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import subprocess
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path


class ActiveWriterError(RuntimeError):
    """Raised when a database family has an unapproved open handle."""


@dataclass(frozen=True)
class DatabaseHandle:
    pid: int
    command: str


@dataclass(frozen=True)
class BackupReport:
    source: str
    destination: str
    checkpoint: tuple[int, int, int]
    integrity: str
    source_counts: Mapping[str, int]
    destination_counts: Mapping[str, int]
    sha256: str


def assert_quiescent_database(
    source: Path,
    *,
    handles: Sequence[DatabaseHandle] | None = None,
    approved_pids: Iterable[int] = (),
) -> None:
    """Refuse to snapshot while another process owns DB, WAL, or SHM."""

    observed = list(handles) if handles is not None else _lsof_handles(source)
    approved = set(approved_pids)
    unapproved = [handle for handle in observed if handle.pid not in approved]
    if unapproved:
        details = ", ".join(f"{handle.pid}:{handle.command}" for handle in unapproved)
        raise ActiveWriterError(f"database has unapproved open handles: {details}")


def backup_database(
    source: Path,
    destination: Path,
    *,
    approved_pids: Iterable[int] = (),
) -> BackupReport:
    """Checkpoint and copy a DB atomically without copying WAL/SHM sidecars."""

    source = Path(source)
    destination = Path(destination)
    if not source.is_file():
        raise FileNotFoundError(source)
    if destination.exists():
        raise FileExistsError(destination)

    assert_quiescent_database(source, approved_pids=approved_pids)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        source_connection = sqlite3.connect(source)
        destination_connection: sqlite3.Connection | None = None
        try:
            checkpoint_row = source_connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            checkpoint = tuple(int(value) for value in checkpoint_row or ())
            if len(checkpoint) != 3:
                raise RuntimeError("SQLite returned an invalid WAL checkpoint result")
            source_counts = _table_counts(source_connection)
            destination_connection = sqlite3.connect(temporary)
            source_connection.backup(destination_connection)
            destination_connection.commit()
        finally:
            if destination_connection is not None:
                destination_connection.close()
            source_connection.close()

        readonly = sqlite3.connect(f"{temporary.resolve().as_uri()}?mode=ro", uri=True)
        try:
            integrity = str(readonly.execute("PRAGMA integrity_check").fetchone()[0])
            destination_counts = _table_counts(readonly)
        finally:
            readonly.close()
        if integrity != "ok":
            raise RuntimeError(f"backup integrity check failed: {integrity}")
        if source_counts != destination_counts:
            raise RuntimeError("backup table counts differ from source snapshot")

        os.chmod(temporary, 0o600)
        backup_sha256 = _sha256(temporary)
        os.link(temporary, destination)
        os.chmod(destination, 0o600)
        return BackupReport(
            source=str(source),
            destination=str(destination),
            checkpoint=checkpoint,
            integrity=integrity,
            source_counts=source_counts,
            destination_counts=destination_counts,
            sha256=backup_sha256,
        )
    finally:
        _remove_temporary_database(temporary)


def _lsof_handles(source: Path) -> list[DatabaseHandle]:
    paths = [str(source), f"{source}-wal", f"{source}-shm"]
    try:
        completed = subprocess.run(
            ["lsof", "-Fpc", "--", *paths],
            capture_output=True,
            check=False,
            text=True,
        )
    except OSError as exc:
        raise RuntimeError("could not run lsof to verify database quiescence") from exc
    if completed.returncode not in (0, 1):
        raise RuntimeError(f"lsof failed while checking database handles: {completed.stderr}")
    return _parse_lsof_handles(completed.stdout)


def _parse_lsof_handles(output: str) -> list[DatabaseHandle]:
    handles: list[DatabaseHandle] = []
    pid: int | None = None
    command = "unknown"
    for line in output.splitlines():
        if line.startswith("p"):
            if pid is not None:
                handles.append(DatabaseHandle(pid=pid, command=command))
            try:
                pid = int(line[1:])
            except ValueError:
                pid = None
            command = "unknown"
        elif line.startswith("c") and pid is not None:
            command = line[1:] or "unknown"
    if pid is not None:
        handles.append(DatabaseHandle(pid=pid, command=command))
    return handles


def _table_counts(connection: sqlite3.Connection) -> dict[str, int]:
    names = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {
        str(row[0]): int(
            connection.execute(
                f'SELECT COUNT(*) FROM "{str(row[0]).replace(chr(34), chr(34) * 2)}"'
            ).fetchone()[0]
        )
        for row in names
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as database:
        for chunk in iter(lambda: database.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _remove_temporary_database(path: Path) -> None:
    for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm"), Path(f"{path}-journal")):
        candidate.unlink(missing_ok=True)
