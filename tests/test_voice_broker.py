from __future__ import annotations

import sqlite3
import threading
import time
import wave
from io import BytesIO
from pathlib import Path

import pytest

from dan.store.db import close_quietly, initialize_database
from dan.voice.broker import (
    VoiceBroker,
    VoiceBrokerError,
    VoiceBrokerOwnershipError,
    VoiceBrokerShutdownTimeout,
)
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


def _wav_audio(duration_seconds: float = 0.1) -> bytes:
    output = BytesIO()
    sample_rate = 1_000
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(b"\x00\x00" * round(duration_seconds * sample_rate))
    return output.getvalue()


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


def test_stop_tracks_blocked_watcher_and_stale_probe_has_no_side_effects(
    db_path: Path,
) -> None:
    resolver = MutableResolver()
    conn = connect(db_path)
    request = VoiceService(VoiceQueue(conn), resolver).submit(
        intent("watcher", interrupt_policy="interruptible")
    )
    player = _CountingStopPlayer()
    broker = VoiceBroker(
        lambda: connect(db_path),
        engine=MockTTSEngine(),
        player=player,
    )
    probe_entered = threading.Event()
    release_probe = threading.Event()
    cancel_calls: list[str] = []

    class QueueRecorder:
        def cancel_superseded_request(self, request_id: str, *, reason: str) -> None:
            cancel_calls.append(request_id)

    def blocking_superseded_probe(_request) -> bool:
        probe_entered.set()
        release_probe.wait(timeout=2)
        return True

    original_status = broker._status
    original_superseded_probe = broker._same_session_noninterruptible_waiting
    original_with_queue = broker._with_queue
    broker._status = lambda _request: "speaking"
    broker._same_session_noninterruptible_waiting = blocking_superseded_probe
    broker._with_queue = lambda action: action(QueueRecorder())
    watcher = broker._start_interrupt_watcher(request)
    assert watcher is not None
    assert probe_entered.wait(timeout=1)

    try:
        with pytest.raises(VoiceBrokerShutdownTimeout):
            broker.stop(join_timeout=0.025)
        with pytest.raises(VoiceBrokerOwnershipError):
            broker.start()

        release_probe.set()
        watcher[1].join(timeout=1)

        assert cancel_calls == []
        assert player.stop_calls == 1
        assert not watcher[1].is_alive()

        broker.stop(join_timeout=1)
        broker._status = original_status
        broker._same_session_noninterruptible_waiting = original_superseded_probe
        broker._with_queue = original_with_queue
        broker.start()
        broker.stop(join_timeout=1)
        assert not any(
            thread.name.startswith("dan-voice-interrupt-")
            for thread in threading.enumerate()
        )
    finally:
        release_probe.set()
        broker._status = original_status
        broker._same_session_noninterruptible_waiting = original_superseded_probe
        broker._with_queue = original_with_queue
        try:
            broker.stop(join_timeout=1)
        except VoiceBrokerError:
            pass
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


class _FailingStopPlayer(MockAudioPlayer):
    def __init__(self) -> None:
        super().__init__()
        self.fail_stop = True

    def stop(self) -> None:
        if self.fail_stop:
            raise RuntimeError("native owner is still live")
        super().stop()


def test_failed_native_player_stop_retains_ownership_and_blocks_start(
    db_path: Path,
) -> None:
    player = _FailingStopPlayer()
    broker = VoiceBroker(
        lambda: connect(db_path),
        engine=MockTTSEngine(),
        player=player,
    )

    try:
        with pytest.raises(VoiceBrokerError, match="player owner"):
            broker.stop()
        with pytest.raises(VoiceBrokerOwnershipError):
            broker.start()
    finally:
        player.fail_stop = False
        broker.stop()


class _BlockedPrefetchEngine(MockTTSEngine):
    def __init__(self) -> None:
        super().__init__()
        self.prefetch_started = threading.Event()
        self.release_prefetch = threading.Event()
        self._active = 0
        self.max_concurrent_synthesis = 0
        self._active_lock = threading.Lock()

    def synthesize(self, text, render_snapshot):
        with self._active_lock:
            self._active += 1
            self.max_concurrent_synthesis = max(
                self.max_concurrent_synthesis,
                self._active,
            )
        try:
            if text == "prefetch":
                self.prefetch_started.set()
                self.release_prefetch.wait(timeout=10)
            return super().synthesize(text, render_snapshot)
        finally:
            with self._active_lock:
                self._active -= 1


def test_stop_with_live_prefetch_preserves_owner_and_refuses_second_executor(
    db_path: Path,
) -> None:
    resolver = MutableResolver()
    conn = connect(db_path)
    service = VoiceService(VoiceQueue(conn), resolver)
    service.submit(intent("current", utterance_index=0))
    service.submit(intent("prefetch", utterance_index=1))
    play_gate = threading.Event()
    engine = _BlockedPrefetchEngine()
    broker = VoiceBroker(
        lambda: connect(db_path),
        engine=engine,
        player=MockAudioPlayer(play_gate=play_gate),
    )
    broker.start()
    assert engine.prefetch_started.wait(timeout=2)
    first_executor = broker._executor

    try:
        with pytest.raises(Exception) as shutdown_error:
            broker.stop(join_timeout=0.025)
        assert shutdown_error.type.__name__ == "VoiceBrokerShutdownTimeout"
        assert broker._executor is first_executor

        with pytest.raises(Exception) as ownership_error:
            broker.start()
        assert ownership_error.type.__name__ == "VoiceBrokerOwnershipError"
        assert broker._executor is first_executor
        assert engine.max_concurrent_synthesis == 1
    finally:
        engine.release_prefetch.set()
        play_gate.set()
        try:
            broker.stop(join_timeout=2)
        except Exception:
            pass
        close_quietly(conn)


class _BlockedCurrentEngine(MockTTSEngine):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def synthesize(self, text, render_snapshot):
        self.started.set()
        self.release.wait(timeout=10)
        return super().synthesize(text, render_snapshot)


def test_stop_waits_for_direct_synthesis_before_releasing_executor(db_path: Path) -> None:
    resolver = MutableResolver()
    conn = connect(db_path)
    VoiceService(VoiceQueue(conn), resolver).submit(intent("blocked-current"))
    engine = _BlockedCurrentEngine()
    broker = VoiceBroker(
        lambda: connect(db_path),
        engine=engine,
        player=MockAudioPlayer(),
    )
    broker.start()
    assert engine.started.wait(timeout=2)
    first_executor = broker._executor

    try:
        with pytest.raises(Exception) as shutdown_error:
            broker.stop(join_timeout=0.025)
        assert shutdown_error.type.__name__ == "VoiceBrokerShutdownTimeout"
        assert broker._executor is first_executor
    finally:
        engine.release.set()
        broker.stop(join_timeout=2)
        close_quietly(conn)


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

    with pytest.raises(Exception) as shutdown_error:
        broker.stop(join_timeout=0.2)
    assert shutdown_error.type.__name__ == "VoiceBrokerShutdownTimeout"

    first = broker._thread
    assert first is not None and first.is_alive(), (
        "stop() must keep the reference to a still-running broker thread"
    )
    with pytest.raises(Exception) as ownership_error:
        broker.start()
    assert ownership_error.type.__name__ == "VoiceBrokerOwnershipError"
    assert broker._thread is first
    assert sum(1 for t in threading.enumerate() if t.name == "dan-voice-broker") == 1

    player.release.set()
    broker.stop(join_timeout=3)
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


class _PostStopSchedulingPlayer(MockAudioPlayer):
    def __init__(self) -> None:
        super().__init__()
        self.stop_called = threading.Event()
        self.plays_after_stop = 0

    def play(self, chunk, *, should_play, on_started) -> None:
        if self.stop_called.is_set():
            self.plays_after_stop += 1
        super().play(chunk, should_play=should_play, on_started=on_started)

    def stop(self) -> None:
        self.stop_called.set()
        super().stop()


def test_stop_is_a_barrier_against_playback_admission(db_path: Path) -> None:
    conn = connect(db_path)
    request = VoiceService(VoiceQueue(conn), MutableResolver()).submit(
        intent("Stop must close playback admission.")
    )
    player = _PostStopSchedulingPlayer()
    broker = VoiceBroker(
        lambda: connect(db_path),
        engine=MockTTSEngine(),
        player=player,
    )
    admission_entered = threading.Event()
    release_admission = threading.Event()
    original_start_watcher = broker._start_interrupt_watcher

    def blocked_start_watcher(current_request):
        admission_entered.set()
        release_admission.wait(timeout=2)
        return original_start_watcher(current_request)

    broker._start_interrupt_watcher = blocked_start_watcher
    broker.start()
    assert admission_entered.wait(timeout=1)

    stop_errors: list[BaseException] = []

    def stop_broker() -> None:
        try:
            broker.stop(join_timeout=2)
        except BaseException as exc:
            stop_errors.append(exc)

    stopper = threading.Thread(target=stop_broker)
    stopper.start()
    assert player.stop_called.wait(timeout=1)
    release_admission.set()
    stopper.join(timeout=2)

    row = conn.execute(
        "SELECT status, playback_confirmed FROM voice_queue WHERE id = ?",
        (request.id,),
    ).fetchone()
    assert not stopper.is_alive()
    assert stop_errors == []
    assert player.plays_after_stop == 0
    assert player.log == []
    assert row != ("done", 1)
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
        self.running = False

    def start(self) -> None:
        self.running = True

    def is_running(self) -> bool:
        return self.running

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
        player=CoreAudioPlayer(
            backend=backend,
            deadline_for_audio=lambda _audio: 0.025,
        ),
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

    def is_running(self) -> bool:
        return True

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
        player=CoreAudioPlayer(
            backend=_ExplodingBackend(),
            deadline_for_audio=lambda _audio: 0.025,
        ),
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


class _WavEngine(MockTTSEngine):
    def synthesize(self, text, render_snapshot):
        chunk = super().synthesize(text, render_snapshot)
        return type(chunk)(text=chunk.text, audio=_wav_audio())


class _RecoveringRouteLossBackend(_FakeCoreAudioBackend):
    def __init__(self) -> None:
        super().__init__()
        self.play_calls = 0
        self.stop_calls = 0
        self.recover_calls = 0

    def play(self, buffer: bytes, completion) -> None:
        from dan.voice.player import NativePlaybackRouteLost

        self.play_calls += 1
        if self.play_calls == 1:
            raise NativePlaybackRouteLost("output route lost")
        super().play(buffer, completion)

    def stop(self) -> None:
        self.stop_calls += 1

    def recover(self) -> None:
        self.recover_calls += 1


def test_route_loss_row_error_is_persisted_without_stalling_broker(db_path: Path) -> None:
    resolver = MutableResolver()
    conn = connect(db_path)
    service = VoiceService(VoiceQueue(conn), resolver)
    first = service.submit(intent("route-lost", utterance_index=0))
    second = service.submit(intent("next", utterance_index=1))
    backend = _RecoveringRouteLossBackend()

    from dan.voice.player import CoreAudioPlayer

    broker = VoiceBroker(
        lambda: connect(db_path),
        engine=_WavEngine(),
        player=CoreAudioPlayer(backend=backend),
    )

    assert broker.drain_all() == 1

    rows = dict(
        conn.execute(
            "SELECT id, status FROM voice_queue WHERE id IN (?, ?)",
            (first.id, second.id),
        )
    )
    errors = dict(
        conn.execute(
            "SELECT id, error FROM voice_queue WHERE id IN (?, ?)",
            (first.id, second.id),
        )
    )
    assert rows == {first.id: "failed", second.id: "done"}
    assert "output route lost" in errors[first.id]
    assert errors[second.id] is None
    assert backend.recover_calls == 1
    close_quietly(conn)
