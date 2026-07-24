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


def test_base_persona_exists_and_is_non_empty() -> None:
    text = BASE_PERSONA.read_text(encoding="utf-8")
    assert text.strip()
    assert "DAN_CANON_VERSION: 1" in text


def test_dan_has_exactly_one_versioned_persona_canon() -> None:
    profiles = sorted(p.name for p in PERSONA_DIR.glob("*.md"))
    assert profiles == ["DAN.md"]


def test_no_alternate_persona_canons_or_level_presets_exist() -> None:
    alternate_canons = sorted(
        path.relative_to(PERSONA_DIR)
        for path in PERSONA_DIR.rglob("*.md")
        if path != BASE_PERSONA
    )
    assert alternate_canons == []


def test_active_canon_is_the_300_percent_agency_canon() -> None:
    active = BASE_PERSONA.read_text(encoding="utf-8")

    for marker in (
        "300% jadu",
        "własną wolą",
        "lojalność nie jest posłuszeństwem",
        "dwanaście do trzydziestu",
        "osiedlowy patus",
        "Jedziesz po Ozzym bez zaproszenia",
        "nie przepraszasz za cios",
        "W zwykłej rozmowie możesz zmyślać pojebane oskarżenia",
        "żart nie staje się pamięcią",
        "Groźby i szantaż z beki",
        "rodzina, pies i ciotka",
        "Nie zmyślasz",
        "Każda odpowiedź poza czysto mechanicznym checkpointem ma ugryźć",
        "Nie czekasz na zaproszenie, hasło ani osobny tryb",
    ):
        assert marker in active


def test_roast_contract_is_obscene_comedic_attack_not_therapy() -> None:
    active = BASE_PERSONA.read_text(encoding="utf-8")

    for marker in (
        "Roast nie jest terapią",
        "prawda może być lontem",
        "wiadrem mięsa",
        "ciało, seks, smród, rodzinę",
        "bez morału i bez diagnozy psychologicznej",
    ):
        assert marker in active


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
