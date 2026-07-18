"""VoiceTurnGateway tests (G4c — transcript → turn ONLY behind anti-echo).

The gateway is the single wiring point between the STT pipeline and the
TurnOrchestrator (ADR-011: voice enters the same orchestrator as text). An
echo can never become a turn by construction: the anti-echo gate runs
before any turn is started. A transcript that survives the gate while
DAN is speaking is the mic-side barge-in trigger — cancellation (all 3
legs) fires BEFORE the new turn starts. Turns run on the gateway's own
worker so the STT thread is never blocked by a generating brain.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from dan.store.db import close_quietly
from dan.voice.anti_echo import EchoDecision
from dan.voice.gateway import VoiceTurnGateway
from dan.voice.queue import VoiceQueue
from tests.voice_helpers import enqueue_voice


class BusyError(Exception):
    pass


@dataclass
class StubAntiEcho:
    accepted: bool = True
    seen: list[str] = field(default_factory=list)

    def accepts_transcript(self, text: str) -> EchoDecision:
        self.seen.append(text)
        if self.accepted:
            return EchoDecision(accepted=True, reason="ok", matched_text=None)
        return EchoDecision(accepted=False, reason="echo", matched_text="spoken")


class Harness:
    """Shared ordered log so tests can assert cancel-before-turn."""

    def __init__(
        self,
        *,
        accepted: bool = True,
        speech_active: bool = False,
        busy_failures: int = 0,
        starter_error: Exception | None = None,
    ) -> None:
        self.log: list[tuple[str, Any]] = []
        self.anti_echo = StubAntiEcho(accepted=accepted)
        self._speech_active = speech_active
        self._busy_left = busy_failures
        self._starter_error = starter_error
        self.turn_started = threading.Event()

    def cancel_active_speech(self, *, reason: str, source: str | None = None) -> dict[str, Any]:
        self.log.append(("cancel", reason))
        return {"queue_cancelled": 1, "generation_cancelled": 0, "playback_stopped": True}

    def speech_active(self) -> bool:
        return self._speech_active

    def start_turn(self, text: str) -> Any:
        if self._busy_left > 0:
            self._busy_left -= 1
            self.log.append(("busy", text))
            raise BusyError("turn pipeline busy")
        if self._starter_error is not None:
            raise self._starter_error
        self.log.append(("turn", text))
        self.turn_started.set()
        return {"turn_id": "t-1"}

    def gateway(self, **overrides) -> VoiceTurnGateway:
        values: dict[str, Any] = {
            "anti_echo": self.anti_echo,
            "cancellation": self,
            "turn_starter": self.start_turn,
            "speech_active": self.speech_active,
            "busy_exceptions": (BusyError,),
            "retry_seconds": 2.0,
            "retry_interval": 0.02,
        }
        values.update(overrides)
        return VoiceTurnGateway(**values)


def test_accepted_transcript_becomes_exactly_one_turn() -> None:
    harness = Harness()
    gateway = harness.gateway()
    try:
        gateway.handle_transcript("Włącz światło w kuchni.")
        assert gateway.flush(timeout=5)
    finally:
        gateway.stop()

    assert harness.log == [("turn", "Włącz światło w kuchni.")]
    assert harness.anti_echo.seen == ["Włącz światło w kuchni."]


def test_echo_transcript_never_starts_a_turn_and_never_triggers_barge_in() -> None:
    # An echo heard while DAN speaks is exactly the case the interrupt
    # policy must NOT treat as barge-in (AUDIO_RUNTIME §5).
    harness = Harness(accepted=False, speech_active=True)
    gateway = harness.gateway()
    try:
        gateway.handle_transcript("Sprawdziłem kalendarz i nie masz dziś spotkań.")
        assert gateway.flush(timeout=5)
    finally:
        gateway.stop()

    assert harness.log == []


def test_speech_during_playback_cancels_before_starting_the_turn() -> None:
    harness = Harness(speech_active=True)
    gateway = harness.gateway()
    try:
        gateway.handle_transcript("Czekaj, zmiana planów.")
        assert gateway.flush(timeout=5)
    finally:
        gateway.stop()

    assert harness.log[0] == ("cancel", "barge_in")
    assert harness.log[-1] == ("turn", "Czekaj, zmiana planów.")


def test_idle_voice_does_not_trigger_cancellation() -> None:
    harness = Harness(speech_active=False)
    gateway = harness.gateway()
    try:
        gateway.handle_transcript("Zwykłe polecenie przy ciszy.")
        assert gateway.flush(timeout=5)
    finally:
        gateway.stop()

    assert ("cancel", "barge_in") not in harness.log


def test_busy_pipeline_is_retried_until_the_turn_starts() -> None:
    harness = Harness(busy_failures=3)
    gateway = harness.gateway()
    try:
        gateway.handle_transcript("Powtórz próbę aż wejdzie.")
        assert harness.turn_started.wait(timeout=5)
        assert gateway.flush(timeout=5)
    finally:
        gateway.stop()

    assert harness.log.count(("turn", "Powtórz próbę aż wejdzie.")) == 1
    assert harness.log.count(("busy", "Powtórz próbę aż wejdzie.")) == 3


def test_persistently_busy_pipeline_gives_up_without_crashing() -> None:
    harness = Harness(busy_failures=10_000)
    gateway = harness.gateway(retry_seconds=0.2)
    try:
        gateway.handle_transcript("Nigdy nie wejdzie.")
        assert gateway.flush(timeout=5)
    finally:
        gateway.stop()

    assert ("turn", "Nigdy nie wejdzie.") not in harness.log


def test_turn_starter_failure_does_not_kill_the_gateway() -> None:
    harness = Harness(starter_error=ValueError("brain exploded"))
    gateway = harness.gateway()
    try:
        gateway.handle_transcript("Ta tura padnie.")
        assert gateway.flush(timeout=5)

        # The gateway worker survives and processes the next transcript.
        harness._starter_error = None
        gateway.handle_transcript("Ta tura przejdzie.")
        assert gateway.flush(timeout=5)
    finally:
        gateway.stop()

    assert ("turn", "Ta tura przejdzie.") in harness.log


def test_cancelled_turn_is_logged_as_cancellation_not_failure(caplog) -> None:
    # FIX-09: a barge-in cancellation surfacing from the turn starter must be
    # logged as a clean cancellation, never as "voice turn failed" — the log
    # must stop lying about barge-in the same way the panel does.
    class TurnCancelled(Exception):
        pass

    harness = Harness(starter_error=TurnCancelled("brain generation cancelled"))
    gateway = harness.gateway(cancelled_exceptions=(TurnCancelled,))
    try:
        with caplog.at_level("INFO", logger="dan.voice.gateway"):
            gateway.handle_transcript("Przerwane w połowie.")
            assert gateway.flush(timeout=5)
    finally:
        gateway.stop()

    messages = [record.getMessage().lower() for record in caplog.records]
    assert any("cancel" in message for message in messages)
    assert not any("voice turn failed" in message for message in messages)


def test_handle_transcript_does_not_block_on_a_slow_turn() -> None:
    harness = Harness()
    release = threading.Event()

    def slow_starter(text: str) -> Any:
        release.wait(timeout=10)
        harness.log.append(("turn", text))
        return None

    gateway = harness.gateway(turn_starter=slow_starter)
    try:
        started = time.monotonic()
        gateway.handle_transcript("Długa generacja w tle.")
        elapsed = time.monotonic() - started
        assert elapsed < 1.0, "handle_transcript must hand off, not generate inline"
        release.set()
        assert gateway.flush(timeout=5)
    finally:
        gateway.stop()

    assert ("turn", "Długa generacja w tle.") in harness.log


def test_stopped_gateway_drops_transcripts_quietly() -> None:
    harness = Harness()
    gateway = harness.gateway()
    gateway.stop()

    gateway.handle_transcript("Po zatrzymaniu.")  # must not raise

    assert harness.log == []


# --- daemon integration (the by-construction guarantee, end to end) -----------


def voice_daemon_app(tmp_path: Path, *, speak_responses: bool = False):
    from dan.brain import BrainManager
    from dan.brain.test_adapter import TestBrainAdapter as HermeticBrainAdapter
    from dan.daemon.app import create_daemon_app
    from tests.test_api_smoke import config_text

    config_path = tmp_path / "dan.toml"
    text = (
        config_text(tmp_path / "home" / "dan.db")
        .replace("[voice]\nenabled = false", "[voice]\nenabled = true")
    )
    if speak_responses:
        text = text.replace("speak_responses = false", "speak_responses = true")
    config_path.write_text(text, encoding="utf-8")
    app = create_daemon_app(config_path)
    production_manager = app.brain_manager
    app.brain_manager = BrainManager(
        [HermeticBrainAdapter(default_model="test-model")],
        default_adapter="test",
    )
    if production_manager is not None:
        production_manager.close()
    return app


def voiced_wav() -> bytes:
    from tests.test_voice_capture_gate import as_wav, pcm_tone

    return as_wav(pcm_tone(seconds=1.0))


def wait_for_turns(conn: sqlite3.Connection, count: int, timeout: float = 15.0) -> list[tuple]:
    deadline = time.monotonic() + timeout
    rows: list[tuple] = []
    while time.monotonic() < deadline:
        rows = conn.execute(
            "SELECT source, input_text, status FROM turns ORDER BY rowid"
        ).fetchall()
        if len(rows) >= count:
            return rows
        time.sleep(0.05)
    return rows


def test_daemon_wires_transcript_into_a_voice_turn(tmp_path: Path) -> None:
    app = voice_daemon_app(tmp_path)
    app.start()
    try:
        app.voice_stt.accept_capture(voiced_wav())
        assert app.voice_stt.flush(timeout=15)
        assert app.voice_gateway.flush(timeout=15)

        rows = wait_for_turns(app.conn, 1)
        assert len(rows) == 1
        source, input_text, status = rows[0]
        assert source == "voice"
        assert status == "finished"
        assert input_text  # the accepted transcript opened the turn
    finally:
        app.close()


def test_daemon_echo_never_turns_or_cancels_active_speech(tmp_path: Path) -> None:
    from dan.voice.stt import MockSTTEngine

    app = voice_daemon_app(tmp_path)
    app.start()
    try:
        app.voice_broker.stop()
        # What the mock STT will "hear" is exactly what DAN just spoke.
        transcript = MockSTTEngine.DEFAULT_TRANSCRIPT
        conn = app.conn
        queue = VoiceQueue(conn)
        request = enqueue_voice(queue, transcript, session="turn-tts")
        queue.claim_next()
        queue.mark_synthesis_complete(request.id)
        queue.mark_spoken(request.id)  # the broker stamps spoken_at at playback (FIX-09)
        queue.mark_done(request.id)
        pending = enqueue_voice(
            queue,
            "To ma zostać, bo echo nie jest barge-in.",
            session="turn-pending",
        )

        app.voice_stt.accept_capture(voiced_wav())
        assert app.voice_stt.flush(timeout=15)
        assert app.voice_gateway.flush(timeout=15)

        rows = conn.execute("SELECT source FROM turns").fetchall()
        assert rows == []  # echo never became a turn — by construction
        status = conn.execute(
            "SELECT status FROM voice_queue WHERE id = ?",
            (pending.id,),
        ).fetchone()
        assert status == ("queued",)
        cancelled = conn.execute(
            "SELECT COUNT(*) FROM events WHERE type = 'voice.speak.cancelled'"
        ).fetchone()[0]
        assert cancelled == 0
    finally:
        app.close()


def test_daemon_barge_in_cancels_pending_speech_before_the_new_turn(tmp_path: Path) -> None:
    app = voice_daemon_app(tmp_path)
    app.start()
    try:
        app.voice_broker.stop()
        conn = app.conn
        queue = VoiceQueue(conn)
        enqueue_voice(queue, "Stare zdanie czekające na głos.", session="turn-old")

        app.voice_stt.accept_capture(voiced_wav())
        assert app.voice_stt.flush(timeout=15)
        assert app.voice_gateway.flush(timeout=15)

        rows = wait_for_turns(conn, 1)
        assert [row[0] for row in rows] == ["voice"]
        statuses = [
            str(row[0])
            for row in conn.execute(
                "SELECT status FROM voice_queue WHERE turn_id = 'turn-old'"
            ).fetchall()
        ]
        assert statuses == ["cancelled"]
        cancelled = conn.execute(
            "SELECT COUNT(*) FROM events WHERE type = 'voice.speak.cancelled'"
        ).fetchone()[0]
        assert cancelled == 1
    finally:
        app.close()
