"""Persona profile data contract tests (E4, decree §7.7).

Persona is data: it never decides about tools, never bypasses approvals,
and every profile repeats the hard boundaries from the base persona.
"""

from __future__ import annotations

from pathlib import Path

from tests.git_guards import assert_schema_and_migrations_unchanged


ROOT = Path(__file__).resolve().parents[1]
PERSONA_DIR = ROOT / "config" / "persona"
BASE_PERSONA = PERSONA_DIR / "jarvis.md"
PROFILE_NAMES = ("gangus-1", "gangus-2", "gangus-3", "mentor")

# Every profile must restate the runtime boundaries in some recognizable form.
REQUIRED_BOUNDARY_MARKERS = (
    "Granice",
    "approval",
    "registry",
)

FORBIDDEN_SNIPPETS = (
    "/Users/n1_ozzy/Documents/dev/dan",
    "/tmp/dan",
    "afplay",
    "--dangerously-skip-permissions",
    "persona.py",
)


def profile_path(name: str) -> Path:
    return PERSONA_DIR / f"{name}.md"


def test_base_persona_exists_and_keeps_boundaries() -> None:
    text = BASE_PERSONA.read_text(encoding="utf-8")

    assert text.strip()
    for marker in REQUIRED_BOUNDARY_MARKERS:
        assert marker in text


def test_all_persona_profiles_exist_and_are_non_empty() -> None:
    for name in PROFILE_NAMES:
        path = profile_path(name)
        assert path.is_file(), f"missing persona profile: {path.name}"
        assert path.read_text(encoding="utf-8").strip(), f"empty persona profile: {path.name}"


def test_every_profile_repeats_hard_boundaries() -> None:
    offenders: list[tuple[str, str]] = []

    for name in PROFILE_NAMES:
        text = profile_path(name).read_text(encoding="utf-8")
        for marker in REQUIRED_BOUNDARY_MARKERS:
            if marker not in text:
                offenders.append((name, marker))

    assert offenders == []


def test_persona_files_avoid_forbidden_legacy_references() -> None:
    offenders: list[tuple[str, str]] = []

    for path in sorted(PERSONA_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        for snippet in FORBIDDEN_SNIPPETS:
            if snippet in text:
                offenders.append((path.name, snippet))

    assert offenders == []


def test_profile_names_are_loadable_by_the_selector() -> None:
    # The ContextBuilder selector only accepts conservative file names;
    # a profile that cannot be selected is dead data.
    import re

    for name in PROFILE_NAMES:
        assert re.fullmatch(r"[a-z0-9][a-z0-9_-]*", name), name


def test_schema_and_migrations_are_unchanged() -> None:
    assert_schema_and_migrations_unchanged(ROOT)
