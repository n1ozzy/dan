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

REQUIRED_TABLES = {
    "schema_version",
    "events",
    "conversations",
    "turns",
    "memory_blocks",
    "settings",
    "worker_jobs",
    "tool_runs",
    "approvals",
    "voice_queue",
    "listening_leases",
    "audio_device_snapshots",
    "runtime_process_observations",
}

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

    version_rows = conn.execute("SELECT version FROM schema_version").fetchall()
    assert version_rows == [(LATEST_SCHEMA_VERSION,)]
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
            for snippet in forbidden:
                if snippet in text:
                    offenders.append((str(path.relative_to(ROOT)), snippet))

    assert offenders == []
