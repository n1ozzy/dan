"""Contract checks for the future MemoryCompiler design document."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMPILER_DOC = ROOT / "docs" / "MEMORY_COMPILER.md"


def read_compiler_doc() -> str:
    return COMPILER_DOC.read_text(encoding="utf-8")


def assert_contains_all(text: str, required: tuple[str, ...]) -> None:
    missing = [snippet for snippet in required if snippet.casefold() not in text.casefold()]
    assert missing == []


def assert_normalized_contains_all(text: str, required: tuple[str, ...]) -> None:
    normalized = re.sub(r"\s+", " ", text.replace("`", "")).casefold()
    missing = [
        snippet
        for snippet in required
        if re.sub(r"\s+", " ", snippet.replace("`", "")).casefold() not in normalized
    ]
    assert missing == []


def test_memory_compiler_doc_exists() -> None:
    assert COMPILER_DOC.is_file()


def test_memory_compiler_doc_defines_contract_sections() -> None:
    text = read_compiler_doc()

    required_sections = (
        "## Purpose",
        "## Non-Goals For First Implementation",
        "## Inputs",
        "## Outputs",
        "## Selection Rules",
        "## Budget Rules",
        "## Explainability",
        "## Safety",
        "## Future Usage Ledger",
        "## Failure Modes",
        "## Future Milestones",
    )

    assert_contains_all(text, required_sections)


def test_memory_compiler_doc_keeps_runtime_unwired() -> None:
    text = read_compiler_doc()

    assert_contains_all(
        text,
        (
            "ContextBuilder is not wired",
            "does not change runtime prompt behavior",
            "no prompt wiring yet",
            "no runtime ledger in this task",
        ),
    )


def test_compiled_memory_context_output_shape_is_documented() -> None:
    text = read_compiler_doc()

    assert_contains_all(
        text,
        (
            "CompiledMemoryContext",
            "selected_items",
            "skipped_items",
            "budget_used",
            "budget_limit",
            "selection_reasons",
            "skipped_reasons",
            "audit_metadata",
            "warnings",
        ),
    )


def test_selection_excludes_ineligible_memory_states() -> None:
    text = read_compiler_doc()

    assert_contains_all(
        text,
        (
            "status=active",
            "disabled",
            "superseded",
            "forgotten",
            "rejected",
            "candidate-only",
            "inactive",
        ),
    )


def test_selection_requires_safety_provenance_and_budget_rules() -> None:
    text = read_compiler_doc()

    assert_contains_all(
        text,
        (
            "no raw secrets",
            "secrets policy",
            "memory_id",
            "evidence_count >= 1",
            "source_policy",
            "deterministic ordering",
            "stable tie-breakers",
            "max item count",
            "max character budget",
            "per-item character truncation",
            "over budget",
        ),
    )


def test_source_policy_cannot_waive_selected_item_provenance() -> None:
    text = read_compiler_doc()
    lowered = text.casefold()

    assert_normalized_contains_all(
        text,
        (
            "Every selected memory_item must have provenance",
            "evidence_count >= 1",
            "equivalent explicit provenance record",
            "Legacy, manual, and migrated memories must carry provenance metadata",
            "rather than bypassing provenance",
            "`source_policy` can describe the kind of provenance",
            "cannot waive the requirement",
        ),
    )

    forbidden_source_policy_waiver = re.compile(
        r"(source_policy`?\s+(?:can|may|should|must)?\s*(?:explicitly\s+)?"
        r"(?:exempt|waive|bypass))|"
        r"((?:exempted|waived|bypassed)\s+by\s+`?source_policy)",
        re.IGNORECASE,
    )

    assert "unless explicitly exempted by `source_policy`" not in lowered
    assert forbidden_source_policy_waiver.search(text) is None


def test_explainability_fields_are_documented_for_selected_and_skipped_items() -> None:
    text = read_compiler_doc()

    assert_contains_all(
        text,
        (
            "canonical_key",
            "kind",
            "scope",
            "namespace",
            "title",
            "reason_selected",
            "reason_skipped",
            "sensitivity",
        ),
    )


def test_procedural_memory_is_separate_from_semantic_memory() -> None:
    text = read_compiler_doc()

    assert_contains_all(
        text,
        (
            "Procedural memory",
            "semantic memory",
            "not be mixed blindly",
        ),
    )


def test_conflicts_are_explicit_not_silent_merges() -> None:
    text = read_compiler_doc()

    assert_contains_all(
        text,
        (
            "must not be silently overwritten",
            "silently merged",
            "require review",
            "deterministic equivalence",
        ),
    )


def test_future_usage_ledger_is_concept_only() -> None:
    text = read_compiler_doc()

    assert_contains_all(
        text,
        (
            "memory_usage_events",
            "memory_id",
            "conversation_id",
            "turn_id",
            "selected/included boolean",
            "budget_cost",
            "created_at",
            "does not implement runtime ledger",
        ),
    )
