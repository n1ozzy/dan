"""G3 persisted VoiceQueue tests (CONTRACTS §7, ADR-005).

One sentence = one VoiceRequest row; statuses queued -> speaking ->
done|cancelled|failed with voice.speak.* events; queued items recover
after a restart; only the broker ever claims work.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from jarvis.store.db import close_quietly, initialize_database
from jarvis.voice.queue import VoiceQueue
from tests.git_guards import assert_schema_and_migrations_unchanged


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = initialize_database(tmp_path / "queue.db")
    try:
        yield connection
    finally:
        close_quietly(connection)


class Events:
    def __init__(self) -> None:
        self.items: list[tuple[str, dict]] = []

    def append(self, event_type, source, payload):
        self.items.append((getattr(event_type, "value", str(event_type)), payload))

    def names(self) -> list[str]:
        return [name for name, _ in self.items]


def queue(conn, events=None):
    return VoiceQueue(conn, event_store=events)


def test_enqueue_persists_row_with_seq_and_event(conn) -> None:
    events = Events()
    q = queue(conn, events)

    request = q.enqueue(text="Pierwsze zdanie.", turn_id="turn-1", kind="sentence", seq=0)

    assert request.status == "queued"
    row = conn.execute(
        "SELECT text, turn_id, status, metadata_json FROM voice_queue"
    ).fetchone()
    assert row[0] == "Pierwsze zdanie."
    assert row[1] == "turn-1"
    assert row[2] == "queued"
    assert '"seq": 0' in row[3] and '"kind": "sentence"' in row[3]
    assert events.names() == ["voice.speak.queued"]


def test_claim_next_plays_in_seq_order(conn) -> None:
    events = Events()
    q = queue(conn, events)
    q.enqueue(text="Drugie zdanie w kolejce.", turn_id="turn-1", kind="sentence", seq=1)
    q.enqueue(text="Pierwsze zdanie w kolejce.", turn_id="turn-1", kind="sentence", seq=0)

    first = q.claim_next()
    second = q.claim_next()

    assert first.text == "Pierwsze zdanie w kolejce."
    assert second.text == "Drugie zdanie w kolejce."
    assert first.status == "speaking"
    assert "voice.speak.started" in events.names()


def test_done_and_failed_lifecycle(conn) -> None:
    events = Events()
    q = queue(conn, events)
    q.enqueue(text="Zdanie numer jeden.", turn_id="t", kind="sentence", seq=0)
    q.enqueue(text="Zdanie numer dwa.", turn_id="t", kind="sentence", seq=1)

    first = q.claim_next()
    q.mark_done(first.id)
    second = q.claim_next()
    q.mark_failed(second.id, error="engine exploded")

    statuses = dict(conn.execute("SELECT text, status FROM voice_queue").fetchall())
    assert statuses["Zdanie numer jeden."] == "done"
    assert statuses["Zdanie numer dwa."] == "failed"
    assert "voice.speak.finished" in events.names()
    assert "voice.speak.failed" in events.names()


def test_cancel_turn_cancels_queued_and_speaking(conn) -> None:
    events = Events()
    q = queue(conn, events)
    q.enqueue(text="Aktualnie mówione zdanie.", turn_id="turn-x", kind="sentence", seq=0)
    q.enqueue(text="Jeszcze niewypowiedziane zdanie.", turn_id="turn-x", kind="sentence", seq=1)
    q.enqueue(text="Zdanie innego turnu zostaje.", turn_id="turn-y", kind="sentence", seq=0)
    q.claim_next()

    cancelled = q.cancel_turn("turn-x")

    assert cancelled == 2
    statuses = dict(conn.execute("SELECT text, status FROM voice_queue").fetchall())
    assert statuses["Aktualnie mówione zdanie."] == "cancelled"
    assert statuses["Jeszcze niewypowiedziane zdanie."] == "cancelled"
    assert statuses["Zdanie innego turnu zostaje."] == "queued"
    assert events.names().count("voice.speak.cancelled") == 2


def test_orphaned_speaking_rows_recover_to_queued(conn) -> None:
    q = queue(conn)
    q.enqueue(text="Przerwane restartem zdanie.", turn_id="t", kind="sentence", seq=0)
    q.claim_next()

    recovered = q.recover_orphans()

    assert recovered == 1
    row = conn.execute("SELECT status FROM voice_queue").fetchone()
    assert row[0] == "queued"


def test_no_duplicate_claim_of_the_same_request(conn) -> None:
    q = queue(conn)
    q.enqueue(text="Jednorazowe zdanie do zagrania.", turn_id="t", kind="sentence", seq=0)

    first = q.claim_next()
    second = q.claim_next()

    assert first is not None
    assert second is None


def test_schema_and_migrations_are_unchanged() -> None:
    assert_schema_and_migrations_unchanged(ROOT)
