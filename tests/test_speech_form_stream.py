"""The stream router keeps first-sound: only the [[GŁOS]] inner text is spoken.

The model opens its answer with a [[GŁOS]]…[[/GŁOS]] block (a short, redacted
form for the ear); the router forwards only that inner text to TTS as deltas
arrive, so Jarvis starts talking immediately, and drops the rich chat text that
follows. Markers may be split across delta boundaries.
"""

from __future__ import annotations

from jarvis.voice.speech_form_stream import SpeechFormStreamRouter


def _run(deltas: list[str]) -> str:
    out: list[str] = []
    router = SpeechFormStreamRouter(out.append)
    for delta in deltas:
        router.feed(delta)
    return "".join(out)


def test_routes_only_inner_of_block_in_single_delta() -> None:
    assert _run(["[[GŁOS]]cześć świecie[[/GŁOS]]bogaty **tekst** na czat"]) == "cześć świecie"


def test_handles_markers_split_across_deltas() -> None:
    assert _run(["[[GŁ", "OS]]hej", " tam", "[[/GŁ", "OS]]reszta"]) == "hej tam"


def test_ignores_text_after_close() -> None:
    assert _run(["[[GŁOS]]x[[/GŁOS]]", "aaa", "bbb"]) == "x"


def test_ignores_text_before_open() -> None:
    assert _run(["śmieć przed ", "[[GŁOS]]", "tylko to", "[[/GŁOS]]"]) == "tylko to"


def test_no_block_short_answer_stays_buffered_for_finalize() -> None:
    # Under the passthrough threshold nothing is emitted live — finalize
    # speaks the canonical text instead, so nothing is ever said twice.
    assert _run(["zwykły tekst bez bloku, model nie dołożył formy"]) == ""


def test_close_split_does_not_leak_partial_marker_to_speech() -> None:
    # The "[[/" of the close marker must never be spoken as content.
    assert _run(["[[GŁOS]]koniec", "[[/", "GŁOS]]"]) == "koniec"


def test_no_block_long_answer_falls_through_to_live_passthrough() -> None:
    """First-sound safety valve: when the model ignores the [[GŁOS]]
    instruction, live speech must NOT stay silent until finalize — past the
    threshold the router switches to passthrough and streams the raw text."""

    head = "To jest długa odpowiedź bez żadnego bloku formy głosowej, " * 2
    tail = "dalszy ciąg odpowiedzi."
    spoken = _run([head, tail])
    assert spoken.startswith("To jest długa odpowiedź")
    assert spoken.endswith(tail)


def test_passthrough_still_strips_late_markers() -> None:
    # A block that shows up AFTER the threshold cannot restore form-routing —
    # but its markers must never be spoken aloud.
    head = "x" * 100
    spoken = _run([head, "[[GŁOS]]forma[[/GŁOS]] dalej"])
    assert "[[GŁOS]]" not in spoken
    assert "[[/GŁOS]]" not in spoken
    assert spoken.startswith("x" * 100)
    assert "forma" in spoken and "dalej" in spoken


def test_compliant_block_within_threshold_is_unaffected_by_passthrough() -> None:
    # A little pre-block junk stays tolerated as long as the block opens
    # within the threshold — routing works exactly as before.
    assert _run(["Ok. ", "[[GŁOS]]czysta forma[[/GŁOS]]", "czat czat czat"]) == "czysta forma"
