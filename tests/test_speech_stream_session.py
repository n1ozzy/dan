"""SpeechStreamSession tests (G4d — deltas → chunker → VoiceQueue, live).

The session is the jarvisd-side consumer of adapter deltas (G0 §5): each
completed sentence becomes one VoiceRequest the moment the chunker emits
it — this is what makes first-sound fast. Deltas are NEVER persisted (the
only events are the frozen voice.speak.* family), tool-call blocks hold
emission fail-closed, and when no deltas ever arrive the canonical final
text is chunked after the fact (the degradation path every non-streaming
adapter takes).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.store.db import close_quietly, initialize_database
from jarvis.voice.speech import SpeechPipeline


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "stream-speech.db"
    close_quietly(initialize_database(path))
    return path


def connect(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path)


def voice_config(**overrides) -> SimpleNamespace:
    values = {
        "enabled": True,
        "speak_responses": True,
        "broker_enabled": False,
        "fillers": ["A spierdalaj..."],
        "filler_after_ms": 50,
        "min_sentence_chars": 12,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def pipeline_for(db_path: Path, **overrides) -> SpeechPipeline:
    return SpeechPipeline(lambda: connect(db_path), config=voice_config(**overrides))


def queued_rows(db_path: Path) -> list[tuple[str, str]]:
    conn = connect(db_path)
    try:
        return [
            (str(row[0]), str(row[1]))
            for row in conn.execute(
                "SELECT text, metadata_json FROM voice_queue ORDER BY rowid"
            ).fetchall()
        ]
    finally:
        close_quietly(conn)


def event_types(db_path: Path) -> set[str]:
    conn = connect(db_path)
    try:
        return {
            str(row[0])
            for row in conn.execute("SELECT DISTINCT type FROM events").fetchall()
        }
    finally:
        close_quietly(conn)


class FakeFillerTimer:
    def __init__(self) -> None:
        self.disarmed = 0

    def disarm(self) -> None:
        self.disarmed += 1


# --- live sentence emission ---------------------------------------------------


def test_sentences_are_enqueued_as_deltas_arrive_not_at_the_end(db_path: Path) -> None:
    session = pipeline_for(db_path).start_stream(turn_id="turn-1")

    session.feed("Pierwsze zdanie odpo")
    assert queued_rows(db_path) == []  # sentence not complete yet
    session.feed("wiedzi. Drugie zda")
    texts = [text for text, _ in queued_rows(db_path)]
    assert texts == ["Pierwsze zdanie odpowiedzi."]  # queued BEFORE the stream ends
    session.feed("nie odpowiedzi.")
    session.finalize("Pierwsze zdanie odpowiedzi. Drugie zdanie odpowiedzi.")

    rows = queued_rows(db_path)
    assert [text for text, _ in rows] == [
        "Pierwsze zdanie odpowiedzi.",
        "Drugie zdanie odpowiedzi.",
    ]
    assert '"seq": 0' in rows[0][1]
    assert '"seq": 1' in rows[1][1]


def test_finalize_without_any_deltas_chunks_the_canonical_text(db_path: Path) -> None:
    session = pipeline_for(db_path).start_stream(turn_id="turn-1")

    count = session.finalize(
        "Pierwsze zdanie odpowiedzi. Drugie zdanie odpowiedzi."
    )

    assert count == 2
    assert [text for text, _ in queued_rows(db_path)] == [
        "Pierwsze zdanie odpowiedzi.",
        "Drugie zdanie odpowiedzi.",
    ]


def test_tool_call_block_split_across_deltas_is_never_spoken(db_path: Path) -> None:
    session = pipeline_for(db_path).start_stream(turn_id="turn-1")

    session.feed("Sprawdzam plik dla ciebie. <jarvis_tool")
    session.feed('_call>{"name":"file_read"}</jarvis_tool_call>')
    session.feed(" Po bloku jeszcze jedno zdanie.")
    session.finalize(
        "Sprawdzam plik dla ciebie. "
        '<jarvis_tool_call>{"name":"file_read"}</jarvis_tool_call>'
        " Po bloku jeszcze jedno zdanie."
    )

    texts = [text for text, _ in queued_rows(db_path)]
    assert texts
    assert all("tool_call" not in text and "file_read" not in text for text in texts)


def test_deltas_are_not_persisted_anywhere(db_path: Path) -> None:
    session = pipeline_for(db_path).start_stream(turn_id="turn-1")
    session.feed("Pierwsze zdanie odpowiedzi. Drugie zdanie")
    session.finalize("Pierwsze zdanie odpowiedzi. Drugie zdanie")

    types = event_types(db_path)
    assert types <= {"voice.speak.queued"}  # only the frozen family, no deltas
    assert not any("delta" in event_type for event_type in types)


# --- filler interlock (G0 §6: never after the first real sentence) -------------


def test_filler_is_disarmed_by_first_meaningful_delta_before_sentence(db_path: Path) -> None:
    timer = FakeFillerTimer()
    session = pipeline_for(db_path).start_stream(turn_id="turn-1", filler_timer=timer)

    session.feed("Pierwsza delta bez kropki")

    assert timer.disarmed == 1
    assert queued_rows(db_path) == []


def test_filler_disarm_is_idempotent_after_more_deltas(db_path: Path) -> None:
    timer = FakeFillerTimer()
    session = pipeline_for(db_path).start_stream(turn_id="turn-1", filler_timer=timer)

    session.feed("Nic jeszcze nie ma")
    session.feed(" pełnego. Pierwsze pełne zdanie. A dalej")

    assert timer.disarmed == 1


def test_filler_is_disarmed_by_finalize_even_without_deltas(db_path: Path) -> None:
    timer = FakeFillerTimer()
    session = pipeline_for(db_path).start_stream(turn_id="turn-1", filler_timer=timer)

    session.finalize("Jedno pełne zdanie odpowiedzi.")

    assert timer.disarmed >= 1


# --- resilience -----------------------------------------------------------------


def test_disabled_pipeline_yields_a_no_op_session(db_path: Path) -> None:
    session = pipeline_for(db_path, speak_responses=False).start_stream(turn_id="t")

    session.feed("Pierwsze zdanie odpowiedzi. ")
    count = session.finalize("Pierwsze zdanie odpowiedzi.")

    assert count == 0
    assert queued_rows(db_path) == []


def test_feed_survives_a_broken_queue_and_never_raises(tmp_path: Path) -> None:
    def broken_factory() -> sqlite3.Connection:
        raise sqlite3.OperationalError("db is gone")

    pipeline = SpeechPipeline(broken_factory, config=voice_config())
    session = pipeline.start_stream(turn_id="turn-1")

    session.feed("Pierwsze zdanie odpowiedzi. Drugie zdanie odpowiedzi.")
    count = session.finalize("Pierwsze zdanie odpowiedzi. Drugie zdanie odpowiedzi.")

    assert count == 0  # spoken best-effort; the turn itself must never fail
