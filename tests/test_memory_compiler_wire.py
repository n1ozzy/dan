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
from jarvis.memory.compiler import CompiledMemoryContext, MemoryCompiler
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


class SpyCompiler:
    def __init__(self) -> None:
        self.calls = 0

    def compile(self, request: Any) -> CompiledMemoryContext:
        del request
        self.calls += 1
        return CompiledMemoryContext()


class FailingCompiler:
    def __init__(self) -> None:
        self.calls = 0

    def compile(self, request: Any) -> CompiledMemoryContext:
        del request
        self.calls += 1
        raise RuntimeError("compiler boom with traceback bait")


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


def render_context(request: BrainRequest, snapshot: dict[str, Any]) -> str:
    return json.dumps(
        {
            "request": asdict(request),
            "snapshot": snapshot,
        },
        sort_keys=True,
    )


def insert_conversation(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        INSERT INTO conversations (id, created_at, updated_at, title, status, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "conversation-1",
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
