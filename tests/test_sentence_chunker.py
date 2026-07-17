"""G3 SentenceChunker tests (VOICE_STREAMING.md §3–§4).

Deterministic state machine: deltas in, sentence chunks out. Tool-call
blocks hold emission fail-closed and are never spoken.
"""

from __future__ import annotations

from dan.voice.chunker import SentenceChunker


def collect(chunker: SentenceChunker, deltas: list[str]) -> list[str]:
    out: list[str] = []
    for delta in deltas:
        out.extend(chunker.feed(delta))
    out.extend(chunker.flush())
    return out


def test_single_delta_with_two_sentences() -> None:
    chunks = collect(SentenceChunker(), ["Pierwsze zdanie jest tu. Drugie zdanie też jest."])

    assert chunks == ["Pierwsze zdanie jest tu.", "Drugie zdanie też jest."]


def test_sentence_split_across_deltas() -> None:
    chunks = collect(
        SentenceChunker(),
        ["Pierwsza połowa zdania ", "i druga połowa. Nowe zda", "nie kończy się tutaj."],
    )

    assert chunks == ["Pierwsza połowa zdania i druga połowa.", "Nowe zdanie kończy się tutaj."]


def test_short_fragment_waits_for_more_text() -> None:
    chunker = SentenceChunker(min_chars=12)

    first = chunker.feed("Ok. ")
    rest = chunker.feed("Dalsza część odpowiedzi przychodzi teraz.")
    tail = chunker.flush()

    # "Ok." alone is below min_chars, so it rides with the next sentence.
    assert first == []
    combined = rest + tail
    assert combined
    assert combined[0].startswith("Ok.")


def test_abbreviations_do_not_cut() -> None:
    chunks = collect(
        SentenceChunker(),
        ["Weź np. ten przypadek i sprawdź go dokładnie. Potem wróć."],
    )

    assert chunks == ["Weź np. ten przypadek i sprawdź go dokładnie.", "Potem wróć."]


def test_comma_is_not_a_sentence_cut_point() -> None:
    chunker = SentenceChunker()

    assert chunker.feed("To jest bardzo długi wstęp, ") == []
    assert chunker.feed("a to nadal jedno zdanie po przecinku.") == []
    assert chunker.flush() == ["To jest bardzo długi wstęp, a to nadal jedno zdanie po przecinku."]


def test_newline_is_a_cut_point() -> None:
    chunks = collect(SentenceChunker(), ["Pierwsza linia bez kropki\nDruga linia tutaj."])

    assert chunks == ["Pierwsza linia bez kropki", "Druga linia tutaj."]


def test_flush_emits_the_unterminated_tail() -> None:
    chunker = SentenceChunker()

    assert chunker.feed("Zdanie bez końca") == []
    assert chunker.flush() == ["Zdanie bez końca"]


def test_blank_line_after_unterminated_line_does_not_crash() -> None:
    # Regression (G4 live gate 2026-07-02): a streamed answer with a blank
    # line after a line without a sentence terminator ("Jasne:\n\n- punkt")
    # made _next_sentence return bare None -> TypeError in _drain -> the
    # speech session muted the rest of the turn.
    chunks = collect(
        SentenceChunker(),
        ["Oto plan działania:\n", "\nPierwszy krok jest gotowy."],
    )

    assert chunks == ["Oto plan działania:", "Pierwszy krok jest gotowy."]


def test_leading_newline_delta_is_consumed() -> None:
    chunker = SentenceChunker()

    assert chunker.feed("\n") == []
    assert chunker.feed("\n\n") == []
    assert chunker.feed("Po pustych liniach zdanie.") == []
    assert chunker.flush() == ["Po pustych liniach zdanie."]


def test_blank_line_before_tool_call_suspicion_stays_fail_closed() -> None:
    chunker = SentenceChunker()

    # Empty line consumed, the tool-call prefix suspicion is held, nothing
    # of the block ever comes out.
    assert chunker.feed("Nagłówek bez kropki\n\n<dan_tool") == ["Nagłówek bez kropki"]
    assert chunker.flush() == []


def test_tool_call_block_is_never_spoken() -> None:
    text = (
        "Muszę sprawdzić plik konfiguracyjny teraz. "
        '<dan_tool_call>{"name":"file_read","arguments":{"path":"/x"}}</dan_tool_call> '
        "Wracam z wynikiem za chwilę."
    )
    chunks = collect(SentenceChunker(), [text])

    joined = " ".join(chunks)
    assert "dan_tool_call" not in joined
    assert "file_read" not in joined
    assert chunks[0] == "Muszę sprawdzić plik konfiguracyjny teraz."
    assert any("Wracam z wynikiem" in chunk for chunk in chunks)


def test_tool_call_split_across_deltas_is_never_spoken() -> None:
    deltas = [
        "Sprawdzam to od razu dla ciebie. <dan_",
        'tool_call>{"name":"echo","arguments":{}}</dan_',
        "tool_call> Gotowe, wynik zaraz będzie.",
    ]
    chunks = collect(SentenceChunker(), deltas)

    joined = " ".join(chunks)
    assert "tool_call" not in joined
    assert "echo" not in joined
    assert chunks[0] == "Sprawdzam to od razu dla ciebie."


def test_legacy_tool_call_split_across_deltas_never_speaks_raw_json() -> None:
    # Compatibility input only: a legacy provider block must be consumed,
    # while all runtime output remains on the canonical DAN tag.
    deltas = [
        "Sprawdzam stary format bez wycieku. <jarvis_",
        'tool_call>{"name":"echo","arguments":{"text":"SECRET_RAW_JSON"}}</jarvis_',
        "tool_call> Gotowe po zgodności wstecznej.",
    ]

    chunks = collect(SentenceChunker(), deltas)
    joined = " ".join(chunks)
    assert "jarvis_tool_call" not in joined
    assert "SECRET_RAW_JSON" not in joined
    assert '"arguments"' not in joined
    assert chunks[0] == "Sprawdzam stary format bez wycieku."
    assert any("Gotowe po zgodności" in chunk for chunk in chunks)


def test_false_prefix_is_released_as_ordinary_text() -> None:
    chunks = collect(
        SentenceChunker(),
        ["Porównanie a<b zachodzi tutaj zawsze. Koniec sprawdzania testu."],
    )

    assert chunks == ["Porównanie a<b zachodzi tutaj zawsze.", "Koniec sprawdzania testu."]


def test_unterminated_tool_call_is_held_forever_fail_closed() -> None:
    chunker = SentenceChunker()

    emitted = chunker.feed("Zaczynam działanie narzędzia teraz. <dan_tool_call>{never closed")
    tail = chunker.flush()

    assert emitted == ["Zaczynam działanie narzędzia teraz."]
    # Fail-closed: a suspicious, unresolved block never leaves the buffer.
    assert all("dan_tool_call" not in chunk for chunk in tail)
    assert all("never closed" not in chunk for chunk in tail)


def test_determinism_same_input_same_chunks() -> None:
    deltas = ["Raz dwa trzy. Czte", "ry pięć sześć! Siedem", " osiem?"]

    first = collect(SentenceChunker(), deltas)
    second = collect(SentenceChunker(), deltas)

    assert first == second
    assert first == ["Raz dwa trzy.", "Cztery pięć sześć!", "Siedem osiem?"]
