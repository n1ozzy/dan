"""Idempotent SQLite migrations for Jarvis."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path


LATEST_SCHEMA_VERSION = 1
SCHEMA_PATH = Path(__file__).with_name("schema.sql")
INITIAL_SCHEMA_DESCRIPTION = "initial Jarvis v4.1 schema"


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
            (LATEST_SCHEMA_VERSION, _utc_now_iso(), INITIAL_SCHEMA_DESCRIPTION),
        )


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
