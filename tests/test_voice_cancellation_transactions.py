"""Transactional cancellation race regressions."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

import pytest

from dan.store.db import close_quietly, initialize_database
from dan.store.event_store import EventStoreError, create_event_store
from dan.voice.cancellation import CancellationCoordinator, GenerationRegistry
from dan.voice.queue import (
    VoiceQueue,
    VoiceQueueCancelledError,
    VoiceQueueError,
)
from tests.voice_helpers import enqueue_voice


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = initialize_database(tmp_path / "cancellation-transactions.db")
    try:
        yield connection
    finally:
        close_quietly(connection)


def _fail_cancelled_event_inserts(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TRIGGER fail_cancelled_event
        BEFORE INSERT ON events
        WHEN NEW.type = 'voice.speak.cancelled'
        BEGIN
          SELECT RAISE(ABORT, 'forced cancelled event failure');
        END
        """
    )
    conn.commit()


def _assert_cancel_rolled_back(conn: sqlite3.Connection, request_id: str) -> None:
    status = conn.execute(
        "SELECT status FROM voice_queue WHERE id = ?", (request_id,)
    ).fetchone()[0]
    assert status == "queued"
    assert conn.execute("SELECT COUNT(*) FROM cancelled_turns").fetchone()[0] == 0
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM events WHERE type = 'voice.speak.cancelled'"
        ).fetchone()[0]
        == 0
    )


def test_cancel_session_rolls_back_queue_tombstone_and_events_together(
    conn: sqlite3.Connection,
) -> None:
    queue = VoiceQueue(conn, event_store=create_event_store(conn))
    request = enqueue_voice(queue, "cancel session", session="turn-session")
    _fail_cancelled_event_inserts(conn)

    with pytest.raises(EventStoreError, match="forced cancelled event failure"):
        queue.cancel_session("turn-session", reason="barge_in")

    _assert_cancel_rolled_back(conn, request.id)


def test_cancel_request_rolls_back_queue_tombstone_and_event_together(
    conn: sqlite3.Connection,
) -> None:
    queue = VoiceQueue(conn, event_store=create_event_store(conn))
    request = enqueue_voice(queue, "cancel request", session="turn-request")
    _fail_cancelled_event_inserts(conn)

    with pytest.raises(EventStoreError, match="forced cancelled event failure"):
        queue.cancel_request(request.id, reason="barge_in")

    _assert_cancel_rolled_back(conn, request.id)


def test_coordinator_rolls_back_all_turns_when_one_cancel_event_fails(
    conn: sqlite3.Connection,
) -> None:
    queue = VoiceQueue(conn, event_store=create_event_store(conn))
    enqueue_voice(queue, "first turn", session="turn-a")
    enqueue_voice(queue, "second turn", session="turn-b")
    conn.execute(
        """
        CREATE TRIGGER fail_turn_a_cancelled_event
        BEFORE INSERT ON events
        WHEN NEW.type = 'voice.speak.cancelled'
          AND instr(NEW.payload_json, '"session_id":"turn-a"') > 0
        BEGIN
          SELECT RAISE(ABORT, 'forced second turn event failure');
        END
        """
    )
    conn.commit()
    db_path = Path(conn.execute("PRAGMA database_list").fetchone()[2])

    class Owner:
        def __init__(self) -> None:
            self.stops = 0

        def stop_playback(self) -> None:
            self.stops += 1

    owner = Owner()
    coordinator = CancellationCoordinator(
        lambda: sqlite3.connect(db_path),
        generation_registry=GenerationRegistry(),
        playback_owner=owner,
    )

    with pytest.raises(EventStoreError, match="forced second turn event failure"):
        coordinator.cancel_active_speech(reason="barge_in")

    statuses = [row[0] for row in conn.execute("SELECT status FROM voice_queue")]
    assert statuses == ["queued", "queued"]
    assert conn.execute("SELECT COUNT(*) FROM cancelled_turns").fetchone()[0] == 0
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM events WHERE type = 'voice.speak.cancelled'"
        ).fetchone()[0]
        == 0
    )
    assert owner.stops == 0


@pytest.mark.parametrize("operation", ["session", "request"])
def test_direct_cancellation_tombstones_the_turn_before_late_enqueue(
    conn: sqlite3.Connection,
    operation: str,
) -> None:
    queue = VoiceQueue(conn, event_store=create_event_store(conn))
    request = enqueue_voice(queue, "original", session=f"turn-{operation}")

    if operation == "session":
        assert queue.cancel_session(request.session_id, reason="barge_in") == [request.id]
    else:
        assert queue.cancel_request(request.id, reason="barge_in") is True

    with pytest.raises(VoiceQueueCancelledError):
        enqueue_voice(queue, "late tail", session=request.session_id, utterance_index=1)


def test_cancellation_rejects_event_store_on_a_different_connection(
    conn: sqlite3.Connection,
) -> None:
    other = sqlite3.connect(
        conn.execute("PRAGMA database_list").fetchone()[2]
    )
    try:
        with pytest.raises(VoiceQueueError, match="same SQLite connection"):
            VoiceQueue(conn, event_store=create_event_store(other))
    finally:
        close_quietly(other)


def test_cancellation_rejects_nontransactional_event_store(
    conn: sqlite3.Connection,
) -> None:
    request = enqueue_voice(VoiceQueue(conn), "original", session="turn-events")

    class AppendOnlyStore:
        connection = conn

        def append(self, *_args: Any, **_kwargs: Any) -> None:
            return None

    queue = VoiceQueue(conn, event_store=AppendOnlyStore())

    with pytest.raises(VoiceQueueError, match="transactional event append"):
        queue.cancel_request(request.id, reason="barge_in")

    assert queue.get(request.id).status == "queued"
    assert queue.is_tombstoned("turn-events") is False


def test_superseded_filler_cancel_preserves_sibling_and_future_final_chunks(
    conn: sqlite3.Connection,
) -> None:
    queue = VoiceQueue(conn, event_store=create_event_store(conn))
    filler = enqueue_voice(
        queue,
        "filler",
        session="turn-filler",
        interrupt_policy="interruptible",
    )
    final = enqueue_voice(
        queue,
        "final",
        session="turn-filler",
        utterance_index=1,
    )

    assert hasattr(queue, "cancel_superseded_request")
    assert queue.cancel_superseded_request(filler.id, reason="superseded") is True

    assert queue.get(filler.id).status == "cancelled"
    assert queue.get(final.id).status == "queued"
    assert queue.is_tombstoned("turn-filler") is False
    later = enqueue_voice(
        queue,
        "later final",
        session="turn-filler",
        utterance_index=2,
    )
    assert later.status == "queued"


def test_enqueue_started_during_cancel_cannot_survive_as_active(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "cancellation-race.db"
    conn = initialize_database(db_path)
    enqueue_voice(VoiceQueue(conn), "original", session="turn-race")
    enqueue_attempted = threading.Event()
    enqueue_done = threading.Event()
    late_errors: list[BaseException] = []
    late_thread: list[threading.Thread] = []

    class SignallingQueue(VoiceQueue):
        def _begin_immediate(self) -> None:
            enqueue_attempted.set()
            super()._begin_immediate()

    def enqueue_late() -> None:
        late_conn = sqlite3.connect(db_path, timeout=2)
        try:
            enqueue_voice(
                SignallingQueue(late_conn),
                "late tail",
                session="turn-race",
                utterance_index=1,
            )
        except BaseException as exc:  # noqa: BLE001 - asserted below
            late_errors.append(exc)
        finally:
            close_quietly(late_conn)
            enqueue_done.set()

    started = False

    def start_late_enqueue() -> int:
        nonlocal started
        if not started:
            started = True
            thread = threading.Thread(target=enqueue_late, daemon=True)
            late_thread.append(thread)
            thread.start()
            assert enqueue_attempted.wait(timeout=1)
        return 0

    class RaceEventStore:
        def __init__(self) -> None:
            self._delegate = create_event_store(conn)

        @property
        def connection(self) -> sqlite3.Connection:
            return conn

        def append(self, *args: Any, **kwargs: Any) -> Any:
            if not conn.in_transaction:
                assert enqueue_done.wait(timeout=2)
            return self._delegate.append(*args, **kwargs)

        def append_in_transaction(self, *args: Any, **kwargs: Any) -> Any:
            return self._delegate.append_in_transaction(*args, **kwargs)

    conn.create_function("start_late_enqueue", 0, start_late_enqueue)
    conn.execute(
        """
        CREATE TEMP TRIGGER start_late_enqueue_after_cancel
        AFTER UPDATE OF status ON voice_queue
        WHEN NEW.status = 'cancelled' AND OLD.status != 'cancelled'
        BEGIN
          SELECT start_late_enqueue();
        END
        """
    )
    conn.commit()

    try:
        VoiceQueue(conn, event_store=RaceEventStore()).cancel_session(
            "turn-race", reason="barge_in"
        )
        assert enqueue_done.wait(timeout=2)
        late_thread[0].join(timeout=1)

        assert len(late_errors) == 1
        assert isinstance(late_errors[0], VoiceQueueCancelledError)
        active = conn.execute(
            "SELECT COUNT(*) FROM voice_queue "
            "WHERE status IN ('queued', 'synthesizing', 'speaking')"
        ).fetchone()[0]
        assert active == 0
    finally:
        close_quietly(conn)
