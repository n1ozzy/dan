from pathlib import Path

import pytest

from scripts.dobranocka_prepare import compile_story


def test_compiler_preserves_spoken_lines_without_inventing_direction(
    tmp_path: Path,
) -> None:
    source = tmp_path / "story.txt"
    source.write_text(
        "dan|Cicho. Dobranoc.\n"
        "danusia|Jeszcze nie skończyłam.\n",
        encoding="utf-8",
    )

    lines, manifest = compile_story(source, mode="radio")

    assert lines == [
        "dan|Cicho. Dobranoc.",
        "danusia|Jeszcze nie skończyłam.",
    ]
    assert manifest["personas"] == ["dan", "danusia"]
    assert set(manifest) == {
        "source",
        "mode",
        "personas",
        "utterances",
        "word_count",
        "direction",
    }
    assert manifest["direction"] == "owner-listening-required"


def test_compiler_rejects_a_third_persona_instead_of_skipping_it(
    tmp_path: Path,
) -> None:
    source = tmp_path / "story.txt"
    source.write_text(
        "dan|Zaczynam.\n"
        "jarvis|Próbuję wrócić.\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="dan albo danusia"):
        compile_story(source, mode="radio")
