"""Read-only MemoryCompiler preview API tests."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from jarvis.daemon.app import DaemonApp, create_daemon_app
from tests.test_api_smoke import (
    request_json,
    request_raw,
    running_server,
    table_count,
    write_config,
)
from tests.test_memory_compiler import (
    insert_evidence,
    insert_memory_item,
    projected_memory_id,
)


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    return write_config(tmp_path / "jarvis.toml", tmp_path / "home" / "jarvis.db")


@pytest.fixture
def app(config_path: Path) -> Iterator[DaemonApp]:
    daemon_app = create_daemon_app(config_path)
    try:
        yield daemon_app
    finally:
        daemon_app.close()


def test_post_memory_compile_preview_returns_compiled_memory_context(
    app: DaemonApp,
) -> None:
    app.start()
    assert app.conn is not None
    insert_memory_item(
        app.conn,
        memory_id="mem-preview",
        canonical_key="semantic:project/default:preview",
        namespace="project/default",
        title="Preview title",
        claim="Preview claim",
    )
    insert_evidence(app.conn, memory_id="mem-preview")

    with running_server(app) as base_url:
        status, payload = request_json(
            "POST",
            f"{base_url}/memory/compile-preview",
            {
                "conversation_id": "conversation-preview",
                "current_turn_id": "turn-preview",
                "current_user_text": "What memory would be used?",
                "max_items": 2,
                "max_chars": 5000,
            },
        )

    assert status == 200
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
    assert payload["selected_items"] == [
        {
            "memory_id": projected_memory_id("mem-preview"),
            "canonical_key": "semantic:project/default:preview",
            "kind": "semantic",
            "scope": "project",
            "namespace": "project/default",
            "title": "Preview title",
            "claim": "Preview claim",
            "reason_selected": "eligible",
            "evidence_count": 1,
            "source_policy": "candidate_evidence",
            "sensitivity": "low",
            "budget_cost": len("Preview title") + len("Preview claim"),
        }
    ]
    assert payload["skipped_items"] == []
    assert payload["budget_used"] == len("Preview title") + len("Preview claim")
    assert payload["budget_limit"] == 5000
    assert payload["selection_reasons"] == {
        projected_memory_id("mem-preview"): "eligible"
    }
    assert payload["skipped_reasons"] == {}
    assert payload["audit_metadata"]["policy"] == "memory_compiler_v1"
    assert payload["warnings"] == []


@pytest.mark.parametrize(
    "payload",
    [
        {"max_items": 0},
        {"max_chars": 0},
        {"include_procedural": "yes"},
        {"namespace_filter": {"namespace": "project/default"}},
        {"namespace_filter": ["project/default"]},
        {"conversation_id": 123},
    ],
)
def test_memory_compile_preview_invalid_payload_returns_json_400(
    app: DaemonApp,
    payload: object,
) -> None:
    app.start()

    with running_server(app) as base_url:
        status, content_type, body = request_raw(
            "POST",
            f"{base_url}/memory/compile-preview",
            payload,
        )

    assert status == 400
    assert "application/json" in content_type
    response = json.loads(body)
    assert response["status"] == 400
    assert "error" in response
    assert "<html" not in body.lower()
    assert "<!doctype" not in body.lower()


def test_memory_compile_preview_does_not_expose_raw_evidence_quote(
    app: DaemonApp,
) -> None:
    app.start()
    assert app.conn is not None
    marker = "RAW_PREVIEW_EVIDENCE_QUOTE_MARKER"
    insert_memory_item(app.conn, memory_id="mem-evidence-preview")
    insert_evidence(
        app.conn,
        memory_id="mem-evidence-preview",
        quote=f"Evidence quote includes {marker}",
    )

    with running_server(app) as base_url:
        status, payload = request_json(
            "POST",
            f"{base_url}/memory/compile-preview",
            {"max_items": 3, "max_chars": 5000},
        )

    assert status == 200
    rendered = json.dumps(payload, sort_keys=True)
    assert marker not in rendered


def test_memory_compile_preview_does_not_expose_raw_secrets(
    app: DaemonApp,
) -> None:
    app.start()
    assert app.conn is not None
    fake_secret = "sk-previewsecret1234567890abcdef"
    raw_scope = f"project/{fake_secret}"
    raw_namespace = f"project/{fake_secret}/memory"
    insert_memory_item(
        app.conn,
        memory_id=f"mem-{fake_secret}",
        canonical_key=f"semantic:project:{fake_secret}",
        kind=f"semantic {fake_secret}",
        scope=raw_scope,
        namespace=raw_namespace,
        title=f"Title {fake_secret}",
        claim=f"Claim {fake_secret}",
        content=f"Content {fake_secret}",
        source_policy=f"policy {fake_secret}",
        sensitivity=f"sensitivity {fake_secret}",
    )
    insert_evidence(app.conn, memory_id=f"mem-{fake_secret}")

    with running_server(app) as base_url:
        status, payload = request_json(
            "POST",
            f"{base_url}/memory/compile-preview",
            {
                "conversation_id": f"conversation-{fake_secret}",
                "current_turn_id": f"turn-{fake_secret}",
                "current_user_text": f"Preview text {fake_secret}",
                "scope_filter": raw_scope,
                "namespace_filter": raw_namespace,
                "max_items": 3,
                "max_chars": 5000,
            },
        )

    assert status == 200
    rendered = json.dumps(payload, sort_keys=True)
    assert fake_secret not in rendered
    assert payload["selected_items"][0]["kind"] != f"semantic {fake_secret}"
    assert fake_secret not in payload["selected_items"][0]["kind"]


def test_memory_compile_preview_forgotten_skipped_output_omits_content(
    app: DaemonApp,
) -> None:
    app.start()
    assert app.conn is not None
    insert_memory_item(
        app.conn,
        memory_id="mem-forgotten-preview",
        status="forgotten",
        title="Forgotten preview title must not surface",
        claim="Forgotten preview claim must not surface",
        content="Forgotten preview content must not surface",
    )
    insert_evidence(app.conn, memory_id="mem-forgotten-preview")

    with running_server(app) as base_url:
        status, payload = request_json(
            "POST",
            f"{base_url}/memory/compile-preview",
            {"max_items": 3, "max_chars": 5000},
        )

    assert status == 200
    assert payload["selected_items"] == []
    assert payload["skipped_items"] == [
        {
            "memory_id": projected_memory_id("mem-forgotten-preview"),
            "reason_skipped": "forgotten",
        }
    ]
    rendered = json.dumps(payload, sort_keys=True)
    assert "Forgotten preview title must not surface" not in rendered
    assert "Forgotten preview claim must not surface" not in rendered
    assert "Forgotten preview content must not surface" not in rendered
    assert "title" not in payload["skipped_items"][0]
    assert "claim" not in payload["skipped_items"][0]
    assert "content" not in payload["skipped_items"][0]


def test_memory_compile_preview_namespace_filter_ranks_exact_before_global_fallback(
    app: DaemonApp,
) -> None:
    app.start()
    assert app.conn is not None
    insert_memory_item(
        app.conn,
        memory_id="mem-global-preview",
        namespace="global/default",
        updated_at="2026-07-04T13:00:00+00:00",
    )
    insert_memory_item(
        app.conn,
        memory_id="mem-project-preview",
        namespace="project/default",
        updated_at="2026-07-04T12:00:00+00:00",
    )
    insert_memory_item(
        app.conn,
        memory_id="mem-other-preview",
        namespace="project/other",
    )
    for memory_id in (
        "mem-global-preview",
        "mem-project-preview",
        "mem-other-preview",
    ):
        insert_evidence(app.conn, memory_id=memory_id)

    with running_server(app) as base_url:
        status, payload = request_json(
            "POST",
            f"{base_url}/memory/compile-preview",
            {
                "namespace_filter": "project/default",
                "max_items": 10,
                "max_chars": 5000,
            },
        )

    assert status == 200
    assert [item["memory_id"] for item in payload["selected_items"]] == [
        projected_memory_id("mem-project-preview"),
        projected_memory_id("mem-global-preview"),
    ]
    assert payload["skipped_reasons"] == {
        projected_memory_id("mem-other-preview"): "namespace_mismatch"
    }


def test_memory_compile_preview_procedural_memory_is_opt_in(
    app: DaemonApp,
) -> None:
    app.start()
    assert app.conn is not None
    insert_memory_item(app.conn, memory_id="mem-procedural-preview", kind="procedural")
    insert_evidence(app.conn, memory_id="mem-procedural-preview")

    with running_server(app) as base_url:
        default_status, default_payload = request_json(
            "POST",
            f"{base_url}/memory/compile-preview",
            {"max_items": 10, "max_chars": 5000},
        )
        opt_in_status, opt_in_payload = request_json(
            "POST",
            f"{base_url}/memory/compile-preview",
            {"include_procedural": True, "max_items": 10, "max_chars": 5000},
        )

    assert default_status == 200
    assert opt_in_status == 200
    assert default_payload["selected_items"] == []
    assert default_payload["skipped_reasons"] == {
        projected_memory_id("mem-procedural-preview"): "procedural_not_requested"
    }
    assert [item["memory_id"] for item in opt_in_payload["selected_items"]] == [
        projected_memory_id("mem-procedural-preview")
    ]


def test_memory_compile_preview_endpoint_is_read_only(app: DaemonApp) -> None:
    app.start()
    assert app.conn is not None
    insert_memory_item(
        app.conn,
        memory_id="mem-readonly-preview",
        last_used_at="2026-07-03T10:00:00+00:00",
        last_confirmed_at="2026-07-03T11:00:00+00:00",
    )
    insert_evidence(app.conn, memory_id="mem-readonly-preview")
    before_counts = {
        "events": table_count(app, "events"),
        "memory_blocks": table_count(app, "memory_blocks"),
        "memory_items": table_count(app, "memory_items"),
        "memory_evidence": table_count(app, "memory_evidence"),
        "memory_usage_events": table_count(app, "memory_usage_events"),
    }
    before_timestamps = app.conn.execute(
        """
        SELECT last_used_at, last_confirmed_at, updated_at
        FROM memory_items
        WHERE id = ?
        """,
        ("mem-readonly-preview",),
    ).fetchone()

    with running_server(app) as base_url:
        status, payload = request_json(
            "POST",
            f"{base_url}/memory/compile-preview",
            {
                "conversation_id": "conversation-readonly",
                "current_turn_id": "turn-readonly",
                "current_user_text": "Preview only.",
                "max_items": 3,
                "max_chars": 5000,
            },
        )

    after_timestamps = app.conn.execute(
        """
        SELECT last_used_at, last_confirmed_at, updated_at
        FROM memory_items
        WHERE id = ?
        """,
        ("mem-readonly-preview",),
    ).fetchone()
    assert status == 200
    assert payload["selected_items"]
    assert {
        "events": table_count(app, "events"),
        "memory_blocks": table_count(app, "memory_blocks"),
        "memory_items": table_count(app, "memory_items"),
        "memory_evidence": table_count(app, "memory_evidence"),
        "memory_usage_events": table_count(app, "memory_usage_events"),
    } == before_counts
    assert tuple(after_timestamps) == tuple(before_timestamps)
