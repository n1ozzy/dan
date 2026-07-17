"""Prompt 03 SQLite schema and migration tests."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from jarvis.store.db import (
    close_quietly,
    connect_db,
    get_schema_version,
    initialize_database,
    table_names,
)
from jarvis.store.migrations import LATEST_SCHEMA_VERSION, apply_migrations

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_SQL = ROOT / "jarvis" / "store" / "schema.sql"

MEMORY_OS_TABLES = {
    "memory_observations",
    "memory_candidates",
    "memory_items",
    "memory_evidence",
    "memory_topics",
    "memory_usage_events",
    "memory_review_decisions",
}

REQUIRED_TABLES = {
    "schema_version",
    "events",
    "conversations",
    "turns",
    "memory_blocks",
    "memory_archive_documents",
    "memory_archive_sync_state",
    "memory_archive_fts",
    "settings",
    "worker_jobs",
    "tool_runs",
    "approvals",
    "voice_queue",
    "cancelled_turns",
    "listening_leases",
    "audio_device_snapshots",
    "runtime_process_observations",
    "migration_sources",
    "migration_record_map",
} | MEMORY_OS_TABLES

REQUIRED_INDEXES = {
    "idx_events_created_at",
    "idx_events_type",
    "idx_events_correlation_id",
    "idx_events_turn_id",
    "idx_turns_conversation_id",
    "idx_turns_created_at",
    "idx_turns_status",
    "idx_memory_blocks_kind",
    "idx_memory_blocks_active",
    "idx_memory_blocks_priority",
    "idx_memory_archive_documents_source",
    "idx_worker_jobs_status",
    "idx_worker_jobs_created_at",
    "idx_worker_jobs_worker_kind",
    "idx_tool_runs_turn_id",
    "idx_tool_runs_status",
    "idx_tool_runs_tool_name",
    "idx_approvals_status",
    "idx_approvals_created_at",
    "idx_approvals_risk",
    "idx_voice_queue_status",
    "idx_voice_queue_priority",
    "idx_voice_queue_turn_id",
    "idx_listening_leases_status",
    "idx_listening_leases_expires_at",
    "idx_listening_leases_turn_id",
    "idx_audio_device_snapshots_created_at",
    "idx_runtime_process_observations_created_at",
    "idx_runtime_process_observations_kind",
    "idx_runtime_process_observations_status",
    "idx_runtime_process_observations_risk",
    "idx_memory_candidates_status",
    "idx_memory_candidates_namespace",
    "idx_memory_items_status",
    "idx_memory_items_namespace",
    "idx_memory_evidence_memory_id",
    "idx_memory_evidence_candidate_id",
    "idx_memory_usage_events_turn_id",
    "idx_migration_sources_source_sha256",
}

CRITICAL_COLUMNS = {
    "events": {
        "id",
        "created_at",
        "type",
        "source",
        "correlation_id",
        "turn_id",
        "payload_json",
    },
    "turns": {
        "id",
        "conversation_id",
        "created_at",
        "updated_at",
        "source",
        "status",
        "input_text",
        "final_text",
        "brain_adapter",
        "brain_model",
        "context_snapshot_json",
        "error",
        "metadata_json",
    },
    "memory_blocks": {
        "id",
        "kind",
        "title",
        "body",
        "priority",
        "active",
        "created_at",
        "updated_at",
        "source_event_id",
        "metadata_json",
    },
    "memory_archive_documents": {
        "canonical_id",
        "source_type",
        "source_uri",
        "source_item_id",
        "title",
        "content",
        "content_hash",
        "source_updated_at",
        "metadata_json",
        "created_at",
        "updated_at",
    },
    "memory_archive_sync_state": {
        "source_type",
        "source_uri",
        "cursor",
        "fingerprint",
        "synced_at",
        "metadata_json",
    },
    "approvals": {
        "id",
        "created_at",
        "decided_at",
        "status",
        "risk",
        "requested_by",
        "action_type",
        "payload_json",
        "decision_reason",
        "metadata_json",
    },
    "voice_queue": {
        "id",
        "created_at",
        "updated_at",
        "turn_id",
        "text",
        "priority",
        "voice_id",
        "interrupt_policy",
        "status",
        "error",
        "metadata_json",
        "spoken_at",
    },
    "cancelled_turns": {
        "turn_id",
        "cancelled_at",
    },
    "listening_leases": {
        "id",
        "created_at",
        "updated_at",
        "released_at",
        "expires_at",
        "source",
        "mode",
        "status",
        "owner_process",
        "turn_id",
        "metadata_json",
    },
    "audio_device_snapshots": {
        "id",
        "created_at",
        "input_device_name",
        "input_device_uid",
        "output_device_name",
        "output_device_uid",
        "preferred_input",
        "output_policy",
        "bluetooth_microphone_allowed",
        "warning",
        "raw_json",
    },
    "runtime_process_observations": {
        "id",
        "created_at",
        "label",
        "pid",
        "process_name",
        "command",
        "kind",
        "status",
        "risk",
        "details_json",
    },
    "memory_observations": {
        "id",
        "source_type",
        "source_id",
        "conversation_id",
        "turn_id",
        "event_id",
        "observed_text",
        "detected_kind",
        "sensitivity",
        "created_at",
    },
    "memory_candidates": {
        "id",
        "candidate_kind",
        "scope",
        "namespace",
        "claim",
        "title",
        "reason",
        "confidence",
        "sensitivity",
        "recommended_action",
        "target_memory_id",
        "status",
        "created_at",
        "reviewed_at",
    },
    "memory_items": {
        "id",
        "canonical_key",
        "kind",
        "scope",
        "namespace",
        "title",
        "claim",
        "content",
        "status",
        "confidence",
        "sensitivity",
        "source_policy",
        "created_at",
        "updated_at",
        "last_used_at",
        "last_confirmed_at",
        "supersedes",
        "superseded_by",
    },
    "memory_evidence": {
        "id",
        "memory_id",
        "candidate_id",
        "observation_id",
        "conversation_id",
        "turn_id",
        "event_id",
        "quote",
        "weight",
        "created_at",
    },
    "memory_topics": {
        "id",
        "namespace",
        "title",
        "summary",
        "status",
        "last_consolidated_at",
        "token_estimate",
        "created_at",
        "updated_at",
    },
    "memory_usage_events": {
        "id",
        "memory_id",
        "turn_id",
        "reason",
        "rank",
        "included",
        "created_at",
    },
    "memory_review_decisions": {
        "id",
        "candidate_id",
        "decision",
        "edited_claim",
        "reason",
        "created_at",
    },
    "migration_sources": {
        "id", "source_path_hash", "source_schema", "imported_at", "source_sha256",
    },
    "migration_record_map": {
        "source_id", "source_table", "source_record_id", "target_table",
        "target_record_id", "outcome", "reason",
    },
}


def config_text(db_path: Path) -> str:
    return f"""
[daemon]
name = "jarvisd"
host = "127.0.0.1"
port = 41741
log_level = "INFO"

[database]
path = "{db_path}"
migrations = "manual"
destroy_existing = false

[brain]
default_adapter = "mock"
default_model = "mock-local"
timeout_seconds = 60
context_budget_chars = 24000
provider_sessions_are_memory = false

[memory]
enabled = true
max_active_blocks = 50
max_context_chars = 12000
worker_candidates_require_promotion = true

[voice]
enabled = false
speak_responses = false
broker_enabled = false
default_tts = "mock"
default_stt = "mock"
ptt_mode = "hold"
queue_persisted = true

[audio]
enabled = false
input_policy = "pin_builtin_mic"
preferred_input = "Mikrofon (MacBook Air)"
output_policy = "follow_system_default"
allow_bluetooth_microphone = false
always_listen_enabled = false

[panel]
enabled = false
api_base_url = "http://127.0.0.1:41741"
width = 420
height = 620

[security]
localhost_only = true
require_approval_for_shell = true
require_approval_for_file_write = true
require_approval_for_network = true
destructive_tools_enabled = false

[runtime]
home = "{db_path.parent}"
logs_dir = "{db_path.parent / "logs"}"
runtime_dir = "{db_path.parent / "runtime"}"
pid_file = "{db_path.parent / "runtime" / "jarvisd.pid"}"
legacy_detection = "report_only"

[launchd]
enabled = false
label = "com.ozzy.jarvisd"
install_automatically = false
"""


def write_config(path: Path, db_path: Path) -> Path:
    path.write_text(config_text(db_path), encoding="utf-8")
    return path


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("JARVIS_CONFIG", None)
    env["PYTHONPATH"] = str(ROOT)
    return subprocess.run(
        [sys.executable, "-m", "jarvis.cli", *args],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _remove_memory_os_v1_schema(conn: sqlite3.Connection) -> None:
    for index_name in REQUIRED_INDEXES:
        if index_name.startswith("idx_memory_"):
            conn.execute(f"DROP INDEX IF EXISTS {index_name}")
    for table_name in MEMORY_OS_TABLES:
        conn.execute(f"DROP TABLE IF EXISTS {table_name}")
    conn.commit()


def _has_unique_index_on(conn: sqlite3.Connection, table: str, column: str) -> bool:
    for index_row in conn.execute(f"PRAGMA index_list({table})"):
        index_name = index_row[1]
        is_unique = index_row[2] == 1
        if not is_unique:
            continue
        indexed_columns = {
            column_row[2] for column_row in conn.execute(f"PRAGMA index_info({index_name})")
        }
        if indexed_columns == {column}:
            return True
    return False


def test_initialize_database_creates_db_at_tmp_path(tmp_path: Path) -> None:
    db_path = tmp_path / "jarvis.db"
    conn = initialize_database(db_path)
    close_quietly(conn)

    assert db_path.is_file()


def test_initialize_database_creates_only_expected_parent_directory(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime-home" / "data" / "jarvis.db"

    conn = initialize_database(db_path)
    close_quietly(conn)

    actual_dirs = {path.relative_to(tmp_path) for path in tmp_path.rglob("*") if path.is_dir()}
    assert actual_dirs == {Path("runtime-home"), Path("runtime-home/data")}


def test_applying_migrations_twice_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "jarvis.db"
    conn = initialize_database(db_path)

    apply_migrations(conn)
    apply_migrations(conn)

    # schema_version is an append log: one row per applied version, and a
    # re-run adds no duplicates (FIX-09 bumped the schema to v2).
    version_rows = conn.execute(
        "SELECT version FROM schema_version ORDER BY version"
    ).fetchall()
    assert version_rows == [(version,) for version in range(1, LATEST_SCHEMA_VERSION + 1)]
    close_quietly(conn)


def test_migration_lineage_is_the_v4_core_schema_bump() -> None:
    assert LATEST_SCHEMA_VERSION == 4


def test_schema_sql_declares_memory_os_v1_tables() -> None:
    schema = SCHEMA_SQL.read_text(encoding="utf-8")

    missing = [
        table
        for table in MEMORY_OS_TABLES
        if f"CREATE TABLE IF NOT EXISTS {table}" not in schema
    ]

    assert missing == []


def test_sidecar_migration_creates_memory_os_tables_for_preexisting_v2_database(
    tmp_path: Path,
) -> None:
    conn = initialize_database(tmp_path / "jarvis.db")
    _remove_memory_os_v1_schema(conn)

    apply_migrations(conn)

    assert MEMORY_OS_TABLES.issubset(table_names(conn))
    assert get_schema_version(conn) == LATEST_SCHEMA_VERSION
    assert conn.execute("SELECT COUNT(*) FROM schema_version WHERE version = 4").fetchone()[0] == 1
    close_quietly(conn)


def test_v4_migration_creates_lineage_tables_for_preexisting_v3_database(
    tmp_path: Path,
) -> None:
    conn = initialize_database(tmp_path / "jarvis.db")
    conn.execute("DROP TABLE migration_record_map")
    conn.execute("DROP TABLE migration_sources")
    conn.execute("DELETE FROM schema_version WHERE version = 4")
    conn.commit()

    apply_migrations(conn)

    assert {"migration_sources", "migration_record_map"}.issubset(table_names(conn))
    assert get_schema_version(conn) == 4
    close_quietly(conn)


def test_sidecar_migration_does_not_migrate_existing_memory_blocks_data(
    tmp_path: Path,
) -> None:
    conn = initialize_database(tmp_path / "jarvis.db")
    _remove_memory_os_v1_schema(conn)
    conn.execute(
        """
        INSERT INTO memory_blocks (
          id, kind, title, body, priority, active, created_at, updated_at, metadata_json
        )
        VALUES (
          'memory-old',
          'preference',
          'Old memory',
          'Keep this v0 row untouched.',
          7,
          1,
          '2026-07-04T00:00:00Z',
          '2026-07-04T00:00:00Z',
          '{"origin":"v0"}'
        )
        """
    )
    conn.commit()

    apply_migrations(conn)

    old_row = conn.execute(
        """
        SELECT id, kind, title, body, priority, active, metadata_json
        FROM memory_blocks
        WHERE id = 'memory-old'
        """
    ).fetchone()
    memory_items_count = conn.execute("SELECT COUNT(*) FROM memory_items").fetchone()[0]

    assert old_row == (
        "memory-old",
        "preference",
        "Old memory",
        "Keep this v0 row untouched.",
        7,
        1,
        '{"origin":"v0"}',
    )
    assert memory_items_count == 0
    close_quietly(conn)


def test_migration_adds_spoken_at_to_a_preexisting_v1_voice_queue(tmp_path: Path) -> None:
    # An existing v1 database (voice_queue without spoken_at) must gain the
    # column through the idempotent v2 migration without losing its rows.
    db_path = tmp_path / "jarvis.db"
    conn = initialize_database(db_path)
    # Roll the fixture back to a real v1 shape: no spoken_at column, no index,
    # no v2 version row (the index must go before the column it references).
    conn.execute("DROP INDEX IF EXISTS idx_voice_queue_spoken_at")
    conn.execute("ALTER TABLE voice_queue DROP COLUMN spoken_at")
    conn.execute("DELETE FROM schema_version WHERE version >= 2")
    conn.execute(
        """
        INSERT INTO voice_queue (id, created_at, updated_at, text, status, metadata_json)
        VALUES (
          'keep-me', '2026-07-03T00:00:00Z', '2026-07-03T00:00:00Z',
          'stare zdanie', 'done', '{}'
        )
        """
    )
    conn.commit()

    apply_migrations(conn)

    columns = {row[1] for row in conn.execute("PRAGMA table_info(voice_queue)")}
    assert "spoken_at" in columns
    survived = conn.execute("SELECT text FROM voice_queue WHERE id = 'keep-me'").fetchone()
    assert survived[0] == "stare zdanie"
    close_quietly(conn)


def test_get_schema_version_returns_latest_schema_version(tmp_path: Path) -> None:
    conn = initialize_database(tmp_path / "jarvis.db")

    assert get_schema_version(conn) == LATEST_SCHEMA_VERSION
    close_quietly(conn)


def test_all_required_tables_exist(tmp_path: Path) -> None:
    conn = initialize_database(tmp_path / "jarvis.db")

    assert REQUIRED_TABLES.issubset(table_names(conn))
    close_quietly(conn)


def test_required_indexes_exist(tmp_path: Path) -> None:
    conn = initialize_database(tmp_path / "jarvis.db")
    indexes = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND name NOT LIKE 'sqlite_%'"
        )
    }

    assert REQUIRED_INDEXES.issubset(indexes)
    close_quietly(conn)


def test_memory_topics_namespace_has_unique_index(tmp_path: Path) -> None:
    conn = initialize_database(tmp_path / "jarvis.db")

    assert _has_unique_index_on(conn, "memory_topics", "namespace")
    close_quietly(conn)


def test_required_columns_exist_for_critical_tables(tmp_path: Path) -> None:
    conn = initialize_database(tmp_path / "jarvis.db")

    for table, expected_columns in CRITICAL_COLUMNS.items():
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        assert expected_columns.issubset(columns), table

    close_quietly(conn)


def test_existing_user_table_and_data_survives_migration_rerun(tmp_path: Path) -> None:
    db_path = tmp_path / "jarvis.db"
    conn = initialize_database(db_path)
    conn.execute("CREATE TABLE user_owned_data (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("INSERT INTO user_owned_data (value) VALUES (?)", ("keep me",))
    conn.commit()

    apply_migrations(conn)
    apply_migrations(conn)

    value = conn.execute("SELECT value FROM user_owned_data WHERE id = 1").fetchone()[0]
    assert value == "keep me"
    close_quietly(conn)


def test_connect_db_enables_foreign_keys(tmp_path: Path) -> None:
    db_path = tmp_path / "jarvis.db"
    sqlite3.connect(db_path).close()

    conn = connect_db(db_path)

    foreign_keys_enabled = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert foreign_keys_enabled == 1
    close_quietly(conn)


def test_connect_db_uses_wal_and_busy_timeout(tmp_path: Path) -> None:
    conn = initialize_database(tmp_path / "jarvis.db")

    journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    assert journal_mode.lower() == "wal"
    assert busy_timeout >= 5000
    close_quietly(conn)


def test_db_status_cli_returns_json_and_does_not_create_database(tmp_path: Path) -> None:
    db_path = tmp_path / "missing" / "jarvis.db"
    config_path = write_config(tmp_path / "jarvis.toml", db_path)

    result = run_cli("--config", str(config_path), "db", "status")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["db_exists"] is False
    assert payload["schema_version"] == 0
    assert payload["db_path"] == str(db_path)
    assert not db_path.exists()
    assert not db_path.parent.exists()


def test_db_init_cli_initializes_temp_config_database(tmp_path: Path) -> None:
    db_path = tmp_path / "safe-home" / "jarvis.db"
    config_path = write_config(tmp_path / "jarvis.toml", db_path)

    result = run_cli("--config", str(config_path), "db", "init")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["db_exists"] is True
    assert payload["schema_version"] == LATEST_SCHEMA_VERSION
    assert db_path.exists()


def test_runtime_files_do_not_contain_forbidden_legacy_strings() -> None:
    allowed_contracts = {
        ("jarvis/brain/context_builder.py", "/Users/n1_ozzy/Documents/dev/dan"),
        ("jarvis/voice/shared_broker.py", "/tmp/dan"),
        ("jarvis/migration/test_safety.py", "/tmp/dan"),
        ("jarvis/migration/test_safety.py", "afplay"),
    }
    forbidden = (
        "/Users/n1_ozzy/Documents/dev/dan",
        "/tmp/dan",
        "afplay",
        "--dangerously-skip-permissions",
    )
    roots = (
        ROOT / "jarvis",
        ROOT / "config",
        ROOT / "scripts",
        ROOT / "launchd",
    )
    text_suffixes = {".py", ".sql", ".toml", ".md", ".sh", ".example"}
    offenders: list[tuple[str, str]] = []

    for root in roots:
        files = [path for path in root.rglob("*") if path.is_file()]
        for path in files:
            if "__pycache__" in path.parts or path.suffix not in text_suffixes:
                continue
            text = path.read_text(encoding="utf-8")
            relative = str(path.relative_to(ROOT))
            for snippet in forbidden:
                if snippet in text and (relative, snippet) not in allowed_contracts:
                    offenders.append((relative, snippet))

    assert offenders == []
