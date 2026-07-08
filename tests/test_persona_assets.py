"""Persona data contract test.

Jarvis has ONE persona — his own (config/persona/jarvis.md). Persona is
character, not mechanism: the runtime enforces permissions in CODE
(registry/approvals/policy), never in the persona text. So these tests only
guard that the single persona exists, is non-empty, and carries no stale
legacy paths — not that it recites any boundary wording.
"""

from __future__ import annotations

from pathlib import Path

from tests.git_guards import assert_schema_and_migrations_unchanged


ROOT = Path(__file__).resolve().parents[1]
PERSONA_DIR = ROOT / "config" / "persona"
BASE_PERSONA = PERSONA_DIR / "jarvis.md"

FORBIDDEN_SNIPPETS = (
    "/Users/n1_ozzy/Documents/dev/dan",
    "/tmp/dan",
    "afplay",
    "--dangerously-skip-permissions",
    "persona.py",
)


def test_base_persona_exists_and_is_non_empty() -> None:
    assert BASE_PERSONA.read_text(encoding="utf-8").strip()


def test_jarvis_has_a_single_persona() -> None:
    profiles = sorted(p.name for p in PERSONA_DIR.glob("*.md"))
    assert profiles == ["jarvis.md"], f"Jarvis has one persona; found {profiles}"


def test_persona_files_avoid_forbidden_legacy_references() -> None:
    offenders: list[tuple[str, str]] = []

    for path in sorted(PERSONA_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        for snippet in FORBIDDEN_SNIPPETS:
            if snippet in text:
                offenders.append((path.name, snippet))

    assert offenders == []


def test_schema_and_migrations_are_unchanged() -> None:
    assert_schema_and_migrations_unchanged(ROOT)
