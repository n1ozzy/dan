from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from dan.store.db import close_quietly, initialize_database
from dan.voice.models import RenderSnapshot, SnapshotValidationError, SpeechIntent
from dan.voice.queue import QueueBackpressure, VoiceQueue
from dan.voice.service import VoiceService


def speech_intent(
    text: str = "DAN ma powiedziec dokladnie ten tekst.",
    *,
    source: str = "claude",
    session: str = "standup",
    lane: str = "normal",
    priority: int = 0,
    utterance_index: int = 0,
) -> SpeechIntent:
    return SpeechIntent(
        text=text,
        persona="dan",
        source=source,
        session=session,
        participant="dan",
        priority=priority,
        lane=lane,
        interrupt_policy="finish_current",
        utterance_index=utterance_index,
    )


def complete_snapshot(*, voice: str = "M3") -> RenderSnapshot:
    return RenderSnapshot(
        engine="supertonic",
        engine_version="1.3.1",
        voice_or_style=voice,
        speed=1.0,
        mastering_profile="default",
        dsp="none",
        pronunciations={"runtime": "rantajm"},
        pronunciations_sha256="a" * 64,
        gain=1.0,
        asset_sha256={f"voice:{voice}": "b" * 64},
        config_revision="voice-catalog-v1",
    )


class Resolver:
    def __init__(self, snapshot: RenderSnapshot) -> None:
        self.snapshot = snapshot
        self.calls: list[SpeechIntent] = []

    def resolve(self, intent: SpeechIntent) -> RenderSnapshot:
        self.calls.append(intent)
        return self.snapshot


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = initialize_database(tmp_path / "voice-service.db")
    try:
        yield connection
    finally:
        close_quietly(connection)


def test_submit_resolves_once_and_persists_the_exact_snapshot(conn: sqlite3.Connection) -> None:
    resolver = Resolver(complete_snapshot())
    service = VoiceService(VoiceQueue(conn), resolver)
    intent = speech_intent()

    request = service.submit(intent)

    assert resolver.calls == [intent]
    assert request.intent == intent
    assert request.render_snapshot == resolver.snapshot


def test_incomplete_snapshot_never_reaches_queued(conn: sqlite3.Connection) -> None:
    resolver = Resolver(
        RenderSnapshot(
            engine="supertonic",
            engine_version="1.3.1",
            voice_or_style="M3",
            speed=1.0,
            mastering_profile="default",
            dsp="none",
            pronunciations={},
            pronunciations_sha256="a" * 64,
            gain=1.0,
            asset_sha256={},
            config_revision="voice-catalog-v1",
        )
    )
    service = VoiceService(VoiceQueue(conn), resolver)

    with pytest.raises(SnapshotValidationError):
        service.submit(speech_intent())

    assert service.queue.list() == []


def test_global_backpressure_rejects_without_hiding_a_drop(conn: sqlite3.Connection) -> None:
    queue = VoiceQueue(conn, global_pending_limit=2, session_pending_limit=2)
    service = VoiceService(queue, Resolver(complete_snapshot()))
    service.submit(speech_intent("pierwszy", utterance_index=0))
    service.submit(speech_intent("drugi", utterance_index=1))

    with pytest.raises(QueueBackpressure, match="global"):
        service.submit(speech_intent("trzeci", session="inna", utterance_index=2))

    assert [request.text for request in queue.list()] == ["pierwszy", "drugi"]


def test_per_session_backpressure_names_the_full_session(conn: sqlite3.Connection) -> None:
    queue = VoiceQueue(conn, global_pending_limit=10, session_pending_limit=1)
    service = VoiceService(queue, Resolver(complete_snapshot()))
    service.submit(speech_intent("pierwszy"))

    with pytest.raises(QueueBackpressure, match="standup"):
        service.submit(speech_intent("drugi", utterance_index=1))


def test_claim_order_is_lane_then_priority_then_creation(conn: sqlite3.Connection) -> None:
    service = VoiceService(VoiceQueue(conn), Resolver(complete_snapshot()))
    background = service.submit(speech_intent("background", lane="background", priority=99))
    normal = service.submit(speech_intent("normal", lane="normal", priority=100))
    live_low = service.submit(speech_intent("live low", lane="live", priority=1))
    live_high = service.submit(speech_intent("live high", lane="live", priority=2))

    claimed = [service.queue.claim_next() for _ in range(4)]

    assert [request.id for request in claimed if request is not None] == [
        live_high.id,
        live_low.id,
        normal.id,
        background.id,
    ]


def test_cancel_session_atomically_cancels_current_and_pending(conn: sqlite3.Connection) -> None:
    service = VoiceService(VoiceQueue(conn), Resolver(complete_snapshot()))
    first = service.submit(speech_intent("current", utterance_index=0))
    second = service.submit(speech_intent("pending", utterance_index=1))
    service.queue.claim_next()

    cancelled = service.cancel_session("standup", reason="barge-in")

    assert set(cancelled) == {first.id, second.id}
    assert {request.status for request in service.queue.list()} == {"cancelled"}
