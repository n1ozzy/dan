"""Contract checks for the Jarvis Memory OS design docs."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "docs" / "MEMORY_CONTRACT.md"
ARCHITECTURE = ROOT / "docs" / "MEMORY_ARCHITECTURE.md"
DOCS_INDEX = ROOT / "docs" / "DOCS_INDEX.md"
STATUS = ROOT / "docs" / "STATUS.md"


def read_doc(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_memory_contract_docs_exist() -> None:
    assert CONTRACT.is_file()
    assert ARCHITECTURE.is_file()


def test_memory_docs_are_classified() -> None:
    assert "Classification: authoritative." in read_doc(CONTRACT)
    assert "Classification: current." in read_doc(ARCHITECTURE)


def test_contract_names_all_memory_layers() -> None:
    text = read_doc(CONTRACT)

    required_layers = (
        "Working Memory",
        "Thread Memory",
        "Episodic Memory",
        "Semantic Memory",
        "Procedural Memory",
    )
    missing = [layer for layer in required_layers if layer not in text]

    assert missing == []


def test_contract_names_all_lifecycle_states() -> None:
    text = read_doc(CONTRACT)

    required_states = (
        "observed",
        "candidate",
        "needs_review",
        "approved",
        "active",
        "rejected",
        "superseded",
        "disabled",
        "forgotten",
    )
    missing = [state for state in required_states if state not in text]

    assert missing == []


def test_active_memory_requires_evidence_and_provenance() -> None:
    text = read_doc(CONTRACT).casefold()

    required = (
        "active memory must be evidence-backed",
        "conversation_id",
        "turn_id",
        "event_id",
        "manual source",
        "quote or evidence excerpt",
        "confidence",
        "sensitivity",
    )
    missing = [snippet for snippet in required if snippet not in text]

    assert missing == []


def test_contract_defines_intent_non_goals_and_write_paths() -> None:
    text = read_doc(CONTRACT).casefold()

    required = (
        "product intent",
        "non-goals",
        "manual panel/api/cli",
        'explicit user "remember this"',
        "model-originated memory_save",
        "future background consolidator",
        "future topic document consolidation",
        "manual memory is not the same as automatic assistant memory",
    )
    missing = [snippet for snippet in required if snippet not in text]

    assert missing == []


def test_model_cannot_silently_write_active_durable_memory() -> None:
    text = read_doc(CONTRACT).casefold()

    assert "a model-originated memory_save cannot silently write active durable memory" in text
    assert "memory_save requires approval/execution policy" in text


def test_contract_marks_auto_memory_not_implemented() -> None:
    text = read_doc(CONTRACT).casefold()

    assert "auto-memory extraction is not implemented yet" in text
    assert "summarization/consolidator is not implemented yet" in text
    assert "memory usage events are not implemented yet" in text


def test_contract_defines_privacy_policy() -> None:
    text = read_doc(CONTRACT).casefold()

    required = (
        "no secret storage",
        "no hidden psychological inference",
        "no sensitive inference without approval",
        "forget/disable must prevent default retrieval",
        "secrets must be rejected or redacted",
    )
    missing = [snippet for snippet in required if snippet not in text]

    assert missing == []


def test_topic_documents_are_future_consolidation_units() -> None:
    text = read_doc(CONTRACT).casefold()

    assert "topic documents are future consolidation units" in text
    assert "project/jarvis/*" in text
    assert "user/ozzy/*" in text
    assert "agent/procedural/*" in text


def test_default_retrieval_excludes_inactive_memory() -> None:
    text = read_doc(CONTRACT).casefold()

    assert "default retrieval excludes disabled, superseded, and forgotten memory" in text
    assert "active only by default" in text
    assert "explain why included" in text
    assert "budgeted memorycompiler" in text


def test_architecture_maps_current_v0_memory_and_future_components() -> None:
    text = read_doc(ARCHITECTURE)

    required = (
        "memory_blocks are v0 semantic memory items",
        "ContextBuilder currently injects active memory",
        "memory_save/tool approval path",
        "Memory Inbox",
        "memory_observations",
        "memory_candidates",
        "memory_items",
        "memory_evidence",
        "memory_topics",
        "memory_usage_events",
        "memory_review_decisions",
        "MemoryCompiler",
        "Topic Documents",
        "Episode Cards",
        "Memory Audit",
    )
    missing = [snippet for snippet in required if snippet not in text]

    assert missing == []


def test_architecture_defines_schema_boundary_and_migration_path() -> None:
    text = read_doc(ARCHITECTURE).casefold()

    required = (
        "no schema change in this task",
        "no migration in this task",
        "preserve current memory_blocks",
        "introduce additive structures later",
        "maintain contextbuilder compatibility during transition",
        "future cutover must be explicit and tested",
    )
    missing = [snippet for snippet in required if snippet not in text]

    assert missing == []


def test_architecture_names_required_phase_plan() -> None:
    text = read_doc(ARCHITECTURE)

    phases = (
        "1. Contract",
        "2. Reality tests",
        "3. ADR/data model",
        "4. Additive schema",
        "5. Memory Inbox",
        "6. Evidence ledger",
        "7. memory_save v2",
        "8. MemoryCompiler",
        "9. Topic Documents",
        "10. Governance/dedupe",
        "11. Audit API",
        "12. Panel UX",
        "13. Auto-candidates",
        "14. Manual consolidator",
        "15. Privacy/forgetting",
    )
    missing = [phase for phase in phases if phase not in text]

    assert missing == []


def test_docs_index_references_memory_design_docs() -> None:
    text = read_doc(DOCS_INDEX)

    assert "docs/MEMORY_CONTRACT.md" in text
    assert "docs/MEMORY_ARCHITECTURE.md" in text


def test_status_declares_design_only_and_runtime_unchanged() -> None:
    text = read_doc(STATUS).casefold()

    required = (
        "memory os is in design/contract phase",
        "current memory_blocks remain v0 infrastructure",
        "auto-memory extraction is not implemented yet",
        "no runtime behavior changed by memory-design-01",
    )
    missing = [snippet for snippet in required if snippet not in text]

    assert missing == []
