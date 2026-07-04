"""Read-only MemoryCompiler preview API tests."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import asdict
from pathlib import Path

import pytest

from jarvis.daemon.app import DaemonApp, create_daemon_app
from jarvis.memory.compiler import MemoryCompilerConfig, MemoryCompilerRequest
from jarvis.security.redaction import REDACTION_PLACEHOLDER
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


FIELD_CLASSIFICATION_KEYS = {
    "source",
    "free_text_possible",
    "secret_possible",
    "redaction_required",
    "uniqueness_required",
    "hidden_for_forgotten",
}

FORBIDDEN_400_RESPONSE_KEYS = {
    "debug",
    "detail",
    "traceback",
    "exception",
    "fields",
    "raw",
}

EXPECTED_200_RESPONSE_FIELD_INVENTORY = {
    "selected_items": {
        "source": "computed",
        "free_text_possible": False,
        "secret_possible": False,
        "redaction_required": False,
        "uniqueness_required": False,
        "hidden_for_forgotten": True,
    },
    "selected_items[].memory_id": {
        "source": "computed",
        "free_text_possible": False,
        "secret_possible": True,
        "redaction_required": True,
        "uniqueness_required": True,
        "hidden_for_forgotten": True,
    },
    "selected_items[].canonical_key": {
        "source": "DB",
        "free_text_possible": True,
        "secret_possible": True,
        "redaction_required": True,
        "uniqueness_required": False,
        "hidden_for_forgotten": True,
    },
    "selected_items[].kind": {
        "source": "DB",
        "free_text_possible": True,
        "secret_possible": True,
        "redaction_required": True,
        "uniqueness_required": False,
        "hidden_for_forgotten": True,
    },
    "selected_items[].scope": {
        "source": "DB",
        "free_text_possible": True,
        "secret_possible": True,
        "redaction_required": True,
        "uniqueness_required": False,
        "hidden_for_forgotten": True,
    },
    "selected_items[].namespace": {
        "source": "DB",
        "free_text_possible": True,
        "secret_possible": True,
        "redaction_required": True,
        "uniqueness_required": False,
        "hidden_for_forgotten": True,
    },
    "selected_items[].title": {
        "source": "DB",
        "free_text_possible": True,
        "secret_possible": True,
        "redaction_required": True,
        "uniqueness_required": False,
        "hidden_for_forgotten": True,
    },
    "selected_items[].claim": {
        "source": "DB",
        "free_text_possible": True,
        "secret_possible": True,
        "redaction_required": True,
        "uniqueness_required": False,
        "hidden_for_forgotten": True,
    },
    "selected_items[].reason_selected": {
        "source": "constant",
        "free_text_possible": False,
        "secret_possible": False,
        "redaction_required": False,
        "uniqueness_required": False,
        "hidden_for_forgotten": True,
    },
    "selected_items[].evidence_count": {
        "source": "computed",
        "free_text_possible": False,
        "secret_possible": False,
        "redaction_required": False,
        "uniqueness_required": False,
        "hidden_for_forgotten": True,
    },
    "selected_items[].source_policy": {
        "source": "DB",
        "free_text_possible": True,
        "secret_possible": True,
        "redaction_required": True,
        "uniqueness_required": False,
        "hidden_for_forgotten": True,
    },
    "selected_items[].sensitivity": {
        "source": "DB",
        "free_text_possible": True,
        "secret_possible": True,
        "redaction_required": True,
        "uniqueness_required": False,
        "hidden_for_forgotten": True,
    },
    "selected_items[].budget_cost": {
        "source": "computed",
        "free_text_possible": False,
        "secret_possible": False,
        "redaction_required": False,
        "uniqueness_required": False,
        "hidden_for_forgotten": True,
    },
    "skipped_items": {
        "source": "computed",
        "free_text_possible": False,
        "secret_possible": False,
        "redaction_required": False,
        "uniqueness_required": False,
        "hidden_for_forgotten": False,
    },
    "skipped_items[].memory_id": {
        "source": "computed",
        "free_text_possible": False,
        "secret_possible": True,
        "redaction_required": True,
        "uniqueness_required": True,
        "hidden_for_forgotten": False,
    },
    "skipped_items[].reason_skipped": {
        "source": "computed",
        "free_text_possible": False,
        "secret_possible": False,
        "redaction_required": False,
        "uniqueness_required": False,
        "hidden_for_forgotten": False,
    },
    "selection_reasons": {
        "source": "computed",
        "free_text_possible": False,
        "secret_possible": False,
        "redaction_required": False,
        "uniqueness_required": False,
        "hidden_for_forgotten": True,
    },
    "selection_reasons.<memory_id_key>": {
        "source": "computed",
        "free_text_possible": False,
        "secret_possible": True,
        "redaction_required": True,
        "uniqueness_required": True,
        "hidden_for_forgotten": True,
    },
    "selection_reasons.<reason_value>": {
        "source": "constant",
        "free_text_possible": False,
        "secret_possible": False,
        "redaction_required": False,
        "uniqueness_required": False,
        "hidden_for_forgotten": True,
    },
    "skipped_reasons": {
        "source": "computed",
        "free_text_possible": False,
        "secret_possible": False,
        "redaction_required": False,
        "uniqueness_required": False,
        "hidden_for_forgotten": False,
    },
    "skipped_reasons.<memory_id_key>": {
        "source": "computed",
        "free_text_possible": False,
        "secret_possible": True,
        "redaction_required": True,
        "uniqueness_required": True,
        "hidden_for_forgotten": False,
    },
    "skipped_reasons.<reason_value>": {
        "source": "computed",
        "free_text_possible": False,
        "secret_possible": False,
        "redaction_required": False,
        "uniqueness_required": False,
        "hidden_for_forgotten": False,
    },
    "audit_metadata": {
        "source": "computed",
        "free_text_possible": False,
        "secret_possible": False,
        "redaction_required": False,
        "uniqueness_required": False,
        "hidden_for_forgotten": False,
    },
    "audit_metadata.policy": {
        "source": "constant",
        "free_text_possible": False,
        "secret_possible": False,
        "redaction_required": False,
        "uniqueness_required": False,
        "hidden_for_forgotten": False,
    },
    "audit_metadata.conversation_id": {
        "source": "caller input",
        "free_text_possible": True,
        "secret_possible": True,
        "redaction_required": True,
        "uniqueness_required": False,
        "hidden_for_forgotten": False,
    },
    "audit_metadata.current_turn_id": {
        "source": "caller input",
        "free_text_possible": True,
        "secret_possible": True,
        "redaction_required": True,
        "uniqueness_required": False,
        "hidden_for_forgotten": False,
    },
    "audit_metadata.source_count": {
        "source": "computed",
        "free_text_possible": False,
        "secret_possible": False,
        "redaction_required": False,
        "uniqueness_required": False,
        "hidden_for_forgotten": False,
    },
    "audit_metadata.selected_count": {
        "source": "computed",
        "free_text_possible": False,
        "secret_possible": False,
        "redaction_required": False,
        "uniqueness_required": False,
        "hidden_for_forgotten": False,
    },
    "audit_metadata.skipped_count": {
        "source": "computed",
        "free_text_possible": False,
        "secret_possible": False,
        "redaction_required": False,
        "uniqueness_required": False,
        "hidden_for_forgotten": False,
    },
    "audit_metadata.include_procedural": {
        "source": "caller input",
        "free_text_possible": False,
        "secret_possible": False,
        "redaction_required": False,
        "uniqueness_required": False,
        "hidden_for_forgotten": False,
    },
    "audit_metadata.scope_filter": {
        "source": "caller input",
        "free_text_possible": True,
        "secret_possible": True,
        "redaction_required": True,
        "uniqueness_required": False,
        "hidden_for_forgotten": False,
    },
    "audit_metadata.namespace_filter": {
        "source": "caller input",
        "free_text_possible": True,
        "secret_possible": True,
        "redaction_required": True,
        "uniqueness_required": False,
        "hidden_for_forgotten": False,
    },
    "warnings": {
        "source": "constant",
        "free_text_possible": False,
        "secret_possible": False,
        "redaction_required": False,
        "uniqueness_required": False,
        "hidden_for_forgotten": False,
    },
    "budget_used": {
        "source": "computed",
        "free_text_possible": False,
        "secret_possible": False,
        "redaction_required": False,
        "uniqueness_required": False,
        "hidden_for_forgotten": False,
    },
    "budget_limit": {
        "source": "caller input",
        "free_text_possible": False,
        "secret_possible": False,
        "redaction_required": False,
        "uniqueness_required": False,
        "hidden_for_forgotten": False,
    },
}


EXPECTED_400_RESPONSE_FIELD_INVENTORY = {
    "error": {
        "source": "API error wrapper",
        "free_text_possible": True,
        "secret_possible": False,
        "redaction_required": False,
        "uniqueness_required": False,
        "hidden_for_forgotten": False,
    },
    "status": {
        "source": "API error wrapper",
        "free_text_possible": False,
        "secret_possible": False,
        "redaction_required": False,
        "uniqueness_required": False,
        "hidden_for_forgotten": False,
    },
}


def _assert_response_field_inventory(
    field_paths: set[str],
    expected_inventory: dict[str, dict[str, object]],
) -> None:
    assert field_paths == set(expected_inventory)
    for classification in expected_inventory.values():
        assert set(classification) == FIELD_CLASSIFICATION_KEYS


def _response_field_paths(payload: dict[str, object]) -> set[str]:
    paths = set(payload)
    selected_items = payload["selected_items"]
    skipped_items = payload["skipped_items"]
    audit_metadata = payload["audit_metadata"]
    assert isinstance(selected_items, list)
    assert isinstance(skipped_items, list)
    assert isinstance(audit_metadata, dict)
    assert payload["selection_reasons"]
    assert payload["skipped_reasons"]
    assert selected_items
    assert skipped_items

    paths.update(f"selected_items[].{field}" for item in selected_items for field in item)
    paths.update(f"skipped_items[].{field}" for item in skipped_items for field in item)
    paths.update(
        {
            "selection_reasons.<memory_id_key>",
            "selection_reasons.<reason_value>",
            "skipped_reasons.<memory_id_key>",
            "skipped_reasons.<reason_value>",
        }
    )
    paths.update(f"audit_metadata.{field}" for field in audit_metadata)
    return paths


def _memory_table_counts(app: DaemonApp) -> dict[str, int]:
    assert app.conn is not None
    table_names = [
        row[0]
        for row in app.conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name LIKE 'memory_%'
            ORDER BY name
            """
        ).fetchall()
    ]
    return {
        table_name: int(
            app.conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
        )
        for table_name in table_names
    }


def _total_changes(app: DaemonApp) -> int:
    assert app.conn is not None
    raw_connections = getattr(app.conn, "_all", None)
    if raw_connections is None:
        return int(app.conn.total_changes)
    return sum(int(connection.total_changes) for connection in raw_connections)


def test_response_field_inventory_has_no_unclassified_fields(
    app: DaemonApp,
) -> None:
    app.start()
    assert app.conn is not None
    insert_memory_item(app.conn, memory_id="mem-inventory-selected")
    insert_memory_item(app.conn, memory_id="mem-inventory-skipped", status="disabled")
    insert_evidence(app.conn, memory_id="mem-inventory-selected")
    insert_evidence(app.conn, memory_id="mem-inventory-skipped")

    with running_server(app) as base_url:
        status, payload = request_json(
            "POST",
            f"{base_url}/memory/compile-preview",
            {
                "conversation_id": "conversation-inventory",
                "current_turn_id": "turn-inventory",
                "scope_filter": "project",
                "namespace_filter": "project/jarvis",
                "max_items": 10,
                "max_chars": 5000,
            },
        )

    assert status == 200
    _assert_response_field_inventory(
        _response_field_paths(payload),
        EXPECTED_200_RESPONSE_FIELD_INVENTORY,
    )


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
        {"namespace_filter": []},
        {"scope_filter": {}},
        {"current_user_text": []},
    ],
)
def test_invalid_payload_matrix_returns_json_400(
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
    assert set(response) == {"error", "status"}
    _assert_response_field_inventory(
        set(response),
        EXPECTED_400_RESPONSE_FIELD_INVENTORY,
    )
    assert response["status"] == 400
    assert isinstance(response["error"], str)
    assert FORBIDDEN_400_RESPONSE_KEYS.isdisjoint(response)
    assert "<html" not in body.lower()
    assert "<!doctype" not in body.lower()
    for forbidden in FORBIDDEN_400_RESPONSE_KEYS:
        assert forbidden not in body.lower()


def test_no_raw_evidence_or_observation_quote(
    app: DaemonApp,
) -> None:
    app.start()
    assert app.conn is not None
    evidence_marker = "RAW_PREVIEW_EVIDENCE_QUOTE_MARKER"
    observation_marker = "RAW_PREVIEW_OBSERVATION_TEXT_MARKER"
    fake_secret = "sk-evidencequotesecret1234567890abcdef"
    insert_memory_item(app.conn, memory_id="mem-evidence-preview")
    app.conn.execute(
        """
        INSERT INTO memory_observations (
          id, source_type, source_id, conversation_id, turn_id, event_id,
          observed_text, detected_kind, sensitivity, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "observation-mem-evidence-preview",
            "turn",
            "turn-observation-preview",
            "conversation-1",
            "turn-1",
            1,
            f"Observed text includes {observation_marker} and {fake_secret}",
            "semantic",
            "high",
            "2026-07-04T12:00:30+00:00",
        ),
    )
    app.conn.commit()
    insert_evidence(
        app.conn,
        memory_id="mem-evidence-preview",
        quote=f"Evidence quote includes {evidence_marker} and {fake_secret}",
    )

    with running_server(app) as base_url:
        status, payload = request_json(
            "POST",
            f"{base_url}/memory/compile-preview",
            {"max_items": 3, "max_chars": 5000},
        )

    assert status == 200
    rendered = json.dumps(payload, sort_keys=True)
    assert evidence_marker not in rendered
    assert observation_marker not in rendered
    assert fake_secret not in rendered


def test_no_secret_in_any_response_field_matrix(
    app: DaemonApp,
) -> None:
    app.start()
    assert app.conn is not None
    fake_secret = "sk-previewmatrixsecret1234567890abcdef"
    raw_memory_id = f"mem-{fake_secret}"
    raw_scope = f"project/{fake_secret}"
    raw_namespace = f"project/{fake_secret}/memory"
    insert_memory_item(
        app.conn,
        memory_id=raw_memory_id,
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
    insert_evidence(app.conn, memory_id=raw_memory_id)

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
    selected = payload["selected_items"][0]
    audit_metadata = payload["audit_metadata"]
    assert fake_secret not in rendered
    assert raw_memory_id not in rendered
    assert REDACTION_PLACEHOLDER in rendered
    assert selected["memory_id"] == projected_memory_id(raw_memory_id)
    assert selected["memory_id"] in payload["selection_reasons"]
    assert REDACTION_PLACEHOLDER in selected["canonical_key"]
    assert selected["kind"] == f"semantic {REDACTION_PLACEHOLDER}"
    assert selected["scope"] == f"project/{REDACTION_PLACEHOLDER}"
    assert selected["namespace"] == f"project/{REDACTION_PLACEHOLDER}/memory"
    assert selected["title"] == f"Title {REDACTION_PLACEHOLDER}"
    assert selected["claim"] == f"Claim {REDACTION_PLACEHOLDER}"
    assert selected["source_policy"] == f"policy {REDACTION_PLACEHOLDER}"
    assert selected["sensitivity"] == f"sensitivity {REDACTION_PLACEHOLDER}"
    assert audit_metadata["conversation_id"] == f"conversation-{REDACTION_PLACEHOLDER}"
    assert audit_metadata["current_turn_id"] == f"turn-{REDACTION_PLACEHOLDER}"
    assert "current_user_text" not in audit_metadata
    assert audit_metadata["scope_filter"] == f"project/{REDACTION_PLACEHOLDER}"
    assert (
        audit_metadata["namespace_filter"]
        == f"project/{REDACTION_PLACEHOLDER}/memory"
    )


def test_no_secret_in_reason_map_keys_or_values(
    app: DaemonApp,
) -> None:
    app.start()
    assert app.conn is not None
    fake_secret = "sk-reasonmapsecret1234567890abcdef"
    selected_raw_id = f"mem-selected-{fake_secret}"
    skipped_raw_id = f"mem-skipped-{fake_secret}"
    insert_memory_item(app.conn, memory_id=selected_raw_id)
    insert_memory_item(app.conn, memory_id=skipped_raw_id, status="disabled")
    insert_evidence(app.conn, memory_id=selected_raw_id)
    insert_evidence(app.conn, memory_id=skipped_raw_id)

    with running_server(app) as base_url:
        status, payload = request_json(
            "POST",
            f"{base_url}/memory/compile-preview",
            {"max_items": 10, "max_chars": 5000},
        )

    assert status == 200
    rendered_reasons = json.dumps(
        {
            "selection_reasons": payload["selection_reasons"],
            "skipped_reasons": payload["skipped_reasons"],
        },
        sort_keys=True,
    )
    selected_projected_id = projected_memory_id(selected_raw_id)
    skipped_projected_id = projected_memory_id(skipped_raw_id)
    selected_ids = [item["memory_id"] for item in payload["selected_items"]]
    skipped_ids = [item["memory_id"] for item in payload["skipped_items"]]
    reason_keys = list(payload["selection_reasons"]) + list(payload["skipped_reasons"])
    assert fake_secret not in rendered_reasons
    assert selected_raw_id not in rendered_reasons
    assert skipped_raw_id not in rendered_reasons
    assert payload["selection_reasons"] == {selected_projected_id: "eligible"}
    assert payload["skipped_reasons"] == {skipped_projected_id: "disabled"}
    assert set(payload["selection_reasons"]) == set(selected_ids)
    assert set(payload["skipped_reasons"]) == set(skipped_ids)
    assert len(reason_keys) == len(set(reason_keys))
    assert len({selected_projected_id, skipped_projected_id}) == 2


def test_forgotten_response_minimization(
    app: DaemonApp,
) -> None:
    app.start()
    assert app.conn is not None
    fake_secret = "sk-forgottenpreviewsecret1234567890abcdef"
    raw_memory_id = f"mem-forgotten-{fake_secret}"
    insert_memory_item(
        app.conn,
        memory_id=raw_memory_id,
        canonical_key=f"forgotten:{fake_secret}",
        kind=f"semantic {fake_secret}",
        scope=f"project/{fake_secret}",
        namespace=f"project/{fake_secret}/memory",
        status="forgotten",
        title=f"Forgotten title {fake_secret}",
        claim=f"Forgotten claim {fake_secret}",
        content=f"Forgotten content {fake_secret}",
        source_policy=f"policy {fake_secret}",
        sensitivity=f"sensitivity {fake_secret}",
    )
    insert_evidence(app.conn, memory_id=raw_memory_id)

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
            "memory_id": projected_memory_id(raw_memory_id),
            "reason_skipped": "forgotten",
        }
    ]
    rendered = json.dumps(payload, sort_keys=True)
    assert fake_secret not in rendered
    assert raw_memory_id not in rendered
    assert "title" not in payload["skipped_items"][0]
    assert "claim" not in payload["skipped_items"][0]
    assert "content" not in payload["skipped_items"][0]
    assert set(payload["skipped_items"][0]) == {"memory_id", "reason_skipped"}


def test_namespace_filter_still_matches_compiler(
    app: DaemonApp,
) -> None:
    app.start()
    assert app.conn is not None
    insert_memory_item(
        app.conn,
        memory_id="mem-global-bare-preview",
        namespace="global",
        updated_at="2026-07-04T13:00:00+00:00",
    )
    insert_memory_item(
        app.conn,
        memory_id="mem-global-slash-preview",
        namespace="global/default",
        updated_at="2026-07-04T12:30:00+00:00",
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
        "mem-global-bare-preview",
        "mem-global-slash-preview",
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
        projected_memory_id("mem-global-bare-preview"),
        projected_memory_id("mem-global-slash-preview"),
    ]
    assert payload["skipped_reasons"] == {
        projected_memory_id("mem-other-preview"): "namespace_mismatch"
    }


def test_procedural_opt_in_still_works_through_api(
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
        false_status, false_payload = request_json(
            "POST",
            f"{base_url}/memory/compile-preview",
            {
                "include_procedural": False,
                "max_items": 10,
                "max_chars": 5000,
            },
        )
        opt_in_status, opt_in_payload = request_json(
            "POST",
            f"{base_url}/memory/compile-preview",
            {"include_procedural": True, "max_items": 10, "max_chars": 5000},
        )

    assert default_status == 200
    assert false_status == 200
    assert opt_in_status == 200
    assert default_payload["selected_items"] == []
    assert default_payload["skipped_reasons"] == {
        projected_memory_id("mem-procedural-preview"): "procedural_not_requested"
    }
    assert false_payload["selected_items"] == []
    assert false_payload["skipped_reasons"] == default_payload["skipped_reasons"]
    assert [item["memory_id"] for item in opt_in_payload["selected_items"]] == [
        projected_memory_id("mem-procedural-preview")
    ]


def test_preview_api_is_read_only_strong(app: DaemonApp) -> None:
    app.start()
    assert app.conn is not None
    insert_memory_item(
        app.conn,
        memory_id="mem-readonly-preview",
        last_used_at="2026-07-03T10:00:00+00:00",
        last_confirmed_at="2026-07-03T11:00:00+00:00",
    )
    insert_evidence(app.conn, memory_id="mem-readonly-preview")
    before_total_changes = _total_changes(app)
    before_counts = {"events": table_count(app, "events"), **_memory_table_counts(app)}
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
    assert _total_changes(app) == before_total_changes
    assert {
        "events": table_count(app, "events"),
        **_memory_table_counts(app),
    } == before_counts
    assert tuple(after_timestamps) == tuple(before_timestamps)


def test_route_uses_compiler_projection_not_raw_rows(app: DaemonApp) -> None:
    app.start()
    assert app.conn is not None
    fake_secret = "sk-routeprojectionsecret1234567890abcdef"
    raw_memory_id = f"mem-route-{fake_secret}"
    insert_memory_item(
        app.conn,
        memory_id=raw_memory_id,
        canonical_key=f"route:{fake_secret}",
        kind=f"semantic {fake_secret}",
        title=f"Route title {fake_secret}",
        claim=f"Route claim {fake_secret}",
        content=f"Route content {fake_secret}",
    )
    insert_evidence(
        app.conn,
        memory_id=raw_memory_id,
        quote=f"Route evidence quote {fake_secret}",
    )
    expected_context = app.compile_memory_preview(
        MemoryCompilerRequest(
            config=MemoryCompilerConfig(max_items=3, max_chars=5000)
        )
    )

    with running_server(app) as base_url:
        status, payload = request_json(
            "POST",
            f"{base_url}/memory/compile-preview",
            {"max_items": 3, "max_chars": 5000},
        )

    rendered = json.dumps(payload, sort_keys=True)
    assert status == 200
    assert payload == asdict(expected_context)
    assert fake_secret not in rendered
    assert raw_memory_id not in rendered
    assert payload["selected_items"][0]["memory_id"] == projected_memory_id(
        raw_memory_id
    )
    assert set(payload["selected_items"][0]) == {
        "memory_id",
        "canonical_key",
        "kind",
        "scope",
        "namespace",
        "title",
        "claim",
        "reason_selected",
        "evidence_count",
        "source_policy",
        "sensitivity",
        "budget_cost",
    }
