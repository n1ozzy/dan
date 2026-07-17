"""SQLite connection helpers for DAN-owned state."""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dan.paths import RUNTIME_FILE_MODE, secure_path
from dan.store.migrations import ensure_schema, get_applied_schema_version


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
    """Connect to a SQLite database and enable DAN connection pragmas."""

    db_path = Path(path).expanduser()
    db_path.parent.mkdir(parents=True, exist_ok=True)
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
        # The DB may hold secrets in event/tool_run rows: owner-only, created
        # world-readable by sqlite otherwise (mirrors security/transport.py).
        secure_path(db_path, RUNTIME_FILE_MODE)
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


class ThreadLocalConnection:
    """A sqlite3.Connection facade that gives every thread its own connection.

    One shared connection with check_same_thread=False makes all threads join
    a single implicit transaction: one thread's rollback discards another
    thread's uncommitted writes. WAL supports concurrent writers on separate
    connections, so each thread transparently gets (and reuses) its own.

    close() may be called from any thread and closes every connection ever
    handed out (they are opened with check_same_thread=False for exactly
    this reason — each is still used by a single thread only).
    """

    def __init__(self, path: str | Path):
        self._path = Path(path).expanduser()
        self._local = threading.local()
        self._all: list[sqlite3.Connection] = []
        self._lock = threading.Lock()
        self._closed = False

    def _connection(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            return conn
        if self._closed:
            raise sqlite3.ProgrammingError("Cannot operate on a closed database.")
        try:
            conn = sqlite3.connect(self._path, check_same_thread=False)
            _configure_connection(conn)
        except (OSError, sqlite3.Error) as exc:
            raise DatabaseError(f"Could not connect to database {self._path}: {exc}") from exc
        self._local.conn = conn
        with self._lock:
            self._all.append(conn)
        return conn

    def execute(self, *args: Any, **kwargs: Any) -> sqlite3.Cursor:
        return self._connection().execute(*args, **kwargs)

    def executemany(self, *args: Any, **kwargs: Any) -> sqlite3.Cursor:
        return self._connection().executemany(*args, **kwargs)

    def executescript(self, *args: Any, **kwargs: Any) -> sqlite3.Cursor:
        return self._connection().executescript(*args, **kwargs)

    def cursor(self, *args: Any, **kwargs: Any) -> sqlite3.Cursor:
        return self._connection().cursor(*args, **kwargs)

    def commit(self) -> None:
        self._connection().commit()

    def rollback(self) -> None:
        self._connection().rollback()

    def interrupt(self) -> None:
        self._connection().interrupt()

    @property
    def in_transaction(self) -> bool:
        return self._connection().in_transaction

    def __enter__(self) -> ThreadLocalConnection:
        self._connection().__enter__()
        return self

    def __exit__(self, *exc_info: Any) -> bool:
        return bool(self._connection().__exit__(*exc_info))

    def close_current_thread(self) -> bool:
        """Close the SQLite connection cached for the calling thread, if any."""

        conn = getattr(self._local, "conn", None)
        if conn is None:
            return False
        try:
            delattr(self._local, "conn")
        except AttributeError:
            pass
        with self._lock:
            self._all = [existing for existing in self._all if existing is not conn]
        close_quietly(conn)
        return True

    def close(self) -> None:
        self._closed = True
        with self._lock:
            connections, self._all = list(self._all), []
        self._local = threading.local()
        for conn in connections:
            close_quietly(conn)
