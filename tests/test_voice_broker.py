from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

import pytest

from dan.store.db import close_quietly, initialize_database
from dan.voice.broker import VoiceBroker
from dan.voice.models import RenderSnapshot, SpeechIntent
from dan.voice.player import MockAudioPlayer
from dan.voice.queue import VoiceQueue
from dan.voice.service import VoiceService
from dan.voice.tts import (
    BannedEngineError,
    MockTTSEngine,
    PlaybackCancelled,
    TTSEngineError,
    build_tts_engine,
)


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


def test_external_cancel_stops_noninterruptible_playback_without_tail(db_path: Path) -> None:
    # An external cancel_request (VoiceService/API) must reach the live
    # player even for a non-interruptible request: no chunk may finish its
    # tail after the row was cancelled in the DB.
    resolver = MutableResolver()
    conn = connect(db_path)
    service = VoiceService(VoiceQueue(conn), resolver)
    request = service.submit(intent("Zewnetrzny cancel ma zatrzymac playback."))
    assert request.interrupt_policy == "finish_current"
    play_gate = threading.Event()
    player = MockAudioPlayer(play_gate=play_gate)
    broker = VoiceBroker(lambda: connect(db_path), engine=MockTTSEngine(), player=player)
    thread = threading.Thread(target=broker.drain_all)
    thread.start()
    assert player.started.wait(timeout=2)
    # Get past the player's one-shot pre-schedule re-check into the actual
    # playback loop, where only an explicit player.stop() can interrupt.
    time.sleep(0.1)

    assert service.cancel_request(request.id, reason="external-cancel")
    thread.join(timeout=3)

    row = conn.execute(
        "SELECT status, playback_confirmed FROM voice_queue WHERE id = ?",
        (request.id,),
    ).fetchone()
    assert not thread.is_alive()
    assert row == ("cancelled", 0)
    assert ("play_interrupted", request.text) in player.log
    assert ("play", request.text) not in player.log
    close_quietly(conn)


class _CountingStopPlayer(MockAudioPlayer):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.stop_calls = 0

    def stop(self) -> None:
        self.stop_calls += 1
        super().stop()


def test_watcher_never_stops_playback_after_a_normal_done(db_path: Path) -> None:
    # The always-on cancel watcher must distinguish terminal states: a row
    # that finished as 'done' is not a cancellation, so player.stop() must
    # not fire into the next request.
    resolver = MutableResolver()
    conn = connect(db_path)
    service = VoiceService(VoiceQueue(conn), resolver)
    service.submit(intent("pierwszy", utterance_index=0))
    service.submit(intent("drugi", utterance_index=1))
    player = _CountingStopPlayer()
    broker = VoiceBroker(lambda: connect(db_path), engine=MockTTSEngine(), player=player)

    assert broker.drain_all() == 2

    assert player.stop_calls == 0
    assert [text for operation, text in player.log if operation == "play"] == [
        "pierwszy",
        "drugi",
    ]
    close_quietly(conn)


class _WedgedPlayer:
    """Player stuck in native playback that ignores stop(): worst-case join."""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def play(self, chunk, *, should_play, on_started) -> None:
        on_started()
        self.started.set()
        self.release.wait(timeout=10)

    def stop(self) -> None:
        pass


def test_stop_timeout_keeps_thread_ownership_and_start_refuses_second_broker(
    db_path: Path,
) -> None:
    resolver = MutableResolver()
    conn = connect(db_path)
    service = VoiceService(VoiceQueue(conn), resolver)
    service.submit(intent("Wedged player blokuje join."))
    player = _WedgedPlayer()
    broker = VoiceBroker(lambda: connect(db_path), engine=MockTTSEngine(), player=player)
    broker.start()
    assert player.started.wait(timeout=2)

    broker.stop(join_timeout=0.2)

    first = broker._thread
    assert first is not None and first.is_alive(), (
        "stop() must keep the reference to a still-running broker thread"
    )
    broker.start()
    assert broker._thread is first
    assert sum(1 for t in threading.enumerate() if t.name == "dan-voice-broker") == 1

    player.release.set()
    first.join(timeout=3)
    assert not first.is_alive()
    close_quietly(conn)


def test_synthesis_finished_after_stop_never_starts_playback(db_path: Path) -> None:
    resolver = MutableResolver()
    conn = connect(db_path)
    service = VoiceService(VoiceQueue(conn), resolver)
    service.submit(intent("Po stopie ma byc cisza."))
    engine = MockTTSEngine()
    player = MockAudioPlayer()
    broker = VoiceBroker(lambda: connect(db_path), engine=engine, player=player)

    broker.stop()  # no thread yet: just raises the stop flag
    assert broker.drain_all() == 0

    assert player.log == []
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


class _FakeCoreAudioBackend:
    """Minimal native-backend stand-in: schedules synchronously, logs buffers."""

    def __init__(self) -> None:
        self.audio: list[bytes] = []

    def start(self) -> None:
        pass

    def make_buffer(self, audio: bytes) -> bytes:
        return bytes(audio)

    def play(self, buffer: bytes, completion) -> None:
        self.audio.append(buffer)
        completion()

    def stop(self) -> None:
        pass


def test_real_broker_with_real_coreaudio_player_confirms_playback(db_path: Path) -> None:
    # Regression for the broker x CoreAudioPlayer should_play contract: the
    # native player re-checks the predicate AFTER on_started (row is already
    # 'speaking'), so a predicate limited to 'synthesizing' cancels every
    # native playback before schedule and no row can ever reach
    # playback_confirmed=1.
    from dan.voice.player import CoreAudioPlayer

    resolver = MutableResolver()
    conn = connect(db_path)
    service = VoiceService(VoiceQueue(conn), resolver)
    request = service.submit(intent("Prawdziwy player ma zagrać ten chunk."))
    backend = _FakeCoreAudioBackend()
    broker = VoiceBroker(
        lambda: connect(db_path),
        engine=MockTTSEngine(),
        player=CoreAudioPlayer(backend=backend),
    )

    assert broker.drain_all() == 1

    assert len(backend.audio) == 1
    row = conn.execute(
        "SELECT status, playback_confirmed, playback_started_at FROM voice_queue WHERE id = ?",
        (request.id,),
    ).fetchone()
    assert row[:2] == ("done", 1)
    assert row[2] is not None  # successful schedule reports playback start
    close_quietly(conn)


class _ExplodingBackend:
    """Native backend whose schedule call fails before any audio starts."""

    def start(self) -> None:
        pass

    def make_buffer(self, audio: bytes) -> bytes:
        return bytes(audio)

    def play(self, buffer: bytes, completion) -> None:
        raise RuntimeError("schedule blew up")

    def stop(self) -> None:
        pass


def test_failed_schedule_never_reports_playback_started(db_path: Path) -> None:
    # Telemetry truth: on_started (-> 'speaking' + playback_started_at) may
    # only fire AFTER the buffer was actually handed to the backend. A failed
    # schedule must leave the row without any playback telemetry.
    from dan.voice.player import CoreAudioPlayer

    resolver = MutableResolver()
    conn = connect(db_path)
    service = VoiceService(VoiceQueue(conn), resolver)
    request = service.submit(intent("Ten chunk nigdy nie zagra."))
    broker = VoiceBroker(
        lambda: connect(db_path),
        engine=MockTTSEngine(),
        player=CoreAudioPlayer(backend=_ExplodingBackend()),
    )

    assert broker.drain_all() == 0

    row = conn.execute(
        "SELECT status, playback_started_at FROM voice_queue WHERE id = ?",
        (request.id,),
    ).fetchone()
    assert row == ("failed", None)
    close_quietly(conn)


class _PreScheduleCancelPlayer:
    """Player double for the check->schedule gap: every play is skipped."""

    def play(self, chunk, *, should_play, on_started) -> None:
        raise PlaybackCancelled(f"playback skipped for {chunk.text!r}")

    def stop(self) -> None:
        pass


def test_pre_schedule_cancel_leaves_no_hanging_synthesizing_row(db_path: Path) -> None:
    resolver = MutableResolver()
    conn = connect(db_path)
    service = VoiceService(VoiceQueue(conn), resolver)
    request = service.submit(intent("Skip przed schedule bez wiszacego rowa."))
    broker = VoiceBroker(
        lambda: connect(db_path),
        engine=MockTTSEngine(),
        player=_PreScheduleCancelPlayer(),
    )

    assert broker.drain_all() == 0

    row = conn.execute(
        "SELECT status, playback_started_at FROM voice_queue WHERE id = ?",
        (request.id,),
    ).fetchone()
    assert row == ("cancelled", None)
    close_quietly(conn)
