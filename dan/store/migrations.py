"""Idempotent SQLite migrations for DAN."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

INITIAL_SCHEMA_VERSION = 1
LATEST_SCHEMA_VERSION = 5
SCHEMA_PATH = Path(__file__).with_name("schema.sql")
INITIAL_SCHEMA_DESCRIPTION = "initial DAN v4.1 schema"
V2_DESCRIPTION = "FIX-09 voice_queue.spoken_at + cancelled_turns tombstone"
V3_DESCRIPTION = "shared local memory archive with FTS5"
V4_DESCRIPTION = "database migration lineage and record mappings"
V5_DESCRIPTION = "complete immutable voice render snapshots and native playback lifecycle"


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
    if current_version < 3:
        _apply_v3_memory_archive(conn)
    if current_version < 4:
        _apply_v4_migration_lineage(conn)
    if current_version < 5:
        _apply_v5_voice_snapshots(conn)
    _ensure_memory_os_sidecar_tables(conn)


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


def _apply_v3_memory_archive(conn: sqlite3.Connection) -> None:
    """Add the local recall archive without touching existing memory tables."""

    existing_fts = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'memory_archive_fts'"
    ).fetchone()
    if existing_fts is not None and "using fts5" not in str(existing_fts[0]).lower():
        raise RuntimeError("memory_archive_fts exists but is not an FTS5 virtual table")

    with conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS memory_archive_documents (
              canonical_id TEXT PRIMARY KEY,
              source_type TEXT NOT NULL,
              source_uri TEXT NOT NULL,
              source_item_id TEXT NOT NULL,
              title TEXT,
              content TEXT NOT NULL,
              content_hash TEXT NOT NULL,
              source_updated_at TEXT,
              metadata_json TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              UNIQUE(source_type, source_uri, source_item_id)
            );

            CREATE INDEX IF NOT EXISTS idx_memory_archive_documents_source
            ON memory_archive_documents(source_type, source_uri);

            CREATE TABLE IF NOT EXISTS memory_archive_sync_state (
              source_type TEXT NOT NULL,
              source_uri TEXT NOT NULL,
              cursor TEXT,
              fingerprint TEXT,
              synced_at TEXT NOT NULL,
              metadata_json TEXT NOT NULL DEFAULT '{}',
              PRIMARY KEY(source_type, source_uri)
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS memory_archive_fts USING fts5(
              canonical_id UNINDEXED,
              title,
              content,
              tokenize = 'unicode61'
            );

            """
        )
        created_fts = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'memory_archive_fts'"
        ).fetchone()
        if created_fts is None or "using fts5" not in str(created_fts[0]).lower():
            raise RuntimeError("memory_archive_fts was not created as an FTS5 virtual table")
        conn.execute(
            """
            INSERT OR IGNORE INTO schema_version (version, applied_at, description)
            VALUES (?, ?, ?)
            """,
            (3, _utc_now_iso(), V3_DESCRIPTION),
        )


def _apply_v4_migration_lineage(conn: sqlite3.Connection) -> None:
    """Record every legacy source and its deterministic import result."""

    with conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS migration_sources (
              id TEXT PRIMARY KEY,
              source_path_hash TEXT NOT NULL,
              source_schema TEXT NOT NULL,
              imported_at TEXT NOT NULL,
              source_sha256 TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_migration_sources_source_sha256
            ON migration_sources(source_sha256);
            CREATE TABLE IF NOT EXISTS migration_record_map (
              source_id TEXT NOT NULL,
              source_table TEXT NOT NULL,
              source_record_id TEXT NOT NULL,
              target_table TEXT,
              target_record_id TEXT,
              outcome TEXT NOT NULL CHECK (outcome IN ('imported', 'merged', 'rejected')),
              reason TEXT,
              PRIMARY KEY (source_id, source_table, source_record_id),
              FOREIGN KEY (source_id) REFERENCES migration_sources(id)
            );
            """
        )
        conn.execute(
            """INSERT OR IGNORE INTO schema_version (version, applied_at, description)
               VALUES (?, ?, ?)""",
            (4, _utc_now_iso(), V4_DESCRIPTION),
        )


def _apply_v5_voice_snapshots(conn: sqlite3.Connection) -> None:
    """Persist complete snapshots and quarantine pre-snapshot queue rows."""

    with conn:
        if not _column_exists(conn, "voice_queue", "render_snapshot_json"):
            for index in (
                "idx_voice_queue_status",
                "idx_voice_queue_priority",
                "idx_voice_queue_turn_id",
                "idx_voice_queue_spoken_at",
            ):
                conn.execute(f"DROP INDEX IF EXISTS {index}")
            conn.execute("ALTER TABLE voice_queue RENAME TO voice_queue_v4")
            _create_v5_voice_queue(conn)
            conn.execute(
                """
                INSERT INTO voice_queue (
                  id, created_at, updated_at, turn_id, text, priority, voice_id,
                  interrupt_policy, status, error, metadata_json, spoken_at,
                  source, session_id, participant, persona, lane, utterance_index,
                  render_snapshot_json, synthesis_started_at, synthesis_completed_at,
                  playback_started_at, playback_completed_at, playback_confirmed
                )
                SELECT
                  id, created_at, updated_at, turn_id, text, priority, voice_id,
                  CASE
                    WHEN interrupt_policy = 'interruptible' THEN 'interruptible'
                    ELSE 'finish_current'
                  END,
                  CASE
                    WHEN status IN ('queued', 'speaking', 'done', 'cancelled', 'failed')
                    THEN status ELSE 'failed'
                  END,
                  error, metadata_json, spoken_at,
                  'legacy-migration', COALESCE(NULLIF(turn_id, ''), 'legacy-' || id),
                  'legacy-unresolved', 'legacy-unresolved', 'normal',
                  MAX(0, COALESCE(CAST(json_extract(metadata_json, '$.seq') AS INTEGER), 0)),
                  'legacy-unresolved', NULL, NULL, spoken_at, NULL, 0
                FROM voice_queue_v4
                """
            )
            conn.execute("DROP TABLE voice_queue_v4")
        _create_v5_voice_queue_artifacts(conn)
        conn.execute(
            """
            INSERT OR IGNORE INTO schema_version (version, applied_at, description)
            VALUES (?, ?, ?)
            """,
            (5, _utc_now_iso(), V5_DESCRIPTION),
        )


def _create_v5_voice_queue(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE voice_queue (
          id TEXT PRIMARY KEY,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          turn_id TEXT,
          text TEXT NOT NULL,
          priority INTEGER NOT NULL DEFAULT 0,
          voice_id TEXT,
          interrupt_policy TEXT NOT NULL DEFAULT 'finish_current',
          status TEXT NOT NULL CHECK (
            status IN ('queued', 'synthesizing', 'speaking', 'done', 'cancelled', 'failed')
          ),
          error TEXT,
          metadata_json TEXT NOT NULL DEFAULT '{}',
          spoken_at TEXT,
          source TEXT NOT NULL,
          session_id TEXT NOT NULL,
          participant TEXT NOT NULL,
          persona TEXT NOT NULL,
          lane TEXT NOT NULL CHECK (lane IN ('live', 'normal', 'background')),
          utterance_index INTEGER NOT NULL CHECK (utterance_index >= 0),
          render_snapshot_json TEXT NOT NULL,
          synthesis_started_at TEXT,
          synthesis_completed_at TEXT,
          playback_started_at TEXT,
          playback_completed_at TEXT,
          playback_confirmed INTEGER NOT NULL DEFAULT 0 CHECK (playback_confirmed IN (0, 1))
        )
        """
    )


def _create_v5_voice_queue_artifacts(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_voice_queue_status ON voice_queue(status);
        CREATE INDEX IF NOT EXISTS idx_voice_queue_priority ON voice_queue(priority);
        CREATE INDEX IF NOT EXISTS idx_voice_queue_turn_id ON voice_queue(turn_id);
        CREATE INDEX IF NOT EXISTS idx_voice_queue_spoken_at ON voice_queue(spoken_at);
        CREATE INDEX IF NOT EXISTS idx_voice_queue_session_id ON voice_queue(session_id);
        CREATE INDEX IF NOT EXISTS idx_voice_queue_lane_status ON voice_queue(lane, status);

        CREATE TRIGGER IF NOT EXISTS voice_queue_snapshot_complete
        BEFORE INSERT ON voice_queue
        WHEN CASE
          WHEN json_valid(NEW.render_snapshot_json) = 0 THEN 1
          ELSE
            COALESCE(json_extract(NEW.render_snapshot_json, '$.engine'), '') = '' OR
            COALESCE(json_extract(NEW.render_snapshot_json, '$.engine_version'), '') = '' OR
            COALESCE(json_extract(NEW.render_snapshot_json, '$.voice_or_style'), '') = '' OR
            COALESCE(json_extract(NEW.render_snapshot_json, '$.speed'), 0) <= 0 OR
            json_type(NEW.render_snapshot_json, '$.mastering_profile') IS NULL OR
            json_type(NEW.render_snapshot_json, '$.dsp') IS NULL OR
            json_type(NEW.render_snapshot_json, '$.pronunciations') != 'object' OR
            COALESCE(json_extract(NEW.render_snapshot_json, '$.pronunciations_sha256'), '') = '' OR
            COALESCE(json_extract(NEW.render_snapshot_json, '$.gain'), 0) <= 0 OR
            json_type(NEW.render_snapshot_json, '$.asset_sha256') != 'object' OR
            json_extract(NEW.render_snapshot_json, '$.asset_sha256') = '{}' OR
            COALESCE(json_extract(NEW.render_snapshot_json, '$.config_revision'), '') = ''
        END
        BEGIN
          SELECT RAISE(ABORT, 'voice snapshot incomplete');
        END;

        CREATE TRIGGER IF NOT EXISTS voice_queue_legacy_marker_reserved
        BEFORE INSERT ON voice_queue
        WHEN NEW.render_snapshot_json = 'legacy-unresolved'
        BEGIN
          SELECT RAISE(ABORT, 'legacy-unresolved snapshot is migration-only');
        END;

        CREATE TRIGGER IF NOT EXISTS voice_queue_snapshot_immutable
        BEFORE UPDATE OF render_snapshot_json ON voice_queue
        WHEN OLD.render_snapshot_json IS NOT NEW.render_snapshot_json
        BEGIN
          SELECT RAISE(ABORT, 'immutable render snapshot');
        END;

        CREATE TRIGGER IF NOT EXISTS voice_queue_status_transition
        BEFORE UPDATE OF status ON voice_queue
        WHEN OLD.status IS NOT NEW.status AND NOT (
          (OLD.status = 'queued' AND NEW.status IN ('synthesizing', 'cancelled', 'failed')) OR
          (OLD.status = 'synthesizing' AND NEW.status IN (
            'queued', 'speaking', 'cancelled', 'failed'
          )) OR
          (OLD.status = 'speaking' AND NEW.status IN ('done', 'cancelled', 'failed'))
        )
        BEGIN
          SELECT RAISE(ABORT, 'invalid voice queue transition');
        END;
        """
    )


def _ensure_memory_os_sidecar_tables(conn: sqlite3.Connection) -> None:
    """Add future Memory OS v1 sidecars without bumping core schema version."""

    with conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS memory_observations (
              id TEXT PRIMARY KEY,
              source_type TEXT NOT NULL,
              source_id TEXT,
              conversation_id TEXT,
              turn_id TEXT,
              event_id INTEGER,
              observed_text TEXT NOT NULL,
              detected_kind TEXT,
              sensitivity TEXT NOT NULL DEFAULT 'unknown',
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS memory_candidates (
              id TEXT PRIMARY KEY,
              candidate_kind TEXT NOT NULL,
              scope TEXT NOT NULL,
              namespace TEXT NOT NULL,
              claim TEXT NOT NULL,
              title TEXT,
              reason TEXT,
              confidence TEXT NOT NULL DEFAULT 'unknown',
              sensitivity TEXT NOT NULL DEFAULT 'unknown',
              recommended_action TEXT NOT NULL,
              target_memory_id TEXT,
              status TEXT NOT NULL,
              created_at TEXT NOT NULL,
              reviewed_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_memory_candidates_status
            ON memory_candidates(status);
            CREATE INDEX IF NOT EXISTS idx_memory_candidates_namespace
            ON memory_candidates(namespace);

            CREATE TABLE IF NOT EXISTS memory_items (
              id TEXT PRIMARY KEY,
              canonical_key TEXT NOT NULL,
              kind TEXT NOT NULL,
              scope TEXT NOT NULL,
              namespace TEXT NOT NULL,
              title TEXT,
              claim TEXT NOT NULL,
              content TEXT,
              status TEXT NOT NULL,
              confidence TEXT NOT NULL DEFAULT 'unknown',
              sensitivity TEXT NOT NULL DEFAULT 'unknown',
              source_policy TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              last_used_at TEXT,
              last_confirmed_at TEXT,
              supersedes TEXT,
              superseded_by TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_memory_items_status
            ON memory_items(status);
            CREATE INDEX IF NOT EXISTS idx_memory_items_namespace
            ON memory_items(namespace);

            CREATE TABLE IF NOT EXISTS memory_evidence (
              id TEXT PRIMARY KEY,
              memory_id TEXT,
              candidate_id TEXT,
              observation_id TEXT,
              conversation_id TEXT,
              turn_id TEXT,
              event_id INTEGER,
              quote TEXT,
              weight REAL NOT NULL DEFAULT 1.0,
              created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_memory_evidence_memory_id
            ON memory_evidence(memory_id);
            CREATE INDEX IF NOT EXISTS idx_memory_evidence_candidate_id
            ON memory_evidence(candidate_id);

            CREATE TABLE IF NOT EXISTS memory_topics (
              id TEXT PRIMARY KEY,
              namespace TEXT NOT NULL UNIQUE,
              title TEXT NOT NULL,
              summary TEXT NOT NULL DEFAULT '',
              status TEXT NOT NULL,
              last_consolidated_at TEXT,
              token_estimate INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS memory_usage_events (
              id TEXT PRIMARY KEY,
              memory_id TEXT NOT NULL,
              turn_id TEXT,
              reason TEXT NOT NULL,
              rank INTEGER,
              included INTEGER NOT NULL,
              created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_memory_usage_events_turn_id
            ON memory_usage_events(turn_id);

            CREATE TABLE IF NOT EXISTS memory_review_decisions (
              id TEXT PRIMARY KEY,
              candidate_id TEXT NOT NULL,
              decision TEXT NOT NULL,
              edited_claim TEXT,
              reason TEXT,
              created_at TEXT NOT NULL
            );
            """
        )
def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(row[1] == column for row in conn.execute(f"PRAGMA table_info({table})"))


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
