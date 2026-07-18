"""Prompt 04 event model, store and bus tests."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from dan.events.bus import EventBus
from dan.events.models import (
    Event,
    event_from_row,
    event_to_row_payload,
    utc_now_iso,
)
from dan.events.types import EventType
from dan.store.db import close_quietly, initialize_database
from dan.store.event_store import EventStore, EventStoreError, create_event_store


ROOT = Path(__file__).resolve().parents[1]

REQUIRED_EVENT_TYPES = {
    "daemon.started",
    "daemon.stopped",
    "daemon.failed",
    "state.changed",
    "input.text.received",
    "input.voice.transcribed",
    "input.rejected",
    "turn.started",
    "turn.context.built",
    "turn.finished",
    "turn.failed",
    "brain.requested",
    "brain.responded",
    "brain.failed",
    "brain.switched",
    "voice.speak.queued",
    "voice.speak.started",
    "voice.speak.finished",
    "voice.speak.cancelled",
    "voice.speak.failed",
    "audio.devices.snapshot",
    "listening.lease.created",
    "listening.lease.released",
    "listening.lease.expired",
    "listening.lease.cancelled",
    "tool.requested",
    "tool.approval.required",
    "tool.approved",
    "tool.rejected",
    "tool.started",
    "tool.finished",
    "tool.failed",
    "approval.created",
    "approval.approved",
    "approval.rejected",
    "approval.expired",
    "memory.updated",
    "memory.candidate.created",
    "memory.candidate.promoted",
    "memory.disabled",
    "worker.job.created",
    "worker.job.started",
    "worker.job.progress",
    "worker.job.finished",
    "worker.job.failed",
    "worker.job.cancelled",
    "runtime.process.observed",
    "runtime.legacy.conflict.detected",
    "error.raised",
}


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    db_conn = initialize_database(tmp_path / "dan.db")
    try:
        yield db_conn
    finally:
        close_quietly(db_conn)


@pytest.fixture
def store(conn: sqlite3.Connection) -> EventStore:
    return create_event_store(conn)


def append_sample(
    store: EventStore,
    *,
    index: int = 1,
    correlation_id: str | None = None,
    turn_id: str | None = None,
) -> Event:
    return store.append(
        EventType.TURN_STARTED,
        "tests",
        {"index": index},
        correlation_id=correlation_id,
        turn_id=turn_id,
    )


def test_event_type_catalogue_contains_required_values() -> None:
    assert REQUIRED_EVENT_TYPES.issubset({event_type.value for event_type in EventType})


def test_append_persists_event_and_returns_id(
    conn: sqlite3.Connection, store: EventStore
) -> None:
    event = store.append(
        EventType.INPUT_TEXT_RECEIVED,
        "tests",
        {"text": "hello"},
        correlation_id="corr-1",
        turn_id="turn-1",
    )

    row = conn.execute(
        """
        SELECT id, type, source, correlation_id, turn_id, payload_json
        FROM events
        WHERE id = ?
        """,
        (event.id,),
    ).fetchone()

    assert event.id > 0
    assert row[0] == event.id
    assert row[1] == "input.text.received"
    assert row[2] == "tests"
    assert row[3] == "corr-1"
    assert row[4] == "turn-1"
    assert '"text":"hello"' in row[5]


def test_append_ids_are_monotonic(store: EventStore) -> None:
    first = append_sample(store, index=1)
    second = append_sample(store, index=2)

    assert first.id < second.id


def test_get_returns_correct_event(store: EventStore) -> None:
    event = append_sample(store, index=7, correlation_id="corr-get", turn_id="turn-get")

    assert store.get(event.id) == event
    assert store.get(event.id + 999) is None


def test_latest_returns_newest_first_and_respects_limit(store: EventStore) -> None:
    events = [append_sample(store, index=index) for index in range(5)]

    latest = store.latest(limit=3)

    assert [event.id for event in latest] == [events[4].id, events[3].id, events[2].id]


def test_list_after_returns_ascending_events_after_id(store: EventStore) -> None:
    events = [append_sample(store, index=index) for index in range(4)]

    after = store.list_after(events[1].id, limit=10)

    assert [event.id for event in after] == [events[2].id, events[3].id]


def test_list_by_correlation_id_returns_matching_events(store: EventStore) -> None:
    first = append_sample(store, index=1, correlation_id="corr-match")
    append_sample(store, index=2, correlation_id="corr-other")
    second = append_sample(store, index=3, correlation_id="corr-match")

    matched = store.list_by_correlation_id("corr-match")

    assert [event.id for event in matched] == [first.id, second.id]


def test_list_by_turn_id_returns_matching_events(store: EventStore) -> None:
    first = append_sample(store, index=1, turn_id="turn-match")
    append_sample(store, index=2, turn_id="turn-other")
    second = append_sample(store, index=3, turn_id="turn-match")

    matched = store.list_by_turn_id("turn-match")

    assert [event.id for event in matched] == [first.id, second.id]


def test_invalid_payload_non_dict_fails_before_insert(
    conn: sqlite3.Connection, store: EventStore
) -> None:
    with pytest.raises(EventStoreError, match="payload"):
        store.append(EventType.ERROR_RAISED, "tests", ["not", "an", "object"])  # type: ignore[arg-type]

    count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert count == 0


def test_invalid_payload_non_json_serializable_fails_before_insert(
    conn: sqlite3.Connection, store: EventStore
) -> None:
    with pytest.raises(EventStoreError, match="JSON"):
        store.append(EventType.ERROR_RAISED, "tests", {"bad": object()})

    count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert count == 0


@pytest.mark.parametrize(
    ("event_type", "source"),
    [
        ("", "tests"),
        ("   ", "tests"),
        ("turn.started", ""),
        ("turn.started", "   "),
    ],
)
def test_empty_event_type_or_source_fails(
    conn: sqlite3.Connection,
    store: EventStore,
    event_type: str,
    source: str,
) -> None:
    with pytest.raises(EventStoreError):
        store.append(event_type, source, {})

    count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert count == 0


def test_limit_must_be_positive_and_at_most_1000(store: EventStore) -> None:
    with pytest.raises(EventStoreError, match="positive"):
        store.latest(limit=0)

    with pytest.raises(EventStoreError, match="1000"):
        store.list_after(0, limit=1001)


def test_event_bus_sends_event_to_multiple_subscribers() -> None:
    bus = EventBus()
    event = Event(
        id=1,
        created_at=utc_now_iso(),
        type=EventType.STATE_CHANGED,
        source="tests",
        payload={"state": "idle"},
    )
    first: list[Event] = []
    second: list[Event] = []

    bus.subscribe(first.append)
    bus.subscribe(second.append)
    bus.publish(event)

    assert first == [event]
    assert second == [event]
    assert bus.subscriber_count() == 2


def test_event_bus_unsubscribe_works() -> None:
    bus = EventBus()
    event = Event(
        id=1,
        created_at=utc_now_iso(),
        type=EventType.STATE_CHANGED,
        source="tests",
        payload={"state": "idle"},
    )
    received: list[Event] = []

    unsubscribe = bus.subscribe(received.append)
    unsubscribe()
    bus.publish(event)

    assert received == []
    assert bus.subscriber_count() == 0


def test_event_bus_failing_subscriber_does_not_block_others() -> None:
    bus = EventBus()
    event = Event(
        id=1,
        created_at=utc_now_iso(),
        type=EventType.ERROR_RAISED,
        source="tests",
        payload={"message": "boom"},
    )
    received: list[Event] = []

    def fail(_: Event) -> None:
        raise RuntimeError("subscriber failed")

    bus.subscribe(fail)
    bus.subscribe(received.append)

    bus.publish(event)

    assert received == [event]


def test_event_models_roundtrip_payload_json() -> None:
    payload: dict[str, Any] = {"nested": {"ok": True}, "items": [1, "two"]}
    created_at = utc_now_iso()
    row = {
        "id": 42,
        "created_at": created_at,
        "type": "custom.forward.compatible",
        "source": "tests",
        "correlation_id": "corr-roundtrip",
        "turn_id": "turn-roundtrip",
        "payload_json": event_to_row_payload(payload),
    }

    event = event_from_row(row)

    assert event == Event(
        id=42,
        created_at=created_at,
        type="custom.forward.compatible",
        source="tests",
        payload=payload,
        correlation_id="corr-roundtrip",
        turn_id="turn-roundtrip",
    )
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(Z|\+00:00)$", created_at)


def test_runtime_files_do_not_contain_forbidden_legacy_strings() -> None:
    allowed_contracts = {("dan/voice/shared_broker.py", "/tmp/dan")}
    forbidden = (
        "/Users/" "n1_ozzy" "/Documents/dev/dan",
        "/tmp/dan",
        "afplay",
        "--dangerously-skip-permissions",
    )
    roots = (
        ROOT / "dan",
        ROOT / "config",
        ROOT / "scripts",
        ROOT / "launchd",
    )
    text_suffixes = {".py", ".sql", ".toml", ".md", ".sh", ".example", ".html", ".js", ".css"}
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
