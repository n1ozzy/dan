"""Cancellation tests (G4c — VOICE_STREAMING §7: one idempotent op, 3 legs).

Leg 1 (generation): whatever cancel handle the active generation registered
is invoked. Leg 2 (queue): every unfinished VoiceRequest flips to cancelled
with the frozen voice.speak.cancelled event. Leg 3 (playback): the engine's
current player is stopped — only the broker/engine ever touch audio, so
cancellation never spawns a second speaker path. Repeating the operation is
a documented no-op.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest

from dan.store.db import close_quietly, initialize_database
from dan.voice.cancellation import (
    CancellationCoordinator,
    GenerationRegistration,
    GenerationRegistry,
)
from dan.voice.queue import VoiceQueue, VoiceQueueCancelledError
from tests.voice_helpers import enqueue_voice


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "cancel.db"
    close_quietly(initialize_database(path))
    return path


def connect(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path)


def factory_for(db_path: Path) -> Callable[[], sqlite3.Connection]:
    return lambda: connect(db_path)


def cancelled_events(db_path: Path) -> list[dict]:
    conn = connect(db_path)
    try:
        rows = conn.execute(
            "SELECT payload_json FROM events WHERE type = 'voice.speak.cancelled' ORDER BY id"
        ).fetchall()
        return [json.loads(str(row[0])) for row in rows]
    finally:
        close_quietly(conn)


def queue_statuses(db_path: Path) -> list[str]:
    conn = connect(db_path)
    try:
        return [
            str(row[0])
            for row in conn.execute(
                "SELECT status FROM voice_queue ORDER BY rowid"
            ).fetchall()
        ]
    finally:
        close_quietly(conn)


class StoppableOwner:
    def __init__(self) -> None:
        self.stops = 0

    def stop_playback(self) -> None:
        self.stops += 1


# --- GenerationRegistry (leg 1 handles) --------------------------------------


def test_registry_cancels_registered_generation_once() -> None:
    registry = GenerationRegistry()
    calls: list[str] = []
    registry.register("turn-1", lambda: calls.append("killed"))

    assert registry.active_count() == 1
    assert len(registry.cancel_all()) == 1
    assert calls == ["killed"]
    assert registry.active_count() == 0
    # Idempotent: nothing left to cancel.
    assert len(registry.cancel_all()) == 0
    assert calls == ["killed"]


def test_registry_unregister_removes_the_handle() -> None:
    registry = GenerationRegistry()
    calls: list[str] = []
    registration = registry.register("turn-1", lambda: calls.append("killed"))
    registry.unregister(registration)

    assert registry.active_count() == 0
    assert len(registry.cancel_all()) == 0
    assert calls == []


def test_registry_stale_unregister_cannot_remove_newer_same_turn_generation() -> None:
    registry = GenerationRegistry()
    calls: list[str] = []
    stale = registry.register("turn-1", lambda: calls.append("stale"))
    current = registry.register("turn-1", lambda: calls.append("current"))

    assert isinstance(stale, GenerationRegistration)
    assert isinstance(current, GenerationRegistration)
    assert stale is not current

    registry.unregister(stale)

    assert registry.active_count() == 1
    assert registry.cancel_all() == ["turn-1"]
    assert calls == ["current"]


def test_cancel_all_returns_the_cancelled_turn_ids() -> None:
    # FIX-09: the coordinator tombstones what cancel_all reports, so it must
    # name the turns it cancelled — including generations with no queue rows yet.
    registry = GenerationRegistry()
    registry.register("turn-1", lambda: None)
    registry.register("turn-2", lambda: None)

    cancelled = registry.cancel_all()

    assert sorted(cancelled) == ["turn-1", "turn-2"]


def test_registry_survives_a_cancel_callable_that_raises() -> None:
    registry = GenerationRegistry()
    calls: list[str] = []

    def explode() -> None:
        raise RuntimeError("already dead")

    registry.register("turn-1", explode)
    registry.register("turn-2", lambda: calls.append("killed"))

    assert len(registry.cancel_all()) == 2
    assert calls == ["killed"]
    assert registry.active_count() == 0


def test_cancellation_epoch_captures_registrations_before_queue_linearization(
    db_path: Path,
) -> None:
    registry = GenerationRegistry()
    calls: list[str] = []

    def cancel_initial() -> None:
        calls.append("initial")
        registry.register("turn-admitted", lambda: calls.append("admitted"))

    registry.register("turn-initial", cancel_initial)
    coordinator = CancellationCoordinator(
        factory_for(db_path),
        generation_registry=registry,
        playback_owner=StoppableOwner(),
    )

    result = coordinator.cancel_active_speech(reason="barge_in")

    assert calls == ["initial", "admitted"]
    assert result["generation_cancelled"] == 2
    conn = connect(db_path)
    try:
        with pytest.raises(VoiceQueueCancelledError, match="turn-admitted was cancelled"):
            enqueue_voice(VoiceQueue(conn), "late", session="turn-admitted")
    finally:
        close_quietly(conn)

    after_epoch = registry.register(
        "turn-after-linearization", lambda: calls.append("after")
    )
    assert calls == ["initial", "admitted"]
    assert registry.active_count() == 1
    registry.unregister(after_epoch)


# --- CancellationCoordinator (3 legs, idempotent) ------------------------------


def seed_queue(db_path: Path) -> None:
    conn = connect(db_path)
    try:
        queue = VoiceQueue(conn)
        enqueue_voice(queue, "Pierwsze zdanie tury A.", session="turn-a")
        enqueue_voice(
            queue,
            "Drugie zdanie tury A.",
            session="turn-a",
            utterance_index=1,
        )
        enqueue_voice(queue, "Zdanie zupełnie innej tury B.", session="turn-b")
        queue.claim_next()
    finally:
        close_quietly(conn)


def test_cancel_active_speech_runs_all_three_legs(db_path: Path) -> None:
    seed_queue(db_path)
    registry = GenerationRegistry()
    kills: list[str] = []
    registry.register("turn-a", lambda: kills.append("generation"))
    owner = StoppableOwner()
    coordinator = CancellationCoordinator(
        factory_for(db_path), generation_registry=registry, playback_owner=owner
    )

    result = coordinator.cancel_active_speech(reason="barge_in")

    assert kills == ["generation"]                       # leg 1
    assert queue_statuses(db_path) == ["cancelled"] * 3  # leg 2
    assert owner.stops == 1                              # leg 3
    events = cancelled_events(db_path)
    assert len(events) == 3
    assert {event["turn_id"] for event in events} == {"turn-a", "turn-b"}
    assert result["generation_cancelled"] == 1
    assert result["queue_cancelled"] == 3


def test_cancel_active_speech_is_idempotent(db_path: Path) -> None:
    seed_queue(db_path)
    coordinator = CancellationCoordinator(
        factory_for(db_path),
        generation_registry=GenerationRegistry(),
        playback_owner=StoppableOwner(),
    )

    first = coordinator.cancel_active_speech(reason="barge_in")
    second = coordinator.cancel_active_speech(reason="barge_in")

    assert first["queue_cancelled"] == 3
    assert second["queue_cancelled"] == 0
    assert second["generation_cancelled"] == 0
    assert len(cancelled_events(db_path)) == 3  # no duplicate events


def test_queue_leg_runs_before_playback_leg(db_path: Path) -> None:
    """By the time the player dies, its row is already cancelled — the broker
    must never see a killed playback whose row is still 'speaking' (it would
    mark it failed instead of cancelled)."""

    seed_queue(db_path)

    observed: list[list[str]] = []

    class OrderProbeOwner:
        def stop_playback(self) -> None:
            observed.append(queue_statuses(db_path))

    coordinator = CancellationCoordinator(
        factory_for(db_path),
        generation_registry=GenerationRegistry(),
        playback_owner=OrderProbeOwner(),
    )
    coordinator.cancel_active_speech(reason="barge_in")

    assert observed == [["cancelled", "cancelled", "cancelled"]]


def test_cancel_active_speech_tombstones_cancelled_and_generating_turns(db_path: Path) -> None:
    # FIX-09: after a barge-in, a late delta or FillerTimer of a cancelled turn
    # must be refused at enqueue — for BOTH a turn that had queue rows and a
    # generation that had none yet (registry-only), since the mic barge-in fires
    # on active generation too.
    from dan.voice.queue import VoiceQueueCancelledError

    seed_queue(db_path)  # turn-a (rows), turn-b (rows)
    registry = GenerationRegistry()
    registry.register("turn-generating", lambda: None)  # no queue rows yet
    coordinator = CancellationCoordinator(
        factory_for(db_path),
        generation_registry=registry,
        playback_owner=StoppableOwner(),
    )

    coordinator.cancel_active_speech(reason="barge_in")

    conn = connect(db_path)
    try:
        q = VoiceQueue(conn)
        for cancelled_turn in ("turn-a", "turn-b", "turn-generating"):
            with pytest.raises(VoiceQueueCancelledError):
                enqueue_voice(
                    q,
                    "Spóźniona delta.",
                    session=cancelled_turn,
                    utterance_index=9,
                )
        # An unrelated live turn is unaffected.
        assert enqueue_voice(q, "Żywe zdanie.", session="turn-live").status == "queued"
    finally:
        close_quietly(conn)


def test_engine_without_stop_playback_is_tolerated(db_path: Path) -> None:
    seed_queue(db_path)

    class OwnerWithoutStop:
        pass

    coordinator = CancellationCoordinator(
        factory_for(db_path),
        generation_registry=GenerationRegistry(),
        playback_owner=OwnerWithoutStop(),
    )

    result = coordinator.cancel_active_speech(reason="panel_stop")

    assert result["queue_cancelled"] == 3
    assert result["playback_stopped"] is False
