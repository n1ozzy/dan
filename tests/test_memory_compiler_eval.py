"""Golden scenario tests for deterministic MemoryCompiler behavior."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

from jarvis.memory.compiler import (
    CompiledMemoryContext,
    MemoryCompiler,
    MemoryCompilerConfig,
    MemoryCompilerRequest,
)
from jarvis.memory.items import MemoryItemRepository
from jarvis.memory.manager import MemoryManager
from jarvis.store.db import close_quietly, initialize_database


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = initialize_database(tmp_path / "jarvis.db")
    try:
        yield connection
    finally:
        close_quietly(connection)


@pytest.fixture
def compiler(conn: sqlite3.Connection) -> MemoryCompiler:
    return MemoryCompiler(MemoryItemRepository(conn))


def test_project_specific_memory_ranks_before_global_fallback(
    conn: sqlite3.Connection,
    compiler: MemoryCompiler,
) -> None:
    insert_memory_item(
        conn,
        memory_id="mem-global-fact",
        namespace="global/fact",
        claim="Global fallback memory is usable.",
        updated_at="2026-07-04T13:00:00+00:00",
    )
    insert_memory_item(
        conn,
        memory_id="mem-project-default",
        namespace="project/default",
        claim="Project-specific memory is more relevant.",
        updated_at="2026-07-04T12:00:00+00:00",
    )
    insert_evidence(conn, memory_id="mem-global-fact")
    insert_evidence(conn, memory_id="mem-project-default")

    context = compiler.compile(
        MemoryCompilerRequest(
            config=MemoryCompilerConfig(
                max_items=10,
                max_chars=5000,
                namespace_filter="project/default",
            )
        )
    )

    assert [item.memory_id for item in context.selected_items] == [
        projected_memory_id("mem-project-default"),
        projected_memory_id("mem-global-fact"),
    ]


def test_unrelated_namespace_skipped_but_global_fallback_survives(
    conn: sqlite3.Connection,
    compiler: MemoryCompiler,
) -> None:
    insert_memory_item(
        conn,
        memory_id="mem-other-project",
        namespace="other/project",
        claim="Other project memory must not compile.",
    )
    insert_memory_item(
        conn,
        memory_id="mem-global-preference",
        namespace="global/preference",
        claim="Global preference can fall back into the context.",
    )
    insert_memory_item(
        conn,
        memory_id="mem-global-bare",
        namespace="global",
        claim="Bare global memory can fall back into the context.",
    )
    insert_evidence(conn, memory_id="mem-other-project")
    insert_evidence(conn, memory_id="mem-global-preference")
    insert_evidence(conn, memory_id="mem-global-bare")

    context = compiler.compile(
        MemoryCompilerRequest(
            config=MemoryCompilerConfig(
                max_items=10,
                max_chars=5000,
                namespace_filter="project/default",
            )
        )
    )

    assert [item.memory_id for item in context.selected_items] == [
        projected_memory_id("mem-global-bare"),
        projected_memory_id("mem-global-preference"),
    ]
    assert context.skipped_reasons == {
        projected_memory_id("mem-other-project"): "namespace_mismatch"
    }


def test_governance_status_beats_confidence_recency_and_namespace_filter(
    conn: sqlite3.Connection,
    compiler: MemoryCompiler,
) -> None:
    governed_status_words = ("disabled", "superseded", "conflict", "forgotten")
    current_user_text = "deterministic project compiler memory"
    governed_items = {
        "mem-governed-a": (
            "disabled",
            "disabled",
            "other/project",
            "Orange receipt",
            "Archived orange receipt from warehouse shelf seven.",
        ),
        "mem-governed-b": (
            "superseded",
            "superseded",
            "other/project",
            "Blue label",
            "Blue warehouse label for loading dock inventory.",
        ),
        "mem-governed-c": (
            "conflict",
            "conflict",
            "other/project",
            "Brass token",
            "Brass token note for cabinet drawer audit.",
        ),
        "mem-governed-d": (
            "forgotten",
            "forgotten",
            "other/project",
            "Paper ticket",
            "Paper ticket from storage bin rotation.",
        ),
    }
    assert_no_status_words(current_user_text, governed_status_words)
    for index, (
        memory_id,
        (status, _reason, namespace, title, claim),
    ) in enumerate(governed_items.items()):
        assert_no_status_words(title, governed_status_words)
        assert_no_status_words(claim, governed_status_words)
        insert_memory_item(
            conn,
            memory_id=memory_id,
            canonical_key=f"key-governed-{index}",
            status=status,
            namespace=namespace,
            title=title,
            confidence="high",
            claim=claim,
            content=claim,
            updated_at=f"2026-07-04T13:0{index}:00+00:00",
        )
        insert_evidence(conn, memory_id=memory_id)
    insert_memory_item(
        conn,
        memory_id="mem-active-low-confidence",
        status="active",
        namespace="project/default",
        confidence="low",
        title="Deterministic project compiler memory",
        claim="Deterministic project compiler memory should compile.",
        updated_at="2026-07-03T12:00:00+00:00",
    )
    insert_memory_item(
        conn,
        memory_id="mem-active-other-namespace",
        status="active",
        namespace="other/project",
        confidence="high",
        claim="Active unrelated memory should still get namespace mismatch.",
        updated_at="2026-07-04T14:00:00+00:00",
    )
    insert_evidence(conn, memory_id="mem-active-low-confidence")
    insert_evidence(conn, memory_id="mem-active-other-namespace")

    context = compiler.compile(
        MemoryCompilerRequest(
            current_user_text=current_user_text,
            config=MemoryCompilerConfig(
                max_items=10,
                max_chars=5000,
                namespace_filter="project/default",
            )
        )
    )

    assert [item.memory_id for item in context.selected_items] == [
        projected_memory_id("mem-active-low-confidence")
    ]
    assert context.skipped_reasons == {
        **{
            projected_memory_id(memory_id): reason
            for memory_id, (
                _status,
                reason,
                _namespace,
                _title,
                _claim,
            ) in governed_items.items()
        },
        projected_memory_id("mem-active-other-namespace"): "namespace_mismatch",
    }
    assert all(
        context.skipped_reasons[projected_memory_id(memory_id)]
        not in {"namespace_mismatch", "relevance_mismatch"}
        and "relevance" not in context.skipped_reasons[projected_memory_id(memory_id)]
        for memory_id in governed_items
    )


def test_missing_provenance_loses_to_lower_confidence_with_evidence(
    conn: sqlite3.Connection,
    compiler: MemoryCompiler,
) -> None:
    insert_memory_item(
        conn,
        memory_id="mem-high-confidence-no-evidence",
        confidence="high",
        claim="High confidence without evidence must not compile.",
        updated_at="2026-07-04T13:00:00+00:00",
    )
    insert_memory_item(
        conn,
        memory_id="mem-low-confidence-with-evidence",
        confidence="low",
        claim="Lower confidence with evidence should compile.",
        updated_at="2026-07-03T12:00:00+00:00",
    )
    insert_evidence(conn, memory_id="mem-low-confidence-with-evidence")

    context = compiler.compile(
        MemoryCompilerRequest(
            config=MemoryCompilerConfig(max_items=10, max_chars=5000)
        )
    )

    assert [item.memory_id for item in context.selected_items] == [
        projected_memory_id("mem-low-confidence-with-evidence")
    ]
    assert context.skipped_reasons == {
        projected_memory_id("mem-high-confidence-no-evidence"): "missing_provenance"
    }


def test_procedural_memory_is_opt_in(
    conn: sqlite3.Connection,
    compiler: MemoryCompiler,
) -> None:
    insert_memory_item(
        conn,
        memory_id="mem-procedural",
        kind="procedural",
        claim="Run this procedure only when procedural memory is requested.",
    )
    insert_memory_item(
        conn,
        memory_id="mem-semantic",
        kind="semantic",
        claim="Semantic memory can compile by default.",
    )
    insert_evidence(conn, memory_id="mem-procedural")
    insert_evidence(conn, memory_id="mem-semantic")

    default_context = compiler.compile(
        MemoryCompilerRequest(
            config=MemoryCompilerConfig(max_items=10, max_chars=5000)
        )
    )
    opt_in_context = compiler.compile(
        MemoryCompilerRequest(
            config=MemoryCompilerConfig(
                max_items=10,
                max_chars=5000,
                include_procedural=True,
            )
        )
    )

    assert [item.memory_id for item in default_context.selected_items] == [
        projected_memory_id("mem-semantic")
    ]
    assert default_context.skipped_reasons == {
        projected_memory_id("mem-procedural"): "procedural_not_requested"
    }
    assert projected_memory_id("mem-procedural") in {
        item.memory_id for item in opt_in_context.selected_items
    }


def test_forgotten_and_secret_content_never_surface(
    conn: sqlite3.Connection,
    compiler: MemoryCompiler,
) -> None:
    fake_secret = "sk-forgottenevalsecret1234567890"
    raw_memory_id = f"mem-{fake_secret}"
    insert_memory_item(
        conn,
        memory_id=raw_memory_id,
        canonical_key=f"key-{fake_secret}",
        status="forgotten",
        namespace=f"project/{fake_secret}",
        title=f"Forgotten title {fake_secret}",
        claim=f"Forgotten claim {fake_secret}",
        content=f"Forgotten content {fake_secret}",
        source_policy=f"policy {fake_secret}",
        sensitivity=f"sensitivity {fake_secret}",
    )
    insert_evidence(conn, memory_id=raw_memory_id)

    context = compiler.compile(MemoryCompilerRequest())

    rendered = json.dumps(asdict(context), sort_keys=True)
    skipped = asdict(context.skipped_items[0])
    assert fake_secret not in rendered
    assert skipped == {
        "memory_id": projected_memory_id(raw_memory_id),
        "reason_skipped": "forgotten",
    }
    assert "title" not in skipped
    assert "claim" not in skipped
    assert "content" not in skipped
    assert skipped["memory_id"].startswith("mem_ref_")
    assert raw_memory_id not in skipped["memory_id"]


def test_output_shape_is_stable_for_mixed_set(
    conn: sqlite3.Connection,
    compiler: MemoryCompiler,
) -> None:
    insert_memory_item(
        conn,
        memory_id="mem-selected-project",
        namespace="project/default",
        claim="Project memory should be selected.",
    )
    insert_memory_item(
        conn,
        memory_id="mem-skipped-procedural",
        kind="procedural",
        namespace="project/default",
        claim="Procedural memory should be skipped by default.",
    )
    insert_memory_item(
        conn,
        memory_id="mem-skipped-no-evidence",
        namespace="project/default",
        claim="Missing-evidence memory should be skipped.",
    )
    insert_memory_item(
        conn,
        memory_id="mem-skipped-namespace",
        namespace="other/project",
        claim="Other namespace memory should be skipped.",
    )
    insert_memory_item(
        conn,
        memory_id="mem-selected-global",
        namespace="global/fact",
        claim="Global fallback should be selected.",
    )
    for memory_id in (
        "mem-selected-project",
        "mem-skipped-procedural",
        "mem-skipped-namespace",
        "mem-selected-global",
    ):
        insert_evidence(conn, memory_id=memory_id)

    context = compiler.compile(
        MemoryCompilerRequest(
            config=MemoryCompilerConfig(
                max_items=10,
                max_chars=5000,
                namespace_filter="project/default",
            )
        )
    )
    payload = asdict(context)

    assert isinstance(context, CompiledMemoryContext)
    assert set(payload) == {
        "selected_items",
        "skipped_items",
        "budget_used",
        "budget_limit",
        "selection_reasons",
        "skipped_reasons",
        "audit_metadata",
        "warnings",
    }
    assert "skipped_reason" not in payload
    assert [item.memory_id for item in context.selected_items] == [
        projected_memory_id("mem-selected-project"),
        projected_memory_id("mem-selected-global"),
    ]
    assert context.skipped_reasons == {
        projected_memory_id("mem-skipped-procedural"): "procedural_not_requested",
        projected_memory_id("mem-skipped-no-evidence"): "missing_provenance",
        projected_memory_id("mem-skipped-namespace"): "namespace_mismatch",
    }
    for skipped in payload["skipped_items"]:
        assert set(skipped) == {"memory_id", "reason_skipped"}
        assert "skipped_reason" not in skipped
    assert set(context.selection_reasons) == {
        item.memory_id for item in context.selected_items
    }
    assert set(context.skipped_reasons) == {
        item.memory_id for item in context.skipped_items
    }


def test_budget_behavior_is_deterministic(
    conn: sqlite3.Connection,
    compiler: MemoryCompiler,
) -> None:
    for memory_id, title, claim in (
        ("mem-a", "A", "aa"),
        ("mem-b", "B", "bb"),
        ("mem-c", "C", "cc"),
        ("mem-long", "Long title", "This claim does not fit."),
    ):
        insert_memory_item(
            conn,
            memory_id=memory_id,
            title=title,
            claim=claim,
            updated_at="2026-07-04T12:00:00+00:00",
        )
        insert_evidence(conn, memory_id=memory_id)

    request = MemoryCompilerRequest(
        config=MemoryCompilerConfig(max_items=2, max_chars=7)
    )

    first_context = compiler.compile(request)
    second_context = compiler.compile(request)

    assert [item.memory_id for item in first_context.selected_items] == [
        projected_memory_id("mem-a"),
        projected_memory_id("mem-b"),
    ]
    assert [item.memory_id for item in first_context.selected_items] == [
        item.memory_id for item in second_context.selected_items
    ]
    assert [item.memory_id for item in first_context.skipped_items] == [
        item.memory_id for item in second_context.skipped_items
    ]
    assert first_context.skipped_reasons == second_context.skipped_reasons
    assert first_context.skipped_reasons == {
        projected_memory_id("mem-c"): "over_budget",
        projected_memory_id("mem-long"): "over_budget",
    }
    assert first_context.budget_used == sum(
        item.budget_cost for item in first_context.selected_items
    )


def test_raw_evidence_quote_never_in_output(
    conn: sqlite3.Connection,
    compiler: MemoryCompiler,
) -> None:
    evidence_marker = "EVAL_EVIDENCE_MARKER_71a36b33f6d5"
    insert_memory_item(
        conn,
        memory_id="mem-evidence-marker",
        claim="Compiled memory should not include the raw evidence quote.",
    )
    insert_evidence(
        conn,
        memory_id="mem-evidence-marker",
        quote=f"Raw quote contains {evidence_marker}.",
    )

    context = compiler.compile(MemoryCompilerRequest())

    rendered = json.dumps(asdict(context), sort_keys=True)
    assert evidence_marker not in rendered


def test_compiler_eval_has_no_side_effects(
    conn: sqlite3.Connection,
    compiler: MemoryCompiler,
) -> None:
    memory = MemoryManager(conn, now=lambda: "2026-07-04T12:00:00+00:00")
    memory.create_block("fact", "Existing block", "Compiler must not mutate blocks.")
    insert_memory_item(
        conn,
        memory_id="mem-readonly",
        claim="Compiler should read this item without side effects.",
        last_used_at="2026-07-03T10:00:00+00:00",
        last_confirmed_at="2026-07-03T11:00:00+00:00",
    )
    insert_evidence(conn, memory_id="mem-readonly")
    counts_before = memory_table_counts(conn)
    timestamps_before = memory_item_timestamps(conn, "mem-readonly")
    total_changes_before = conn.total_changes

    compiler.compile(
        MemoryCompilerRequest(
            conversation_id="conversation-1",
            current_turn_id="turn-1",
            current_user_text="Use deterministic memory.",
            config=MemoryCompilerConfig(max_items=10, max_chars=5000),
        )
    )
    total_changes_after = conn.total_changes

    assert total_changes_after == total_changes_before
    assert memory_table_counts(conn) == counts_before
    assert memory_item_timestamps(conn, "mem-readonly") == timestamps_before


def projected_memory_id(memory_id: str) -> str:
    digest = hashlib.sha256(memory_id.encode("utf-8")).hexdigest()
    return f"mem_ref_{digest}"


def assert_no_status_words(text: str, status_words: tuple[str, ...]) -> None:
    normalized = text.casefold()
    assert all(status_word not in normalized for status_word in status_words)


def insert_memory_item(
    conn: sqlite3.Connection,
    *,
    memory_id: str,
    canonical_key: str | None = None,
    kind: str = "semantic",
    scope: str = "project",
    namespace: str = "project/default",
    title: str | None = None,
    claim: str | None = None,
    content: str | None = None,
    status: str = "active",
    confidence: str = "high",
    sensitivity: str = "low",
    source_policy: str | None = "candidate_evidence",
    created_at: str = "2026-07-04T11:00:00+00:00",
    updated_at: str = "2026-07-04T12:00:00+00:00",
    last_used_at: str | None = None,
    last_confirmed_at: str | None = None,
    supersedes: str | None = None,
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
            created_at,
            updated_at,
            last_used_at,
            last_confirmed_at,
            supersedes,
            superseded_by,
        ),
    )
    conn.commit()


def insert_evidence(
    conn: sqlite3.Connection,
    *,
    memory_id: str,
    quote: str = "Evidence quote should never compile.",
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
            f"observation-{memory_id}",
            "conversation-1",
            "turn-1",
            1,
            quote,
            1.0,
            "2026-07-04T12:01:00+00:00",
        ),
    )
    conn.commit()


def table_counts(
    conn: sqlite3.Connection,
    tables: Any,
) -> dict[str, int]:
    return {
        table: table_count(conn, table)
        for table in tables
        if table_exists(conn, table)
    }


def memory_table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table' AND name LIKE 'memory_%'
        ORDER BY name
        """
    ).fetchall()
    return table_counts(conn, [str(row[0]) for row in rows])


def table_count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def memory_item_timestamps(
    conn: sqlite3.Connection,
    memory_id: str,
) -> tuple[str, str | None, str | None]:
    row = conn.execute(
        """
        SELECT updated_at, last_used_at, last_confirmed_at
        FROM memory_items
        WHERE id = ?
        """,
        (memory_id,),
    ).fetchone()
    assert row is not None
    return tuple(row)
