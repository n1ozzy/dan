from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from dan.store.db import close_quietly, initialize_database
from dan.voice.broker import VoiceBroker
from dan.voice.models import RenderSnapshot, SpeechIntent
from dan.voice.player import MockAudioPlayer
from dan.voice.queue import VoiceQueue
from dan.voice.service import VoiceService
from dan.voice.tts import BannedEngineError, MockTTSEngine, TTSEngineError, build_tts_engine


def snapshot(voice: str = "M3") -> RenderSnapshot:
    return RenderSnapshot(
        engine="supertonic",
        engine_version="1.3.1",
        voice_or_style=voice,
        speed=1.25,
        mastering_profile="clean",
        dsp="none",
        pronunciations={},
        pronunciations_sha256="a" * 64,
        gain=1.0,
        asset_sha256={f"voice:{voice}": "b" * 64},
        config_revision="voice-catalog-v1",
    )


def intent(
    text: str,
    *,
    source: str = "claude",
    session: str = "standup",
    lane: str = "normal",
    utterance_index: int = 0,
    interrupt_policy: str = "finish_current",
) -> SpeechIntent:
    return SpeechIntent(
        text=text,
        persona="dan",
        source=source,
        session=session,
        participant="dan",
        priority=0,
        lane=lane,
        interrupt_policy=interrupt_policy,
        utterance_index=utterance_index,
    )


class MutableResolver:
    def __init__(self) -> None:
        self.snapshot = snapshot()
        self.calls = 0

    def resolve(self, speech_intent: SpeechIntent) -> RenderSnapshot:
        self.calls += 1
        return self.snapshot


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "voice-broker.db"
    conn = initialize_database(path)
    close_quietly(conn)
    return path


def connect(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path)


def test_build_engine_returns_snapshot_only_mock() -> None:
    assert isinstance(build_tts_engine("mock"), MockTTSEngine)


@pytest.mark.parametrize("name", ["edgetts", "piper", "xtts"])
def test_banned_engines_are_refused(name: str) -> None:
    with pytest.raises(BannedEngineError):
        build_tts_engine(name)


def test_unknown_engine_fails_closed() -> None:
    with pytest.raises(TTSEngineError):
        build_tts_engine("unknown")


def test_broker_executes_stored_snapshot_without_reresolve(db_path: Path) -> None:
    resolver = MutableResolver()
    conn = connect(db_path)
    service = VoiceService(VoiceQueue(conn), resolver)
    request = service.submit(intent("Nie zmieniaj mi glosu po enqueue."))
    resolver.snapshot = snapshot("M1")
    engine = MockTTSEngine()
    player = MockAudioPlayer()
    broker = VoiceBroker(lambda: connect(db_path), engine=engine, player=player)

    assert broker.drain_all() == 1

    assert resolver.calls == 1
    assert engine.synth_calls[0].snapshot == request.render_snapshot
    assert player.log == [("play", "Nie zmieniaj mi glosu po enqueue.")]
    close_quietly(conn)


def test_two_producers_never_overlap_one_player(db_path: Path) -> None:
    resolver = MutableResolver()
    conn = connect(db_path)
    service = VoiceService(VoiceQueue(conn), resolver)
    service.submit(intent("pierwszy", source="claude", utterance_index=0))
    service.submit(intent("drugi", source="codex", utterance_index=1))
    player = MockAudioPlayer()
    broker = VoiceBroker(
        lambda: connect(db_path),
        engine=MockTTSEngine(),
        player=player,
    )

    assert broker.drain_all() == 2

    assert player.max_parallel_buffers == 1
    assert [text for operation, text in player.log if operation == "play"] == [
        "pierwszy",
        "drugi",
    ]
    close_quietly(conn)


def test_prefetch_is_bounded_to_one_request(db_path: Path) -> None:
    resolver = MutableResolver()
    conn = connect(db_path)
    service = VoiceService(VoiceQueue(conn), resolver)
    for index in range(3):
        service.submit(intent(f"chunk-{index}", utterance_index=index))
    play_gate = threading.Event()
    player = MockAudioPlayer(play_gate=play_gate)
    engine = MockTTSEngine()
    broker = VoiceBroker(lambda: connect(db_path), engine=engine, player=player)

    thread = threading.Thread(target=broker.drain_all)
    thread.start()
    assert player.started.wait(timeout=2)

    assert [call.text for call in engine.synth_calls] == ["chunk-0", "chunk-1"]
    play_gate.set()
    thread.join(timeout=3)
    assert not thread.is_alive()
    close_quietly(conn)


def test_cancellation_stops_native_player_and_never_confirms_tail(db_path: Path) -> None:
    resolver = MutableResolver()
    conn = connect(db_path)
    service = VoiceService(VoiceQueue(conn), resolver)
    request = service.submit(intent("To ma zostac uciete bez ogona."))
    play_gate = threading.Event()
    player = MockAudioPlayer(play_gate=play_gate)
    broker = VoiceBroker(lambda: connect(db_path), engine=MockTTSEngine(), player=player)
    thread = threading.Thread(target=broker.drain_all)
    thread.start()
    assert player.started.wait(timeout=2)

    service.cancel_session("standup", reason="barge-in")
    broker.stop_playback()
    thread.join(timeout=3)

    row = conn.execute(
        "SELECT status, playback_confirmed FROM voice_queue WHERE id = ?",
        (request.id,),
    ).fetchone()
    assert not thread.is_alive()
    assert row == ("cancelled", 0)
    assert ("play", request.text) not in player.log
    assert ("play_interrupted", request.text) in player.log
    close_quietly(conn)


def test_synthesis_failure_marks_failed_and_continues(db_path: Path) -> None:
    resolver = MutableResolver()
    conn = connect(db_path)
    service = VoiceService(VoiceQueue(conn), resolver)
    service.submit(intent("EXPLODE", utterance_index=0))
    service.submit(intent("dalej", utterance_index=1))
    broker = VoiceBroker(
        lambda: connect(db_path),
        engine=MockTTSEngine(explode_on="EXPLODE"),
        player=MockAudioPlayer(),
    )

    assert broker.drain_all() == 1

    assert dict(conn.execute("SELECT text, status FROM voice_queue")) == {
        "EXPLODE": "failed",
        "dalej": "done",
    }
    close_quietly(conn)
