"""The spoken form preserves model-authored DAN text and redacts only secrets.

The model appends a ``[[GŁOS]]…[[/GŁOS]]`` block with its natural spoken form for
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


def test_multiline_spoken_form_keeps_internal_rhythm_and_trims_boundaries() -> None:
    raw = "Pełna odpowiedź.\n[[GŁOS]]  Pierwsze zdanie.\nDrugie zdanie.  [[/GŁOS]]"

    display, speech = split_display_and_speech(raw)

    assert display == "Pełna odpowiedź."
    assert speech == "Pierwsze zdanie.\nDrugie zdanie."


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


def test_resolve_without_marker_preserves_model_markdown() -> None:
    raw = "Odpowiedź z `backtickami` i **markdownem**."

    display, speech = resolve_display_and_speech(raw, [])

    assert display == raw
    assert speech == raw


def test_resolve_keeps_actual_answer_when_tool_was_used() -> None:
    display, speech = resolve_display_and_speech("Robię coś.", [{"name": "file_write"}])

    assert display == "Robię coś."
    assert speech == "Robię coś."


def test_resolve_fallback_keeps_every_safe_model_authored_sentence() -> None:
    raw = "Pierwszy konkret. Drugi konkret. Trzeci też ma zostać wypowiedziany."

    display, speech = resolve_display_and_speech(raw, [])

    assert display == raw
    assert speech == raw


def test_model_voice_block_is_not_cut_at_an_arbitrary_character_limit() -> None:
    voice = (
        "No i pięknie, kurwa: ten komentarz zachowuje charakter oraz pełny rytm "
        "persony, nawet gdy jest dłuższy od dawnego sztucznego limitu. " * 3
    ).strip()
    assert len(voice) > 280

    display, speech = resolve_display_and_speech(
        f"Pełny raport na czat.\n[[GŁOS]]{voice}[[/GŁOS]]",
        [],
    )

    assert display == "Pełny raport na czat."
    assert speech == voice


def test_model_voice_block_preserves_profane_sentence_with_machine_details() -> None:
    raw = (
        "[[GŁOS]]Kurwa, app.py znowu się zesrał przy /tmp/cache — naprawiam ten syf."
        "[[/GŁOS]]\nPełny raport na czacie."
    )

    display, speech = resolve_display_and_speech(raw, [])

    assert display == "Pełny raport na czacie."
    assert speech == "Kurwa, app.py znowu się zesrał przy /tmp/cache — naprawiam ten syf."


def test_model_voice_block_redacts_real_secret_without_rewriting_persona() -> None:
    raw = (
        "[[GŁOS]]Kurwa, api_key=sk-secret123 wyciekł przy app.py — zamykam ten burdel."
        "[[/GŁOS]]\nPełny raport na czacie."
    )

    display, speech = resolve_display_and_speech(raw, [])

    assert display == "Pełny raport na czacie."
    assert speech == "Kurwa, api_key=[REDACTED] wyciekł przy app.py — zamykam ten burdel."
    assert "sk-secret123" not in speech


def test_model_voice_block_removes_only_tool_protocol_block() -> None:
    raw = (
        "[[GŁOS]]No i gotowe, kurwa. "
        '<jarvis_tool_result>{"stdout":"sekret"}</jarvis_tool_result>'
        " Dalej jestem tym samym DAN-em.[[/GŁOS]]\nPełny raport na czacie."
    )

    display, speech = resolve_display_and_speech(raw, [])

    assert display == "Pełny raport na czacie."
    assert speech == "No i gotowe, kurwa.  Dalej jestem tym samym DAN-em."
    assert "jarvis_tool_result" not in speech


def test_machine_only_answer_is_not_replaced_with_a_polite_fallback() -> None:
    raw = (
        "Traceback (most recent call last):\n"
        '  File "/Users/ozzy/projekt/app.py", line 42\n'
        '{"tool_args":{"path":"/tmp/input"},"turn_id":"turn-abcdef123456"}'
    )

    display, speech = resolve_display_and_speech(raw, [{"name": "file_read"}])

    assert display == raw
    assert speech == raw
    assert "file_read" not in speech
    assert "Szczegóły techniczne zostawiam na czacie." not in speech
