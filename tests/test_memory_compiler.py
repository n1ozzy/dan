"""Deterministic read-only MemoryCompiler contract tests."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.brain.context_builder import ContextBuilder
from jarvis.memory.compiler import (
    MemoryCompiler,
    MemoryCompilerConfig,
    MemoryCompilerRequest,
)
from jarvis.memory.items import MemoryItemRepository
from jarvis.memory.manager import MemoryManager
from jarvis.security.redaction import REDACTION_PLACEHOLDER
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


def projected_memory_id(memory_id: str) -> str:
    digest = hashlib.sha256(memory_id.encode("utf-8")).hexdigest()
    return f"mem_ref_{digest}"


def test_selects_active_semantic_memory_item_with_linked_evidence(
    conn: sqlite3.Connection,
    compiler: MemoryCompiler,
) -> None:
    insert_memory_item(conn, memory_id="mem-active", claim="Use SQLite as memory.")
    insert_evidence(conn, memory_id="mem-active")

    context = compiler.compile(MemoryCompilerRequest())

    assert [item.memory_id for item in context.selected_items] == [
        projected_memory_id("mem-active")
    ]
    selected = context.selected_items[0]
    assert selected.canonical_key == "key-mem-active"
    assert selected.kind == "semantic"
    assert selected.scope == "project"
    assert selected.namespace == "project/jarvis"
    assert selected.title == "Title mem-active"
    assert selected.claim == "Use SQLite as memory."
    assert selected.reason_selected == "eligible"
    assert selected.evidence_count == 1
    assert selected.source_policy == "candidate_evidence"
    assert selected.sensitivity == "low"
    assert selected.budget_cost == len(selected.title or "") + len(selected.claim)
    assert context.budget_used == selected.budget_cost
    assert context.budget_limit == 1200
    assert context.selection_reasons == {
        projected_memory_id("mem-active"): "eligible"
    }
    assert context.skipped_reasons == {}


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        ("candidate", "candidate_only"),
        ("needs_review", "candidate_only"),
        ("approved", "candidate_only"),
        ("approved-but-not-activated", "candidate_only"),
        ("rejected", "rejected"),
        ("disabled", "disabled"),
        ("superseded", "superseded"),
        ("forgotten", "forgotten"),
        ("conflict", "conflict"),
        ("merge_candidate", "conflict"),
    ],
)
def test_skips_non_active_statuses(
    conn: sqlite3.Connection,
    compiler: MemoryCompiler,
    status: str,
    reason: str,
) -> None:
    insert_memory_item(conn, memory_id=f"mem-{status}", status=status)
    insert_evidence(conn, memory_id=f"mem-{status}")

    context = compiler.compile(MemoryCompilerRequest())

    assert context.selected_items == []
    assert [asdict(item) for item in context.skipped_items] == [
        {"memory_id": projected_memory_id(f"mem-{status}"), "reason_skipped": reason}
    ]
    assert context.skipped_reasons == {projected_memory_id(f"mem-{status}"): reason}


def test_skips_active_item_with_superseded_by_as_superseded(
    conn: sqlite3.Connection,
    compiler: MemoryCompiler,
) -> None:
    insert_memory_item(
        conn,
        memory_id="mem-old",
        status="active",
        superseded_by="mem-new",
    )
    insert_evidence(conn, memory_id="mem-old")

    context = compiler.compile(MemoryCompilerRequest())

    assert context.selected_items == []
    assert context.skipped_items[0].memory_id == projected_memory_id("mem-old")
    assert context.skipped_items[0].reason_skipped == "superseded"


def test_skips_active_item_without_evidence_as_missing_provenance(
    conn: sqlite3.Connection,
    compiler: MemoryCompiler,
) -> None:
    insert_memory_item(conn, memory_id="mem-no-evidence")

    context = compiler.compile(MemoryCompilerRequest())

    assert context.selected_items == []
    assert context.skipped_reasons == {
        projected_memory_id("mem-no-evidence"): "missing_provenance"
    }


def test_source_policy_cannot_waive_missing_evidence(
    conn: sqlite3.Connection,
    compiler: MemoryCompiler,
) -> None:
    insert_memory_item(
        conn,
        memory_id="mem-manual",
        source_policy="trusted_manual_import",
    )

    context = compiler.compile(MemoryCompilerRequest())

    assert context.selected_items == []
    assert context.skipped_reasons == {
        projected_memory_id("mem-manual"): "missing_provenance"
    }


def test_skips_procedural_memory_by_default(
    conn: sqlite3.Connection,
    compiler: MemoryCompiler,
) -> None:
    insert_memory_item(conn, memory_id="mem-procedure", kind="procedural")
    insert_evidence(conn, memory_id="mem-procedure")

    context = compiler.compile(MemoryCompilerRequest())

    assert context.selected_items == []
    assert context.skipped_reasons == {
        projected_memory_id("mem-procedure"): "procedural_not_requested"
    }


def test_include_procedural_allows_procedural_item_when_otherwise_eligible(
    conn: sqlite3.Connection,
    compiler: MemoryCompiler,
) -> None:
    insert_memory_item(conn, memory_id="mem-procedure", kind="procedural")
    insert_evidence(conn, memory_id="mem-procedure")

    context = compiler.compile(
        MemoryCompilerRequest(
            config=MemoryCompilerConfig(include_procedural=True)
        )
    )

    assert [item.memory_id for item in context.selected_items] == [
        projected_memory_id("mem-procedure")
    ]


def test_forgotten_skipped_item_output_does_not_include_title_claim_or_content(
    conn: sqlite3.Connection,
    compiler: MemoryCompiler,
) -> None:
    insert_memory_item(
        conn,
        memory_id="mem-forgotten",
        status="forgotten",
        title="Forgotten title must not surface",
        claim="Forgotten claim must not surface",
        content="Forgotten content must not surface",
    )
    insert_evidence(conn, memory_id="mem-forgotten")

    context = compiler.compile(MemoryCompilerRequest())

    skipped = asdict(context.skipped_items[0])
    assert skipped == {
        "memory_id": projected_memory_id("mem-forgotten"),
        "reason_skipped": "forgotten",
    }
    rendered = json.dumps(asdict(context), sort_keys=True)
    assert "Forgotten title must not surface" not in rendered
    assert "Forgotten claim must not surface" not in rendered
    assert "Forgotten content must not surface" not in rendered


def test_compiler_output_never_includes_raw_evidence_quote(
    conn: sqlite3.Connection,
    compiler: MemoryCompiler,
) -> None:
    raw_quote = "RAW_EVIDENCE_QUOTE_SHOULD_NOT_APPEAR"
    insert_memory_item(conn, memory_id="mem-evidence")
    insert_evidence(conn, memory_id="mem-evidence", quote=raw_quote)

    context = compiler.compile(MemoryCompilerRequest())

    rendered = json.dumps(asdict(context), sort_keys=True)
    assert "mem-evidence" in rendered
    assert raw_quote not in rendered


def test_item_containing_fake_secret_does_not_leak_raw_secret_in_selected_output(
    conn: sqlite3.Connection,
    compiler: MemoryCompiler,
) -> None:
    fake_secret = "sk-testcompilersecret1234567890"
    raw_kind = f"semantic {fake_secret}"
    insert_memory_item(
        conn,
        memory_id="mem-secret",
        canonical_key=f"semantic:project:project/jarvis:token {fake_secret}",
        kind=raw_kind,
        title=f"Token {fake_secret}",
        claim=f"Use token {fake_secret} never.",
        content=f"Body also has {fake_secret}.",
    )
    insert_evidence(conn, memory_id="mem-secret")

    context = compiler.compile(MemoryCompilerRequest())

    rendered = json.dumps(asdict(context), sort_keys=True)
    stored_kind = conn.execute(
        "SELECT kind FROM memory_items WHERE id = ?",
        ("mem-secret",),
    ).fetchone()[0]
    assert fake_secret not in rendered
    assert REDACTION_PLACEHOLDER in rendered
    assert context.selected_items[0].canonical_key == (
        f"semantic:project:project/jarvis:token {REDACTION_PLACEHOLDER}"
    )
    assert context.selected_items[0].kind == f"semantic {REDACTION_PLACEHOLDER}"
    assert fake_secret not in context.selected_items[0].kind
    assert context.selected_items[0].title == f"Token {REDACTION_PLACEHOLDER}"
    assert (
        context.selected_items[0].claim
        == f"Use token {REDACTION_PLACEHOLDER} never."
    )
    assert stored_kind == raw_kind


def test_selected_canonical_key_is_redacted_without_mutating_stored_value(
    conn: sqlite3.Connection,
    compiler: MemoryCompiler,
) -> None:
    fake_secret = "sk-canonicalkeyleak1234567890"
    raw_canonical_key = (
        "semantic:project:project/jarvis:"
        f"title {fake_secret}:claim {fake_secret}"
    )
    raw_quote = f"Evidence quote contains {fake_secret} and must stay private."
    insert_memory_item(
        conn,
        memory_id="mem-canonical-secret",
        canonical_key=raw_canonical_key,
        title=f"Title {fake_secret}",
        claim=f"Claim {fake_secret}",
        content=f"Content {fake_secret}",
    )
    insert_evidence(
        conn,
        memory_id="mem-canonical-secret",
        quote=raw_quote,
    )

    context = compiler.compile(MemoryCompilerRequest())

    selected = context.selected_items[0]
    rendered = json.dumps(asdict(context), sort_keys=True)
    stored_canonical_key = conn.execute(
        "SELECT canonical_key FROM memory_items WHERE id = ?",
        ("mem-canonical-secret",),
    ).fetchone()[0]
    assert fake_secret not in rendered
    assert REDACTION_PLACEHOLDER in rendered
    assert raw_quote not in rendered
    assert REDACTION_PLACEHOLDER in selected.canonical_key
    assert fake_secret not in selected.canonical_key
    assert selected.title == f"Title {REDACTION_PLACEHOLDER}"
    assert selected.claim == f"Claim {REDACTION_PLACEHOLDER}"
    assert stored_canonical_key == raw_canonical_key


def test_selected_scope_and_namespace_are_redacted_without_mutating_stored_values(
    conn: sqlite3.Connection,
    compiler: MemoryCompiler,
) -> None:
    fake_secret = "sk-scopenamespaceleak1234567890"
    raw_scope = f"project/{fake_secret}"
    raw_namespace = f"project/{fake_secret}/memory"
    insert_memory_item(
        conn,
        memory_id="mem-scope-namespace-secret",
        scope=raw_scope,
        namespace=raw_namespace,
    )
    insert_evidence(conn, memory_id="mem-scope-namespace-secret")

    context = compiler.compile(MemoryCompilerRequest())

    selected = context.selected_items[0]
    rendered = json.dumps(asdict(context), sort_keys=True)
    stored_row = conn.execute(
        "SELECT scope, namespace FROM memory_items WHERE id = ?",
        ("mem-scope-namespace-secret",),
    ).fetchone()
    assert fake_secret not in rendered
    assert REDACTION_PLACEHOLDER in rendered
    assert fake_secret not in selected.scope
    assert fake_secret not in selected.namespace
    assert REDACTION_PLACEHOLDER in selected.scope
    assert REDACTION_PLACEHOLDER in selected.namespace
    assert tuple(stored_row) == (raw_scope, raw_namespace)


def test_selected_metadata_fields_are_redacted_without_mutating_stored_values(
    conn: sqlite3.Connection,
    compiler: MemoryCompiler,
) -> None:
    source_policy_secret = "sk-sourcepolicyleak1234567890"
    sensitivity_secret = "sk-sensitivityleak1234567890"
    raw_source_policy = f"manual import {source_policy_secret}"
    raw_sensitivity = f"high {sensitivity_secret}"
    insert_memory_item(
        conn,
        memory_id="mem-source-policy-secret",
        source_policy=raw_source_policy,
        updated_at="2026-07-04T12:01:00+00:00",
    )
    insert_memory_item(
        conn,
        memory_id="mem-sensitivity-secret",
        sensitivity=raw_sensitivity,
        updated_at="2026-07-04T12:00:00+00:00",
    )
    insert_evidence(conn, memory_id="mem-source-policy-secret")
    insert_evidence(conn, memory_id="mem-sensitivity-secret")
    stored_before = {
        row[0]: (row[1], row[2])
        for row in conn.execute(
            """
            SELECT id, source_policy, sensitivity
            FROM memory_items
            WHERE id IN (?, ?)
            ORDER BY id
            """,
            ("mem-source-policy-secret", "mem-sensitivity-secret"),
        ).fetchall()
    }
    usage_events_before = table_count(conn, "memory_usage_events")

    context = compiler.compile(
        MemoryCompilerRequest(
            config=MemoryCompilerConfig(max_items=10, max_chars=5000)
        )
    )

    rendered = json.dumps(asdict(context), sort_keys=True)
    selected_by_id = {item.memory_id: item for item in context.selected_items}
    stored_after = {
        row[0]: (row[1], row[2])
        for row in conn.execute(
            """
            SELECT id, source_policy, sensitivity
            FROM memory_items
            WHERE id IN (?, ?)
            ORDER BY id
            """,
            ("mem-source-policy-secret", "mem-sensitivity-secret"),
        ).fetchall()
    }
    source_policy_selected = selected_by_id[
        projected_memory_id("mem-source-policy-secret")
    ]
    sensitivity_selected = selected_by_id[
        projected_memory_id("mem-sensitivity-secret")
    ]
    assert source_policy_secret not in rendered
    assert sensitivity_secret not in rendered
    assert REDACTION_PLACEHOLDER in rendered
    assert source_policy_selected.source_policy == (
        f"manual import {REDACTION_PLACEHOLDER}"
    )
    assert source_policy_secret not in (source_policy_selected.source_policy or "")
    assert sensitivity_selected.sensitivity == f"high {REDACTION_PLACEHOLDER}"
    assert sensitivity_secret not in sensitivity_selected.sensitivity
    assert stored_before == {
        "mem-source-policy-secret": (raw_source_policy, "low"),
        "mem-sensitivity-secret": ("candidate_evidence", raw_sensitivity),
    }
    assert stored_after == stored_before
    assert table_count(conn, "memory_usage_events") == usage_events_before


def test_audit_metadata_redacts_filters_and_caller_text_without_mutating_sources(
    conn: sqlite3.Connection,
    compiler: MemoryCompiler,
) -> None:
    scope_secret = "sk-auditfilterscope1234567890"
    namespace_secret = "sk-auditfilternamespace1234567890"
    caller_secret = "sk-auditcallersecret1234567890"
    item_secret = "sk-auditselecteditem1234567890"
    raw_scope = f"project/{scope_secret}"
    raw_namespace = f"project/{namespace_secret}/memory"
    raw_canonical_key = f"semantic:{raw_scope}:{raw_namespace}:token {item_secret}"
    raw_title = f"Title {item_secret}"
    raw_claim = f"Claim {item_secret}"
    raw_content = f"Content {item_secret}"
    config = MemoryCompilerConfig(
        max_items=3,
        max_chars=5000,
        scope_filter=raw_scope,
        namespace_filter=raw_namespace,
    )
    request = MemoryCompilerRequest(
        conversation_id=f"conversation-{caller_secret}",
        current_turn_id=f"turn-{caller_secret}",
        current_user_text=f"User text {caller_secret}",
        config=config,
    )
    insert_memory_item(
        conn,
        memory_id="mem-audit-secret",
        canonical_key=raw_canonical_key,
        scope=raw_scope,
        namespace=raw_namespace,
        title=raw_title,
        claim=raw_claim,
        content=raw_content,
    )
    insert_evidence(conn, memory_id="mem-audit-secret")
    stored_before = conn.execute(
        """
        SELECT canonical_key, scope, namespace, title, claim, content
        FROM memory_items
        WHERE id = ?
        """,
        ("mem-audit-secret",),
    ).fetchone()
    usage_events_before = table_count(conn, "memory_usage_events")

    context = compiler.compile(request)

    rendered = json.dumps(asdict(context), sort_keys=True)
    selected = context.selected_items[0]
    stored_after = conn.execute(
        """
        SELECT canonical_key, scope, namespace, title, claim, content
        FROM memory_items
        WHERE id = ?
        """,
        ("mem-audit-secret",),
    ).fetchone()
    assert scope_secret not in rendered
    assert namespace_secret not in rendered
    assert caller_secret not in rendered
    assert item_secret not in rendered
    assert REDACTION_PLACEHOLDER in rendered
    assert context.audit_metadata["scope_filter"] == f"project/{REDACTION_PLACEHOLDER}"
    assert context.audit_metadata["namespace_filter"] == (
        f"project/{REDACTION_PLACEHOLDER}/memory"
    )
    assert context.audit_metadata["conversation_id"] == (
        f"conversation-{REDACTION_PLACEHOLDER}"
    )
    assert context.audit_metadata["current_turn_id"] == f"turn-{REDACTION_PLACEHOLDER}"
    assert "current_user_text" not in context.audit_metadata
    assert selected.canonical_key == (
        f"semantic:project/{REDACTION_PLACEHOLDER}:"
        f"project/{REDACTION_PLACEHOLDER}/memory:token {REDACTION_PLACEHOLDER}"
    )
    assert selected.scope == f"project/{REDACTION_PLACEHOLDER}"
    assert selected.namespace == f"project/{REDACTION_PLACEHOLDER}/memory"
    assert selected.title == f"Title {REDACTION_PLACEHOLDER}"
    assert selected.claim == f"Claim {REDACTION_PLACEHOLDER}"
    assert config.scope_filter == raw_scope
    assert config.namespace_filter == raw_namespace
    assert request.current_user_text == f"User text {caller_secret}"
    assert tuple(stored_after) == tuple(stored_before)
    assert table_count(conn, "memory_usage_events") == usage_events_before


def test_secret_bearing_selected_memory_ids_project_to_unique_reason_keys(
    conn: sqlite3.Connection,
    compiler: MemoryCompiler,
) -> None:
    alpha_secret = "sk-secretalpha1234567890abcdef"
    beta_secret = "sk-secretbeta1234567890abcdef"
    raw_memory_ids = [
        f"mem-{alpha_secret}",
        f"mem-{beta_secret}",
    ]
    for raw_memory_id in raw_memory_ids:
        insert_memory_item(conn, memory_id=raw_memory_id)
        insert_evidence(conn, memory_id=raw_memory_id)

    context = compiler.compile(
        MemoryCompilerRequest(
            config=MemoryCompilerConfig(max_items=2, max_chars=5000)
        )
    )

    rendered = json.dumps(asdict(context), sort_keys=True)
    selected_memory_ids = [item.memory_id for item in context.selected_items]
    stored_memory_ids = [
        row[0]
        for row in conn.execute(
            "SELECT id FROM memory_items ORDER BY id"
        ).fetchall()
    ]
    assert len(context.selected_items) == 2
    assert len(context.selection_reasons) == 2
    assert len(set(selected_memory_ids)) == 2
    assert set(context.selection_reasons) == set(selected_memory_ids)
    assert set(selected_memory_ids) == {
        projected_memory_id(raw_memory_id) for raw_memory_id in raw_memory_ids
    }
    assert all(memory_id.startswith("mem_ref_") for memory_id in selected_memory_ids)
    assert all(secret not in rendered for secret in (alpha_secret, beta_secret))
    assert all(raw_memory_id not in rendered for raw_memory_id in raw_memory_ids)
    assert stored_memory_ids == sorted(raw_memory_ids)


def test_secret_bearing_skipped_memory_ids_project_to_unique_reason_keys(
    conn: sqlite3.Connection,
    compiler: MemoryCompiler,
) -> None:
    alpha_secret = "sk-skippedalpha1234567890abcdef"
    beta_secret = "sk-skippedbeta1234567890abcdef"
    raw_memory_ids = [
        f"mem-{alpha_secret}",
        f"mem-{beta_secret}",
    ]
    for raw_memory_id in raw_memory_ids:
        insert_memory_item(conn, memory_id=raw_memory_id, status="disabled")
        insert_evidence(conn, memory_id=raw_memory_id)

    context = compiler.compile(MemoryCompilerRequest())

    rendered = json.dumps(asdict(context), sort_keys=True)
    skipped_memory_ids = [item.memory_id for item in context.skipped_items]
    stored_memory_ids = [
        row[0]
        for row in conn.execute(
            "SELECT id FROM memory_items ORDER BY id"
        ).fetchall()
    ]
    assert len(context.skipped_items) == 2
    assert len(context.skipped_reasons) == 2
    assert len(set(skipped_memory_ids)) == 2
    assert set(context.skipped_reasons) == set(skipped_memory_ids)
    assert set(skipped_memory_ids) == {
        projected_memory_id(raw_memory_id) for raw_memory_id in raw_memory_ids
    }
    assert all(memory_id.startswith("mem_ref_") for memory_id in skipped_memory_ids)
    assert all(secret not in rendered for secret in (alpha_secret, beta_secret))
    assert all(raw_memory_id not in rendered for raw_memory_id in raw_memory_ids)
    assert stored_memory_ids == sorted(raw_memory_ids)


def test_projected_memory_ids_are_deterministic_across_compiler_calls(
    conn: sqlite3.Connection,
    compiler: MemoryCompiler,
) -> None:
    raw_memory_id = "mem-sk-deterministic1234567890abcdef"
    insert_memory_item(conn, memory_id=raw_memory_id)
    insert_evidence(conn, memory_id=raw_memory_id)

    first_context = compiler.compile(MemoryCompilerRequest())
    second_context = compiler.compile(MemoryCompilerRequest())

    assert [item.memory_id for item in first_context.selected_items] == [
        item.memory_id for item in second_context.selected_items
    ]
    assert first_context.selection_reasons == second_context.selection_reasons
    assert first_context.selected_items[0].memory_id == projected_memory_id(
        raw_memory_id
    )


def test_projected_memory_id_uses_full_digest_format_without_raw_secret(
    conn: sqlite3.Connection,
    compiler: MemoryCompiler,
) -> None:
    fake_secret = "sk-fulldigestformat1234567890abcdef"
    raw_memory_id = f"mem-{fake_secret}"
    insert_memory_item(conn, memory_id=raw_memory_id)
    insert_evidence(conn, memory_id=raw_memory_id)

    context = compiler.compile(MemoryCompilerRequest())

    projected_id = context.selected_items[0].memory_id
    digest = projected_id.removeprefix("mem_ref_")
    assert projected_id.startswith("mem_ref_")
    assert len(digest) == 64
    assert digest == digest.lower()
    assert int(digest, 16) >= 0
    assert fake_secret not in projected_id
    assert raw_memory_id not in projected_id


def test_selected_memory_id_and_reason_key_are_redacted_without_mutating_stored_id(
    conn: sqlite3.Connection,
    compiler: MemoryCompiler,
) -> None:
    fake_secret = "sk-selectedmemoryidsecret1234567890"
    raw_memory_id = f"mem-{fake_secret}"
    raw_kind = f"semantic {fake_secret}"
    raw_scope = f"project/{fake_secret}"
    raw_namespace = f"project/{fake_secret}/memory"
    raw_source_policy = f"manual import {fake_secret}"
    raw_sensitivity = f"high {fake_secret}"
    request = MemoryCompilerRequest(
        conversation_id=f"conversation-{fake_secret}",
        current_turn_id=f"turn-{fake_secret}",
        current_user_text=f"User text {fake_secret}",
        config=MemoryCompilerConfig(
            max_items=3,
            max_chars=5000,
            scope_filter=raw_scope,
            namespace_filter=raw_namespace,
        ),
    )
    insert_memory_item(
        conn,
        memory_id=raw_memory_id,
        canonical_key=f"key-{fake_secret}",
        kind=raw_kind,
        scope=raw_scope,
        namespace=raw_namespace,
        title=f"Title {fake_secret}",
        claim=f"Claim {fake_secret}",
        content=f"Content {fake_secret}",
        source_policy=raw_source_policy,
        sensitivity=raw_sensitivity,
    )
    insert_evidence(conn, memory_id=raw_memory_id)
    usage_events_before = table_count(conn, "memory_usage_events")

    context = compiler.compile(request)

    rendered = json.dumps(asdict(context), sort_keys=True)
    selected = context.selected_items[0]
    stored_memory_id, stored_kind = conn.execute(
        "SELECT id, kind FROM memory_items WHERE id = ?",
        (raw_memory_id,),
    ).fetchone()
    assert fake_secret not in rendered
    assert raw_memory_id not in rendered
    assert REDACTION_PLACEHOLDER in rendered
    assert selected.memory_id == projected_memory_id(raw_memory_id)
    assert selected.memory_id in context.selection_reasons
    assert set(context.selection_reasons) == {selected.memory_id}
    assert raw_memory_id not in context.selection_reasons
    assert all(fake_secret not in key for key in context.selection_reasons)
    assert REDACTION_PLACEHOLDER in selected.canonical_key
    assert REDACTION_PLACEHOLDER in selected.kind
    assert fake_secret not in selected.kind
    assert REDACTION_PLACEHOLDER in (selected.title or "")
    assert REDACTION_PLACEHOLDER in selected.claim
    assert REDACTION_PLACEHOLDER in selected.scope
    assert REDACTION_PLACEHOLDER in selected.namespace
    assert REDACTION_PLACEHOLDER in (selected.source_policy or "")
    assert REDACTION_PLACEHOLDER in selected.sensitivity
    assert REDACTION_PLACEHOLDER in context.audit_metadata["conversation_id"]
    assert REDACTION_PLACEHOLDER in context.audit_metadata["current_turn_id"]
    assert REDACTION_PLACEHOLDER in context.audit_metadata["scope_filter"]
    assert REDACTION_PLACEHOLDER in context.audit_metadata["namespace_filter"]
    assert "current_user_text" not in context.audit_metadata
    assert stored_memory_id == raw_memory_id
    assert stored_kind == raw_kind
    assert table_count(conn, "memory_usage_events") == usage_events_before


def test_skipped_memory_id_and_reason_key_are_redacted_without_mutating_stored_id(
    conn: sqlite3.Connection,
    compiler: MemoryCompiler,
) -> None:
    fake_secret = "sk-skippedmemoryidsecret1234567890"
    raw_memory_id = f"mem-{fake_secret}"
    insert_memory_item(
        conn,
        memory_id=raw_memory_id,
        status="disabled",
        title=f"Skipped title {fake_secret}",
        claim=f"Skipped claim {fake_secret}",
        content=f"Skipped content {fake_secret}",
    )
    insert_evidence(conn, memory_id=raw_memory_id)
    usage_events_before = table_count(conn, "memory_usage_events")

    context = compiler.compile(MemoryCompilerRequest())

    rendered = json.dumps(asdict(context), sort_keys=True)
    skipped = context.skipped_items[0]
    payload = asdict(context)
    stored_memory_id = conn.execute(
        "SELECT id FROM memory_items WHERE id = ?",
        (raw_memory_id,),
    ).fetchone()[0]
    assert fake_secret not in rendered
    assert raw_memory_id not in rendered
    assert skipped.memory_id == projected_memory_id(raw_memory_id)
    assert skipped.memory_id in context.skipped_reasons
    assert set(context.skipped_reasons) == {skipped.memory_id}
    assert raw_memory_id not in context.skipped_reasons
    assert all(fake_secret not in key for key in context.skipped_reasons)
    assert payload["skipped_items"][0] == {
        "memory_id": projected_memory_id(raw_memory_id),
        "reason_skipped": "disabled",
    }
    assert "skipped_reasons" in payload
    assert "skipped_reason" not in payload
    assert "reason_skipped" in payload["skipped_items"][0]
    assert "skipped_reason" not in payload["skipped_items"][0]
    assert stored_memory_id == raw_memory_id
    assert table_count(conn, "memory_usage_events") == usage_events_before


def test_budget_max_items_is_enforced_deterministically(
    conn: sqlite3.Connection,
    compiler: MemoryCompiler,
) -> None:
    for memory_id in ("mem-c", "mem-a", "mem-b"):
        insert_memory_item(
            conn,
            memory_id=memory_id,
            updated_at="2026-07-04T12:00:00+00:00",
        )
        insert_evidence(conn, memory_id=memory_id)

    context = compiler.compile(
        MemoryCompilerRequest(
            config=MemoryCompilerConfig(max_items=2, max_chars=5000)
        )
    )

    assert [item.memory_id for item in context.selected_items] == [
        projected_memory_id("mem-a"),
        projected_memory_id("mem-b"),
    ]
    assert context.skipped_reasons == {
        projected_memory_id("mem-c"): "over_budget"
    }


def test_budget_max_chars_skips_over_budget_items(
    conn: sqlite3.Connection,
    compiler: MemoryCompiler,
) -> None:
    insert_memory_item(
        conn,
        memory_id="mem-a",
        title="Long title",
        claim="This claim is too long for the tiny compiler budget.",
    )
    insert_memory_item(conn, memory_id="mem-b", title="T", claim="ok")
    insert_evidence(conn, memory_id="mem-a")
    insert_evidence(conn, memory_id="mem-b")

    context = compiler.compile(
        MemoryCompilerRequest(config=MemoryCompilerConfig(max_items=3, max_chars=3))
    )

    assert [item.memory_id for item in context.selected_items] == [
        projected_memory_id("mem-b")
    ]
    assert context.skipped_reasons == {
        projected_memory_id("mem-a"): "over_budget"
    }
    assert context.budget_used == context.selected_items[0].budget_cost == 3
    assert context.budget_limit == 3


def test_deterministic_ordering_uses_stable_tie_breaker_by_memory_id(
    conn: sqlite3.Connection,
    compiler: MemoryCompiler,
) -> None:
    for memory_id in ("mem-c", "mem-a", "mem-b"):
        insert_memory_item(
            conn,
            memory_id=memory_id,
            confidence="high",
            updated_at="2026-07-04T12:00:00+00:00",
            last_confirmed_at=None,
        )
        insert_evidence(conn, memory_id=memory_id)

    context = compiler.compile(
        MemoryCompilerRequest(
            config=MemoryCompilerConfig(max_items=10, max_chars=5000)
        )
    )

    assert [item.memory_id for item in context.selected_items] == [
        projected_memory_id("mem-a"),
        projected_memory_id("mem-b"),
        projected_memory_id("mem-c"),
    ]


def test_namespace_filter_orders_exact_before_global_and_skips_mismatch(
    conn: sqlite3.Connection,
    compiler: MemoryCompiler,
) -> None:
    insert_memory_item(
        conn,
        memory_id="mem-global",
        namespace="global",
        updated_at="2026-07-04T13:00:00+00:00",
    )
    insert_memory_item(
        conn,
        memory_id="mem-exact",
        namespace="project/jarvis",
        updated_at="2026-07-04T12:00:00+00:00",
    )
    insert_memory_item(conn, memory_id="mem-other", namespace="project/other")
    for memory_id in ("mem-global", "mem-exact", "mem-other"):
        insert_evidence(conn, memory_id=memory_id)

    context = compiler.compile(
        MemoryCompilerRequest(
            config=MemoryCompilerConfig(
                max_items=10,
                max_chars=5000,
                namespace_filter="project/jarvis",
            )
        )
    )

    assert [item.memory_id for item in context.selected_items] == [
        projected_memory_id("mem-exact"),
        projected_memory_id("mem-global"),
    ]
    assert context.skipped_reasons == {
        projected_memory_id("mem-other"): "namespace_mismatch"
    }


def test_namespace_filter_allows_global_slash_fallback_and_ranks_exact_first(
    conn: sqlite3.Connection,
    compiler: MemoryCompiler,
) -> None:
    insert_memory_item(
        conn,
        memory_id="mem-global",
        namespace="global",
        updated_at="2026-07-04T13:00:00+00:00",
    )
    insert_memory_item(
        conn,
        memory_id="mem-global-fact",
        namespace="global/fact",
        updated_at="2026-07-04T12:30:00+00:00",
    )
    insert_memory_item(
        conn,
        memory_id="mem-exact",
        namespace="project/default",
        updated_at="2026-07-04T12:00:00+00:00",
    )
    insert_memory_item(conn, memory_id="mem-default", namespace="default")
    insert_memory_item(conn, memory_id="mem-all", namespace="all")
    insert_memory_item(conn, memory_id="mem-star", namespace="*")
    insert_memory_item(conn, memory_id="mem-other", namespace="other/project")
    for memory_id in (
        "mem-global",
        "mem-global-fact",
        "mem-exact",
        "mem-default",
        "mem-all",
        "mem-star",
        "mem-other",
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

    assert [item.memory_id for item in context.selected_items] == [
        projected_memory_id("mem-exact"),
        projected_memory_id("mem-global"),
        projected_memory_id("mem-global-fact"),
    ]
    assert context.skipped_reasons == {
        projected_memory_id("mem-default"): "namespace_mismatch",
        projected_memory_id("mem-all"): "namespace_mismatch",
        projected_memory_id("mem-star"): "namespace_mismatch",
        projected_memory_id("mem-other"): "namespace_mismatch",
    }


def test_uses_reason_skipped_and_skipped_reasons_without_skipped_reason_field(
    conn: sqlite3.Connection,
    compiler: MemoryCompiler,
) -> None:
    insert_memory_item(conn, memory_id="mem-disabled", status="disabled")
    insert_evidence(conn, memory_id="mem-disabled")

    context = compiler.compile(MemoryCompilerRequest())
    payload = asdict(context)

    assert "skipped_reasons" in payload
    assert "skipped_reason" not in payload
    assert payload["skipped_items"][0] == {
        "memory_id": projected_memory_id("mem-disabled"),
        "reason_skipped": "disabled",
    }
    assert "skipped_reason" not in payload["skipped_items"][0]


def test_compiler_does_not_write_usage_events_or_update_usage_timestamps(
    conn: sqlite3.Connection,
    compiler: MemoryCompiler,
) -> None:
    insert_memory_item(
        conn,
        memory_id="mem-readonly",
        last_used_at="2026-07-03T10:00:00+00:00",
        last_confirmed_at="2026-07-03T11:00:00+00:00",
    )
    insert_evidence(conn, memory_id="mem-readonly")
    usage_events_before = table_count(conn, "memory_usage_events")

    compiler.compile(
        MemoryCompilerRequest(
            conversation_id="conversation-1",
            current_turn_id="turn-1",
            current_user_text="Use memory.",
        )
    )

    row = conn.execute(
        """
        SELECT last_used_at, last_confirmed_at
        FROM memory_items
        WHERE id = ?
        """,
        ("mem-readonly",),
    ).fetchone()
    assert table_count(conn, "memory_usage_events") == usage_events_before
    assert tuple(row) == (
        "2026-07-03T10:00:00+00:00",
        "2026-07-03T11:00:00+00:00",
    )


def test_compiler_does_not_touch_memory_blocks(
    conn: sqlite3.Connection,
    compiler: MemoryCompiler,
) -> None:
    memory = MemoryManager(conn, now=lambda: "2026-07-04T12:00:00+00:00")
    block = memory.create_block("fact", "Existing block", "Keep this block")
    insert_memory_item(conn, memory_id="mem-active")
    insert_evidence(conn, memory_id="mem-active")
    before = memory.get_block(block.id)

    compiler.compile(MemoryCompilerRequest())

    assert table_count(conn, "memory_blocks") == 1
    assert memory.get_block(block.id) == before


def test_context_builder_behavior_remains_unchanged(
    conn: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    insert_conversation(conn)
    insert_memory_item(
        conn,
        memory_id="mem-contextbuilder",
        claim="Memory item claim must not enter ContextBuilder yet.",
    )
    insert_evidence(conn, memory_id="mem-contextbuilder")
    persona_path = tmp_path / "persona.md"
    persona_path.write_text("Persona: context builder test.", encoding="utf-8")
    builder = ContextBuilder(conn, config=context_config(), persona_path=persona_path)

    result = builder.build_request(
        turn_id="turn-new",
        conversation_id="conversation-1",
        input_text="Now",
    )

    rendered_messages = "\n".join(
        message.content for message in result.request.context_messages
    )
    assert result.request.memory_blocks == []
    assert "Memory item claim must not enter ContextBuilder yet." not in rendered_messages
    assert result.context_snapshot["memory_block_count"] == 0


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


def insert_conversation(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        INSERT INTO conversations (id, created_at, updated_at, title, status, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "conversation-1",
            "2026-07-04T10:00:00+00:00",
            "2026-07-04T10:00:00+00:00",
            "Compiler test",
            "active",
            "{}",
        ),
    )
    conn.commit()


def context_config() -> SimpleNamespace:
    return SimpleNamespace(
        brain=SimpleNamespace(
            default_adapter="mock",
            default_model="mock-local",
            context_budget_chars=24000,
            provider_sessions_are_memory=True,
        ),
        memory=SimpleNamespace(
            enabled=True,
            max_active_blocks=50,
            max_context_chars=12000,
        ),
    )


def table_count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
