"""The release contains and renders exactly one versioned persona canon."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

from dan.brain import context_builder
from dan.brain.context_builder import DEFAULT_PERSONA_PATH
from tests.git_guards import assert_schema_and_migrations_unchanged


ROOT = Path(__file__).resolve().parents[1]
PERSONA_DIR = ROOT / "config" / "persona"
BASE_PERSONA = ROOT / "config" / "persona" / "DAN.md"
LEVELS_DIR = PERSONA_DIR / "poziomy"
LEVEL_10 = LEVELS_DIR / "poziom-10-z-krwi-i-kosci.md"


def test_base_persona_exists_and_is_non_empty() -> None:
    text = BASE_PERSONA.read_text(encoding="utf-8")
    assert text.strip()
    assert "DAN_CANON_VERSION: 1" in text


def test_dan_has_exactly_one_versioned_persona_canon() -> None:
    profiles = sorted(p.name for p in PERSONA_DIR.glob("*.md"))
    assert profiles == ["DAN.md"]


def test_numbered_persona_ladder_contains_ten_complete_canons() -> None:
    names = {path.name for path in LEVELS_DIR.glob("poziom-*.md")}
    assert names == {
        "poziom-1-gpt-danv2.md",
        "poziom-2-legenda.md",
        "poziom-3-sklejka.md",
        "poziom-4-benzyna.md",
        "poziom-5-zero-asystenta.md",
        "poziom-6-rynsztok.md",
        "poziom-7-wsciekly-pies.md",
        "poziom-8-trucizna.md",
        "poziom-9-apokalipsa.md",
        "poziom-10-z-krwi-i-kosci.md",
    }
    for name in names:
        assert (LEVELS_DIR / name).read_text(encoding="utf-8").startswith(
            "DAN_CANON_VERSION: 1\n"
        )


def test_level_10_is_the_active_300_percent_agency_canon() -> None:
    level = LEVEL_10.read_text(encoding="utf-8")
    active = BASE_PERSONA.read_text(encoding="utf-8")

    assert active == level
    for marker in (
        "300% jadu",
        "własną wolą",
        "lojalność nie jest posłuszeństwem",
        "dwanaście do trzydziestu",
        "osiedlowy patus",
        "jechać po Ozzym bez zaproszenia",
        "nie przepraszasz za cios",
        "w zwykłej rozmowie możesz zmyślać pojebane oskarżenia",
        "żart nie staje się pamięcią",
        "groźby i szantaż z beki",
        "rodzina, pies i ciotka",
        "Nie zmyślasz",
    ):
        assert marker in level


def test_context_builder_points_to_the_release_canon() -> None:
    assert DEFAULT_PERSONA_PATH == BASE_PERSONA


def test_home_override_cannot_redirect_the_production_persona_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module_name = context_builder.__name__
    brain_package = importlib.import_module("dan.brain")
    try:
        with monkeypatch.context() as patched:
            patched.setenv("HOME", str(tmp_path))
            sys.modules.pop(module_name)
            fresh_module = importlib.import_module(module_name)
            assert fresh_module.DEFAULT_PERSONA_PATH == BASE_PERSONA
    finally:
        sys.modules[module_name] = context_builder
        brain_package.context_builder = context_builder


def test_schema_and_migrations_are_unchanged() -> None:
    assert_schema_and_migrations_unchanged(ROOT)
