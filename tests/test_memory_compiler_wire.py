"""MemoryCompiler wiring tests for ContextBuilder."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.brain.base import BrainMessage, BrainRequest
from jarvis.brain.context_builder import ContextBuilder
from jarvis.memory.compiler import (
    CompiledMemoryContext,
    MemoryCompiler,
    SelectedMemoryItem,
    SkippedMemoryItem,
)
from jarvis.memory.items import MemoryItemRepository
from jarvis.memory.manager import MemoryManager
from jarvis.security.redaction import REDACTION_PLACEHOLDER
from jarvis.store.db import close_quietly, initialize_database


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = initialize_database(tmp_path / "context.db")
    try:
        yield connection
    finally:
        close_quietly(connection)


@pytest.fixture
def persona_path(tmp_path: Path) -> Path:
    path = tmp_path / "jarvis.md"
    path.write_text("Persona: Jarvis owns memory.", encoding="utf-8")
    return path


def config() -> SimpleNamespace:
    return SimpleNamespace(
        brain=SimpleNamespace(
            default_adapter="mock",
            default_model="mock-local",
            context_budget_chars=24000,
        ),
        memory=SimpleNamespace(
            enabled=True,
            max_active_blocks=50,
            max_context_chars=12000,
        ),
    )


def fixed_now() -> str:
    return "2026-07-04T12:00:00+00:00"


def test_flag_off_contextbuilder_unchanged(
    conn: sqlite3.Connection,
    persona_path: Path,
) -> None:
    insert_conversation(conn)
    memory = MemoryManager(conn, now=fixed_now)
    memory.create_block("fact", "Existing block", "Keep this block", priority=1)
    builder = ContextBuilder(
        conn,
        config=config(),
        persona_path=persona_path,
        memory_manager=memory,
        now=fixed_now,
    )
    before = builder.build_request(
        turn_id="turn-new",
        conversation_id="conversation-1",
        input_text="Now",
    )

    insert_memory_item(
        conn,
        memory_id="mem-compiled-off",
        title="Compiled title must stay out",
        claim="Compiled claim must stay out",
    )
    insert_evidence(conn, memory_id="mem-compiled-off")
    after = builder.build_request(
        turn_id="turn-new",
        conversation_id="conversation-1",
        input_text="Now",
    )

    assert asdict(after.request) == asdict(before.request)
    assert after.context_snapshot == before.context_snapshot
    rendered = render_context(after.request, after.context_snapshot)
    assert "Compiled title must stay out" not in rendered
    assert "Compiled claim must stay out" not in rendered


def test_flag_off_does_not_call_compiler(
    conn: sqlite3.Connection,
    persona_path: Path,
) -> None:
    insert_conversation(conn)
    compiler = SpyCompiler()
    builder = ContextBuilder(
        conn,
        config=config(),
        persona_path=persona_path,
        memory_compiler=compiler,
        now=fixed_now,
    )

    builder.build_request(
        turn_id="turn-new",
        conversation_id="conversation-1",
        input_text="Now",
    )

    assert compiler.calls == 0


def test_context_output_shape_compiled_memory_disabled_has_no_section(
    conn: sqlite3.Connection,
    persona_path: Path,
) -> None:
    insert_conversation(conn)
    memory = MemoryManager(conn, now=fixed_now)
    block = memory.create_block("fact", "Existing block", "Keep this block", priority=1)
    compiler = SpyCompiler()
    builder = ContextBuilder(
        conn,
        config=config(),
        persona_path=persona_path,
        memory_manager=memory,
        memory_compiler=compiler,
        now=fixed_now,
    )
    before = builder.build_request(
        turn_id="turn-new",
        conversation_id="conversation-1",
        input_text="Now",
    )

    insert_memory_item(
        conn,
        memory_id="mem-compiled-off-shape",
        title="Compiled disabled title",
        claim="Compiled disabled claim",
    )
    insert_evidence(conn, memory_id="mem-compiled-off-shape")
    after = builder.build_request(
        turn_id="turn-new",
        conversation_id="conversation-1",
        input_text="Now",
    )

    assert compiler.calls == 0
    assert asdict(after.request) == asdict(before.request)
    assert after.context_snapshot == before.context_snapshot
    assert context_message_kinds(after.request) == ["persona"]
    assert compiled_memory_messages(after.request) == []
    assert [memory_block.id for memory_block in after.request.memory_blocks] == [block.id]
    rendered = render_context(after.request, after.context_snapshot)
    assert "compiled_memory" not in rendered
    assert "Compiled disabled title" not in rendered
    assert "Compiled disabled claim" not in rendered


def test_scoped_override_true_enables_compiled_memory_without_mutating_builder(
    conn: sqlite3.Connection,
    persona_path: Path,
) -> None:
    insert_conversation(conn)
    memory = MemoryManager(conn, now=fixed_now)
    block = memory.create_block(
        "fact",
        "Scoped override block",
        "SCOPED_OVERRIDE_MEMORY_BLOCK_BODY",
        priority=2,
    )
    compiler = StaticCompiler(
        CompiledMemoryContext(
            selected_items=[
                selected_memory_item(
                    title="SCOPED_OVERRIDE_TITLE",
                    claim="SCOPED_OVERRIDE_CLAIM",
                )
            ]
        )
    )
    builder = ContextBuilder(
        conn,
        config=config(),
        persona_path=persona_path,
        memory_manager=memory,
        memory_compiler=compiler,
        compiled_memory_enabled=False,
        now=fixed_now,
    )

    result = builder.build_request(
        turn_id="turn-new",
        conversation_id="conversation-1",
        input_text="Now",
        compiled_memory_enabled_override=True,
    )

    assert compiler.calls == 1
    assert builder._compiled_memory_enabled is False
    assert context_message_kinds(result.request) == ["persona", "compiled_memory"]
    assert compiled_memory_text(result.request) == (
        "Compiled memory:\n"
        "- title: SCOPED_OVERRIDE_TITLE\n"
        "  claim: SCOPED_OVERRIDE_CLAIM\n"
        "  evidence_count: 1"
    )
    assert [memory_block.id for memory_block in result.request.memory_blocks] == [block.id]
    assert result.request.memory_blocks[0].body == "SCOPED_OVERRIDE_MEMORY_BLOCK_BODY"
    assert compiled_memory_diagnostics(result) == {
        "compiled_memory_enabled": True,
        "compiler_available": True,
        "compiled_memory_attempted": True,
        "compiled_memory_section_present": True,
        "selected_count": 1,
        "skipped_count": 0,
        "fail_closed": False,
        "failure_category": None,
        "skipped_categories": {},
    }


def test_scoped_override_false_disables_enabled_builder_without_mutating_builder(
    conn: sqlite3.Connection,
    persona_path: Path,
) -> None:
    insert_conversation(conn)
    compiler = StaticCompiler(
        CompiledMemoryContext(
            selected_items=[
                selected_memory_item(
                    title="SCOPED_OVERRIDE_FALSE_TITLE",
                    claim="SCOPED_OVERRIDE_FALSE_CLAIM",
                )
            ]
        )
    )
    builder = ContextBuilder(
        conn,
        config=config(),
        persona_path=persona_path,
        memory_compiler=compiler,
        compiled_memory_enabled=True,
        now=fixed_now,
    )

    result = builder.build_request(
        turn_id="turn-new",
        conversation_id="conversation-1",
        input_text="Now",
        compiled_memory_enabled_override=False,
    )

    assert compiler.calls == 0
    assert builder._compiled_memory_enabled is True
    assert context_message_kinds(result.request) == ["persona"]
    assert compiled_memory_messages(result.request) == []
    assert compiled_memory_diagnostics(result) == {
        "compiled_memory_enabled": False,
        "compiler_available": True,
        "compiled_memory_attempted": False,
        "compiled_memory_section_present": False,
        "selected_count": 0,
        "skipped_count": 0,
        "fail_closed": False,
        "failure_category": None,
        "skipped_categories": {},
    }
    rendered = render_context(result.request, result.context_snapshot)
    assert "SCOPED_OVERRIDE_FALSE_TITLE" not in rendered
    assert "SCOPED_OVERRIDE_FALSE_CLAIM" not in rendered


def test_scoped_override_true_does_not_persist_across_requests(
    conn: sqlite3.Connection,
    persona_path: Path,
) -> None:
    insert_conversation(conn)
    compiler = StaticCompiler(
        CompiledMemoryContext(
            selected_items=[
                selected_memory_item(
                    title="SCOPED_OVERRIDE_ONE_SHOT_TITLE",
                    claim="SCOPED_OVERRIDE_ONE_SHOT_CLAIM",
                )
            ]
        )
    )
    builder = ContextBuilder(
        conn,
        config=config(),
        persona_path=persona_path,
        memory_compiler=compiler,
        compiled_memory_enabled=False,
        now=fixed_now,
    )

    first = builder.build_request(
        turn_id="turn-first",
        conversation_id="conversation-1",
        input_text="First",
        compiled_memory_enabled_override=True,
    )
    second = builder.build_request(
        turn_id="turn-second",
        conversation_id="conversation-1",
        input_text="Second",
    )

    assert compiler.calls == 1
    assert builder._compiled_memory_enabled is False
    assert len(compiled_memory_messages(first.request)) == 1
    assert compiled_memory_messages(second.request) == []
    assert compiled_memory_diagnostics(first)["compiled_memory_enabled"] is True
    assert compiled_memory_diagnostics(second) == {
        "compiled_memory_enabled": False,
        "compiler_available": True,
        "compiled_memory_attempted": False,
        "compiled_memory_section_present": False,
        "selected_count": 0,
        "skipped_count": 0,
        "fail_closed": False,
        "failure_category": None,
        "skipped_categories": {},
    }


def test_session_profile_enablement_requires_matching_scope_and_does_not_leak(
    conn: sqlite3.Connection,
    persona_path: Path,
) -> None:
    insert_conversation(conn)
    insert_conversation(conn, conversation_id="conversation-2")
    (persona_path.parent / "scope-profile.md").write_text(
        "Persona: scoped profile.",
        encoding="utf-8",
    )
    compiler = StaticCompiler(
        CompiledMemoryContext(
            selected_items=[
                selected_memory_item(
                    title="SESSION_PROFILE_TITLE",
                    claim="SESSION_PROFILE_CLAIM",
                )
            ]
        )
    )
    builder = ContextBuilder(
        conn,
        config=config(),
        persona_path=persona_path,
        memory_compiler=compiler,
        compiled_memory_enabled=False,
        compiled_memory_scope_gate_enabled=True,
        compiled_memory_enabled_session_profiles=(
            ("conversation-1", "scope-profile"),
        ),
        now=fixed_now,
    )

    matched = builder.build_request(
        turn_id="turn-matched",
        conversation_id="conversation-1",
        input_text="Matched",
        settings={"persona.profile": "scope-profile"},
    )
    wrong_profile = builder.build_request(
        turn_id="turn-wrong-profile",
        conversation_id="conversation-1",
        input_text="Wrong profile",
    )
    wrong_session = builder.build_request(
        turn_id="turn-wrong-session",
        conversation_id="conversation-2",
        input_text="Wrong session",
        settings={"persona.profile": "scope-profile"},
    )

    assert compiler.calls == 1
    assert builder._compiled_memory_enabled is False
    assert context_message_kinds(matched.request) == ["persona", "compiled_memory"]
    assert compiled_memory_text(matched.request) == (
        "Compiled memory:\n"
        "- title: SESSION_PROFILE_TITLE\n"
        "  claim: SESSION_PROFILE_CLAIM\n"
        "  evidence_count: 1"
    )
    assert matched.context_snapshot["persona_profile"] == "scope-profile"
    assert compiled_memory_messages(wrong_profile.request) == []
    assert compiled_memory_messages(wrong_session.request) == []
    assert compiled_memory_diagnostics(matched)["compiled_memory_enabled"] is True
    assert compiled_memory_diagnostics(wrong_profile)["compiled_memory_enabled"] is False
    assert compiled_memory_diagnostics(wrong_session)["compiled_memory_enabled"] is False


def test_request_override_false_disables_session_profile_enablement_for_one_request(
    conn: sqlite3.Connection,
    persona_path: Path,
) -> None:
    insert_conversation(conn)
    (persona_path.parent / "scope-profile.md").write_text(
        "Persona: scoped profile.",
        encoding="utf-8",
    )
    compiler = StaticCompiler(
        CompiledMemoryContext(
            selected_items=[
                selected_memory_item(
                    title="OVERRIDE_FALSE_SESSION_PROFILE_TITLE",
                    claim="OVERRIDE_FALSE_SESSION_PROFILE_CLAIM",
                )
            ]
        )
    )
    builder = ContextBuilder(
        conn,
        config=config(),
        persona_path=persona_path,
        memory_compiler=compiler,
        compiled_memory_enabled=False,
        compiled_memory_scope_gate_enabled=True,
        compiled_memory_enabled_session_profiles=(
            ("conversation-1", "scope-profile"),
        ),
        now=fixed_now,
    )

    disabled = builder.build_request(
        turn_id="turn-disabled",
        conversation_id="conversation-1",
        input_text="Disabled",
        settings={"persona.profile": "scope-profile"},
        compiled_memory_enabled_override=False,
    )
    enabled = builder.build_request(
        turn_id="turn-enabled",
        conversation_id="conversation-1",
        input_text="Enabled",
        settings={"persona.profile": "scope-profile"},
    )

    assert compiler.calls == 1
    assert builder._compiled_memory_enabled is False
    assert compiled_memory_messages(disabled.request) == []
    assert len(compiled_memory_messages(enabled.request)) == 1
    assert compiled_memory_diagnostics(disabled) == {
        "compiled_memory_enabled": False,
        "compiler_available": True,
        "compiled_memory_attempted": False,
        "compiled_memory_section_present": False,
        "selected_count": 0,
        "skipped_count": 0,
        "fail_closed": False,
        "failure_category": None,
        "skipped_categories": {},
    }


def test_memory_enabled_false_blocks_session_profile_and_request_override(
    conn: sqlite3.Connection,
    persona_path: Path,
) -> None:
    insert_conversation(conn)
    (persona_path.parent / "scope-profile.md").write_text(
        "Persona: scoped profile.",
        encoding="utf-8",
    )
    disabled_config = config()
    disabled_config.memory.enabled = False
    compiler = StaticCompiler(
        CompiledMemoryContext(
            selected_items=[
                selected_memory_item(
                    title="MEMORY_DISABLED_TITLE",
                    claim="MEMORY_DISABLED_CLAIM",
                )
            ]
        )
    )
    builder = ContextBuilder(
        conn,
        config=disabled_config,
        persona_path=persona_path,
        memory_compiler=compiler,
        compiled_memory_enabled=True,
        compiled_memory_scope_gate_enabled=True,
        compiled_memory_enabled_session_profiles=(
            ("conversation-1", "scope-profile"),
        ),
        now=fixed_now,
    )

    result = builder.build_request(
        turn_id="turn-disabled",
        conversation_id="conversation-1",
        input_text="Disabled",
        settings={"persona.profile": "scope-profile"},
        compiled_memory_enabled_override=True,
    )

    assert compiler.calls == 0
    assert compiled_memory_messages(result.request) == []
    assert compiled_memory_diagnostics(result) == {
        "compiled_memory_enabled": False,
        "compiler_available": True,
        "compiled_memory_attempted": False,
        "compiled_memory_section_present": False,
        "selected_count": 0,
        "skipped_count": 0,
        "fail_closed": False,
        "failure_category": None,
        "skipped_categories": {},
    }


def test_flag_on_includes_selected_compiled_memory(
    conn: sqlite3.Connection,
    persona_path: Path,
) -> None:
    insert_conversation(conn)
    insert_memory_item(
        conn,
        memory_id="mem-active",
        title="Project truth",
        claim="Use SQLite as Jarvis memory.",
    )
    insert_evidence(conn, memory_id="mem-active")
    builder = enabled_builder(conn, persona_path)

    result = builder.build_request(
        turn_id="turn-new",
        conversation_id="conversation-1",
        input_text="Now",
    )

    messages = compiled_memory_messages(result.request)
    assert len(messages) == 1
    assert messages[0].role == "user"
    assert messages[0].metadata == {"kind": "compiled_memory", "untrusted": True}
    assert messages[0].content == (
        "Compiled memory:\n"
        "- title: Project truth\n"
        "  claim: Use SQLite as Jarvis memory.\n"
        "  evidence_count: 1"
    )


def test_context_output_shape_compiled_memory_enabled_has_safe_section(
    conn: sqlite3.Connection,
    persona_path: Path,
) -> None:
    insert_conversation(conn)
    insert_memory_item(
        conn,
        memory_id="mem-safe-shape",
        canonical_key="canonical-key-must-not-render",
        title="Safe shape title",
        claim="Safe shape claim",
        content="Content body must not render",
    )
    insert_evidence(conn, memory_id="mem-safe-shape", quote="Quote must not render")
    builder = enabled_builder(conn, persona_path)
    user_input = "USER_INPUT_MARKER_compile_memory_must_not_replace_me"

    result = builder.build_request(
        turn_id="turn-new",
        conversation_id="conversation-1",
        input_text=user_input,
    )

    assert result.request.input_text == user_input
    messages = compiled_memory_messages(result.request)
    assert len(messages) == 1
    message = messages[0]
    assert message.role == "user"
    assert message.name is None
    assert message.metadata == {"kind": "compiled_memory", "untrusted": True}
    assert context_message_kinds(result.request) == ["persona", "compiled_memory"]
    assert compiled_memory_field_names(message.content) == [
        "title",
        "claim",
        "evidence_count",
    ]
    assert message.content.splitlines() == [
        "Compiled memory:",
        "- title: Safe shape title",
        "  claim: Safe shape claim",
        "  evidence_count: 1",
    ]


def test_context_governance_excludes_disabled_superseded_forgotten_conflict_and_missing_provenance(
    conn: sqlite3.Connection,
    persona_path: Path,
) -> None:
    insert_conversation(conn)
    insert_memory_item(
        conn,
        memory_id="mem-governance-safe",
        title="SAFE_GOVERNANCE_TITLE_MARKER",
        claim="SAFE_GOVERNANCE_CLAIM_MARKER",
    )
    insert_evidence(conn, memory_id="mem-governance-safe")
    insert_memory_item(
        conn,
        memory_id="mem-governance-disabled",
        status="disabled",
        title="DISABLED_GOVERNANCE_TITLE_MARKER",
        claim="DISABLED_GOVERNANCE_CLAIM_MARKER",
        content="DISABLED_GOVERNANCE_CONTENT_MARKER",
    )
    insert_evidence(conn, memory_id="mem-governance-disabled")
    insert_memory_item(
        conn,
        memory_id="mem-governance-superseded",
        superseded_by="mem-governance-replacement",
        title="SUPERSEDED_GOVERNANCE_TITLE_MARKER",
        claim="SUPERSEDED_GOVERNANCE_CLAIM_MARKER",
        content="SUPERSEDED_GOVERNANCE_CONTENT_MARKER",
    )
    insert_evidence(conn, memory_id="mem-governance-superseded")
    insert_memory_item(
        conn,
        memory_id="mem-governance-forgotten",
        status="forgotten",
        title="FORGOTTEN_GOVERNANCE_TITLE_MARKER",
        claim="FORGOTTEN_GOVERNANCE_CLAIM_MARKER",
        content="FORGOTTEN_GOVERNANCE_CONTENT_MARKER",
    )
    insert_evidence(conn, memory_id="mem-governance-forgotten")
    insert_memory_item(
        conn,
        memory_id="mem-governance-conflict",
        status="conflict",
        title="CONFLICT_GOVERNANCE_TITLE_MARKER",
        claim="CONFLICT_GOVERNANCE_CLAIM_MARKER",
        content="CONFLICT_GOVERNANCE_CONTENT_MARKER",
    )
    insert_evidence(conn, memory_id="mem-governance-conflict")
    insert_memory_item(
        conn,
        memory_id="mem-governance-missing-provenance",
        title="MISSING_PROVENANCE_GOVERNANCE_TITLE_MARKER",
        claim="MISSING_PROVENANCE_GOVERNANCE_CLAIM_MARKER",
        content="MISSING_PROVENANCE_GOVERNANCE_CONTENT_MARKER",
    )
    builder = enabled_builder(conn, persona_path)
    user_input = "USER_INPUT_GOVERNANCE_EXCLUSION_MARKER"

    result = builder.build_request(
        turn_id="turn-new",
        conversation_id="conversation-1",
        input_text=user_input,
    )

    messages = compiled_memory_messages(result.request)
    assert len(messages) == 1
    assert messages[0].metadata == {"kind": "compiled_memory", "untrusted": True}
    assert result.request.input_text == user_input
    assert context_message_kinds(result.request) == ["persona", "compiled_memory"]
    assert compiled_memory_field_names(messages[0].content) == [
        "title",
        "claim",
        "evidence_count",
    ]
    assert messages[0].content == (
        "Compiled memory:\n"
        "- title: SAFE_GOVERNANCE_TITLE_MARKER\n"
        "  claim: SAFE_GOVERNANCE_CLAIM_MARKER\n"
        "  evidence_count: 1"
    )

    compiled_text = compiled_memory_text(result.request)
    rendered = render_context(result.request, result.context_snapshot)
    assert "SAFE_GOVERNANCE_TITLE_MARKER" in rendered
    assert "SAFE_GOVERNANCE_CLAIM_MARKER" in rendered
    forbidden_markers = (
        "DISABLED_GOVERNANCE_TITLE_MARKER",
        "DISABLED_GOVERNANCE_CLAIM_MARKER",
        "DISABLED_GOVERNANCE_CONTENT_MARKER",
        "SUPERSEDED_GOVERNANCE_TITLE_MARKER",
        "SUPERSEDED_GOVERNANCE_CLAIM_MARKER",
        "SUPERSEDED_GOVERNANCE_CONTENT_MARKER",
        "FORGOTTEN_GOVERNANCE_TITLE_MARKER",
        "FORGOTTEN_GOVERNANCE_CLAIM_MARKER",
        "FORGOTTEN_GOVERNANCE_CONTENT_MARKER",
        "CONFLICT_GOVERNANCE_TITLE_MARKER",
        "CONFLICT_GOVERNANCE_CLAIM_MARKER",
        "CONFLICT_GOVERNANCE_CONTENT_MARKER",
        "MISSING_PROVENANCE_GOVERNANCE_TITLE_MARKER",
        "MISSING_PROVENANCE_GOVERNANCE_CLAIM_MARKER",
        "MISSING_PROVENANCE_GOVERNANCE_CONTENT_MARKER",
    )
    assert [marker for marker in forbidden_markers if marker in compiled_text] == []
    assert [marker for marker in forbidden_markers if marker in rendered] == []


def test_context_governance_excludes_procedural_memory_by_default(
    conn: sqlite3.Connection,
    persona_path: Path,
) -> None:
    insert_conversation(conn)
    insert_memory_item(
        conn,
        memory_id="mem-governance-procedural-safe",
        title="SAFE_PROCEDURAL_CONTROL_TITLE_MARKER",
        claim="SAFE_PROCEDURAL_CONTROL_CLAIM_MARKER",
    )
    insert_evidence(conn, memory_id="mem-governance-procedural-safe")
    insert_memory_item(
        conn,
        memory_id="mem-governance-procedural",
        kind="procedural",
        title="PROCEDURAL_GOVERNANCE_TITLE_MARKER",
        claim="PROCEDURAL_GOVERNANCE_CLAIM_MARKER",
        content="PROCEDURAL_GOVERNANCE_CONTENT_MARKER",
    )
    insert_evidence(conn, memory_id="mem-governance-procedural")
    builder = enabled_builder(conn, persona_path)

    result = builder.build_request(
        turn_id="turn-new",
        conversation_id="conversation-1",
        input_text="Now",
    )

    messages = compiled_memory_messages(result.request)
    assert len(messages) == 1
    assert messages[0].metadata == {"kind": "compiled_memory", "untrusted": True}
    compiled_text = compiled_memory_text(result.request)
    rendered = render_context(result.request, result.context_snapshot)
    assert "SAFE_PROCEDURAL_CONTROL_TITLE_MARKER" in compiled_text
    assert "SAFE_PROCEDURAL_CONTROL_CLAIM_MARKER" in compiled_text
    assert "PROCEDURAL_GOVERNANCE_TITLE_MARKER" not in compiled_text
    assert "PROCEDURAL_GOVERNANCE_CLAIM_MARKER" not in compiled_text
    assert "PROCEDURAL_GOVERNANCE_CONTENT_MARKER" not in rendered


def test_context_governance_final_output_excludes_raw_internal_fields(
    conn: sqlite3.Connection,
    persona_path: Path,
) -> None:
    insert_conversation(conn)
    raw_evidence_quote = "RAW_EVIDENCE_QUOTE_GOVERNANCE_MARKER"
    raw_observation_text = "RAW_OBSERVATION_TEXT_GOVERNANCE_MARKER"
    raw_secret_marker = "sk-governance1234567890"
    insert_memory_item(
        conn,
        memory_id="MEMORY_ID_GOVERNANCE_RAW_MARKER",
        canonical_key=f"CANONICAL_KEY_GOVERNANCE_RAW_MARKER {raw_secret_marker}",
        title="SAFE_RAW_FIELD_TITLE_MARKER",
        claim=f"SAFE_RAW_FIELD_CLAIM_MARKER {raw_secret_marker}",
        content="RAW_CONTENT_GOVERNANCE_MARKER",
    )
    insert_observation(
        conn,
        observation_id="observation-governance-raw",
        text=raw_observation_text,
    )
    insert_evidence(
        conn,
        memory_id="MEMORY_ID_GOVERNANCE_RAW_MARKER",
        observation_id="observation-governance-raw",
        quote=raw_evidence_quote,
    )
    insert_memory_item(
        conn,
        memory_id="mem-governance-raw-disabled",
        status="disabled",
        title="DISABLED_RAW_FIELD_TITLE_MARKER",
        claim="DISABLED_RAW_FIELD_CLAIM_MARKER",
        content="DISABLED_RAW_FIELD_CONTENT_MARKER",
    )
    insert_evidence(conn, memory_id="mem-governance-raw-disabled")
    insert_memory_item(
        conn,
        memory_id="mem-governance-raw-superseded",
        superseded_by="mem-governance-raw-replacement",
        title="SUPERSEDED_RAW_FIELD_TITLE_MARKER",
        claim="SUPERSEDED_RAW_FIELD_CLAIM_MARKER",
        content="SUPERSEDED_RAW_FIELD_CONTENT_MARKER",
    )
    insert_evidence(conn, memory_id="mem-governance-raw-superseded")
    insert_memory_item(
        conn,
        memory_id="mem-governance-raw-forgotten",
        status="forgotten",
        title="FORGOTTEN_RAW_FIELD_TITLE_MARKER",
        claim="FORGOTTEN_RAW_FIELD_CLAIM_MARKER",
        content="FORGOTTEN_RAW_FIELD_CONTENT_MARKER",
    )
    insert_evidence(conn, memory_id="mem-governance-raw-forgotten")
    insert_memory_item(
        conn,
        memory_id="mem-governance-raw-conflict",
        status="conflict",
        title="CONFLICT_RAW_FIELD_TITLE_MARKER",
        claim="CONFLICT_RAW_FIELD_CLAIM_MARKER",
        content="CONFLICT_RAW_FIELD_CONTENT_MARKER",
    )
    insert_evidence(conn, memory_id="mem-governance-raw-conflict")
    insert_memory_item(
        conn,
        memory_id="mem-governance-raw-missing-provenance",
        title="MISSING_PROVENANCE_RAW_FIELD_TITLE_MARKER",
        claim="MISSING_PROVENANCE_RAW_FIELD_CLAIM_MARKER",
        content="MISSING_PROVENANCE_RAW_FIELD_CONTENT_MARKER",
    )
    builder = enabled_builder(conn, persona_path)

    result = builder.build_request(
        turn_id="turn-new",
        conversation_id="conversation-1",
        input_text="Now",
    )

    messages = compiled_memory_messages(result.request)
    assert len(messages) == 1
    assert messages[0].metadata == {"kind": "compiled_memory", "untrusted": True}
    assert compiled_memory_field_names(messages[0].content) == [
        "title",
        "claim",
        "evidence_count",
    ]
    compiled_text = compiled_memory_text(result.request)
    rendered = render_context(result.request, result.context_snapshot)
    assert "SAFE_RAW_FIELD_TITLE_MARKER" in compiled_text
    assert "SAFE_RAW_FIELD_CLAIM_MARKER" in compiled_text
    assert REDACTION_PLACEHOLDER in compiled_text

    forbidden_markers = (
        "memory_id",
        "canonical_key",
        "audit_metadata",
        "skipped_items",
        "skipped_reasons",
        "selection_reasons",
        "reason_selected",
        "reason_skipped",
        "MEMORY_ID_GOVERNANCE_RAW_MARKER",
        "CANONICAL_KEY_GOVERNANCE_RAW_MARKER",
        raw_evidence_quote,
        raw_observation_text,
        "RAW_CONTENT_GOVERNANCE_MARKER",
        "DISABLED_RAW_FIELD_TITLE_MARKER",
        "DISABLED_RAW_FIELD_CLAIM_MARKER",
        "DISABLED_RAW_FIELD_CONTENT_MARKER",
        "SUPERSEDED_RAW_FIELD_TITLE_MARKER",
        "SUPERSEDED_RAW_FIELD_CLAIM_MARKER",
        "SUPERSEDED_RAW_FIELD_CONTENT_MARKER",
        "FORGOTTEN_RAW_FIELD_TITLE_MARKER",
        "FORGOTTEN_RAW_FIELD_CLAIM_MARKER",
        "FORGOTTEN_RAW_FIELD_CONTENT_MARKER",
        "CONFLICT_RAW_FIELD_TITLE_MARKER",
        "CONFLICT_RAW_FIELD_CLAIM_MARKER",
        "CONFLICT_RAW_FIELD_CONTENT_MARKER",
        "MISSING_PROVENANCE_RAW_FIELD_TITLE_MARKER",
        "MISSING_PROVENANCE_RAW_FIELD_CLAIM_MARKER",
        "MISSING_PROVENANCE_RAW_FIELD_CONTENT_MARKER",
        raw_secret_marker,
        "CompiledMemoryContext(",
        "CompiledMemoryItem(",
        "SkippedMemoryItem(",
        "MemoryCompilerRequest(",
    )
    assert [marker for marker in forbidden_markers if marker in rendered] == []


def test_scoped_override_true_keeps_governance_redaction_and_diagnostics_off_prompt(
    conn: sqlite3.Connection,
    persona_path: Path,
) -> None:
    insert_conversation(conn)
    raw_evidence_quote = "RAW_SCOPED_OVERRIDE_EVIDENCE_QUOTE"
    raw_observation_text = "RAW_SCOPED_OVERRIDE_OBSERVATION_TEXT"
    raw_secret_marker = "sk-scopedoverride1234567890"
    traceback_marker = "Traceback scoped override marker"
    exception_text_marker = "SCOPED_OVERRIDE_EXCEPTION_TEXT_MARKER"
    insert_memory_item(
        conn,
        memory_id="MEMORY_ID_SCOPED_OVERRIDE_RAW_MARKER",
        canonical_key=f"CANONICAL_KEY_SCOPED_OVERRIDE_RAW_MARKER {raw_secret_marker}",
        title="SAFE_SCOPED_OVERRIDE_TITLE",
        claim=f"SAFE_SCOPED_OVERRIDE_CLAIM {raw_secret_marker}",
        content="RAW_SCOPED_OVERRIDE_SELECTED_CONTENT",
    )
    insert_observation(
        conn,
        observation_id="observation-scoped-override-raw",
        text=raw_observation_text,
    )
    insert_evidence(
        conn,
        memory_id="MEMORY_ID_SCOPED_OVERRIDE_RAW_MARKER",
        observation_id="observation-scoped-override-raw",
        quote=raw_evidence_quote,
    )
    insert_memory_item(
        conn,
        memory_id="mem-scoped-override-disabled",
        status="disabled",
        title=f"DISABLED_SCOPED_OVERRIDE_TITLE {traceback_marker}",
        claim=f"DISABLED_SCOPED_OVERRIDE_CLAIM {exception_text_marker}",
        content=f"DISABLED_SCOPED_OVERRIDE_CONTENT {raw_secret_marker}",
    )
    insert_evidence(conn, memory_id="mem-scoped-override-disabled")
    insert_memory_item(
        conn,
        memory_id="mem-scoped-override-missing-provenance",
        title="MISSING_PROVENANCE_SCOPED_OVERRIDE_TITLE",
        claim="MISSING_PROVENANCE_SCOPED_OVERRIDE_CLAIM",
        content="MISSING_PROVENANCE_SCOPED_OVERRIDE_CONTENT",
    )
    compiler = MemoryCompiler(MemoryItemRepository(conn))
    builder = ContextBuilder(
        conn,
        config=config(),
        persona_path=persona_path,
        memory_compiler=compiler,
        compiled_memory_enabled=False,
        now=fixed_now,
    )

    result = builder.build_request(
        turn_id="turn-new",
        conversation_id="conversation-1",
        input_text="Now",
        compiled_memory_enabled_override=True,
    )

    diagnostics = compiled_memory_diagnostics(result)
    assert builder._compiled_memory_enabled is False
    assert diagnostics == {
        "compiled_memory_enabled": True,
        "compiler_available": True,
        "compiled_memory_attempted": True,
        "compiled_memory_section_present": True,
        "selected_count": 1,
        "skipped_count": 2,
        "fail_closed": False,
        "failure_category": None,
        "skipped_categories": {"disabled": 1, "missing_provenance": 1},
    }
    compiled_text = compiled_memory_text(result.request)
    assert "SAFE_SCOPED_OVERRIDE_TITLE" in compiled_text
    assert "SAFE_SCOPED_OVERRIDE_CLAIM" in compiled_text
    assert REDACTION_PLACEHOLDER in compiled_text

    rendered = render_context(result.request, result.context_snapshot)
    diagnostics_text = json.dumps(diagnostics, sort_keys=True)
    combined_non_model_visible = rendered + diagnostics_text
    assert "compiled_memory_diagnostics" not in rendered
    forbidden_markers = (
        "memory_id",
        "canonical_key",
        "audit_metadata",
        "skipped_items",
        "MEMORY_ID_SCOPED_OVERRIDE_RAW_MARKER",
        "CANONICAL_KEY_SCOPED_OVERRIDE_RAW_MARKER",
        raw_evidence_quote,
        raw_observation_text,
        raw_secret_marker,
        traceback_marker,
        exception_text_marker,
        "RAW_SCOPED_OVERRIDE_SELECTED_CONTENT",
        "DISABLED_SCOPED_OVERRIDE_TITLE",
        "DISABLED_SCOPED_OVERRIDE_CLAIM",
        "DISABLED_SCOPED_OVERRIDE_CONTENT",
        "MISSING_PROVENANCE_SCOPED_OVERRIDE_TITLE",
        "MISSING_PROVENANCE_SCOPED_OVERRIDE_CLAIM",
        "MISSING_PROVENANCE_SCOPED_OVERRIDE_CONTENT",
    )
    assert [
        marker
        for marker in forbidden_markers
        if marker in combined_non_model_visible
    ] == []


def test_session_profile_enablement_keeps_prompt_safe_and_diagnostics_redacted(
    conn: sqlite3.Connection,
    persona_path: Path,
) -> None:
    insert_conversation(conn)
    (persona_path.parent / "scope-profile.md").write_text(
        "Persona: scoped profile.",
        encoding="utf-8",
    )
    raw_evidence_quote = "RAW_SESSION_PROFILE_EVIDENCE_QUOTE"
    raw_observation_text = "RAW_SESSION_PROFILE_OBSERVATION_TEXT"
    raw_secret_marker = "sk-sessionprofile1234567890"
    user_input = "USER_INPUT_SESSION_PROFILE_MARKER"
    insert_memory_item(
        conn,
        memory_id="MEMORY_ID_SESSION_PROFILE_RAW_MARKER",
        canonical_key=f"CANONICAL_KEY_SESSION_PROFILE_RAW_MARKER {raw_secret_marker}",
        title="SAFE_SESSION_PROFILE_TITLE",
        claim=f"SAFE_SESSION_PROFILE_CLAIM {raw_secret_marker}",
        content=f"RAW_SESSION_PROFILE_CONTENT {raw_secret_marker}",
    )
    insert_observation(
        conn,
        observation_id="observation-session-profile-raw",
        text=raw_observation_text,
    )
    insert_evidence(
        conn,
        memory_id="MEMORY_ID_SESSION_PROFILE_RAW_MARKER",
        observation_id="observation-session-profile-raw",
        quote=raw_evidence_quote,
    )
    insert_memory_item(
        conn,
        memory_id="mem-session-profile-disabled",
        status="disabled",
        title="DISABLED_SESSION_PROFILE_TITLE",
        claim="DISABLED_SESSION_PROFILE_CLAIM",
        content="DISABLED_SESSION_PROFILE_CONTENT",
    )
    insert_evidence(conn, memory_id="mem-session-profile-disabled")
    insert_memory_item(
        conn,
        memory_id="mem-session-profile-procedural",
        kind="procedural",
        title="PROCEDURAL_SESSION_PROFILE_TITLE",
        claim="PROCEDURAL_SESSION_PROFILE_CLAIM",
        content="PROCEDURAL_SESSION_PROFILE_CONTENT",
    )
    insert_evidence(conn, memory_id="mem-session-profile-procedural")
    insert_memory_item(
        conn,
        memory_id="mem-session-profile-missing-provenance",
        title="MISSING_PROVENANCE_SESSION_PROFILE_TITLE",
        claim="MISSING_PROVENANCE_SESSION_PROFILE_CLAIM",
        content="MISSING_PROVENANCE_SESSION_PROFILE_CONTENT",
    )
    builder = ContextBuilder(
        conn,
        config=config(),
        persona_path=persona_path,
        compiled_memory_enabled=False,
        compiled_memory_scope_gate_enabled=True,
        compiled_memory_enabled_session_profiles=(
            ("conversation-1", "scope-profile"),
        ),
        now=fixed_now,
    )

    result = builder.build_request(
        turn_id="turn-new",
        conversation_id="conversation-1",
        input_text=user_input,
        settings={"persona.profile": "scope-profile"},
    )

    messages = compiled_memory_messages(result.request)
    assert len(messages) == 1
    assert messages[0].metadata == {"kind": "compiled_memory", "untrusted": True}
    assert compiled_memory_field_names(messages[0].content) == [
        "title",
        "claim",
        "evidence_count",
    ]
    diagnostics = compiled_memory_diagnostics(result)
    assert diagnostics == {
        "compiled_memory_enabled": True,
        "compiler_available": True,
        "compiled_memory_attempted": True,
        "compiled_memory_section_present": True,
        "selected_count": 1,
        "skipped_count": 3,
        "fail_closed": False,
        "failure_category": None,
        "skipped_categories": {
            "disabled": 1,
            "missing_provenance": 1,
            "procedural_not_requested": 1,
        },
    }
    compiled_text = compiled_memory_text(result.request)
    assert "SAFE_SESSION_PROFILE_TITLE" in compiled_text
    assert "SAFE_SESSION_PROFILE_CLAIM" in compiled_text
    assert REDACTION_PLACEHOLDER in compiled_text

    rendered = render_context(result.request, result.context_snapshot)
    diagnostics_text = json.dumps(diagnostics, sort_keys=True)
    assert "compiled_memory_diagnostics" not in rendered
    forbidden_markers = (
        "memory_id",
        "canonical_key",
        "audit_metadata",
        "skipped_items",
        "MEMORY_ID_SESSION_PROFILE_RAW_MARKER",
        "CANONICAL_KEY_SESSION_PROFILE_RAW_MARKER",
        raw_evidence_quote,
        raw_observation_text,
        raw_secret_marker,
        user_input,
        "RAW_SESSION_PROFILE_CONTENT",
        "DISABLED_SESSION_PROFILE_TITLE",
        "DISABLED_SESSION_PROFILE_CLAIM",
        "DISABLED_SESSION_PROFILE_CONTENT",
        "PROCEDURAL_SESSION_PROFILE_TITLE",
        "PROCEDURAL_SESSION_PROFILE_CLAIM",
        "PROCEDURAL_SESSION_PROFILE_CONTENT",
        "MISSING_PROVENANCE_SESSION_PROFILE_TITLE",
        "MISSING_PROVENANCE_SESSION_PROFILE_CLAIM",
        "MISSING_PROVENANCE_SESSION_PROFILE_CONTENT",
    )
    assert [marker for marker in forbidden_markers if marker in diagnostics_text] == []
    prompt_forbidden = tuple(
        marker for marker in forbidden_markers if marker != user_input
    )
    assert [marker for marker in prompt_forbidden if marker in rendered] == []


def test_context_governance_positive_control_renders_only_safe_selected_memory(
    conn: sqlite3.Connection,
    persona_path: Path,
) -> None:
    insert_conversation(conn)
    memory = MemoryManager(conn, now=fixed_now)
    block = memory.create_block(
        "fact",
        "Existing block",
        "MEMORY_BLOCK_GOVERNANCE_PRESERVE_MARKER",
        priority=3,
    )
    insert_memory_item(
        conn,
        memory_id="mem-governance-positive-safe",
        title="SAFE_POSITIVE_CONTROL_TITLE_MARKER",
        claim="SAFE_POSITIVE_CONTROL_CLAIM_MARKER",
    )
    insert_evidence(conn, memory_id="mem-governance-positive-safe")
    insert_memory_item(
        conn,
        memory_id="mem-governance-positive-disabled",
        status="disabled",
        title="DISABLED_POSITIVE_CONTROL_TITLE_MARKER",
        claim="DISABLED_POSITIVE_CONTROL_CLAIM_MARKER",
    )
    insert_evidence(conn, memory_id="mem-governance-positive-disabled")
    builder = ContextBuilder(
        conn,
        config=config(),
        persona_path=persona_path,
        memory_manager=memory,
        compiled_memory_enabled=True,
        now=fixed_now,
    )
    user_input = "USER_INPUT_POSITIVE_CONTROL_MARKER"

    result = builder.build_request(
        turn_id="turn-new",
        conversation_id="conversation-1",
        input_text=user_input,
    )

    messages = compiled_memory_messages(result.request)
    assert len(messages) == 1
    assert messages[0].metadata == {"kind": "compiled_memory", "untrusted": True}
    assert messages[0].content == (
        "Compiled memory:\n"
        "- title: SAFE_POSITIVE_CONTROL_TITLE_MARKER\n"
        "  claim: SAFE_POSITIVE_CONTROL_CLAIM_MARKER\n"
        "  evidence_count: 1"
    )
    assert result.request.input_text == user_input
    assert [memory_block.id for memory_block in result.request.memory_blocks] == [
        block.id
    ]
    assert (
        result.request.memory_blocks[0].body
        == "MEMORY_BLOCK_GOVERNANCE_PRESERVE_MARKER"
    )
    assert "DISABLED_POSITIVE_CONTROL_TITLE_MARKER" not in compiled_memory_text(
        result.request
    )
    assert "DISABLED_POSITIVE_CONTROL_CLAIM_MARKER" not in render_context(
        result.request,
        result.context_snapshot,
    )


def test_flag_on_excludes_skipped_memory(
    conn: sqlite3.Connection,
    persona_path: Path,
) -> None:
    insert_conversation(conn)
    insert_memory_item(
        conn,
        memory_id="mem-eligible",
        title="ELIGIBLE_MARKER",
        claim="ELIGIBLE_CLAIM",
    )
    insert_evidence(conn, memory_id="mem-eligible")
    insert_memory_item(
        conn,
        memory_id="mem-procedural",
        kind="procedural",
        title="PROCEDURAL_MARKER",
        claim="PROCEDURAL_CLAIM",
    )
    insert_evidence(conn, memory_id="mem-procedural")
    insert_memory_item(
        conn,
        memory_id="mem-missing-evidence",
        title="MISSING_EVIDENCE_MARKER",
        claim="MISSING_EVIDENCE_CLAIM",
    )
    insert_memory_item(
        conn,
        memory_id="mem-disabled",
        status="disabled",
        title="DISABLED_MARKER",
        claim="DISABLED_CLAIM",
    )
    insert_evidence(conn, memory_id="mem-disabled")
    insert_memory_item(
        conn,
        memory_id="mem-superseded",
        superseded_by="mem-replacement",
        title="SUPERSEDED_MARKER",
        claim="SUPERSEDED_CLAIM",
    )
    insert_evidence(conn, memory_id="mem-superseded")
    insert_memory_item(
        conn,
        memory_id="mem-forgotten",
        status="forgotten",
        title="FORGOTTEN_MARKER",
        claim="FORGOTTEN_CLAIM",
        content="FORGOTTEN_CONTENT",
    )
    insert_evidence(conn, memory_id="mem-forgotten")
    insert_memory_item(
        conn,
        memory_id="mem-conflict",
        status="conflict",
        title="CONFLICT_MARKER",
        claim="CONFLICT_CLAIM",
    )
    insert_evidence(conn, memory_id="mem-conflict")
    builder = enabled_builder(conn, persona_path)

    result = builder.build_request(
        turn_id="turn-new",
        conversation_id="conversation-1",
        input_text="Now",
    )

    rendered = render_context(result.request, result.context_snapshot)
    assert "ELIGIBLE_MARKER" in rendered
    assert "ELIGIBLE_CLAIM" in rendered
    assert "PROCEDURAL_MARKER" not in rendered
    assert "MISSING_EVIDENCE_MARKER" not in rendered
    assert "DISABLED_MARKER" not in rendered
    assert "SUPERSEDED_MARKER" not in rendered
    assert "FORGOTTEN_MARKER" not in rendered
    assert "FORGOTTEN_CONTENT" not in rendered
    assert "CONFLICT_MARKER" not in rendered

    skipped_reasons = [
        item.reason_skipped
        for item in MemoryCompiler(MemoryItemRepository(conn)).compile().skipped_items
    ]
    assert skipped_reasons.count("procedural_not_requested") == 1
    assert skipped_reasons.count("missing_provenance") == 1
    assert skipped_reasons.count("disabled") == 1
    assert skipped_reasons.count("superseded") == 1
    assert skipped_reasons.count("forgotten") == 1
    assert skipped_reasons.count("conflict") == 1


def test_no_secret_in_context_output(
    conn: sqlite3.Connection,
    persona_path: Path,
) -> None:
    insert_conversation(conn)
    fake_secret = "sk-wiresecret1234567890"
    insert_memory_item(
        conn,
        memory_id=f"mem-{fake_secret}",
        canonical_key=f"semantic:key:{fake_secret}",
        kind=f"semantic {fake_secret}",
        scope=f"project/{fake_secret}",
        namespace=f"project/{fake_secret}/memory",
        title=f"Title {fake_secret}",
        claim=f"Claim {fake_secret}",
        content=f"Content {fake_secret}",
        source_policy=f"manual {fake_secret}",
        sensitivity=f"high {fake_secret}",
    )
    insert_evidence(conn, memory_id=f"mem-{fake_secret}")
    builder = enabled_builder(conn, persona_path)

    result = builder.build_request(
        turn_id="turn-new",
        conversation_id="conversation-1",
        input_text="Now",
    )

    rendered = render_context(result.request, result.context_snapshot)
    assert fake_secret not in rendered
    assert REDACTION_PLACEHOLDER in compiled_memory_text(result.request)


def test_no_raw_evidence_or_observation_in_context_output(
    conn: sqlite3.Connection,
    persona_path: Path,
) -> None:
    insert_conversation(conn)
    raw_quote = "RAW_EVIDENCE_QUOTE_WIRE_MARKER"
    raw_observation = "RAW_OBSERVATION_WIRE_MARKER"
    insert_memory_item(
        conn,
        memory_id="mem-evidence",
        title="Evidence-safe title",
        claim="Evidence-safe claim",
    )
    insert_observation(conn, observation_id="observation-mem-evidence", text=raw_observation)
    insert_evidence(
        conn,
        memory_id="mem-evidence",
        observation_id="observation-mem-evidence",
        quote=raw_quote,
    )
    builder = enabled_builder(conn, persona_path)

    result = builder.build_request(
        turn_id="turn-new",
        conversation_id="conversation-1",
        input_text="Now",
    )

    rendered = render_context(result.request, result.context_snapshot)
    assert raw_quote not in rendered
    assert raw_observation not in rendered
    assert "Evidence-safe claim" in rendered


def test_context_output_shape_excludes_raw_memory_fields(
    conn: sqlite3.Connection,
    persona_path: Path,
) -> None:
    insert_conversation(conn)
    raw_quote = "RAW_EVIDENCE_QUOTE_CONTEXT_SHAPE_MARKER"
    raw_observation = "RAW_OBSERVATION_CONTEXT_SHAPE_MARKER"
    forgotten_content = "FORGOTTEN_CONTENT_CONTEXT_SHAPE_MARKER"
    raw_secret_marker = "sk-outputshape1234567890"
    insert_memory_item(
        conn,
        memory_id="MEMORY_ID_CONTEXT_SHAPE_MARKER",
        canonical_key="CANONICAL_KEY_CONTEXT_SHAPE_MARKER",
        title=f"Safe title {raw_secret_marker}",
        claim=f"Safe claim {raw_secret_marker}",
        content="RAW_CONTENT_CONTEXT_SHAPE_MARKER",
    )
    insert_observation(
        conn,
        observation_id="observation-context-shape",
        text=raw_observation,
    )
    insert_evidence(
        conn,
        memory_id="MEMORY_ID_CONTEXT_SHAPE_MARKER",
        observation_id="observation-context-shape",
        quote=raw_quote,
    )
    insert_memory_item(
        conn,
        memory_id="mem-forgotten-context-shape",
        status="forgotten",
        title="FORGOTTEN_TITLE_CONTEXT_SHAPE_MARKER",
        claim="FORGOTTEN_CLAIM_CONTEXT_SHAPE_MARKER",
        content=forgotten_content,
    )
    insert_evidence(conn, memory_id="mem-forgotten-context-shape")
    builder = enabled_builder(conn, persona_path)

    result = builder.build_request(
        turn_id="turn-new",
        conversation_id="conversation-1",
        input_text="Now",
    )

    compiled_text = compiled_memory_text(result.request)
    rendered = render_context(result.request, result.context_snapshot)
    assert REDACTION_PLACEHOLDER in compiled_text
    forbidden_markers = (
        "skipped_items",
        "audit_metadata",
        "memory_id",
        "canonical_key",
        "MEMORY_ID_CONTEXT_SHAPE_MARKER",
        "CANONICAL_KEY_CONTEXT_SHAPE_MARKER",
        raw_quote,
        raw_observation,
        "RAW_CONTENT_CONTEXT_SHAPE_MARKER",
        "FORGOTTEN_TITLE_CONTEXT_SHAPE_MARKER",
        "FORGOTTEN_CLAIM_CONTEXT_SHAPE_MARKER",
        forgotten_content,
        raw_secret_marker,
        "traceback",
        "Traceback",
        "RuntimeError",
        "Exception",
    )
    assert [marker for marker in forbidden_markers if marker in rendered] == []


def test_procedural_opt_in_default_false(
    conn: sqlite3.Connection,
    persona_path: Path,
) -> None:
    insert_conversation(conn)
    insert_memory_item(
        conn,
        memory_id="mem-procedure-default",
        kind="procedural",
        title="Procedure default title",
        claim="Procedure default claim",
    )
    insert_evidence(conn, memory_id="mem-procedure-default")
    builder = enabled_builder(conn, persona_path)

    result = builder.build_request(
        turn_id="turn-new",
        conversation_id="conversation-1",
        input_text="Now",
    )

    rendered = render_context(result.request, result.context_snapshot)
    assert "Procedure default title" not in rendered
    assert "Procedure default claim" not in rendered
    assert compiled_memory_messages(result.request) == []


def test_compiler_read_only_during_context_build(
    conn: sqlite3.Connection,
    persona_path: Path,
) -> None:
    insert_conversation(conn)
    insert_memory_item(
        conn,
        memory_id="mem-read-only",
        title="Read-only title",
        claim="Read-only claim",
    )
    insert_evidence(conn, memory_id="mem-read-only")
    before = conn.total_changes
    builder = enabled_builder(conn, persona_path)

    builder.build_request(
        turn_id="turn-new",
        conversation_id="conversation-1",
        input_text="Now",
    )

    assert conn.total_changes == before


def test_memory_blocks_behavior_preserved(
    conn: sqlite3.Connection,
    persona_path: Path,
) -> None:
    insert_conversation(conn)
    memory = MemoryManager(conn, now=fixed_now)
    block = memory.create_block(
        "fact",
        "Block title",
        "Keep using memory blocks.",
        priority=5,
    )
    insert_memory_item(
        conn,
        memory_id="mem-compiled",
        title="Compiled title",
        claim="Compiled claim",
    )
    insert_evidence(conn, memory_id="mem-compiled")
    builder = ContextBuilder(
        conn,
        config=config(),
        persona_path=persona_path,
        memory_manager=memory,
        compiled_memory_enabled=True,
        now=fixed_now,
    )

    result = builder.build_request(
        turn_id="turn-new",
        conversation_id="conversation-1",
        input_text="Now",
    )

    assert [memory_block.id for memory_block in result.request.memory_blocks] == [block.id]
    assert result.request.memory_blocks[0].body == "Keep using memory blocks."
    assert result.context_snapshot["memory_block_count"] == 1
    compiled_text = compiled_memory_text(result.request)
    assert "Compiled claim" in compiled_text
    assert "Keep using memory blocks." not in compiled_text


def test_context_output_shape_preserves_memory_blocks_with_compiled_memory(
    conn: sqlite3.Connection,
    persona_path: Path,
) -> None:
    insert_conversation(conn)
    memory = MemoryManager(conn, now=fixed_now)
    block = memory.create_block(
        "fact",
        "Memory block title",
        "MEMORY_BLOCK_BODY_CONTEXT_SHAPE",
        priority=7,
    )
    insert_memory_item(
        conn,
        memory_id="mem-compiled-with-block",
        title="Compiled block title",
        claim="Compiled block claim",
    )
    insert_evidence(conn, memory_id="mem-compiled-with-block")

    off = ContextBuilder(
        conn,
        config=config(),
        persona_path=persona_path,
        memory_manager=memory,
        now=fixed_now,
    ).build_request(
        turn_id="turn-new",
        conversation_id="conversation-1",
        input_text="Now",
    )
    on = ContextBuilder(
        conn,
        config=config(),
        persona_path=persona_path,
        memory_manager=memory,
        compiled_memory_enabled=True,
        now=fixed_now,
    ).build_request(
        turn_id="turn-new",
        conversation_id="conversation-1",
        input_text="Now",
    )

    assert [memory_block.id for memory_block in off.request.memory_blocks] == [block.id]
    assert [memory_block.id for memory_block in on.request.memory_blocks] == [block.id]
    assert off.request.memory_blocks[0].body == "MEMORY_BLOCK_BODY_CONTEXT_SHAPE"
    assert on.request.memory_blocks[0].body == "MEMORY_BLOCK_BODY_CONTEXT_SHAPE"
    assert compiled_memory_messages(off.request) == []
    assert len(compiled_memory_messages(on.request)) == 1
    assert context_message_kinds(off.request) == ["persona"]
    assert context_message_kinds(on.request) == ["persona", "compiled_memory"]
    assert "Compiled block claim" in compiled_memory_text(on.request)
    assert "MEMORY_BLOCK_BODY_CONTEXT_SHAPE" not in compiled_memory_text(on.request)


def test_context_section_shape_stable(
    conn: sqlite3.Connection,
    persona_path: Path,
) -> None:
    insert_conversation(conn)
    insert_memory_item(
        conn,
        memory_id="mem-shape",
        title="Shape title",
        claim="Shape claim",
    )
    insert_evidence(conn, memory_id="mem-shape")
    builder = enabled_builder(conn, persona_path)

    result = builder.build_request(
        turn_id="turn-new",
        conversation_id="conversation-1",
        input_text="Now",
    )

    compiled_text = compiled_memory_text(result.request)
    assert compiled_text == (
        "Compiled memory:\n"
        "- title: Shape title\n"
        "  claim: Shape claim\n"
        "  evidence_count: 1"
    )
    assert "audit_metadata" not in compiled_text
    assert "skipped_items" not in compiled_text
    assert "skipped_reasons" not in compiled_text
    assert "selection_reasons" not in compiled_text
    assert "reason_selected" not in compiled_text
    assert "memory_id" not in compiled_text
    assert "canonical_key" not in compiled_text


def test_compiler_failure_fails_closed_without_traceback(
    conn: sqlite3.Connection,
    persona_path: Path,
) -> None:
    insert_conversation(conn)
    compiler = FailingCompiler()
    builder = ContextBuilder(
        conn,
        config=config(),
        persona_path=persona_path,
        memory_compiler=compiler,
        compiled_memory_enabled=True,
        now=fixed_now,
    )

    result = builder.build_request(
        turn_id="turn-new",
        conversation_id="conversation-1",
        input_text="Now",
    )

    rendered = render_context(result.request, result.context_snapshot)
    assert compiler.calls == 1
    assert compiled_memory_messages(result.request) == []
    assert "compiler boom" not in rendered
    assert "RuntimeError" not in rendered


def test_context_output_shape_compiler_failure_fails_closed(
    conn: sqlite3.Connection,
    persona_path: Path,
) -> None:
    insert_conversation(conn)
    compiler = FailingCompiler("EXCEPTION_TEXT_CONTEXT_SHAPE traceback bait")
    builder = ContextBuilder(
        conn,
        config=config(),
        persona_path=persona_path,
        memory_compiler=compiler,
        compiled_memory_enabled=True,
        now=fixed_now,
    )

    result = builder.build_request(
        turn_id="turn-new",
        conversation_id="conversation-1",
        input_text="Now",
    )

    rendered = render_context(result.request, result.context_snapshot)
    assert result.request.turn_id == "turn-new"
    assert result.request.conversation_id == "conversation-1"
    assert compiler.calls == 1
    assert context_message_kinds(result.request) == ["persona"]
    assert compiled_memory_messages(result.request) == []
    assert "compiled_memory" not in rendered
    assert "EXCEPTION_TEXT_CONTEXT_SHAPE" not in rendered
    assert "traceback bait" not in rendered
    assert "Traceback" not in rendered
    assert "RuntimeError" not in rendered


def test_compiled_memory_observe_disabled_reports_no_attempt(
    conn: sqlite3.Connection,
    persona_path: Path,
) -> None:
    insert_conversation(conn)
    compiler = SpyCompiler()
    with_compiler = ContextBuilder(
        conn,
        config=config(),
        persona_path=persona_path,
        memory_compiler=compiler,
        now=fixed_now,
    ).build_request(
        turn_id="turn-new",
        conversation_id="conversation-1",
        input_text="Now",
    )
    without_compiler = ContextBuilder(
        conn,
        config=config(),
        persona_path=persona_path,
        now=fixed_now,
    ).build_request(
        turn_id="turn-new",
        conversation_id="conversation-1",
        input_text="Now",
    )

    assert compiler.calls == 0
    assert asdict(with_compiler.request) == asdict(without_compiler.request)
    assert with_compiler.context_snapshot == without_compiler.context_snapshot
    assert compiled_memory_diagnostics(with_compiler) == {
        "compiled_memory_enabled": False,
        "compiler_available": True,
        "compiled_memory_attempted": False,
        "compiled_memory_section_present": False,
        "selected_count": 0,
        "skipped_count": 0,
        "fail_closed": False,
        "failure_category": None,
        "skipped_categories": {},
    }
    assert compiled_memory_diagnostics(without_compiler)["compiler_available"] is False
    assert compiled_memory_messages(with_compiler.request) == []


def test_compiled_memory_observe_enabled_reports_safe_counts_only(
    conn: sqlite3.Connection,
    persona_path: Path,
) -> None:
    insert_conversation(conn)
    user_input = "USER_INPUT_OBSERVE_ENABLED_MARKER"
    insert_memory_item(
        conn,
        memory_id="mem-observe-safe",
        canonical_key="canonical-observe-safe",
        title="OBSERVE_SAFE_TITLE_MARKER",
        claim="OBSERVE_SAFE_CLAIM_MARKER",
        content="OBSERVE_SAFE_CONTENT_MARKER",
    )
    insert_evidence(
        conn,
        memory_id="mem-observe-safe",
        quote="OBSERVE_SAFE_EVIDENCE_MARKER",
    )
    builder = enabled_builder(conn, persona_path)

    result = builder.build_request(
        turn_id="turn-new",
        conversation_id="conversation-1",
        input_text=user_input,
    )

    diagnostics = compiled_memory_diagnostics(result)
    assert diagnostics == {
        "compiled_memory_enabled": True,
        "compiler_available": True,
        "compiled_memory_attempted": True,
        "compiled_memory_section_present": True,
        "selected_count": 1,
        "skipped_count": 0,
        "fail_closed": False,
        "failure_category": None,
        "skipped_categories": {},
    }
    compiled_text = compiled_memory_text(result.request)
    assert "OBSERVE_SAFE_TITLE_MARKER" in compiled_text
    assert "OBSERVE_SAFE_CLAIM_MARKER" in compiled_text
    assert "OBSERVE_SAFE_EVIDENCE_MARKER" not in compiled_text
    diagnostics_text = json.dumps(diagnostics, sort_keys=True)
    assert "OBSERVE_SAFE_TITLE_MARKER" not in diagnostics_text
    assert "OBSERVE_SAFE_CLAIM_MARKER" not in diagnostics_text
    assert "OBSERVE_SAFE_CONTENT_MARKER" not in diagnostics_text
    assert "OBSERVE_SAFE_EVIDENCE_MARKER" not in diagnostics_text
    assert "USER_INPUT_OBSERVE_ENABLED_MARKER" not in diagnostics_text
    assert "mem-observe-safe" not in diagnostics_text
    assert "canonical-observe-safe" not in diagnostics_text


def test_compiled_memory_observe_budget_drop_reports_no_visible_section(
    conn: sqlite3.Connection,
    persona_path: Path,
) -> None:
    insert_conversation(conn)
    compiler = StaticCompiler(
        CompiledMemoryContext(
            selected_items=[
                selected_memory_item(
                    title="OBSERVE_BUDGET_DROP_TITLE",
                    claim="OBSERVE_BUDGET_DROP_CLAIM " * 8,
                )
            ],
            skipped_items=[
                SkippedMemoryItem(
                    memory_id="raw-budget-drop-skipped-id",
                    reason_skipped="disabled",
                )
            ],
        )
    )
    builder = ContextBuilder(
        conn,
        config=config(),
        persona_path=persona_path,
        memory_compiler=compiler,
        compiled_memory_enabled=True,
        now=fixed_now,
    )

    result = builder.build_request(
        turn_id="turn-new",
        conversation_id="conversation-1",
        input_text="Now",
        max_context_chars=80,
    )

    diagnostics = compiled_memory_diagnostics(result)
    assert compiler.calls == 1
    assert compiled_memory_messages(result.request) == []
    assert context_message_kinds(result.request) == ["persona"]
    assert diagnostics == {
        "compiled_memory_enabled": True,
        "compiler_available": True,
        "compiled_memory_attempted": True,
        "compiled_memory_section_present": False,
        "selected_count": 0,
        "skipped_count": 1,
        "fail_closed": False,
        "failure_category": None,
        "skipped_categories": {"disabled": 1},
    }
    rendered = render_context(result.request, result.context_snapshot)
    diagnostics_text = json.dumps(diagnostics, sort_keys=True)
    assert "OBSERVE_BUDGET_DROP_TITLE" not in rendered
    assert "OBSERVE_BUDGET_DROP_CLAIM" not in rendered
    assert "OBSERVE_BUDGET_DROP_TITLE" not in diagnostics_text
    assert "OBSERVE_BUDGET_DROP_CLAIM" not in diagnostics_text
    assert "raw-budget-drop-skipped-id" not in diagnostics_text


def test_compiled_memory_observe_skipped_items_are_redacted(
    conn: sqlite3.Connection,
    persona_path: Path,
) -> None:
    insert_conversation(conn)
    secret_marker = "SKIPPED_OBSERVE_SECRET_MARKER"
    compiler = StaticCompiler(
        CompiledMemoryContext(
            selected_items=[
                selected_memory_item(
                    title="OBSERVE_SELECTED_TITLE",
                    claim="OBSERVE_SELECTED_CLAIM",
                )
            ],
            skipped_items=[
                SkippedMemoryItem(
                    memory_id=f"raw-skipped-id-{secret_marker}",
                    reason_skipped="disabled",
                ),
                SkippedMemoryItem(
                    memory_id=f"raw-skipped-id-2-{secret_marker}",
                    reason_skipped=f"raw skipped reason {secret_marker}",
                ),
            ],
        )
    )
    builder = ContextBuilder(
        conn,
        config=config(),
        persona_path=persona_path,
        memory_compiler=compiler,
        compiled_memory_enabled=True,
        now=fixed_now,
    )

    result = builder.build_request(
        turn_id="turn-new",
        conversation_id="conversation-1",
        input_text="Now",
    )

    diagnostics = compiled_memory_diagnostics(result)
    assert diagnostics["selected_count"] == 1
    assert diagnostics["skipped_count"] == 2
    assert diagnostics["skipped_categories"] == {"disabled": 1, "other": 1}
    assert diagnostics["fail_closed"] is False
    assert "OBSERVE_SELECTED_CLAIM" in compiled_memory_text(result.request)
    diagnostics_text = json.dumps(diagnostics, sort_keys=True)
    rendered = render_context(result.request, result.context_snapshot)
    assert secret_marker not in diagnostics_text
    assert secret_marker not in rendered
    assert "raw-skipped-id" not in diagnostics_text
    assert "raw skipped reason" not in diagnostics_text
    assert "memory_id" not in diagnostics_text
    assert "canonical_key" not in diagnostics_text


def test_compiled_memory_observe_failure_is_redacted_and_fail_closed(
    conn: sqlite3.Connection,
    persona_path: Path,
) -> None:
    insert_conversation(conn)
    secret_marker = "SECRET_COMPILER_FAILURE_OBSERVE_MARKER"
    compiler = FailingCompiler(
        f"compiler boom {secret_marker}\nTraceback bait\nRuntimeError bait"
    )
    builder = ContextBuilder(
        conn,
        config=config(),
        persona_path=persona_path,
        memory_compiler=compiler,
        compiled_memory_enabled=True,
        now=fixed_now,
    )

    result = builder.build_request(
        turn_id="turn-new",
        conversation_id="conversation-1",
        input_text="Now",
    )

    diagnostics = compiled_memory_diagnostics(result)
    assert compiler.calls == 1
    assert compiled_memory_messages(result.request) == []
    assert diagnostics == {
        "compiled_memory_enabled": True,
        "compiler_available": True,
        "compiled_memory_attempted": True,
        "compiled_memory_section_present": False,
        "selected_count": 0,
        "skipped_count": 0,
        "fail_closed": True,
        "failure_category": "compiler_error",
        "skipped_categories": {},
    }
    rendered = render_context(result.request, result.context_snapshot)
    diagnostics_text = json.dumps(diagnostics, sort_keys=True)
    assert secret_marker not in rendered
    assert secret_marker not in diagnostics_text
    assert "compiler boom" not in rendered
    assert "compiler boom" not in diagnostics_text
    assert "Traceback" not in rendered
    assert "Traceback" not in diagnostics_text
    assert "RuntimeError" not in rendered
    assert "RuntimeError" not in diagnostics_text


def test_scoped_override_true_compiler_failure_fails_closed_and_redacts(
    conn: sqlite3.Connection,
    persona_path: Path,
) -> None:
    insert_conversation(conn)
    secret_marker = "SECRET_SCOPED_OVERRIDE_FAILURE_MARKER"
    compiler = FailingCompiler(
        f"SCOPED_OVERRIDE_COMPILER_BOOM {secret_marker}\nTraceback bait\nRuntimeError bait"
    )
    builder = ContextBuilder(
        conn,
        config=config(),
        persona_path=persona_path,
        memory_compiler=compiler,
        compiled_memory_enabled=False,
        now=fixed_now,
    )

    result = builder.build_request(
        turn_id="turn-new",
        conversation_id="conversation-1",
        input_text="Now",
        compiled_memory_enabled_override=True,
    )

    diagnostics = compiled_memory_diagnostics(result)
    assert compiler.calls == 1
    assert builder._compiled_memory_enabled is False
    assert compiled_memory_messages(result.request) == []
    assert diagnostics == {
        "compiled_memory_enabled": True,
        "compiler_available": True,
        "compiled_memory_attempted": True,
        "compiled_memory_section_present": False,
        "selected_count": 0,
        "skipped_count": 0,
        "fail_closed": True,
        "failure_category": "compiler_error",
        "skipped_categories": {},
    }
    rendered = render_context(result.request, result.context_snapshot)
    diagnostics_text = json.dumps(diagnostics, sort_keys=True)
    assert secret_marker not in rendered
    assert secret_marker not in diagnostics_text
    assert "SCOPED_OVERRIDE_COMPILER_BOOM" not in rendered
    assert "SCOPED_OVERRIDE_COMPILER_BOOM" not in diagnostics_text
    assert "Traceback" not in rendered
    assert "Traceback" not in diagnostics_text
    assert "RuntimeError" not in rendered
    assert "RuntimeError" not in diagnostics_text


def test_session_profile_enablement_compiler_failure_fails_closed_and_redacts(
    conn: sqlite3.Connection,
    persona_path: Path,
) -> None:
    insert_conversation(conn)
    (persona_path.parent / "scope-profile.md").write_text(
        "Persona: scoped profile.",
        encoding="utf-8",
    )
    secret_marker = "SECRET_SESSION_PROFILE_FAILURE_MARKER"
    compiler = FailingCompiler(
        f"SESSION_PROFILE_COMPILER_BOOM {secret_marker}\nTraceback bait\nRuntimeError bait"
    )
    builder = ContextBuilder(
        conn,
        config=config(),
        persona_path=persona_path,
        memory_compiler=compiler,
        compiled_memory_enabled=False,
        compiled_memory_scope_gate_enabled=True,
        compiled_memory_enabled_session_profiles=(
            ("conversation-1", "scope-profile"),
        ),
        now=fixed_now,
    )

    result = builder.build_request(
        turn_id="turn-new",
        conversation_id="conversation-1",
        input_text="Now",
        settings={"persona.profile": "scope-profile"},
    )

    diagnostics = compiled_memory_diagnostics(result)
    assert compiler.calls == 1
    assert compiled_memory_messages(result.request) == []
    assert diagnostics == {
        "compiled_memory_enabled": True,
        "compiler_available": True,
        "compiled_memory_attempted": True,
        "compiled_memory_section_present": False,
        "selected_count": 0,
        "skipped_count": 0,
        "fail_closed": True,
        "failure_category": "compiler_error",
        "skipped_categories": {},
    }
    rendered = render_context(result.request, result.context_snapshot)
    diagnostics_text = json.dumps(diagnostics, sort_keys=True)
    assert secret_marker not in rendered
    assert secret_marker not in diagnostics_text
    assert "SESSION_PROFILE_COMPILER_BOOM" not in rendered
    assert "SESSION_PROFILE_COMPILER_BOOM" not in diagnostics_text
    assert "Traceback" not in rendered
    assert "Traceback" not in diagnostics_text
    assert "RuntimeError" not in rendered
    assert "RuntimeError" not in diagnostics_text


def test_compiled_memory_observe_does_not_change_context_output(
    conn: sqlite3.Connection,
    persona_path: Path,
) -> None:
    insert_conversation(conn)
    insert_memory_item(
        conn,
        memory_id="mem-observe-output",
        title="Observe output title",
        claim="Observe output claim",
    )
    insert_evidence(conn, memory_id="mem-observe-output")
    builder = enabled_builder(conn, persona_path)

    result = builder.build_request(
        turn_id="turn-new",
        conversation_id="conversation-1",
        input_text="Now",
    )

    assert compiled_memory_text(result.request) == (
        "Compiled memory:\n"
        "- title: Observe output title\n"
        "  claim: Observe output claim\n"
        "  evidence_count: 1"
    )
    request_text = json.dumps(asdict(result.request), sort_keys=True)
    snapshot_text = json.dumps(result.context_snapshot, sort_keys=True)
    assert "compiled_memory_diagnostics" not in request_text
    assert "compiled_memory_diagnostics" not in snapshot_text
    assert "skipped_categories" not in request_text
    assert "skipped_categories" not in snapshot_text
    assert "failure_category" not in request_text
    assert "failure_category" not in snapshot_text
    assert "compiled_memory_diagnostics" not in result.request.metadata
    assert "compiled_memory_diagnostics" not in result.context_snapshot


def test_compiled_memory_observe_preserves_memory_blocks(
    conn: sqlite3.Connection,
    persona_path: Path,
) -> None:
    insert_conversation(conn)
    memory = MemoryManager(conn, now=fixed_now)
    block = memory.create_block(
        "fact",
        "Observe block title",
        "OBSERVE_MEMORY_BLOCK_BODY_MARKER",
        priority=9,
    )
    insert_memory_item(
        conn,
        memory_id="mem-observe-block",
        title="Observe compiled title",
        claim="Observe compiled claim",
    )
    insert_evidence(conn, memory_id="mem-observe-block")
    builder = ContextBuilder(
        conn,
        config=config(),
        persona_path=persona_path,
        memory_manager=memory,
        compiled_memory_enabled=True,
        now=fixed_now,
    )

    result = builder.build_request(
        turn_id="turn-new",
        conversation_id="conversation-1",
        input_text="Now",
    )

    assert [memory_block.id for memory_block in result.request.memory_blocks] == [block.id]
    assert result.request.memory_blocks[0].body == "OBSERVE_MEMORY_BLOCK_BODY_MARKER"
    assert result.context_snapshot["memory_block_count"] == 1
    assert compiled_memory_diagnostics(result)["selected_count"] == 1
    assert "OBSERVE_MEMORY_BLOCK_BODY_MARKER" not in compiled_memory_text(result.request)
    diagnostics_text = json.dumps(compiled_memory_diagnostics(result), sort_keys=True)
    assert "OBSERVE_MEMORY_BLOCK_BODY_MARKER" not in diagnostics_text


class SpyCompiler:
    def __init__(self) -> None:
        self.calls = 0

    def compile(self, request: Any) -> CompiledMemoryContext:
        del request
        self.calls += 1
        return CompiledMemoryContext()


class FailingCompiler:
    def __init__(self, message: str = "compiler boom with traceback bait") -> None:
        self.calls = 0
        self.message = message

    def compile(self, request: Any) -> CompiledMemoryContext:
        del request
        self.calls += 1
        raise RuntimeError(self.message)


class StaticCompiler:
    def __init__(self, context: CompiledMemoryContext) -> None:
        self.calls = 0
        self.context = context

    def compile(self, request: Any) -> CompiledMemoryContext:
        del request
        self.calls += 1
        return self.context


def enabled_builder(conn: sqlite3.Connection, persona_path: Path) -> ContextBuilder:
    return ContextBuilder(
        conn,
        config=config(),
        persona_path=persona_path,
        compiled_memory_enabled=True,
        now=fixed_now,
    )


def compiled_memory_messages(request: BrainRequest) -> list[BrainMessage]:
    return [
        message
        for message in request.context_messages
        if message.metadata.get("kind") == "compiled_memory"
    ]


def compiled_memory_text(request: BrainRequest) -> str:
    return "\n\n".join(message.content for message in compiled_memory_messages(request))


def context_message_kinds(request: BrainRequest) -> list[str]:
    return [str(message.metadata.get("kind", "")) for message in request.context_messages]


def compiled_memory_diagnostics(result: Any) -> dict[str, Any]:
    diagnostics = result.compiled_memory_diagnostics
    if hasattr(diagnostics, "__dataclass_fields__"):
        return asdict(diagnostics)
    return dict(diagnostics)


def selected_memory_item(
    *,
    title: str,
    claim: str,
) -> SelectedMemoryItem:
    return SelectedMemoryItem(
        memory_id="mem-ref-selected",
        canonical_key="canonical-selected",
        kind="semantic",
        scope="project",
        namespace="project/jarvis",
        title=title,
        claim=claim,
        reason_selected="eligible",
        evidence_count=1,
        source_policy="candidate_evidence",
        sensitivity="low",
        budget_cost=len(title) + len(claim),
    )


def compiled_memory_field_names(content: str) -> list[str]:
    fields: list[str] = []
    for line in content.splitlines()[1:]:
        stripped = line.strip()
        if stripped.startswith("- "):
            stripped = stripped[2:]
        fields.append(stripped.split(":", 1)[0])
    return fields


def render_context(request: BrainRequest, snapshot: dict[str, Any]) -> str:
    return json.dumps(
        {
            "request": asdict(request),
            "snapshot": snapshot,
        },
        sort_keys=True,
    )


def insert_conversation(
    conn: sqlite3.Connection,
    conversation_id: str = "conversation-1",
) -> None:
    conn.execute(
        """
        INSERT INTO conversations (id, created_at, updated_at, title, status, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            conversation_id,
            "2026-07-04T11:00:00+00:00",
            "2026-07-04T11:00:00+00:00",
            "Test",
            "active",
            "{}",
        ),
    )
    conn.commit()


def insert_memory_item(
    conn: sqlite3.Connection,
    *,
    memory_id: str,
    canonical_key: str | None = None,
    kind: str = "semantic",
    scope: str = "project",
    namespace: str = "project/jarvis",
    title: str | None = None,
    claim: str | None = None,
    content: str | None = None,
    status: str = "active",
    confidence: str = "high",
    sensitivity: str = "low",
    source_policy: str | None = "candidate_evidence",
    superseded_by: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO memory_items (
          id, canonical_key, kind, scope, namespace, title, claim, content,
          status, confidence, sensitivity, source_policy, created_at,
          updated_at, last_used_at, last_confirmed_at, supersedes, superseded_by
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            memory_id,
            canonical_key or f"key-{memory_id}",
            kind,
            scope,
            namespace,
            title if title is not None else f"Title {memory_id}",
            claim if claim is not None else f"Claim {memory_id}",
            content
            if content is not None
            else claim
            if claim is not None
            else f"Claim {memory_id}",
            status,
            confidence,
            sensitivity,
            source_policy,
            "2026-07-04T11:00:00+00:00",
            "2026-07-04T12:00:00+00:00",
            None,
            None,
            None,
            superseded_by,
        ),
    )
    conn.commit()


def insert_observation(
    conn: sqlite3.Connection,
    *,
    observation_id: str,
    text: str,
) -> None:
    conn.execute(
        """
        INSERT INTO memory_observations (
          id, source_type, source_id, conversation_id, turn_id, event_id,
          observed_text, detected_kind, sensitivity, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            observation_id,
            "test",
            "source-1",
            "conversation-1",
            "turn-1",
            1,
            text,
            None,
            "unknown",
            "2026-07-04T12:00:00+00:00",
        ),
    )
    conn.commit()


def insert_evidence(
    conn: sqlite3.Connection,
    *,
    memory_id: str,
    observation_id: str | None = None,
    quote: str = "Evidence quote should never enter prompt context.",
) -> None:
    conn.execute(
        """
        INSERT INTO memory_evidence (
          id, memory_id, candidate_id, observation_id, conversation_id, turn_id,
          event_id, quote, weight, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"evidence-{memory_id}",
            memory_id,
            None,
            observation_id or f"observation-{memory_id}",
            "conversation-1",
            "turn-1",
            1,
            quote,
            1.0,
            "2026-07-04T12:01:00+00:00",
        ),
    )
    conn.commit()
