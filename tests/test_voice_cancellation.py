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
import threading
from pathlib import Path
from typing import Callable

import pytest

from dan.store.db import close_quietly, initialize_database
from dan.voice.cancellation import CancellationCoordinator, GenerationRegistry
from dan.voice.queue import VoiceQueue
from dan.voice.tts import MockTTSEngine, TTSEngineError


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


class StoppableEngine(MockTTSEngine):
    """Engine double that records stop_playback calls."""

    def __init__(self) -> None:
        super().__init__()
        self.stops = 0

    def stop_playback(self) -> None:
        self.stops += 1
        super().stop_playback()


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
    registry.register("turn-1", lambda: calls.append("killed"))
    registry.unregister("turn-1")

    assert registry.active_count() == 0
    assert len(registry.cancel_all()) == 0
    assert calls == []


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


# --- CancellationCoordinator (3 legs, idempotent) ------------------------------


def seed_queue(db_path: Path) -> None:
    conn = connect(db_path)
    try:
        queue = VoiceQueue(conn)
        queue.enqueue(text="Pierwsze zdanie tury A.", turn_id="turn-a", seq=0)
        queue.enqueue(text="Drugie zdanie tury A.", turn_id="turn-a", seq=1)
        queue.enqueue(text="Zdanie zupełnie innej tury B.", turn_id="turn-b", seq=0)
        queue.claim_next()  # turn-a seq 0 is "speaking"
    finally:
        close_quietly(conn)


def test_cancel_active_speech_runs_all_three_legs(db_path: Path) -> None:
    seed_queue(db_path)
    registry = GenerationRegistry()
    kills: list[str] = []
    registry.register("turn-a", lambda: kills.append("generation"))
    engine = StoppableEngine()
    coordinator = CancellationCoordinator(
        factory_for(db_path), generation_registry=registry, engine=engine
    )

    result = coordinator.cancel_active_speech(reason="barge_in")

    assert kills == ["generation"]                       # leg 1
    assert queue_statuses(db_path) == ["cancelled"] * 3  # leg 2
    assert engine.stops == 1                             # leg 3
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
        engine=StoppableEngine(),
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

    class OrderProbeEngine(MockTTSEngine):
        def stop_playback(self) -> None:
            observed.append(queue_statuses(db_path))

    coordinator = CancellationCoordinator(
        factory_for(db_path),
        generation_registry=GenerationRegistry(),
        engine=OrderProbeEngine(),
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
        factory_for(db_path), generation_registry=registry, engine=StoppableEngine()
    )

    coordinator.cancel_active_speech(reason="barge_in")

    conn = connect(db_path)
    try:
        q = VoiceQueue(conn)
        for cancelled_turn in ("turn-a", "turn-b", "turn-generating"):
            with pytest.raises(VoiceQueueCancelledError):
                q.enqueue(text="Spóźniona delta.", turn_id=cancelled_turn, seq=9)
        # An unrelated live turn is unaffected.
        assert q.enqueue(text="Żywe zdanie.", turn_id="turn-live", seq=0).status == "queued"
    finally:
        close_quietly(conn)


def test_engine_without_stop_playback_is_tolerated(db_path: Path) -> None:
    seed_queue(db_path)

    class LegacyEngine:
        name = "legacy"

    coordinator = CancellationCoordinator(
        factory_for(db_path),
        generation_registry=GenerationRegistry(),
        engine=LegacyEngine(),
    )

    result = coordinator.cancel_active_speech(reason="panel_stop")

    assert result["queue_cancelled"] == 3
    assert result["playback_stopped"] is False


# --- MockTTSEngine.stop_playback (leg 3 double) --------------------------------


def test_mock_engine_stop_playback_interrupts_a_blocked_play() -> None:
    gate = threading.Event()  # never set: playback would block forever
    engine = MockTTSEngine(play_gate=gate)
    chunk = engine.synthesize("Zdanie przerwane w trakcie grania.")
    errors: list[Exception] = []
    started = threading.Event()

    def play() -> None:
        started.set()
        try:
            engine.play(chunk)
        except TTSEngineError as exc:
            errors.append(exc)

    thread = threading.Thread(target=play, daemon=True)
    thread.start()
    assert started.wait(timeout=5)

    engine.stop_playback()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert len(errors) == 1


def test_mock_engine_stop_playback_when_idle_is_a_no_op() -> None:
    engine = MockTTSEngine()
    engine.stop_playback()  # nothing playing — must not blow up

    chunk = engine.synthesize("Zdanie grane po bezczynnym stopie.")
    engine.play(chunk)  # and must not poison the NEXT playback

    assert ("play", "Zdanie grane po bezczynnym stopie.") in engine.log
