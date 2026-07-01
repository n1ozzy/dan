"""SQLite connection helpers for Jarvis-owned state."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from jarvis.store.migrations import ensure_schema, get_applied_schema_version


class DatabaseError(Exception):
    """Raised when a database connection or schema operation fails."""


@dataclass(frozen=True)
class Database:
    path: Path

    def connect(self) -> sqlite3.Connection:
        return connect_db(self.path)

    def initialize(self) -> sqlite3.Connection:
        return initialize_database(self.path)


def connect_db(path: str | Path) -> sqlite3.Connection:
    """Connect to a SQLite database and enable Jarvis connection pragmas."""

    db_path = Path(path).expanduser()
    try:
        conn = sqlite3.connect(db_path)
        _configure_connection(conn)
        return conn
    except (OSError, sqlite3.Error) as exc:
        raise DatabaseError(f"Could not connect to database {db_path}: {exc}") from exc


def initialize_database(path: str | Path) -> sqlite3.Connection:
    """Create the DB parent if needed, connect, and apply pending migrations."""

    db_path = Path(path).expanduser()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn: sqlite3.Connection | None = None
    try:
        conn = connect_db(db_path)
        ensure_schema(conn)
        return conn
    except Exception:
        close_quietly(conn)
        raise


def get_schema_version(conn: sqlite3.Connection) -> int:
    return get_applied_schema_version(conn)


def table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {str(row[0]) for row in rows}


def close_quietly(conn: sqlite3.Connection | None) -> None:
    if conn is None:
        return
    try:
        conn.close()
    except sqlite3.Error:
        pass


def _configure_connection(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")
