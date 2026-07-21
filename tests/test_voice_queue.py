"""Persisted Task 7 voice queue contract tests."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from dan.store.db import close_quietly, connect_db, initialize_database
from dan.store.event_store import create_event_store
from dan.voice.queue import VoiceQueue, VoiceQueueCancelledError
from tests.voice_helpers import enqueue_voice


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = initialize_database(tmp_path / "queue.db")
    try:
        yield connection
    finally:
        close_quietly(connection)


class Events:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.items: list[tuple[str, dict]] = []

    def append(self, event_type, source, payload) -> None:
        self.items.append((getattr(event_type, "value", str(event_type)), payload))

    def names(self) -> list[str]:
        return [name for name, _ in self.items]


def test_enqueue_persists_complete_intent_snapshot_and_event(conn) -> None:
    events = Events(conn)
    request = enqueue_voice(
        VoiceQueue(conn, event_store=events),
        "Pierwsze zdanie.",
        session="turn-1",
        utterance_index=3,
    )

    row = conn.execute(
        "SELECT source, session_id, utterance_index, render_snapshot_json, status "
        "FROM voice_queue WHERE id = ?",
        (request.id,),
    ).fetchone()
    assert tuple(row[:3]) == ("pytest", "turn-1", 3)
    assert '"config_revision":"test-config-v1"' in row[3]
    assert row[4] == "queued"
    assert events.names() == ["voice.speak.queued"]


def test_lane_then_priority_then_rowid_controls_claim_order(conn) -> None:
    queue = VoiceQueue(conn)
    enqueue_voice(queue, "background", lane="background", priority=99)
    enqueue_voice(queue, "normal-low", lane="normal", priority=1)
    enqueue_voice(queue, "live", lane="live", priority=-10)
    enqueue_voice(queue, "normal-high", lane="normal", priority=5)

    claimed = [queue.claim_next() for _ in range(4)]

    assert [request.text for request in claimed if request] == [
        "live",
        "normal-high",
        "normal-low",
        "background",
    ]
    assert all(request.status == "synthesizing" for request in claimed if request)


def test_queue_lifecycle_requires_synthesis_before_playback(conn) -> None:
    events = Events(conn)
    queue = VoiceQueue(conn, event_store=events)
    request = enqueue_voice(queue, "Pelny cykl.")

    queue.claim_next()
    queue.mark_synthesis_complete(request.id)
    queue.mark_playback_started(request.id)
    queue.mark_done(request.id)

    row = conn.execute(
        "SELECT status, synthesis_started_at, synthesis_completed_at, "
        "playback_started_at, playback_completed_at, playback_confirmed "
        "FROM voice_queue WHERE id = ?",
        (request.id,),
    ).fetchone()
    assert row[0] == "done"
    assert all(row[index] is not None for index in range(1, 5))
    assert row[5] == 1
    assert events.names() == [
        "voice.speak.queued",
        "voice.speak.synthesis.started",
        "voice.speak.synthesis.completed",
        "voice.speak.started",
        "voice.speak.finished",
    ]


def test_cancel_session_cancels_every_active_phase_only(conn) -> None:
    queue = VoiceQueue(conn)
    first = enqueue_voice(queue, "synthesizing", session="turn-x")
    enqueue_voice(queue, "queued", session="turn-x", utterance_index=1)
    enqueue_voice(queue, "other", session="turn-y")
    assert queue.claim_next().id == first.id

    assert queue.cancel_session("turn-x") == [
        row[0]
        for row in conn.execute(
            "SELECT id FROM voice_queue WHERE session_id = 'turn-x' ORDER BY rowid DESC"
        ).fetchall()
    ]
    statuses = dict(conn.execute("SELECT text, status FROM voice_queue"))
    assert statuses == {
        "synthesizing": "cancelled",
        "queued": "cancelled",
        "other": "queued",
    }


def test_no_duplicate_claim_and_restart_requeues_synthesis(conn) -> None:
    queue = VoiceQueue(conn)
    request = enqueue_voice(queue, "Raz i tylko raz.")
    assert queue.claim_next().id == request.id
    assert queue.claim_next() is None

    assert queue.recover_orphans() == 1
    assert queue.claim_next().id == request.id


def test_concurrent_consumers_claim_each_request_once(tmp_path: Path) -> None:
    db_path = tmp_path / "concurrent-queue.db"
    setup = initialize_database(db_path)
    expected = [
        enqueue_voice(
            VoiceQueue(setup),
            f"request-{index}",
            session=f"session-{index}",
        ).id
        for index in range(24)
    ]
    close_quietly(setup)

    barrier = threading.Barrier(4)
    claimed: list[str] = []
    errors: list[BaseException] = []
    claimed_lock = threading.Lock()

    def consume() -> None:
        connection = connect_db(db_path)
        try:
            queue = VoiceQueue(
                connection,
                event_store=create_event_store(connection),
            )
            barrier.wait(timeout=5)
            while request := queue.claim_next():
                with claimed_lock:
                    claimed.append(request.id)
        except BaseException as exc:  # noqa: BLE001 - surfaced in main thread
            errors.append(exc)
        finally:
            close_quietly(connection)

    threads = [threading.Thread(target=consume) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert len(claimed) == len(expected)
    assert set(claimed) == set(expected)


def test_tombstone_is_idempotent_and_rejects_late_snapshot(conn) -> None:
    queue = VoiceQueue(conn)
    queue.tombstone_turns(["dead", "dead"])

    with pytest.raises(VoiceQueueCancelledError):
        enqueue_voice(queue, "Spuznione.", session="dead")

    assert queue.is_tombstoned("dead") is True
    assert enqueue_voice(queue, "Zywe.", session="live").status == "queued"


def test_tombstone_landing_right_before_transaction_still_rejects_enqueue(conn) -> None:
    # Regression for the enqueue/tombstone race: a cancellation that lands
    # between the pre-transaction check and BEGIN IMMEDIATE must still refuse
    # the late chunk. The tombstone check therefore has to run INSIDE the
    # write transaction, where the raise triggers a rollback.
    queue = VoiceQueue(conn)
    original_begin = queue._begin_immediate

    def begin_then_tombstone() -> None:
        original_begin()
        conn.execute(
            "INSERT OR IGNORE INTO cancelled_turns (turn_id, cancelled_at) VALUES (?, ?)",
            # A cancellation landing *now*: a fixed past date would be an
            # already-expired tombstone, which is a different scenario.
            ("late-turn", queue._now()),
        )

    queue._begin_immediate = begin_then_tombstone

    with pytest.raises(VoiceQueueCancelledError):
        enqueue_voice(queue, "Spozniony chunk po cancelu.", session="late-turn")

    assert not conn.in_transaction  # the raise rolled the transaction back
    assert conn.execute("SELECT COUNT(*) FROM voice_queue").fetchone()[0] == 0


def test_expired_tombstone_stops_blocking_without_a_new_cancellation(conn) -> None:
    """A tombstone older than the TTL must not silence a session forever.

    The purge only ran while writing another tombstone, and the read path had
    no age filter, so on an idle runtime a flushed session stayed mute far past
    TOMBSTONE_TTL_SECONDS — across daemon restarts included.
    """

    from datetime import UTC, datetime, timedelta

    from dan.voice.queue import TOMBSTONE_TTL_SECONDS

    queue = VoiceQueue(conn)
    queue.tombstone_turns(["stale"])
    expired = (
        datetime.now(UTC) - timedelta(seconds=TOMBSTONE_TTL_SECONDS + 60)
    ).isoformat(timespec="seconds").replace("+00:00", "Z")
    conn.execute(
        "UPDATE cancelled_turns SET cancelled_at = ? WHERE turn_id = ?",
        (expired, "stale"),
    )
    conn.commit()

    assert queue.is_tombstoned("stale") is False
    assert enqueue_voice(queue, "Znowu gadam.", session="stale").status == "queued"
