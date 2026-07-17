"""Explicit import from the known pre-consolidation ``~/.dan/memory.db`` schema."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dan.migration.sqlite_backup import (
    BackupReport,
    assert_quiescent_database,
    backup_database,
)
from dan.store.db import initialize_database

JARVIS_SOURCE_SCHEMA = "jarvis/current"
MEMORY_SOURCE_SCHEMA = "dan-memory-v1/current"
_SOURCE_TABLES = ("conversations", "turns", "memory_blocks", "compiled_contexts", "memory_inbox")
_EXPECTED_COLUMNS: Mapping[str, tuple[tuple[str, str, int, int], ...]] = {
    "conversations": (
        ("id", "TEXT", 0, 1),
        ("title", "TEXT", 1, 0),
        ("created_at", "REAL", 1, 0),
        ("updated_at", "REAL", 1, 0),
    ),
    "turns": (
        ("id", "INTEGER", 0, 1),
        ("conversation_id", "TEXT", 1, 0),
        ("role", "TEXT", 1, 0),
        ("content", "TEXT", 1, 0),
        ("created_at", "REAL", 1, 0),
    ),
    "memory_blocks": (
        ("id", "TEXT", 0, 1),
        ("kind", "TEXT", 1, 0),
        ("title", "TEXT", 1, 0),
        ("body", "TEXT", 1, 0),
        ("priority", "INTEGER", 0, 0),
        ("created_at", "REAL", 1, 0),
        ("updated_at", "REAL", 1, 0),
        ("active", "INTEGER", 0, 0),
        ("metadata", "TEXT", 0, 0),
    ),
    "compiled_contexts": (
        ("id", "TEXT", 0, 1),
        ("conversation_id", "TEXT", 1, 0),
        ("summary", "TEXT", 1, 0),
        ("turn_range_start", "INTEGER", 1, 0),
        ("turn_range_end", "INTEGER", 1, 0),
        ("created_at", "REAL", 1, 0),
        ("char_count", "INTEGER", 1, 0),
    ),
    "memory_inbox": (
        ("id", "TEXT", 0, 1),
        ("raw_text", "TEXT", 1, 0),
        ("source", "TEXT", 1, 0),
        ("created_at", "REAL", 1, 0),
        ("processed", "INTEGER", 0, 0),
    ),
}


class SourceProvenanceError(RuntimeError):
    """Raised when an existing target is not descended from the named snapshot."""


@dataclass(frozen=True)
class MigrationOutcomeClass:
    source_table: str
    outcome: str
    reason: str | None
    count: int


@dataclass(frozen=True)
class MemoryMigrationReport:
    imported: int
    merged: int
    rejected: int
    classes: tuple[MigrationOutcomeClass, ...] = ()


@dataclass(frozen=True)
class DatabaseMigrationReport:
    backup: BackupReport | None
    jarvis_rows_preserved: bool
    memory: MemoryMigrationReport


def migrate_databases(
    jarvis_database: Path,
    memory_database: Path,
    target_database: Path,
    *,
    approved_pids: Iterable[int] = (),
) -> tuple[Path, DatabaseMigrationReport]:
    """Create/continue a target from verified backup snapshots only."""

    jarvis_database, memory_database, target_database = map(
        Path, (jarvis_database, memory_database, target_database)
    )
    _assert_distinct_database_paths(jarvis_database, memory_database, target_database)
    approved = tuple(approved_pids)
    _validate_memory_schema(memory_database)
    if target_database.exists():
        assert_quiescent_database(target_database, approved_pids=approved)
        existing_provenance = _read_target_jarvis_provenance(target_database)
    else:
        existing_provenance = None

    with tempfile.TemporaryDirectory(prefix="dan-database-migration-") as temporary_directory:
        temporary = Path(temporary_directory)
        memory_snapshot = temporary / "memory.snapshot.db"
        memory_backup = backup_database(memory_database, memory_snapshot, approved_pids=approved)
        _validate_memory_schema(memory_snapshot)

        backup: BackupReport | None
        jarvis_snapshot = temporary / "jarvis.snapshot.db"
        if existing_provenance is None:
            backup = backup_database(jarvis_database, target_database, approved_pids=approved)
            jarvis_report = backup
        else:
            jarvis_report = backup_database(
                jarvis_database, jarvis_snapshot, approved_pids=approved
            )
            _verify_target_jarvis_provenance(
                existing_provenance, jarvis_database, jarvis_report.sha256
            )
            backup = None

        target = initialize_database(target_database)
        try:
            if existing_provenance is None:
                _record_jarvis_provenance(target, jarvis_database, jarvis_report.sha256)
            memory_report = _import_memory_snapshot(
                target,
                source_path=memory_database,
                snapshot=memory_snapshot,
                source_sha256=memory_backup.sha256,
            )
            jarvis_rows_preserved = _jarvis_rows_preserved(target, jarvis_report.destination_counts)
        finally:
            target.close()

    return target_database, DatabaseMigrationReport(
        backup=backup,
        jarvis_rows_preserved=jarvis_rows_preserved,
        memory=memory_report,
    )


def _validate_memory_schema(database: Path) -> None:
    source = _readonly_connection(database)
    try:
        objects = {
            str(name): (str(kind), str(sql or ""))
            for name, kind, sql in source.execute(
                "SELECT name, type, sql FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        ignored_fts_sidecars = {name for name in objects if name.startswith("memory_fts_")}
        expected = set(_SOURCE_TABLES) | {"memory_fts"}
        actual = set(objects) - ignored_fts_sidecars
        if actual != expected:
            raise ValueError("unsupported legacy memory schema: unexpected table set")
        fts_type, fts_sql = objects["memory_fts"]
        if (
            fts_type != "table"
            or "virtual table memory_fts" not in fts_sql.lower()
            or "content='memory_blocks'" not in fts_sql.lower()
        ):
            raise ValueError(
                "unsupported legacy memory schema: memory_fts is not the known derived index"
            )
        for table, expected_columns in _EXPECTED_COLUMNS.items():
            columns = tuple(
                (str(row[1]), str(row[2]).upper(), int(row[3]), int(row[5]))
                for row in source.execute(f'PRAGMA table_xinfo("{table}")')
            )
            if columns != expected_columns:
                raise ValueError(f"unsupported legacy memory schema: columns for {table} differ")
    finally:
        source.close()


def _import_memory_snapshot(
    target: sqlite3.Connection,
    *,
    source_path: Path,
    snapshot: Path,
    source_sha256: str,
) -> MemoryMigrationReport:
    existing = target.execute(
        "SELECT id FROM migration_sources WHERE source_sha256 = ?", (source_sha256,)
    ).fetchone()
    if existing is not None:
        return MemoryMigrationReport(imported=0, merged=0, rejected=0)

    rows = _snapshot_rows(snapshot)
    source_id = f"dan-memory-v1:{source_sha256}"
    counts = {"imported": 0, "merged": 0, "rejected": 0}
    conversation_ids: dict[str, str] = {}
    with target:
        target.execute(
            """
            INSERT INTO migration_sources (
              id, source_path_hash, source_schema, imported_at, source_sha256
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                source_id,
                _path_hash(source_path),
                MEMORY_SOURCE_SCHEMA,
                _utc_now_iso(),
                source_sha256,
            ),
        )
        for row in rows["conversations"]:
            raw_id = _text(row["id"])
            target_id = _stable_id(source_id, "conversation", raw_id)
            conversation_ids[raw_id] = target_id
            target.execute(
                """
                INSERT INTO conversations (
                  id, created_at, updated_at, title, status, metadata_json
                ) VALUES (?, ?, ?, ?, 'active', ?)
                """,
                (
                    target_id,
                    _iso(row["created_at"]),
                    _iso(row["updated_at"]),
                    _text(row["title"]),
                    _metadata(source_id, "conversations", raw_id),
                ),
            )
            _record_map(
                target,
                source_id,
                "conversations",
                raw_id,
                "conversations",
                target_id,
                "imported",
                None,
            )
            counts["imported"] += 1
        for row in rows["turns"]:
            raw_id = _text(row["id"])
            conversation_id = conversation_ids.get(_text(row["conversation_id"]))
            if conversation_id is None:
                _record_map(
                    target,
                    source_id,
                    "turns",
                    raw_id,
                    None,
                    None,
                    "rejected",
                    "missing legacy conversation",
                )
                counts["rejected"] += 1
                continue
            role, content = _text(row["role"]), _text(row["content"])
            target_id = _stable_id(source_id, "turn", raw_id)
            target.execute(
                """
                INSERT INTO turns (
                  id, conversation_id, created_at, updated_at, source, status,
                  input_text, final_text,
                  brain_adapter, brain_model, context_snapshot_json, error, metadata_json
                ) VALUES (
                  ?, ?, ?, ?, 'legacy-memory-db', 'completed', ?, ?,
                  NULL, NULL, NULL, NULL, ?
                )
                """,
                (
                    target_id,
                    conversation_id,
                    _iso(row["created_at"]),
                    _iso(row["created_at"]),
                    content if role == "user" else None,
                    content if role != "user" else None,
                    _metadata(source_id, "turns", raw_id, {"role": role}),
                ),
            )
            _record_map(target, source_id, "turns", raw_id, "turns", target_id, "imported", None)
            counts["imported"] += 1
        for row in rows["memory_blocks"]:
            raw_id, title, body = _text(row["id"]), _text(row["title"]), _text(row["body"])
            if not raw_id or not body:
                _record_map(
                    target,
                    source_id,
                    "memory_blocks",
                    raw_id,
                    None,
                    None,
                    "rejected",
                    "memory block has no stable id or body",
                )
                counts["rejected"] += 1
                continue
            duplicate = _equivalent_memory_block(target, row)
            if duplicate is not None:
                _record_map(
                    target,
                    source_id,
                    "memory_blocks",
                    raw_id,
                    "memory_blocks",
                    _text(duplicate[0]),
                    "merged",
                    "equivalent Jarvis memory block already exists",
                )
                counts["merged"] += 1
                continue
            target_id = _stable_id(source_id, "memory-block", raw_id)
            target.execute(
                """
                INSERT INTO memory_blocks (
                  id, kind, title, body, priority, active, created_at, updated_at, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    target_id,
                    _text(row["kind"]),
                    title,
                    body,
                    int(row["priority"] or 0),
                    int(bool(row["active"])),
                    _iso(row["created_at"]),
                    _iso(row["updated_at"]),
                    _metadata(
                        source_id,
                        "memory_blocks",
                        raw_id,
                        {"legacy_metadata": _text(row["metadata"])},
                    ),
                ),
            )
            _record_map(
                target,
                source_id,
                "memory_blocks",
                raw_id,
                "memory_blocks",
                target_id,
                "imported",
                None,
            )
            counts["imported"] += 1
        for row in rows["compiled_contexts"]:
            raw_id, summary = _text(row["id"]), _text(row["summary"])
            if not summary:
                _record_map(
                    target,
                    source_id,
                    "compiled_contexts",
                    raw_id,
                    None,
                    None,
                    "rejected",
                    "compiled context has no summary",
                )
                counts["rejected"] += 1
                continue
            legacy_conversation_id = _text(row["conversation_id"])
            if legacy_conversation_id not in conversation_ids:
                _record_map(
                    target,
                    source_id,
                    "compiled_contexts",
                    raw_id,
                    None,
                    None,
                    "rejected",
                    "missing legacy conversation",
                )
                counts["rejected"] += 1
                continue
            target_id = _stable_id(source_id, "compiled-context", raw_id)
            target.execute(
                """
                INSERT INTO memory_blocks (
                  id, kind, title, body, priority, active, created_at, updated_at, metadata_json
                )
                VALUES (?, 'legacy_compiled_context', 'Legacy compiled context', ?, 0, 1, ?, ?, ?)
                """,
                (
                    target_id,
                    summary,
                    _iso(row["created_at"]),
                    _iso(row["created_at"]),
                    _metadata(
                        source_id,
                        "compiled_contexts",
                        raw_id,
                        {
                            "legacy_conversation_id": legacy_conversation_id,
                            "target_conversation_id": conversation_ids[legacy_conversation_id],
                            "turn_range_start": int(row["turn_range_start"]),
                            "turn_range_end": int(row["turn_range_end"]),
                            "char_count": int(row["char_count"]),
                        },
                    ),
                ),
            )
            _record_map(
                target,
                source_id,
                "compiled_contexts",
                raw_id,
                "memory_blocks",
                target_id,
                "imported",
                None,
            )
            counts["imported"] += 1
        for row in rows["memory_inbox"]:
            raw_id, body = _text(row["id"]), _text(row["raw_text"])
            if not body:
                _record_map(
                    target,
                    source_id,
                    "memory_inbox",
                    raw_id,
                    None,
                    None,
                    "rejected",
                    "memory inbox row has no text",
                )
                counts["rejected"] += 1
                continue
            target_id = _stable_id(source_id, "memory-inbox", raw_id)
            target.execute(
                """
                INSERT INTO memory_blocks (
                  id, kind, title, body, priority, active, created_at, updated_at, metadata_json
                )
                VALUES (?, 'legacy_memory_inbox', 'Legacy memory inbox', ?, 0, ?, ?, ?, ?)
                """,
                (
                    target_id,
                    body,
                    int(not bool(row["processed"])),
                    _iso(row["created_at"]),
                    _iso(row["created_at"]),
                    _metadata(
                        source_id,
                        "memory_inbox",
                        raw_id,
                        {
                            "legacy_source": _text(row["source"]),
                            "processed": int(bool(row["processed"])),
                        },
                    ),
                ),
            )
            _record_map(
                target,
                source_id,
                "memory_inbox",
                raw_id,
                "memory_blocks",
                target_id,
                "imported",
                None,
            )
            counts["imported"] += 1
    return MemoryMigrationReport(**counts, classes=_outcome_classes(target, source_id))


def _equivalent_memory_block(
    target: sqlite3.Connection, source_row: sqlite3.Row
) -> sqlite3.Row | tuple[Any, ...] | None:
    candidates = target.execute(
        "SELECT id, kind, priority, active, metadata_json FROM memory_blocks "
        "WHERE title = ? AND body = ? ORDER BY id",
        (_text(source_row["title"]), _text(source_row["body"])),
    ).fetchall()
    source_semantics = (
        _text(source_row["kind"]),
        int(source_row["priority"] or 0),
        int(bool(source_row["active"])),
        _normalized_json(source_row["metadata"]),
    )
    for candidate in candidates:
        target_semantics = (
            _text(candidate[1]),
            int(candidate[2]),
            int(bool(candidate[3])),
            _normalized_json(candidate[4]),
        )
        if target_semantics == source_semantics:
            return candidate
    return None


def _normalized_json(value: Any) -> tuple[str, Any]:
    raw = _text(value)
    try:
        return "json", _canonical_json_value(json.loads(raw))
    except (json.JSONDecodeError, TypeError):
        return "raw", raw


def _canonical_json_value(value: Any) -> tuple[Any, ...]:
    if value is None:
        return ("null",)
    if isinstance(value, bool):
        return "boolean", value
    if isinstance(value, (int, float)):
        return "number", value
    if isinstance(value, str):
        return "string", value
    if isinstance(value, list):
        return "array", tuple(_canonical_json_value(item) for item in value)
    if isinstance(value, dict):
        return "object", tuple(
            (key, _canonical_json_value(item)) for key, item in sorted(value.items())
        )
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


def _outcome_classes(
    target: sqlite3.Connection, source_id: str
) -> tuple[MigrationOutcomeClass, ...]:
    rows = target.execute(
        "SELECT source_table, outcome, reason, COUNT(*) "
        "FROM migration_record_map WHERE source_id = ? "
        "GROUP BY source_table, outcome, reason "
        "ORDER BY source_table, outcome, reason",
        (source_id,),
    ).fetchall()
    return tuple(
        MigrationOutcomeClass(
            source_table=_text(row[0]),
            outcome=_text(row[1]),
            reason=None if row[2] is None else _text(row[2]),
            count=int(row[3]),
        )
        for row in rows
    )


def _snapshot_rows(snapshot: Path) -> dict[str, list[sqlite3.Row]]:
    source = _readonly_connection(snapshot)
    source.row_factory = sqlite3.Row
    try:
        return {
            table: list(source.execute(f'SELECT * FROM "{table}" ORDER BY rowid'))
            for table in _SOURCE_TABLES
        }
    finally:
        source.close()


def _record_map(
    target: sqlite3.Connection,
    source_id: str,
    source_table: str,
    source_record_id: str,
    target_table: str | None,
    target_record_id: str | None,
    outcome: str,
    reason: str | None,
) -> None:
    target.execute(
        """INSERT INTO migration_record_map (
             source_id, source_table, source_record_id, target_table,
             target_record_id, outcome, reason
           ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            source_id,
            source_table,
            source_record_id,
            target_table,
            target_record_id,
            outcome,
            reason,
        ),
    )


def _record_jarvis_provenance(target: sqlite3.Connection, source: Path, sha256: str) -> None:
    with target:
        target.execute(
            """INSERT INTO migration_sources (
                 id, source_path_hash, source_schema, imported_at, source_sha256
               )
               VALUES (?, ?, ?, ?, ?)""",
            (f"jarvis:{sha256}", _path_hash(source), JARVIS_SOURCE_SCHEMA, _utc_now_iso(), sha256),
        )


def _read_target_jarvis_provenance(target: Path) -> tuple[str, str]:
    try:
        connection = _readonly_connection(target)
        try:
            rows = connection.execute(
                "SELECT source_path_hash, source_sha256 FROM migration_sources "
                "WHERE source_schema = ?",
                (JARVIS_SOURCE_SCHEMA,),
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise SourceProvenanceError(
            "existing target has no verified Jarvis source provenance"
        ) from exc
    if len(rows) != 1:
        raise SourceProvenanceError(
            "existing target has no unique verified Jarvis source provenance"
        )
    return _text(rows[0][0]), _text(rows[0][1])


def _verify_target_jarvis_provenance(existing: tuple[str, str], source: Path, sha256: str) -> None:
    if existing != (_path_hash(source), sha256):
        raise SourceProvenanceError(
            "existing target belongs to a different Jarvis source or snapshot"
        )


def _jarvis_rows_preserved(target: sqlite3.Connection, snapshot_counts: Mapping[str, int]) -> bool:
    current = _table_counts(target)
    return all(current.get(table, 0) >= count for table, count in snapshot_counts.items())


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


def _readonly_connection(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def _assert_distinct_database_paths(*paths: Path) -> None:
    resolved = [path.resolve() for path in paths]
    if len(set(resolved)) != len(resolved):
        raise ValueError("migration requires distinct database paths")
    for index, left in enumerate(paths):
        for right in paths[index + 1 :]:
            if left.exists() and right.exists() and os.path.samefile(left, right):
                raise ValueError("migration requires distinct database paths")


def _stable_id(source_id: str, table: str, raw_id: str) -> str:
    digest = hashlib.sha256(f"{source_id}\x00{table}\x00{raw_id}".encode()).hexdigest()
    return f"legacy-{table}-{digest[:32]}"


def _metadata(
    source_id: str, table: str, record_id: str, extra: Mapping[str, Any] | None = None
) -> str:
    payload: dict[str, Any] = {
        "migration_source_id": source_id,
        "legacy_table": table,
        "legacy_record_id": record_id,
    }
    if extra:
        payload.update(extra)
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def _path_hash(path: Path) -> str:
    return hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()


def _iso(value: Any) -> str:
    return (
        datetime.fromtimestamp(float(value), UTC)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
