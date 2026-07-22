from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from dan.store.db import close_quietly, initialize_database
from dan.store.migrations import LATEST_SCHEMA_VERSION, apply_migrations
from dan.voice.models import RenderSnapshot, SpeechIntent
from dan.voice.queue import VoiceQueue


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = initialize_database(tmp_path / "voice-snapshots.db")
    try:
        yield connection
    finally:
        close_quietly(connection)


@pytest.fixture
def intent() -> SpeechIntent:
    return SpeechIntent(
        text="Snapshot ma przetrwac zmiane konfiguracji.",
        persona="dan",
        source="claude",
        session="standup",
        participant="dan",
        priority=7,
        lane="normal",
        interrupt_policy="finish_current",
        utterance_index=3,
    )


@pytest.fixture
def complete_snapshot() -> RenderSnapshot:
    return RenderSnapshot(
        engine="supertonic",
        engine_version="1.3.1",
        voice_or_style="M3",
        speed=1.25,
        mastering_profile="clean",
        dsp="highpass=f=80",
        pronunciations={"runtime": "rantajm"},
        pronunciations_sha256="a" * 64,
        gain=1.0,
        asset_sha256={"voice:M3": "b" * 64},
        config_revision="voice-catalog-v1",
        seed=17,
    )


def test_enqueue_persists_complete_canonical_intent_and_snapshot(
    conn: sqlite3.Connection,
    intent: SpeechIntent,
    complete_snapshot: RenderSnapshot,
) -> None:
    request = VoiceQueue(conn).enqueue(intent, complete_snapshot)

    row = conn.execute(
        """
        SELECT source, session_id, participant, persona, lane, utterance_index,
               render_snapshot_json, status
        FROM voice_queue WHERE id = ?
        """,
        (request.id,),
    ).fetchone()
    assert row == (
        "claude",
        "standup",
        "dan",
        "dan",
        "normal",
        3,
        complete_snapshot.canonical_json(),
        "queued",
    )
    assert request.render_snapshot == complete_snapshot
    assert request.intent == intent
    assert '"seed":17' in complete_snapshot.canonical_json()


def test_snapshot_is_immutable_after_enqueue(
    conn: sqlite3.Connection,
    intent: SpeechIntent,
    complete_snapshot: RenderSnapshot,
) -> None:
    request = VoiceQueue(conn).enqueue(intent, complete_snapshot)

    with pytest.raises(sqlite3.IntegrityError, match="immutable render snapshot"):
        conn.execute(
            "UPDATE voice_queue SET render_snapshot_json = '{}' WHERE id = ?",
            (request.id,),
        )


def test_database_rejects_incomplete_runtime_snapshot(
    conn: sqlite3.Connection,
    intent: SpeechIntent,
) -> None:
    with pytest.raises(sqlite3.IntegrityError, match="voice snapshot incomplete"):
        conn.execute(
            """
            INSERT INTO voice_queue (
              id, created_at, updated_at, turn_id, text, priority, voice_id,
              interrupt_policy, status, error, metadata_json, spoken_at,
              source, session_id, participant, persona, lane, utterance_index,
              render_snapshot_json, playback_confirmed
            ) VALUES (
              'invalid', '2026-07-18T00:00:00Z', '2026-07-18T00:00:00Z',
              'standup', ?, 0, NULL, 'finish_current', 'queued', NULL, '{}', NULL,
              'claude', 'standup', 'dan', 'dan', 'normal', 0, '{}', 0
            )
            """,
            (intent.text,),
        )


def test_database_reserves_legacy_marker_even_when_source_is_spoofed(
    conn: sqlite3.Connection,
) -> None:
    with pytest.raises(sqlite3.IntegrityError, match="migration-only"):
        conn.execute(
            """
            INSERT INTO voice_queue (
              id, created_at, updated_at, turn_id, text, priority, voice_id,
              interrupt_policy, status, error, metadata_json, spoken_at,
              source, session_id, participant, persona, lane, utterance_index,
              render_snapshot_json, playback_confirmed
            ) VALUES (
              'spoofed', '2026-07-18T00:00:00Z', '2026-07-18T00:00:00Z',
              'spoofed', 'Nie jestem migracja.', 0, NULL, 'finish_current',
              'queued', NULL, '{}', NULL, 'legacy-migration', 'spoofed',
              'legacy-unresolved', 'legacy-unresolved', 'normal', 0,
              'legacy-unresolved', 0
            )
            """
        )


def test_transition_timestamps_follow_synthesis_then_native_playback(
    conn: sqlite3.Connection,
    intent: SpeechIntent,
    complete_snapshot: RenderSnapshot,
) -> None:
    queue = VoiceQueue(conn)
    request = queue.enqueue(intent, complete_snapshot)

    claimed = queue.claim_next()
    assert claimed is not None and claimed.status == "synthesizing"
    queue.mark_synthesis_complete(request.id)
    queue.mark_playback_started(request.id)
    queue.mark_done(request.id)

    row = conn.execute(
        """
        SELECT status, synthesis_started_at, synthesis_completed_at,
               playback_started_at, playback_completed_at, playback_confirmed
        FROM voice_queue WHERE id = ?
        """,
        (request.id,),
    ).fetchone()
    assert row[0] == "done"
    assert all(row[index] is not None for index in range(1, 5))
    assert row[5] == 1


def test_database_rejects_direct_queued_to_speaking_transition(
    conn: sqlite3.Connection,
    intent: SpeechIntent,
    complete_snapshot: RenderSnapshot,
) -> None:
    request = VoiceQueue(conn).enqueue(intent, complete_snapshot)

    with pytest.raises(sqlite3.IntegrityError, match="invalid voice queue transition"):
        conn.execute(
            "UPDATE voice_queue SET status = 'speaking' WHERE id = ?",
            (request.id,),
        )


def test_restart_requeues_synthesis_but_never_replays_uncertain_playback(
    conn: sqlite3.Connection,
    intent: SpeechIntent,
    complete_snapshot: RenderSnapshot,
) -> None:
    queue = VoiceQueue(conn)
    synthesizing = queue.enqueue(intent, complete_snapshot)
    queue.claim_next()
    speaking_intent = SpeechIntent(
        **{
            **intent.__dict__,
            "text": "Ten request zdazyl wejsc w playback.",
            "utterance_index": 4,
        }
    )
    speaking = queue.enqueue(speaking_intent, complete_snapshot)
    queue.claim_next()
    queue.mark_synthesis_complete(speaking.id)
    queue.mark_playback_started(speaking.id)

    recovered = queue.recover_orphans()

    statuses = dict(
        conn.execute(
            "SELECT id, status FROM voice_queue WHERE id IN (?, ?)",
            (synthesizing.id, speaking.id),
        ).fetchall()
    )
    assert recovered == 2
    assert statuses[synthesizing.id] == "queued"
    assert statuses[speaking.id] == "failed"


def test_v5_migration_backfills_legacy_rows_and_is_idempotent() -> None:
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE schema_version (
          version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL, description TEXT NOT NULL
        );
        INSERT INTO schema_version VALUES (4, '2026-07-18T00:00:00Z', 'legacy');
        CREATE TABLE voice_queue (
          id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          turn_id TEXT, text TEXT NOT NULL, priority INTEGER NOT NULL DEFAULT 0,
          voice_id TEXT, interrupt_policy TEXT NOT NULL DEFAULT 'no_interrupt',
          status TEXT NOT NULL, error TEXT, metadata_json TEXT NOT NULL DEFAULT '{}',
          spoken_at TEXT
        );
        INSERT INTO voice_queue VALUES (
          'old', '2026-07-17T00:00:00Z', '2026-07-17T00:00:00Z', 'turn-old',
          'Stary nierozstrzygniety request.', 0, 'M1', 'no_interrupt', 'queued',
          NULL, '{"kind":"sentence","seq":0}', NULL
        );
        """
    )

    apply_migrations(conn)
    apply_migrations(conn)

    row = conn.execute(
        """
        SELECT source, session_id, persona, render_snapshot_json, status
        FROM voice_queue WHERE id = 'old'
        """
    ).fetchone()
    assert row == (
        "legacy-migration",
        "turn-old",
        "legacy-unresolved",
        "legacy-unresolved",
        "queued",
    )
    assert VoiceQueue(conn).claim_next() is None
    assert conn.execute(
        "SELECT COUNT(*) FROM schema_version WHERE version = ?",
        (LATEST_SCHEMA_VERSION,),
    ).fetchone()[0] == 1
    conn.close()
