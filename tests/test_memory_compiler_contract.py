"""Contract checks for the future MemoryCompiler design document."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMPILER_DOC = ROOT / "docs" / "MEMORY_COMPILER.md"
GOVERNANCE_DOC = ROOT / "docs" / "MEMORY_GOVERNANCE.md"
POLICY_DOC = ROOT / "docs" / "MEMORY_OS_ARCHITECTURE.md"
PROJECT_RULES_DOC = ROOT / "docs" / "DAN_PROJECT_RULES.md"
CHANGE_GUARDS_DOC = ROOT / "docs" / "DAN_CHANGE_GUARDS.md"
CURRENT_STATE_DOC = ROOT / "docs" / "DAN_CURRENT_STATE.md"
STATUS_DOC = ROOT / "docs" / "STATUS.md"
ROADMAP_DOC = ROOT / "docs" / "DAN_ROADMAP.md"


def read_compiler_doc() -> str:
    return COMPILER_DOC.read_text(encoding="utf-8")


def read_doc(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def read_governance_addendum() -> str:
    text = read_compiler_doc()
    start = text.index("## Governance addendum for first compiler implementation")
    end = text.index("## Future Usage Ledger", start)
    return text[start:end]


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


def test_memory_os_policy_doc_exists() -> None:
    assert POLICY_DOC.is_file()


def test_memory_os_policy_defines_enablement_precedence() -> None:
    text = read_doc(POLICY_DOC)

    assert_contains_all(
        text,
        (
            "## Compiled memory context policy",
            "### Enablement precedence",
        ),
    )
    assert_normalized_contains_all(
        text,
        (
            "global default is off",
            "config dev/local enablement can enable compiled memory when [memory].enabled=true",
            "[memory].enabled=false is an absolute compiled-memory disable",
            "compiled_memory_force_disabled disables compiled memory regardless of config, session/profile, or request override",
            "Session/profile scoped enablement exists and is internal-only",
            "Empty session/profile allow-list enables zero sessions and does not globally leak",
            "None allow-list preserves established global config behavior",
            "Request-scoped override True can enable compiled memory for one request only when [memory].enabled=true and the kill switch is off",
            "request-scoped override False disables compiled memory for one request",
            "request-scoped override must not mutate builder/runtime state",
            "No env, panel, public API, user-facing, or global production enablement exists yet",
        ),
    )


def test_memory_os_policy_defines_prompt_visible_output_contract() -> None:
    text = read_doc(POLICY_DOC)

    assert_contains_all(
        text,
        (
            "### Prompt-visible output contract",
            "### Forbidden prompt-visible data",
        ),
    )
    assert_normalized_contains_all(
        text,
        (
            "compiled memory is represented only as safe compiled_memory context message",
            "metadata remains kind=compiled_memory and untrusted=True",
            "safe fields are title, claim, evidence_count",
            "raw IDs, canonical keys, audit metadata, and skipped items must not appear",
            "raw evidence quotes and raw observations must not appear",
            "raw secrets must not appear",
            "exception text and tracebacks must not appear",
        ),
    )


def test_memory_os_policy_defines_governance_exclusions() -> None:
    text = read_doc(POLICY_DOC)

    assert_contains_all(text, ("### Governance exclusions",))
    assert_normalized_contains_all(
        text,
        (
            "disabled excluded",
            "superseded excluded",
            "forgotten excluded",
            "conflict excluded",
            "missing provenance/evidence excluded",
            "procedural excluded by default",
        ),
    )


def test_memory_os_policy_defines_diagnostics_redaction_contract() -> None:
    text = read_doc(POLICY_DOC)

    assert_contains_all(text, ("### Diagnostics and redaction",))
    assert_normalized_contains_all(
        text,
        (
            "diagnostics are outside model-visible context",
            "diagnostics are coarse/redacted",
            "diagnostics reflect final post-budget BrainRequest",
            "diagnostics must not contain claim, title, evidence, observation, user input, or secret text",
        ),
    )


def test_memory_os_policy_defines_fail_closed_read_only_contract() -> None:
    text = read_doc(POLICY_DOC)

    assert_contains_all(text, ("### Fail-closed and read-only context build",))
    assert_normalized_contains_all(
        text,
        (
            "compiler failure omits compiled memory",
            "compiler failure does not leak exception details",
            "context build remains read-only",
            "no usage ledger, events, or timestamp writes during context build",
            "future work must not change casually",
        ),
    )


def test_project_rules_restate_scoped_workflow_and_refactor_limits() -> None:
    text = read_doc(PROJECT_RULES_DOC)

    assert_normalized_contains_all(
        text,
        (
            "one task at a time",
            "one scope per task",
            "no broad refactors",
        ),
    )


def test_change_guards_cover_compiled_memory_policy_boundaries() -> None:
    text = read_doc(CHANGE_GUARDS_DOC)

    assert_contains_all(text, ("### Compiled memory context policy tasks",))
    assert_normalized_contains_all(
        text,
        (
            "ContextBuilder prompt-visible output",
            "MemoryCompiler selection logic",
            "schema/migrations",
            "API routes",
            "config defaults",
            "env/panel/API/user-facing enablement",
        ),
    )


def test_current_state_documents_compiled_memory_policy_status() -> None:
    text = read_doc(CURRENT_STATE_DOC)

    assert_normalized_contains_all(
        text,
        (
            "Compiled memory remains default-off",
            "config-based dev/local enablement exists",
            "request-scoped override support exists",
            "No env, panel, public API, user-facing, or global production enablement exists",
        ),
    )


def test_memory_status_docs_do_not_publish_stale_rollout_snapshot_metadata() -> None:
    docs = {
        "status": read_doc(STATUS_DOC).casefold(),
        "current_state": read_doc(CURRENT_STATE_DOC).casefold(),
        "architecture": read_doc(POLICY_DOC).casefold(),
    }
    stale = (
        "rescue/" + "audit-8a5a0f0",
        "1411" + "a16",
        "2aa7" + "eb1",
        "policy work " + "uncommitted",
        "design" + "-phase",
        "memory os is in design/contract phase",
    )

    present = {
        name: [snippet for snippet in stale if snippet in text]
        for name, text in docs.items()
    }

    assert present == {"status": [], "current_state": [], "architecture": []}


def test_roadmap_keeps_user_facing_enablement_future() -> None:
    text = read_doc(ROADMAP_DOC)

    assert_normalized_contains_all(
        text,
        (
            "Config-based dev/local compiled memory enablement",
            "Request-scoped compiled memory override",
            "Env/public API/panel/user-facing enablement remains future",
            "Do not add env, panel, public API, or user-facing compiled-memory enablement casually",
        ),
    )


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


def test_governance_addendum_for_first_compiler_is_documented() -> None:
    text = read_compiler_doc()

    assert_contains_all(
        text,
        (
            "## Governance addendum for first compiler implementation",
            "Compiler eligibility statuses",
            "Status precedence",
            "Conflict handling for first compiler",
            "Supersession handling for first compiler",
            "Forget/disable handling for first compiler",
            "Merge policy for first compiler",
            "Procedural memory handling for first compiler",
            "Compiler output reasons",
        ),
    )


def test_governance_addendum_selectable_status_is_active_only() -> None:
    text = read_compiler_doc()

    assert_normalized_contains_all(
        text,
        (
            "selectable: active only",
            "never selectable: candidate, needs_review, approved-but-not-activated, rejected, disabled, superseded, forgotten, conflict, merge_candidate",
        ),
    )


def test_governance_status_beats_relevance_recency_namespace_and_confidence() -> None:
    text = read_compiler_doc()

    assert_normalized_contains_all(
        text,
        (
            "governance status beats relevance, recency, namespace match, and confidence",
            "disabled/superseded/forgotten/conflict",
            "skip it even if it looks highly relevant",
        ),
    )


def test_first_compiler_does_not_resolve_conflicts_or_merge_memories() -> None:
    text = read_compiler_doc()

    assert_normalized_contains_all(
        text,
        (
            "compiler must not resolve conflicts",
            'reason_skipped="conflict"',
            "compiler must not merge memories",
            "must not silently pick one conflicting memory as truth",
            "same title, same namespace, or similar text is not enough to merge",
            "future governance runtime, not compiler runtime",
        ),
    )


def test_disabled_superseded_forgotten_and_conflict_items_are_skipped() -> None:
    text = read_compiler_doc()

    assert_normalized_contains_all(
        text,
        (
            "disabled memory is skipped",
            "forgotten memory is skipped",
            "compiler output must not expose forgotten content",
            "compiler must skip superseded items",
            'reason_skipped="superseded"',
            "compiler must skip conflict-marked items",
        ),
    )


def test_governance_addendum_uses_existing_skip_reason_fields() -> None:
    addendum = read_governance_addendum()

    assert re.search(r"\bskipped_reason\b", addendum) is None
    assert_normalized_contains_all(
        addendum,
        (
            "The addendum defines canonical reason values only",
            "it does not define a new output field",
            "Per-item skip reasons must use the existing reason_skipped field",
            "aggregate skipped reasons must use the existing skipped_reasons collection",
        ),
    )


def test_procedural_memory_is_skipped_by_default_unless_requested() -> None:
    text = read_compiler_doc()

    assert_normalized_contains_all(
        text,
        (
            "first compiler must skip procedural memories by default unless explicitly requested by caller config",
            "procedural memory must not be mixed into semantic memory output without a separate section or reason",
        ),
    )


def test_canonical_skipped_reasons_are_documented_for_first_compiler() -> None:
    text = read_compiler_doc()

    assert_contains_all(
        text,
        (
            "inactive",
            "disabled",
            "superseded",
            "forgotten",
            "conflict",
            "candidate_only",
            "rejected",
            "over_budget",
            "missing_provenance",
            "sensitivity_policy",
            "procedural_not_requested",
            "namespace_mismatch",
        ),
    )


def test_standalone_memory_governance_doc_is_not_created() -> None:
    assert not GOVERNANCE_DOC.exists()


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
