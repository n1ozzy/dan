"""Contract checks for the Jarvis Memory OS design docs."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "docs" / "MEMORY_CONTRACT.md"
ARCHITECTURE = ROOT / "docs" / "MEMORY_ARCHITECTURE.md"


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


def test_model_cannot_silently_write_active_durable_memory() -> None:
    text = read_doc(CONTRACT).casefold()

    assert "a model-originated memory_save cannot silently write active durable memory" in text
    assert "memory_save requires approval/execution policy" in text


def test_contract_marks_auto_memory_not_implemented() -> None:
    text = read_doc(CONTRACT).casefold()

    assert "auto-memory extraction is not implemented yet" in text
    assert "summarization/consolidator is not implemented yet" in text


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
