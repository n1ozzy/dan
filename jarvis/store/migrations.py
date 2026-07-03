"""Idempotent SQLite migrations for Jarvis."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path


INITIAL_SCHEMA_VERSION = 1
LATEST_SCHEMA_VERSION = 2
SCHEMA_PATH = Path(__file__).with_name("schema.sql")
INITIAL_SCHEMA_DESCRIPTION = "initial Jarvis v4.1 schema"
V2_DESCRIPTION = "FIX-09 voice_queue.spoken_at + cancelled_turns tombstone"


def apply_migrations(conn: sqlite3.Connection) -> None:
    """Apply every pending migration without deleting existing data."""

    current_version = get_applied_schema_version(conn)
    if current_version > LATEST_SCHEMA_VERSION:
        raise RuntimeError(
            f"Database schema version {current_version} is newer than supported "
            f"version {LATEST_SCHEMA_VERSION}"
        )

    if current_version < 1:
        _apply_initial_schema(conn)
    if current_version < 2:
        _apply_v2_voice_cancellation(conn)


def ensure_schema(conn: sqlite3.Connection) -> None:
    apply_migrations(conn)


def get_applied_schema_version(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    except sqlite3.OperationalError:
        return 0
    if row is None or row[0] is None:
        return 0
    return int(row[0])


def _apply_initial_schema(conn: sqlite3.Connection) -> None:
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    with conn:
        conn.executescript(schema_sql)
        conn.execute(
            """
            INSERT OR IGNORE INTO schema_version (version, applied_at, description)
            VALUES (?, ?, ?)
            """,
            (INITIAL_SCHEMA_VERSION, _utc_now_iso(), INITIAL_SCHEMA_DESCRIPTION),
        )


def _apply_v2_voice_cancellation(conn: sqlite3.Connection) -> None:
    """FIX-09: add voice_queue.spoken_at and the cancelled_turns tombstone.

    Idempotent by construction: the column is added only when absent (a fresh
    DB already has it from schema.sql) and the tombstone table/index use
    CREATE ... IF NOT EXISTS. Existing user data is never touched."""

    with conn:
        if not _column_exists(conn, "voice_queue", "spoken_at"):
            conn.execute("ALTER TABLE voice_queue ADD COLUMN spoken_at TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_voice_queue_spoken_at "
            "ON voice_queue(spoken_at)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cancelled_turns (
              turn_id TEXT PRIMARY KEY,
              cancelled_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO schema_version (version, applied_at, description)
            VALUES (?, ?, ?)
            """,
            (2, _utc_now_iso(), V2_DESCRIPTION),
        )


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(row[1] == column for row in conn.execute(f"PRAGMA table_info({table})"))


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
