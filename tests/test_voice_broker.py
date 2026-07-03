"""G3 TTS engines + VoiceBroker + speech pipeline tests (ADR-005, decree §7.3).

Only the broker plays speech; engines are pluggable with mock-only tests;
banned engines are refused by name; the next chunk is synthesized while the
previous one plays; fillers fire at most once per turn.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.store.db import close_quietly, initialize_database
from jarvis.voice.broker import VoiceBroker
from jarvis.voice.queue import VoiceQueue
from jarvis.voice.speech import SpeechPipeline
from jarvis.voice.tts import (
    BannedEngineError,
    MockTTSEngine,
    PlaybackCancelled,
    SynthesizedChunk,
    TTSEngineError,
    build_tts_engine,
)
from tests.git_guards import assert_schema_and_migrations_unchanged


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "voice.db"
    conn = initialize_database(path)
    close_quietly(conn)
    return path


def connect(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path)


def voice_config(**overrides) -> SimpleNamespace:
    values = {
        "enabled": True,
        "speak_responses": True,
        "broker_enabled": True,
        "default_tts": "mock",
        "fillers": ["Już sprawdzam.", "Chwila."],
        "filler_after_ms": 50,
        "min_sentence_chars": 12,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


# --- engines -------------------------------------------------------------


def test_build_engine_returns_mock() -> None:
    engine = build_tts_engine("mock")
    assert isinstance(engine, MockTTSEngine)


@pytest.mark.parametrize("banned", ["edgetts", "edgeTTS", "piper", "xtts", "XTTS"])
def test_banned_engines_are_refused_by_decree(banned: str) -> None:
    with pytest.raises(BannedEngineError):
        build_tts_engine(banned)


def test_unknown_engine_fails_closed() -> None:
    with pytest.raises(TTSEngineError):
        build_tts_engine("nope")


def test_decreed_real_engines_are_reserved_not_silent() -> None:
    # Chatterbox is decreed (§7.3) but lands in G5; asking for it must say so
    # loudly instead of falling back to anything else. Supertonic is real now
    # (tests/test_voice_tts_supertonic.py) but still refuses to build without
    # the daemon config instead of guessing paths silently.
    with pytest.raises(TTSEngineError):
        build_tts_engine("chatterbox")
    with pytest.raises(TTSEngineError):
        build_tts_engine("supertonic")


# --- broker ----------------------------------------------------------------


def test_broker_drains_queue_in_order_and_marks_done(db_path: Path) -> None:
    conn = connect(db_path)
    queue = VoiceQueue(conn)
    queue.enqueue(text="Pierwsze zdanie kolejki.", turn_id="t", kind="sentence", seq=0)
    queue.enqueue(text="Drugie zdanie kolejki.", turn_id="t", kind="sentence", seq=1)
    engine = MockTTSEngine()
    broker = VoiceBroker(lambda: connect(db_path), config=voice_config(), engine=engine)

    played = broker.drain_all()

    assert played == 2
    assert [op for op, _ in engine.log if op == "play"] == ["play", "play"]
    texts = [text for op, text in engine.log if op == "play"]
    assert texts == ["Pierwsze zdanie kolejki.", "Drugie zdanie kolejki."]
    statuses = [row[0] for row in conn.execute("SELECT status FROM voice_queue").fetchall()]
    assert statuses == ["done", "done"]
    close_quietly(conn)


def test_broker_prefetches_next_chunk_while_playing(db_path: Path) -> None:
    conn = connect(db_path)
    queue = VoiceQueue(conn)
    queue.enqueue(text="Zdanie grane jako pierwsze.", turn_id="t", kind="sentence", seq=0)
    queue.enqueue(text="Zdanie syntezowane w tle.", turn_id="t", kind="sentence", seq=1)
    close_quietly(conn)

    gate = threading.Event()
    engine = MockTTSEngine(play_gate=gate)
    broker = VoiceBroker(lambda: connect(db_path), config=voice_config(), engine=engine)

    thread = threading.Thread(target=broker.drain_all, daemon=True)
    thread.start()
    # While play #1 is blocked on the gate, synth #2 must already happen.
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        synths = [text for op, text in engine.log if op == "synth"]
        if "Zdanie syntezowane w tle." in synths:
            break
        time.sleep(0.01)
    else:
        gate.set()
        thread.join(timeout=5)
        pytest.fail(f"next chunk was not prefetched during playback: {engine.log}")
    gate.set()
    thread.join(timeout=5)
    assert not thread.is_alive()


def test_broker_engine_failure_marks_failed_and_continues(db_path: Path) -> None:
    conn = connect(db_path)
    queue = VoiceQueue(conn)
    queue.enqueue(text="EXPLODE podczas syntezy.", turn_id="t", kind="sentence", seq=0)
    queue.enqueue(text="Zdanie po awarii silnika.", turn_id="t", kind="sentence", seq=1)
    engine = MockTTSEngine(explode_on="EXPLODE")
    broker = VoiceBroker(lambda: connect(db_path), config=voice_config(), engine=engine)

    played = broker.drain_all()

    assert played == 1
    statuses = dict(conn.execute("SELECT text, status FROM voice_queue").fetchall())
    assert statuses["EXPLODE podczas syntezy."] == "failed"
    assert statuses["Zdanie po awarii silnika."] == "done"
    close_quietly(conn)


def test_broker_never_plays_a_row_cancelled_after_claim(db_path: Path) -> None:
    """Barge-in race (G4c): a prefetched row cancelled during the current
    playback must be skipped — the broker re-checks DB truth before playing."""

    conn = connect(db_path)
    queue = VoiceQueue(conn)
    queue.enqueue(text="Zdanie grane przed barge-in.", turn_id="turn-1", kind="sentence", seq=0)
    queue.enqueue(text="Zdanie anulowane w locie.", turn_id="turn-2", kind="sentence", seq=1)

    gate = threading.Event()
    engine = MockTTSEngine(play_gate=gate)
    broker = VoiceBroker(lambda: connect(db_path), config=voice_config(), engine=engine)

    thread = threading.Thread(target=broker.drain_all, daemon=True)
    thread.start()
    # Wait until row #2 is claimed (prefetch) while play #1 blocks on the gate.
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        row = conn.execute(
            "SELECT status FROM voice_queue WHERE turn_id = 'turn-2'"
        ).fetchone()
        if row and row[0] == "speaking":
            break
        time.sleep(0.01)
    else:
        gate.set()
        thread.join(timeout=5)
        pytest.fail("row #2 was never claimed for prefetch")

    queue.cancel_turn("turn-2")  # leg 2 flips the claimed row mid-playback
    gate.set()
    thread.join(timeout=5)
    assert not thread.is_alive()

    played = [text for op, text in engine.log if op == "play"]
    assert played == ["Zdanie grane przed barge-in."]
    statuses = dict(conn.execute("SELECT text, status FROM voice_queue").fetchall())
    assert statuses["Zdanie anulowane w locie."] == "cancelled"
    close_quietly(conn)


def test_broker_stamps_spoken_at_only_on_rows_it_actually_played(db_path: Path) -> None:
    # FIX-09: the anti-echo corpus reads spoken_at, so the broker must stamp it
    # for every chunk it plays — and only those. A synthesis failure (never
    # reached the speaker) must leave spoken_at NULL.
    conn = connect(db_path)
    queue = VoiceQueue(conn)
    queue.enqueue(text="Zdanie które realnie zagra.", turn_id="t", kind="sentence", seq=0)
    queue.enqueue(text="EXPLODE zanim cokolwiek zabrzmi.", turn_id="t", kind="sentence", seq=1)
    engine = MockTTSEngine(explode_on="EXPLODE")
    broker = VoiceBroker(lambda: connect(db_path), config=voice_config(), engine=engine)

    broker.drain_all()

    spoken = dict(
        conn.execute("SELECT text, spoken_at FROM voice_queue").fetchall()
    )
    assert spoken["Zdanie które realnie zagra."] is not None
    assert spoken["EXPLODE zanim cokolwiek zabrzmi."] is None
    close_quietly(conn)


def test_mock_engine_skips_playback_when_should_play_is_false() -> None:
    # FIX-09 TOCTOU: the engine consults should_play under its player lock right
    # before spawning, so a row cancelled in the check->spawn gap never plays.
    engine = MockTTSEngine()

    with pytest.raises(PlaybackCancelled):
        engine.play(SynthesizedChunk(text="Anulowane w locie.", audio=b"audio"), should_play=lambda: False)

    assert [op for op, _ in engine.log if op == "play"] == []


def test_broker_does_not_play_a_row_cancelled_in_the_spawn_gap(db_path: Path) -> None:
    # The narrow TOCTOU the plain pre-play _still_speaking check cannot close:
    # the cancel lands AFTER that check, in the gap before the player spawns.
    # The engine's should_play re-check (under the player lock) closes it.
    conn = connect(db_path)
    queue = VoiceQueue(conn)
    request = queue.enqueue(text="Anulowane tuż przed Popen.", turn_id="turn-x", seq=0)

    class GapCancelEngine:
        name = "gap"

        def __init__(self) -> None:
            self.played: list[str] = []

        def synthesize(self, text: str) -> SynthesizedChunk:
            return SynthesizedChunk(text=text, audio=b"audio-bytes")

        def play(self, chunk: SynthesizedChunk, should_play=None) -> None:
            # Barge-in lands right here, in the check->spawn gap.
            gap_conn = connect(db_path)
            try:
                VoiceQueue(gap_conn).cancel_turn("turn-x")
            finally:
                close_quietly(gap_conn)
            if should_play is not None and not should_play():
                raise PlaybackCancelled("cancelled in the spawn gap")
            self.played.append(chunk.text)

        def stop_playback(self) -> None:
            return None

    engine = GapCancelEngine()
    broker = VoiceBroker(lambda: connect(db_path), config=voice_config(), engine=engine)

    broker.drain_all()

    assert engine.played == []
    status = conn.execute(
        "SELECT status FROM voice_queue WHERE id = ?", (request.id,)
    ).fetchone()[0]
    assert status == "cancelled"
    close_quietly(conn)


def test_broker_recovers_orphaned_speaking_rows_on_start(db_path: Path) -> None:
    conn = connect(db_path)
    queue = VoiceQueue(conn)
    queue.enqueue(text="Osierocone przez restart zdanie.", turn_id="t", kind="sentence", seq=0)
    queue.claim_next()  # simulate a crash mid-playback
    close_quietly(conn)

    broker = VoiceBroker(
        lambda: connect(db_path), config=voice_config(), engine=MockTTSEngine()
    )
    played = broker.drain_all()

    assert played == 1


# --- speech pipeline (chunker -> queue + fillers) ---------------------------


def test_speak_text_enqueues_sentences_with_seq(db_path: Path) -> None:
    pipeline = SpeechPipeline(lambda: connect(db_path), config=voice_config())

    count = pipeline.speak_text(
        turn_id="turn-1",
        text="Pierwsze zdanie odpowiedzi. Drugie zdanie odpowiedzi.",
    )

    assert count == 2
    conn = connect(db_path)
    rows = conn.execute(
        "SELECT text, metadata_json FROM voice_queue ORDER BY rowid"
    ).fetchall()
    close_quietly(conn)
    assert [row[0] for row in rows] == [
        "Pierwsze zdanie odpowiedzi.",
        "Drugie zdanie odpowiedzi.",
    ]
    assert '"seq": 0' in rows[0][1]
    assert '"seq": 1' in rows[1][1]


def test_speak_text_never_enqueues_tool_call_blocks(db_path: Path) -> None:
    pipeline = SpeechPipeline(lambda: connect(db_path), config=voice_config())

    pipeline.speak_text(
        turn_id="turn-1",
        text=(
            "Sprawdzam plik dla ciebie teraz. "
            '<jarvis_tool_call>{"name":"file_read"}</jarvis_tool_call> '
            "Zaraz wrócę z wynikiem pliku."
        ),
    )

    conn = connect(db_path)
    texts = [row[0] for row in conn.execute("SELECT text FROM voice_queue").fetchall()]
    close_quietly(conn)
    assert texts
    assert all("tool_call" not in text and "file_read" not in text for text in texts)


def test_speak_text_disabled_is_a_no_op(db_path: Path) -> None:
    pipeline = SpeechPipeline(
        lambda: connect(db_path), config=voice_config(speak_responses=False)
    )

    count = pipeline.speak_text(turn_id="t", text="Nie powinno trafić do kolejki.")

    assert count == 0
    conn = connect(db_path)
    rows = conn.execute("SELECT COUNT(*) FROM voice_queue").fetchone()[0]
    close_quietly(conn)
    assert rows == 0


def test_filler_fires_once_when_generation_is_slow(db_path: Path) -> None:
    pipeline = SpeechPipeline(lambda: connect(db_path), config=voice_config())

    timer = pipeline.arm_filler(turn_id="turn-slow")
    time.sleep(0.2)  # past filler_after_ms=50
    timer.disarm()

    conn = connect(db_path)
    rows = conn.execute(
        "SELECT text, metadata_json, interrupt_policy FROM voice_queue"
    ).fetchall()
    close_quietly(conn)
    assert len(rows) == 1
    assert rows[0][0] in ("Już sprawdzam.", "Chwila.")
    assert '"kind": "filler"' in rows[0][1]
    assert rows[0][2] == "interruptible"


def test_filler_does_not_fire_when_disarmed_in_time(db_path: Path) -> None:
    pipeline = SpeechPipeline(
        lambda: connect(db_path), config=voice_config(filler_after_ms=5000)
    )

    timer = pipeline.arm_filler(turn_id="turn-fast")
    timer.disarm()
    time.sleep(0.1)

    conn = connect(db_path)
    rows = conn.execute("SELECT COUNT(*) FROM voice_queue").fetchone()[0]
    close_quietly(conn)
    assert rows == 0


# --- daemon integration -------------------------------------------------------


def test_finished_turn_is_spoken_through_the_broker(tmp_path: Path) -> None:
    from jarvis.daemon.app import create_daemon_app
    from tests.test_api_smoke import config_text, request_json, running_server

    config_path = tmp_path / "jarvis.toml"
    config_path.write_text(
        config_text(tmp_path / "home" / "jarvis.db")
        .replace("[voice]\nenabled = false", "[voice]\nenabled = true")
        .replace("speak_responses = false", "speak_responses = true")
        .replace("broker_enabled = false", "broker_enabled = true"),
        encoding="utf-8",
    )
    daemon_app = create_daemon_app(config_path)
    daemon_app.start()
    try:
        with running_server(daemon_app) as base_url:
            status, turn = request_json(
                "POST",
                f"{base_url}/input/text",
                {"text": "Powiedz pierwsze zdanie. Powiedz drugie zdanie."},
            )
            assert status == 200, turn

            db = tmp_path / "home" / "jarvis.db"
            deadline = time.monotonic() + 10
            statuses: list[str] = []
            while time.monotonic() < deadline:
                conn = sqlite3.connect(db)
                statuses = [
                    row[0]
                    for row in conn.execute(
                        "SELECT status FROM voice_queue ORDER BY rowid"
                    ).fetchall()
                ]
                close_quietly(conn)
                if statuses and all(s == "done" for s in statuses):
                    break
                time.sleep(0.1)
            assert statuses, "no voice requests were enqueued for the finished turn"
            assert all(s == "done" for s in statuses), statuses
    finally:
        daemon_app.close()


def test_banned_engine_in_config_kills_daemon_at_startup(tmp_path: Path) -> None:
    from jarvis.daemon.app import create_daemon_app
    from tests.test_api_smoke import config_text

    config_path = tmp_path / "jarvis.toml"
    config_path.write_text(
        config_text(tmp_path / "home" / "jarvis.db")
        .replace("[voice]\nenabled = false", "[voice]\nenabled = true")
        .replace('default_tts = "mock"', 'default_tts = "edgetts"'),
        encoding="utf-8",
    )
    daemon_app = create_daemon_app(config_path)
    with pytest.raises(BannedEngineError):
        daemon_app.start()


def test_schema_and_migrations_are_unchanged() -> None:
    assert_schema_and_migrations_unchanged(ROOT)
