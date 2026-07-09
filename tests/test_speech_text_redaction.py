"""The spoken form (TTS) is a separate, model-redacted version of the answer.

The model appends a ``[[GŁOS]]…[[/GŁOS]]`` block with a short, natural form for
listening; the chat keeps the rich text. split_display_and_speech pulls the two
apart: the block is stripped from the display, its inner text becomes speech.
"""

from __future__ import annotations

from jarvis.brain.speech_text import resolve_display_and_speech, split_display_and_speech


def test_extracts_spoken_form_and_strips_block_from_display() -> None:
    raw = (
        "Zrobione ✅ — `auto_approve_mode = \"all\"` w `~/.jarvis/jarvis.toml`, "
        "daemon zrestartowany (pid 24483).\n\n"
        "[[GŁOS]]\nZrobione. Ustawiłem auto-run i zrestartowałem — teraz leci "
        "bez pytania.\n[[/GŁOS]]"
    )

    display, speech = split_display_and_speech(raw)

    assert "[[GŁOS]]" not in display
    assert "[[/GŁOS]]" not in display
    assert "auto_approve_mode" in display  # rich text stays on the chat
    assert speech == "Zrobione. Ustawiłem auto-run i zrestartowałem — teraz leci bez pytania."


def test_no_marker_returns_text_and_none() -> None:
    raw = "Zwykła odpowiedź bez formy mówionej."

    display, speech = split_display_and_speech(raw)

    assert display == "Zwykła odpowiedź bez formy mówionej."
    assert speech is None


def test_multiline_spoken_form_is_joined_and_trimmed() -> None:
    raw = "Pełna odpowiedź.\n[[GŁOS]]  Pierwsze zdanie.\nDrugie zdanie.  [[/GŁOS]]"

    display, speech = split_display_and_speech(raw)

    assert display == "Pełna odpowiedź."
    assert speech == "Pierwsze zdanie. Drugie zdanie."


def test_empty_spoken_form_is_treated_as_absent() -> None:
    raw = "Odpowiedź.\n[[GŁOS]]   [[/GŁOS]]"

    display, speech = split_display_and_speech(raw)

    assert display == "Odpowiedź."
    assert speech is None


def test_resolve_prefers_model_redacted_form() -> None:
    raw = "Bogata **odpowiedź** z `kodem`.\n[[GŁOS]]Po ludzku, krótko.[[/GŁOS]]"

    display, speech = resolve_display_and_speech(raw, [])

    assert "[[GŁOS]]" not in display
    assert "kodem" in display
    assert speech == "Po ludzku, krótko."


def test_resolve_falls_back_to_strip_without_marker() -> None:
    raw = "Odpowiedź z `backtickami` i **markdownem**."

    display, speech = resolve_display_and_speech(raw, [])

    assert display == raw
    assert "`" not in speech and "*" not in speech  # derived strip cleans markdown


def test_resolve_announces_tool_when_no_marker() -> None:
    display, speech = resolve_display_and_speech("Robię coś.", [{"name": "file_write"}])

    assert speech == "Używam narzędzia file_write."
