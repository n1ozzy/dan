"""Turn-level streaming tests (G4d — orchestrator wires on_delta end to end).

A streaming adapter's deltas reach the VoiceQueue while generation is still
running (first-sound requirement, MASTER_PLAN §4a); the finished turn does
NOT re-enqueue what was already spoken from deltas; `Turn.final_text` and
`brain.responded` are built from the canonical BrainResponse.text, never
from a delta reassembly; and a failed streaming turn cancels its own queued
speech (turn failure is a §7 cancellation trigger).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.brain import BrainAdapterError, BrainRequest, BrainResponse
from jarvis.brain.context_builder import ContextBuilder
from jarvis.brain.manager import BrainManager
from jarvis.daemon.state_machine import RuntimeState, RuntimeStateMachine
from jarvis.store.db import close_quietly, initialize_database
from jarvis.store.event_store import create_event_store
from jarvis.turns.orchestrator import TurnOrchestrator, TurnOrchestratorError
from jarvis.voice.speech import SpeechPipeline


class StreamingFakeAdapter:
    """Emits scripted deltas, then returns a canonical response."""

    name = "streaming-fake"
    default_model = "streaming-model"
    supports_streaming = True

    def __init__(
        self,
        deltas: list[str],
        final_text: str,
        *,
        fail_after_deltas: bool = False,
        probe=None,
    ) -> None:
        self._deltas = deltas
        self._final_text = final_text
        self._fail = fail_after_deltas
        self._probe = probe
        self.saw_on_delta = False

    def available_models(self) -> list[str]:
        return [self.default_model]

    def generate(self, request: BrainRequest, *, on_delta=None) -> BrainResponse:
        if on_delta is not None:
            self.saw_on_delta = True
            for delta in self._deltas:
                on_delta(delta)
        if self._probe is not None:
            self._probe()  # lets tests inspect the queue mid-generation
        if self._fail:
            raise BrainAdapterError("stream died after partial deltas")
        return BrainResponse(text=self._final_text, model=self.default_model)


class SpyFillerTimer:
    def __init__(self) -> None:
        self.disarmed = 0

    def disarm(self) -> None:
        self.disarmed += 1


class SpySpeechSession:
    def __init__(self) -> None:
        self.deltas: list[str] = []
        self.final_texts: list[str] = []

    def feed(self, delta: str) -> None:
        self.deltas.append(delta)

    def finalize(self, final_text: str) -> int:
        self.final_texts.append(final_text)
        return 0


class SpySpeechPipeline:
    def __init__(self) -> None:
        self.armed_fillers = 0
        self.started_streams = 0
        self.session = SpySpeechSession()

    def arm_filler(self, *, turn_id: str) -> SpyFillerTimer:
        self.armed_fillers += 1
        return SpyFillerTimer()

    def start_stream(self, *, turn_id: str, filler_timer=None) -> SpySpeechSession:
        self.started_streams += 1
        return self.session

    def speak_text(self, *, turn_id: str, text: str) -> int:
        self.session.finalize(text)
        return 0


def voice_config(**overrides) -> SimpleNamespace:
    values = {
        "enabled": True,
        "speak_responses": True,
        "broker_enabled": False,
        "fillers": ["A spierdalaj..."],
        "filler_after_ms": 60_000,  # never fires unless a test wants it
        "min_sentence_chars": 12,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "turn-stream.db"
    close_quietly(initialize_database(path))
    return path


def connect(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path)


def make_orchestrator(conn: sqlite3.Connection, adapter, db_path: Path) -> TurnOrchestrator:
    event_store = create_event_store(conn)
    state_machine = RuntimeStateMachine(
        event_store, event_bus=None, initial_state=RuntimeState.IDLE
    )
    return TurnOrchestrator(
        conn=conn,
        event_store=event_store,
        event_bus=None,
        state_machine=state_machine,
        brain_manager=BrainManager([adapter], default_adapter=adapter.name),
        context_builder=ContextBuilder(conn),
        speech_pipeline=SpeechPipeline(lambda: connect(db_path), config=voice_config()),
    )


def queue_snapshot(db_path: Path) -> list[tuple[str, str]]:
    conn = connect(db_path)
    try:
        return [
            (str(row[0]), str(row[1]))
            for row in conn.execute(
                "SELECT text, status FROM voice_queue ORDER BY rowid"
            ).fetchall()
        ]
    finally:
        close_quietly(conn)


def test_deltas_reach_the_voice_queue_during_generation(db_path: Path) -> None:
    conn = connect(db_path)
    mid_generation: list[list[tuple[str, str]]] = []
    adapter = StreamingFakeAdapter(
        deltas=["Pierwsze zdanie odpowiedzi. Drugie zda", "nie odpowiedzi."],
        final_text="Pierwsze zdanie odpowiedzi. Drugie zdanie odpowiedzi.",
        probe=lambda: mid_generation.append(queue_snapshot(db_path)),
    )
    orchestrator = make_orchestrator(conn, adapter, db_path)

    result = orchestrator.handle_text(text="Opowiedz mi coś ciekawego.")

    assert adapter.saw_on_delta, "orchestrator never passed on_delta to the adapter"
    # The first sentence was queued BEFORE the adapter returned.
    assert mid_generation == [[("Pierwsze zdanie odpowiedzi.", "queued")]]
    # After the turn: both sentences queued exactly once — no double-speak.
    assert [text for text, _ in queue_snapshot(db_path)] == [
        "Pierwsze zdanie odpowiedzi.",
        "Drugie zdanie odpowiedzi.",
    ]
    assert result.final_text == "Pierwsze zdanie odpowiedzi. Drugie zdanie odpowiedzi."
    close_quietly(conn)


def test_panel_text_turn_streams_without_arming_filler(db_path: Path) -> None:
    conn = connect(db_path)
    adapter = StreamingFakeAdapter(
        deltas=["Pierwsze zdanie odpowiedzi."],
        final_text="Pierwsze zdanie odpowiedzi.",
    )
    speech = SpySpeechPipeline()
    event_store = create_event_store(conn)
    state_machine = RuntimeStateMachine(
        event_store, event_bus=None, initial_state=RuntimeState.IDLE
    )
    orchestrator = TurnOrchestrator(
        conn=conn,
        event_store=event_store,
        event_bus=None,
        state_machine=state_machine,
        brain_manager=BrainManager([adapter], default_adapter=adapter.name),
        context_builder=ContextBuilder(conn),
        speech_pipeline=speech,
    )

    orchestrator.handle_text(text="Panel chat must not speak filler.", source="panel")

    assert adapter.saw_on_delta
    assert speech.started_streams == 1
    assert speech.armed_fillers == 0
    assert speech.session.deltas == ["Pierwsze zdanie odpowiedzi."]
    close_quietly(conn)


def test_canonical_text_wins_when_deltas_disagree(db_path: Path) -> None:
    conn = connect(db_path)
    adapter = StreamingFakeAdapter(
        deltas=["Urwana wersja z delt"],
        final_text="Kanoniczna wersja odpowiedzi.",
    )
    orchestrator = make_orchestrator(conn, adapter, db_path)

    result = orchestrator.handle_text(text="Pytanie testowe o kanon.")

    # Turn truth comes from BrainResponse.text, never from delta reassembly.
    assert result.final_text == "Kanoniczna wersja odpowiedzi."
    assert result.turn.final_text == "Kanoniczna wersja odpowiedzi."
    events = create_event_store(conn).list_by_turn_id(result.turn_id, limit=100)
    responded = [event for event in events if event.type == "brain.responded"]
    assert len(responded) == 1
    assert responded[0].payload["text_length"] == len("Kanoniczna wersja odpowiedzi.")
    close_quietly(conn)


def test_failed_streaming_turn_cancels_its_queued_speech(db_path: Path) -> None:
    conn = connect(db_path)
    adapter = StreamingFakeAdapter(
        deltas=["Pierwsze zdanie odpowiedzi. Drugie zdanie odpo"],
        final_text="nieużyte",
        fail_after_deltas=True,
    )
    orchestrator = make_orchestrator(conn, adapter, db_path)

    with pytest.raises(TurnOrchestratorError):
        orchestrator.handle_text(text="To się wywali w połowie.")

    rows = queue_snapshot(db_path)
    assert rows, "the first sentence should have been queued before the failure"
    assert all(status == "cancelled" for _, status in rows), rows
    cancelled_events = conn.execute(
        "SELECT COUNT(*) FROM events WHERE type = 'voice.speak.cancelled'"
    ).fetchone()[0]
    assert cancelled_events == len(rows)
    close_quietly(conn)


def test_non_streaming_adapter_still_speaks_via_degradation(db_path: Path) -> None:
    class BlockingFakeAdapter:
        name = "blocking-fake"
        default_model = "blocking-model"

        def available_models(self) -> list[str]:
            return [self.default_model]

        def generate(self, request: BrainRequest) -> BrainResponse:
            return BrainResponse(
                text="Zdanie z adaptera bez streamingu.", model=self.default_model
            )

    conn = connect(db_path)
    orchestrator = make_orchestrator(conn, BlockingFakeAdapter(), db_path)

    orchestrator.handle_text(text="Pytanie do blokującego adaptera.")

    assert [text for text, _ in queue_snapshot(db_path)] == [
        "Zdanie z adaptera bez streamingu."
    ]
    close_quietly(conn)
