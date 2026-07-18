"""FIX-04: hot-mic containment + voice broker survivability.

(a) DaemonApp.stop() must stop the recorder (no orphaned sox after an
    in-process restart), (b) stale leases must expire daemon-side without any
    API call (sweeper), (c) the broker thread must survive non-TTS exceptions,
    (d) VoiceBroker.stop() must actually terminate the drain loop and its
    executor.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

from dan.daemon.app import DaemonApp, create_daemon_app
from dan.store.db import close_quietly, initialize_database
from dan.voice.broker import VoiceBroker
from dan.voice.models import RenderSnapshot
from dan.voice.player import MockAudioPlayer
from dan.voice.queue import VoiceQueue
from dan.voice.recorder import MockRecorder
from dan.voice.tts import MockTTSEngine, SynthesizedChunk
from tests.test_api_smoke import write_config
from tests.voice_helpers import enqueue_voice, render_snapshot


@pytest.fixture
def app(tmp_path: Path) -> Iterator[DaemonApp]:
    config_path = write_config(tmp_path / "dan.toml", tmp_path / "home" / "dan.db")
    daemon_app = create_daemon_app(config_path)
    try:
        yield daemon_app
    finally:
        daemon_app.close()


def _write_voice_enabled_config(tmp_path: Path) -> Path:
    config_path = write_config(tmp_path / "dan.toml", tmp_path / "home" / "dan.db")
    body = config_path.read_text(encoding="utf-8")
    body = body.replace(
        "[voice]\nenabled = false",
        "[voice]\nenabled = true",
        1,
    )
    body = body.replace(
        "speak_responses = false",
        "speak_responses = true",
        1,
    )
    body = body.replace(
        "broker_enabled = false",
        "broker_enabled = true",
        1,
    )
    config_path.write_text(body, encoding="utf-8")
    return config_path


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "voice.db"
    close_quietly(initialize_database(path))
    return path


def _connect_factory(path: Path):
    import sqlite3

    # check_same_thread=False: tests hand these connections to sweeper/broker
    # threads while asserting from the main thread.
    return lambda: sqlite3.connect(path, check_same_thread=False)


def voice_config(**overrides) -> SimpleNamespace:
    values = {
        "enabled": True,
        "speak_responses": True,
        "broker_enabled": True,
        "default_tts": "mock",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


# --- (a) stop() must stop the recorder --------------------------------------


def test_daemon_stop_stops_recorder(app: DaemonApp) -> None:
    app.start()
    recorder = app.voice_recorder
    assert isinstance(recorder, MockRecorder)
    recorder.start()  # a lease started listening before shutdown

    app.stop()

    assert recorder.recording is False, "stop() left the microphone hot"
    assert recorder.stopped == 1
    assert app.voice_recorder is None


# --- (b) daemon-side lease sweeper ------------------------------------------


def test_sweeper_expires_stale_lease_without_any_api_call(db_path: Path) -> None:
    """A crashed panel never calls release(); the daemon-side sweeper must
    expire the lease and stop the recorder on its own."""

    from dan.voice.listening import ListeningLeaseManager, ListeningLeaseSweeper

    clock = {"now": "2026-07-03T10:00:00+00:00"}
    recorder = MockRecorder()
    conn = _connect_factory(db_path)()
    manager = ListeningLeaseManager(
        conn,
        config=SimpleNamespace(ptt_hold_ttl_seconds=1, listen_lock_ttl_seconds=1),
        recorder=recorder,
        now=lambda: clock["now"],
    )
    manager.acquire(mode="hold", source="ptt")
    assert recorder.recording is True

    clock["now"] = "2026-07-03T10:00:05+00:00"  # past TTL, client is dead
    sweeper = ListeningLeaseSweeper(manager.active, interval_seconds=0.02)
    sweeper.start()
    try:
        deadline = time.monotonic() + 5
        while recorder.recording and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        sweeper.stop()
    close_quietly(conn)

    assert recorder.recording is False, "expired lease kept recording forever"
    assert recorder.stopped == 1


def test_sweeper_survives_exceptions_and_stops_cleanly() -> None:
    from dan.voice.listening import ListeningLeaseSweeper

    calls = {"count": 0}

    def flaky_sweep():
        calls["count"] += 1
        raise RuntimeError("db hiccup")

    sweeper = ListeningLeaseSweeper(flaky_sweep, interval_seconds=0.01)
    sweeper.start()
    deadline = time.monotonic() + 5
    while calls["count"] < 3 and time.monotonic() < deadline:
        time.sleep(0.01)
    sweeper.stop()

    assert calls["count"] >= 3, "sweeper thread died on the first exception"
    assert not sweeper.is_alive()


def test_daemon_start_wires_lease_sweeper_and_stop_kills_it(app: DaemonApp) -> None:
    app.start()
    sweeper = app.voice_lease_sweeper
    assert sweeper is not None and sweeper.is_alive()

    app.stop()

    assert not sweeper.is_alive()
    assert app.voice_lease_sweeper is None


def test_voice_enabled_daemon_builds_one_native_broker_and_player(tmp_path: Path) -> None:
    daemon_app = create_daemon_app(
        _write_voice_enabled_config(tmp_path),
        voice_resolver=SimpleNamespace(resolve=lambda _intent: render_snapshot()),
    )
    daemon_app.start()
    recorder = daemon_app.voice_recorder
    stt = daemon_app.voice_stt
    gateway = daemon_app.voice_gateway
    sweeper = daemon_app.voice_lease_sweeper
    assert isinstance(recorder, MockRecorder)
    assert daemon_app.voice_service is not None
    assert daemon_app.voice_engine is not None
    assert isinstance(daemon_app.voice_player, MockAudioPlayer)
    assert daemon_app.voice_broker is not None
    assert daemon_app.voice_cancellation is not None
    assert stt is not None
    assert gateway is not None
    assert sweeper is not None and sweeper.is_alive()

    recorder.start()
    daemon_app.close()

    assert recorder.recording is False
    assert recorder.stopped == 1
    assert sweeper.is_alive() is False
    assert daemon_app.voice_recorder is None
    assert daemon_app.voice_service is None
    assert daemon_app.voice_engine is None
    assert daemon_app.voice_player is None
    assert daemon_app.voice_broker is None
    assert daemon_app.voice_stt is None
    assert daemon_app.voice_gateway is None
    assert daemon_app.voice_lease_sweeper is None


# --- (c) broker thread survives non-TTS exceptions ---------------------------


class _RuntimeErrorEngine(MockTTSEngine):
    """Not a TTSEngineError: e.g. sqlite 'database is locked' or a vanished
    binary surfaces as a plain OSError/RuntimeError inside synthesis."""

    def synthesize(
        self,
        text: str,
        snapshot: RenderSnapshot,
    ) -> SynthesizedChunk:
        if "BOOM" in text:
            raise RuntimeError("database is locked")
        return super().synthesize(text, snapshot)


def test_broker_thread_survives_non_tts_exception(db_path: Path) -> None:
    factory = _connect_factory(db_path)
    conn = factory()
    queue = VoiceQueue(conn)
    enqueue_voice(queue, "BOOM w trakcie syntezy.", session="t1")
    engine = _RuntimeErrorEngine()
    broker = VoiceBroker(
        factory,
        config=voice_config(),
        engine=engine,
        player=MockAudioPlayer(),
        poll_interval=0.02,
    )

    broker.start()
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            row = conn.execute(
                "SELECT status FROM voice_queue WHERE turn_id = 't1'"
            ).fetchone()
            if row and row[0] in ("failed", "queued"):
                if row[0] == "failed":
                    break
            time.sleep(0.01)

        assert broker._thread is not None and broker._thread.is_alive(), (
            "a non-TTSEngineError killed the broker thread — DAN is mute"
        )

        # And it still speaks afterwards.
        enqueue_voice(queue, "Zdanie po awarii bazy.", session="t2")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            row = conn.execute(
                "SELECT status FROM voice_queue WHERE turn_id = 't2'"
            ).fetchone()
            if row and row[0] == "done":
                break
            time.sleep(0.01)
        else:
            pytest.fail("broker never recovered after the non-TTS exception")
    finally:
        broker.stop()
        close_quietly(conn)


# --- (d) stop() terminates the drain loop and executor -----------------------


class _SpyPlayer(MockAudioPlayer):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.stop_playback_calls = 0

    def stop(self) -> None:
        self.stop_playback_calls += 1
        super().stop()

    def play(self, chunk: SynthesizedChunk, *, should_play, on_started) -> None:
        time.sleep(0.05)
        super().play(chunk, should_play=should_play, on_started=on_started)


def test_broker_stop_interrupts_drain_loop_and_shuts_executor(db_path: Path) -> None:
    factory = _connect_factory(db_path)
    conn = factory()
    queue = VoiceQueue(conn)
    for seq in range(100):
        enqueue_voice(
            queue,
            f"Zdanie numer {seq} w kolejce.",
            session=f"t-{seq}",
            utterance_index=seq,
        )
    engine = MockTTSEngine()
    player = _SpyPlayer()
    broker = VoiceBroker(
        factory,
        config=voice_config(),
        engine=engine,
        player=player,
        poll_interval=0.02,
    )

    broker.start()
    deadline = time.monotonic() + 5
    while not any(op == "play" for op, _ in player.log) and time.monotonic() < deadline:
        time.sleep(0.01)

    started = time.monotonic()
    broker.stop()
    elapsed = time.monotonic() - started

    assert elapsed < 4, f"stop() did not interrupt the drain loop (took {elapsed:.1f}s)"
    assert broker._thread is None
    assert player.stop_playback_calls >= 1, "stop() must interrupt the current playback"
    assert broker._executor._shutdown, "stop() must shut the prefetch executor down"
    played = sum(1 for op, _ in player.log if op == "play")
    assert played < 100, "stop() played the whole queue instead of stopping"
    close_quietly(conn)
