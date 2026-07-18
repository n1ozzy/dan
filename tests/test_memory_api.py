"""Prompt 16 memory API integration tests."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from dan.brain.manager import BrainManager
from dan.brain.test_adapter import TestBrainAdapter as HermeticBrainAdapter
from dan.daemon.app import DaemonApp, create_daemon_app
from tests.git_guards import assert_schema_and_migrations_unchanged
from tests.test_api_smoke import (
    event_types,
    request_json,
    request_raw,
    running_server,
    table_count,
    write_config,
)


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    return write_config(tmp_path / "dan.toml", tmp_path / "home" / "dan.db")


@pytest.fixture
def app(config_path: Path) -> Iterator[DaemonApp]:
    daemon_app = create_daemon_app(config_path)
    production_manager = daemon_app.brain_manager
    daemon_app.brain_manager = BrainManager(
        [HermeticBrainAdapter(default_model="test-model")],
        default_adapter="test",
    )
    if production_manager is not None:
        production_manager.close()
    try:
        yield daemon_app
    finally:
        daemon_app.close()


def memory_events(app: DaemonApp) -> list[dict[str, object]]:
    assert app.event_store is not None
    return [
        dict(event.payload)
        for event in app.event_store.list_after(0, limit=100)
        if event.type == "memory.updated"
    ]


def create_memory(base_url: str, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "kind": "fact",
        "title": "Useful fact",
        "body": "DAN memory is stored in SQLite.",
        "priority": 3,
        "active": True,
        "metadata": {"source": "test"},
    }
    payload.update(overrides)
    status, response = request_json("POST", f"{base_url}/memory", payload)
    assert status in {200, 201}
    return response["memory"]  # type: ignore[return-value]


def test_get_memory_requires_started_app(app: DaemonApp) -> None:
    with running_server(app) as base_url:
        status, payload = request_json("GET", f"{base_url}/memory")

    assert status == 503
    assert payload["status"] == 503


def test_get_memory_initially_returns_empty_list(app: DaemonApp) -> None:
    app.start()

    with running_server(app) as base_url:
        status, payload = request_json("GET", f"{base_url}/memory")

    assert status == 200
    assert payload == {"memory": [], "active_only": False, "limit": 100}


def test_post_memory_creates_block_and_get_memory_lists_it(app: DaemonApp) -> None:
    app.start()

    with running_server(app) as base_url:
        created = create_memory(base_url)
        status, listed = request_json("GET", f"{base_url}/memory")

    assert created["id"]
    assert created["kind"] == "fact"
    assert created["title"] == "Useful fact"
    assert created["body"] == "DAN memory is stored in SQLite."
    assert created["priority"] == 3
    assert created["active"] is True
    assert created["metadata"] == {"source": "test"}
    assert listed["memory"] == [created]
    assert table_count(app, "voice_queue") == 0
    assert table_count(app, "worker_jobs") == 0


def test_get_memory_by_id_returns_block(app: DaemonApp) -> None:
    app.start()

    with running_server(app) as base_url:
        created = create_memory(base_url)
        status, payload = request_json("GET", f"{base_url}/memory/{created['id']}")

    assert status == 200
    assert payload["memory"] == created


def test_patch_memory_updates_provided_fields(app: DaemonApp) -> None:
    app.start()

    with running_server(app) as base_url:
        created = create_memory(base_url)
        status, payload = request_json(
            "PATCH",
            f"{base_url}/memory/{created['id']}",
            {
                "title": "Updated title",
                "body": "Updated body",
                "priority": 9,
                "metadata": {"updated": True},
            },
        )

    assert status == 200
    updated = payload["memory"]
    assert updated["id"] == created["id"]
    assert updated["kind"] == "fact"
    assert updated["title"] == "Updated title"
    assert updated["body"] == "Updated body"
    assert updated["priority"] == 9
    assert updated["active"] is True
    assert updated["metadata"] == {"updated": True}


def test_delete_memory_soft_disables_block_and_active_only_excludes_it(app: DaemonApp) -> None:
    app.start()

    with running_server(app) as base_url:
        created = create_memory(base_url)
        delete_status, disabled_payload = request_json("DELETE", f"{base_url}/memory/{created['id']}")
        get_status, fetched = request_json("GET", f"{base_url}/memory/{created['id']}")
        list_status, listed = request_json("GET", f"{base_url}/memory?active_only=true")

    assert delete_status == 200
    assert disabled_payload["memory"]["id"] == created["id"]
    assert disabled_payload["memory"]["active"] is False
    assert get_status == 200
    assert fetched["memory"]["active"] is False
    assert list_status == 200
    assert listed["memory"] == []
    assert listed["active_only"] is True


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("GET", "/memory?kind=provider_session", None),
        ("POST", "/memory", {"kind": "provider_session", "title": "Bad", "body": "Bad"}),
    ],
)
def test_memory_routes_reject_invalid_kind(
    app: DaemonApp,
    method: str,
    path: str,
    payload: object | None,
) -> None:
    app.start()

    with running_server(app) as base_url:
        status, response = request_json(method, f"{base_url}{path}", payload)

    assert status == 400
    assert response["status"] == 400
    assert "kind" in response["error"].lower()


@pytest.mark.parametrize("query", ["limit=0", "limit=501", "limit=bad"])
def test_get_memory_rejects_invalid_limit(app: DaemonApp, query: str) -> None:
    app.start()

    with running_server(app) as base_url:
        status, response = request_json("GET", f"{base_url}/memory?{query}")

    assert status == 400
    assert response["status"] == 400
    assert "limit" in response["error"]


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "fact", "title": "", "body": "Body"},
        {"kind": "fact", "title": "Title", "body": ""},
    ],
)
def test_post_memory_rejects_empty_title_or_body(app: DaemonApp, payload: dict[str, object]) -> None:
    app.start()

    with running_server(app) as base_url:
        status, response = request_json("POST", f"{base_url}/memory", payload)

    assert status == 400
    assert response["status"] == 400
    assert "non-empty" in response["error"]


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "fact", "title": "Bad metadata", "body": "Body", "metadata": ["not-object"]},
        ["not-object"],
    ],
)
def test_post_memory_rejects_non_object_payloads_or_metadata(
    app: DaemonApp,
    payload: object,
) -> None:
    app.start()

    with running_server(app) as base_url:
        status, response = request_json("POST", f"{base_url}/memory", payload)

    assert status == 400
    assert response["status"] == 400


def test_post_memory_rejects_malformed_json(app: DaemonApp) -> None:
    app.start()

    with running_server(app) as base_url:
        status, content_type, body = request_raw("POST", f"{base_url}/memory", b"{not-json")

    assert status == 400
    assert "application/json" in content_type
    assert json.loads(body)["status"] == 400


def test_patch_memory_rejects_non_object_metadata(app: DaemonApp) -> None:
    app.start()

    with running_server(app) as base_url:
        created = create_memory(base_url)
        status, response = request_json(
            "PATCH",
            f"{base_url}/memory/{created['id']}",
            {"metadata": ["not-object"]},
        )

    assert status == 400
    assert response["status"] == 400
    assert "metadata" in response["error"]


@pytest.mark.parametrize("method", ["GET", "PATCH", "DELETE"])
def test_memory_routes_return_404_for_missing_id(app: DaemonApp, method: str) -> None:
    app.start()
    payload = {"title": "No row"} if method == "PATCH" else None

    with running_server(app) as base_url:
        status, response = request_json(method, f"{base_url}/memory/missing", payload)

    assert status == 404
    assert response["status"] == 404


def test_memory_create_update_disable_emit_events_but_get_is_read_only(app: DaemonApp) -> None:
    app.start()

    with running_server(app) as base_url:
        created = create_memory(base_url)
        after_create_events = list(memory_events(app))
        request_json("GET", f"{base_url}/memory")
        after_get_events = list(memory_events(app))
        request_json("PATCH", f"{base_url}/memory/{created['id']}", {"body": "Updated"})
        request_json("DELETE", f"{base_url}/memory/{created['id']}")

    assert [event["action"] for event in after_create_events] == ["created"]
    assert after_get_events == after_create_events
    assert [event["action"] for event in memory_events(app)] == ["created", "updated", "disabled"]


def test_created_active_memory_reaches_context_builder_and_disabled_memory_is_excluded(
    app: DaemonApp,
) -> None:
    app.start()

    with running_server(app) as base_url:
        active = create_memory(base_url, title="Active", body="Include this", priority=8)
        disabled = create_memory(base_url, title="Disabled", body="Do not include", priority=99)
        request_json("DELETE", f"{base_url}/memory/{disabled['id']}")
        status, response = request_json("POST", f"{base_url}/input/text", {"text": "use memory"})

    assert status == 200
    snapshot = response["turn"]["context_snapshot"]
    assert snapshot["memory_block_count"] == 1
    assert response["turn"]["final_text"] == "Test response: use memory"
    assert event_types(app).count("memory.updated") == 3

    assert app.context_builder is not None
    context_result = app.context_builder.build_request(
        turn_id="manual-context-check",
        conversation_id=response["conversation_id"],
        input_text="check active memory",
    )
    assert [block.id for block in context_result.request.memory_blocks] == [active["id"]]


def test_memory_api_does_not_touch_voice_queue_or_worker_jobs(app: DaemonApp) -> None:
    app.start()

    with running_server(app) as base_url:
        created = create_memory(base_url)
        request_json("PATCH", f"{base_url}/memory/{created['id']}", {"priority": 5})
        request_json("DELETE", f"{base_url}/memory/{created['id']}")

    assert table_count(app, "voice_queue") == 0
    assert table_count(app, "worker_jobs") == 0


def test_memory_api_keeps_schema_and_migrations_unchanged() -> None:
    assert_schema_and_migrations_unchanged(ROOT)


def test_runtime_memory_api_files_do_not_contain_forbidden_legacy_strings() -> None:
    forbidden = (
        "/Users/" "n1_ozzy" "/Documents/dev/dan",
        "/tmp/dan",
        "afplay",
        "--dangerously-skip-permissions",
    )
    scanned = (
        ROOT / "dan" / "api" / "routes_memory.py",
        ROOT / "dan" / "daemon" / "app.py",
        ROOT / "dan" / "daemon" / "lifecycle.py",
        ROOT / "dan" / "cli.py",
    )
    offenders: list[tuple[str, str]] = []

    for path in scanned:
        source = path.read_text(encoding="utf-8")
        for snippet in forbidden:
            if snippet in source:
                offenders.append((str(path.relative_to(ROOT)), snippet))

    assert offenders == []
