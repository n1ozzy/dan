"""AntiEchoGate tests (G4c — echo of Jarvis's own TTS never becomes a turn).

The gate compares an incoming transcript against what the daemon recently
sent to the speaker — read from the persisted voice_queue (daemon state,
never a /tmp flag — AUDIO_RUNTIME §4). Deterministic token-overlap: same
inputs, same decision. Fail-closed for turn creation: a dropped user
sentence that duplicates Jarvis's own words is acceptable; an echo that
becomes a turn is a contract violation by construction.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import pytest

from jarvis.store.db import close_quietly, initialize_database
from jarvis.voice.anti_echo import AntiEchoGate
from jarvis.voice.queue import VoiceQueue


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "anti-echo.db"
    close_quietly(initialize_database(path))
    return path


def connect(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path)


def factory_for(db_path: Path) -> Callable[[], sqlite3.Connection]:
    return lambda: connect(db_path)


def gate_config(**overrides) -> SimpleNamespace:
    values = {
        "anti_echo_window_seconds": 30,
        "anti_echo_overlap_threshold": 0.75,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def build_gate(db_path: Path, **overrides) -> AntiEchoGate:
    return AntiEchoGate(factory_for(db_path), config=gate_config(**overrides))


def speak_and_finish(db_path: Path, text: str, *, now: str | None = None) -> None:
    """Put one row through queued -> speaking -> spoken -> done, like the broker
    would: mark_spoken stamps spoken_at at playback, which is what the anti-echo
    corpus now reads (FIX-09)."""

    conn = connect(db_path)
    try:
        queue = VoiceQueue(conn, now=(lambda: now) if now else None)
        request = queue.enqueue(text=text, turn_id="turn-spoken", kind="sentence", seq=0)
        claimed = queue.claim_next()
        assert claimed is not None and claimed.id == request.id
        queue.mark_spoken(request.id)
        queue.mark_done(request.id)
    finally:
        close_quietly(conn)


def enqueue_only(db_path: Path, text: str) -> None:
    conn = connect(db_path)
    try:
        VoiceQueue(conn).enqueue(text=text, turn_id="turn-queued", kind="sentence", seq=0)
    finally:
        close_quietly(conn)


def iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


# --- acceptance --------------------------------------------------------------


def test_fresh_transcript_is_accepted_when_nothing_was_spoken(db_path: Path) -> None:
    decision = build_gate(db_path).accepts_transcript("Włącz proszę światło w kuchni.")

    assert decision.accepted is True
    assert decision.reason == "ok"


def test_unrelated_transcript_is_accepted_despite_recent_speech(db_path: Path) -> None:
    speak_and_finish(db_path, "Sprawdziłem kalendarz i nie masz dziś spotkań.")

    decision = build_gate(db_path).accepts_transcript("Puść jakąś muzykę do pracy.")

    assert decision.accepted is True


def test_transcript_with_mostly_new_tokens_is_accepted(db_path: Path) -> None:
    speak_and_finish(db_path, "Raport jest gotowy.")

    # Shares a token or two but is clearly the user's own sentence.
    decision = build_gate(db_path).accepts_transcript(
        "Dobra, skoro raport gotowy, to wyślij go teraz mailem do zespołu."
    )

    assert decision.accepted is True


def test_long_tts_history_does_not_falsely_reject_original_sentence(db_path: Path) -> None:
    # FIX-#8: with a long TTS history the union of spoken tokens covers most
    # words, so union overlap alone would falsely reject an original user
    # sentence that merely reuses one word from each of many sentences. The
    # best-row guard (no single spoken row overlaps enough) keeps it accepted.
    for sentence in (
        "lampa świeci bardzo jasno dzisiaj wieczorem",
        "stół jest okrągły oraz drewniany naprawdę",
        "okno wychodzi na zielony spory ogród",
        "krzesło stoi sobie w rogu pokoju",
        "ściana ma kolor jasnoniebieski właśnie teraz",
        "podłoga została zrobiona z solidnego dębu",
    ):
        speak_and_finish(db_path, sentence)

    # One word from each spoken sentence: union overlap ~1.0, but no single row
    # overlaps more than 1/6 (~0.17), below min_row_overlap.
    decision = build_gate(db_path).accepts_transcript(
        "lampa stół okno krzesło ściana podłoga"
    )

    assert decision.accepted is True


def test_verbatim_echo_of_one_sentence_is_still_rejected(db_path: Path) -> None:
    # The best-row guard must NOT let a genuine echo through: a near-verbatim
    # copy of a single spoken sentence lands high overlap on that row.
    speak_and_finish(db_path, "Sprawdziłem kalendarz i nie masz dzisiaj żadnych spotkań")

    decision = build_gate(db_path).accepts_transcript(
        "sprawdziłem kalendarz i nie masz dzisiaj żadnych spotkań"
    )

    assert decision.accepted is False
    assert decision.reason == "echo"


# --- echo rejection ----------------------------------------------------------


def test_echo_spanning_multiple_spoken_chunks_is_rejected(db_path: Path) -> None:
    # Regression (G4 live gate 2026-07-02): a 14 s PTT capture picked up
    # SEVERAL consecutive TTS sentences. Against any single row the token
    # overlap was ~0.52 < 0.75, so the echo became a turn and its barge-in
    # killed Jarvis's own answer mid-sentence. Coverage must be computed
    # against the union of everything recently spoken, not row by row.
    speak_and_finish(db_path, "Chcesz szczegóły, to siadaj wygodnie.")
    speak_and_finish(db_path, "Architektura jest prostsza niż twoje oczekiwania.")
    speak_and_finish(db_path, "Mózg to jest to, co teraz gada.")

    decision = build_gate(db_path).accepts_transcript(
        "Chcesz szczegóły. To siadaj. Architektura jest prostsza niż twoje "
        "oczekiwania. Mózg, czyli to, co teraz gada."
    )

    assert decision.accepted is False
    assert decision.reason == "echo"


def test_real_interjection_during_speech_stays_accepted(db_path: Path) -> None:
    # The union must not swallow genuine user speech over playing TTS: live
    # measurement of Ozzy's real interjection gave union coverage 0.31.
    speak_and_finish(db_path, "Jestem bezstanowy i trzymam prawdę w bazie.")
    speak_and_finish(db_path, "Każdy turn ma swoje zdarzenia i kolejkę głosu.")

    decision = build_gate(db_path).accepts_transcript(
        "Ciekawe, nie rozumiem o czym ty mówisz, na pewno jesteś bezstanowy, jasne."
    )

    assert decision.accepted is True


def test_exact_echo_of_spoken_sentence_is_rejected(db_path: Path) -> None:
    spoken = "Sprawdziłem kalendarz i nie masz dziś spotkań."
    speak_and_finish(db_path, spoken)

    decision = build_gate(db_path).accepts_transcript(spoken)

    assert decision.accepted is False
    assert decision.reason == "echo"
    assert decision.matched_text == spoken


def test_echo_survives_case_and_punctuation_differences(db_path: Path) -> None:
    speak_and_finish(db_path, "Sprawdziłem kalendarz i nie masz dziś spotkań.")

    decision = build_gate(db_path).accepts_transcript(
        "sprawdziłem kalendarz, i nie masz dziś spotkań"
    )

    assert decision.accepted is False


def test_fragment_of_spoken_sentence_is_rejected(db_path: Path) -> None:
    speak_and_finish(
        db_path, "Sprawdziłem kalendarz i nie masz dziś żadnych spotkań po południu."
    )

    # The mic caught only the tail of the playback.
    decision = build_gate(db_path).accepts_transcript("nie masz dziś żadnych spotkań")

    assert decision.accepted is False
    assert decision.reason == "echo"


def test_recently_cancelled_speech_still_counts_as_echo_source(db_path: Path) -> None:
    # A barge-in cancels rows mid-play; their audio was already in the air, so
    # the broker had stamped spoken_at before the cancel landed.
    conn = connect(db_path)
    try:
        queue = VoiceQueue(conn)
        request = queue.enqueue(text="Zdanie przerwane w połowie grania.", turn_id="t", seq=0)
        queue.claim_next()
        queue.mark_spoken(request.id)
        queue.cancel_turn("t")
    finally:
        close_quietly(conn)

    decision = build_gate(db_path).accepts_transcript("Zdanie przerwane w połowie grania.")

    assert decision.accepted is False


# --- what does NOT count as spoken -------------------------------------------


def test_queued_but_never_played_text_does_not_block_the_user(db_path: Path) -> None:
    enqueue_only(db_path, "To zdanie nigdy nie zagrało w głośniku.")

    decision = build_gate(db_path).accepts_transcript(
        "To zdanie nigdy nie zagrało w głośniku."
    )

    assert decision.accepted is True


def test_queued_then_cancelled_text_is_not_an_echo_source(db_path: Path) -> None:
    # FIX-09 core bug: a barge-in cancels a whole turn, flipping even 'queued'
    # rows that NEVER reached the speaker to 'cancelled'. Under the old status
    # filter those polluted the echo corpus and wrongly rejected the user's next
    # sentence. spoken_at (NULL here — never played) keeps them out.
    conn = connect(db_path)
    try:
        queue = VoiceQueue(conn)
        queue.enqueue(text="Zaplanowane ale nigdy niewypowiedziane.", turn_id="t", seq=0)
        # No claim → never played; barge-in cancels the whole turn anyway.
        queue.cancel_turn("t")
    finally:
        close_quietly(conn)

    decision = build_gate(db_path).accepts_transcript("Zaplanowane ale nigdy niewypowiedziane.")

    assert decision.accepted is True


def test_failed_after_partial_audio_still_counts_as_echo_source(db_path: Path) -> None:
    # A row that reached the speaker (spoken_at stamped) then failed mid-play
    # DID put audio in the air, so it must stay an echo source (FIX-09).
    conn = connect(db_path)
    try:
        queue = VoiceQueue(conn)
        request = queue.enqueue(text="Częściowo odtworzone zdanie zanim padło.", turn_id="t", seq=0)
        queue.claim_next()
        queue.mark_spoken(request.id)
        queue.mark_failed(request.id, error="player died mid-play")
    finally:
        close_quietly(conn)

    decision = build_gate(db_path).accepts_transcript("Częściowo odtworzone zdanie zanim padło.")

    assert decision.accepted is False
    assert decision.reason == "echo"


def test_speech_older_than_window_does_not_block_the_user(db_path: Path) -> None:
    stale = iso(datetime.now(UTC) - timedelta(seconds=120))
    speak_and_finish(db_path, "Stare zdanie sprzed dwóch minut.", now=stale)

    decision = build_gate(db_path, anti_echo_window_seconds=30).accepts_transcript(
        "Stare zdanie sprzed dwóch minut."
    )

    assert decision.accepted is True


def test_decision_is_deterministic_for_identical_inputs(db_path: Path) -> None:
    speak_and_finish(db_path, "Sprawdziłem kalendarz i nie masz dziś spotkań.")
    gate = build_gate(db_path)

    first = gate.accepts_transcript("Sprawdziłem kalendarz i nie masz dziś spotkań.")
    second = gate.accepts_transcript("Sprawdziłem kalendarz i nie masz dziś spotkań.")

    assert (first.accepted, first.reason) == (second.accepted, second.reason)
