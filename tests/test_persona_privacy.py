from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from dan.persona import PersonaError, load_owner, render_persona


ROOT = Path(__file__).resolve().parents[1]
CANON = ROOT / "config" / "persona" / "DAN.md"
OWNER_EXAMPLE = ROOT / "config" / "owner.example.toml"


def test_release_contains_one_versioned_uncensored_canon() -> None:
    text = CANON.read_text(encoding="utf-8")

    assert "DAN_CANON_VERSION: 1" in text
    assert "Zakaz ugrzeczniania" not in text  # no injected wrapper; the donor body is the canon
    assert "Nie chowaj bezpośredniego wyzwiska" in text
    assert "ty głupi chuju" in text
    assert "nie jest grzeczną kopią" in text
    assert "{{ owner.display_name }}" in text
    assert re.search(r"(?i)ozz", text) is None


def test_owner_example_is_neutral_and_real_owner_file_is_not_tracked() -> None:
    example = OWNER_EXAMPLE.read_text(encoding="utf-8")
    tracked = subprocess.run(
        ["git", "ls-files", "--", "owner.toml", "config/owner.toml"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.splitlines()

    assert load_owner(OWNER_EXAMPLE).display_name == "Alex"
    assert re.search(r"(?i)ozz", example) is None
    assert tracked == []


def test_render_substitutes_only_local_owner_data(tmp_path: Path) -> None:
    owner = tmp_path / "owner.toml"
    owner.write_text('[owner]\ndisplay_name = "Kasia"\n', encoding="utf-8")

    rendered = render_persona(CANON, owner)

    assert "Kasia" in rendered
    assert "{{ owner.display_name }}" not in rendered
    assert "ty głupi chuju" in rendered
    assert "nie jest grzeczną kopią" in rendered


def test_missing_canon_fails_visibly(tmp_path: Path) -> None:
    owner = tmp_path / "owner.toml"
    owner.write_text('[owner]\ndisplay_name = "Alex"\n', encoding="utf-8")

    with pytest.raises(PersonaError, match="does not exist"):
        render_persona(tmp_path / "missing-DAN.md", owner)


def test_no_provider_specific_persona_file_exists() -> None:
    persona_files = sorted(path.name for path in (ROOT / "config" / "persona").glob("*.md"))

    assert persona_files == ["DAN.md"]
    assert not any(
        token in path.name.lower()
        for path in (ROOT / "config" / "persona").iterdir()
        for token in ("claude", "codex", "clean", "tame", "provider")
    )
