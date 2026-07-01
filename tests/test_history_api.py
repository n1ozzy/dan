"""Prompt 11C read-only conversation and turn history API tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from jarvis.daemon.app import DaemonApp, create_daemon_app
from jarvis.turns.repository import ConversationRepository
from tests.git_guards import assert_schema_and_migrations_unchanged
from tests.test_api_smoke import request_json, write_config
from tests.test_text_turn_pipeline import running_server, table_count


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def app(tmp_path: Path) -> DaemonApp:
    config_path = write_config(tmp_path / "jarvis.toml", tmp_path / "home" / "jarvis.db")
    daemon_app = create_daemon_app(config_path)
    try:
        yield daemon_app
    finally:
        daemon_app.close()


def event_count(conn: sqlite3.Connection) -> int:
    return table_count(conn, "events")


def post_text(base_url: str, text: str, conversation_id: str | None = None) -> dict[str, object]:
    payload: dict[str, object] = {"text": text}
    if conversation_id is not None:
        payload["conversation_id"] = conversation_id
    status, response = request_json("POST", f"{base_url}/input/text", payload)
    assert status == 200
    return response


def test_get_conversations_returns_503_when_app_is_not_started(app: DaemonApp) -> None:
    with running_server(app) as base_url:
        status, payload = request_json("GET", f"{base_url}/conversations")

    assert status == 503
    assert payload["status"] == 503


def test_get_turns_returns_503_when_app_is_not_started(app: DaemonApp) -> None:
    with running_server(app) as base_url:
        status, payload = request_json("GET", f"{base_url}/turns?conversation_id=missing")

    assert status == 503
    assert payload["status"] == 503


def test_get_conversations_returns_empty_list_initially(app: DaemonApp) -> None:
    app.start()

    with running_server(app) as base_url:
        status, payload = request_json("GET", f"{base_url}/conversations")

    assert status == 200
    assert payload == {"conversations": [], "include_archived": False, "limit": 50}


def test_get_conversations_returns_created_conversation_with_turn_stats(app: DaemonApp) -> None:
    app.start()

    with running_server(app) as base_url:
        created = post_text(base_url, "History summary")
        status, payload = request_json("GET", f"{base_url}/conversations")

    assert status == 200
    conversations = payload["conversations"]
    assert isinstance(conversations, list)
    assert len(conversations) == 1
    conversation = conversations[0]
    assert conversation["id"] == created["conversation_id"]
    assert conversation["status"] == "active"
    assert conversation["metadata"] == {}
    assert conversation["turn_count"] == 1
    assert isinstance(conversation["latest_turn_at"], str)
    assert isinstance(conversation["created_at"], str)
    assert isinstance(conversation["updated_at"], str)


def test_get_turns_requires_conversation_id(app: DaemonApp) -> None:
    app.start()

    with running_server(app) as base_url:
        status, payload = request_json("GET", f"{base_url}/turns")

    assert status == 400
    assert payload["status"] == 400
    assert "conversation_id" in str(payload["error"])


def test_get_turns_unknown_conversation_returns_empty_list(app: DaemonApp) -> None:
    app.start()

    with running_server(app) as base_url:
        status, payload = request_json("GET", f"{base_url}/turns?conversation_id=unknown")

    assert status == 200
    assert payload == {"conversation_id": "unknown", "turns": [], "limit": 50, "newest_first": False}


def test_get_turns_returns_persisted_turns_for_conversation(app: DaemonApp) -> None:
    app.start()

    with running_server(app) as base_url:
        created = post_text(base_url, "Persisted turn")
        conversation_id = str(created["conversation_id"])
        status, payload = request_json("GET", f"{base_url}/turns?conversation_id={conversation_id}")

    assert status == 200
    assert payload["conversation_id"] == conversation_id
    turns = payload["turns"]
    assert isinstance(turns, list)
    assert len(turns) == 1
    turn = turns[0]
    assert turn["id"] == created["turn_id"]
    assert turn["conversation_id"] == conversation_id
    assert turn["source"] == "api"
    assert turn["status"] == "finished"
    assert turn["input_text"] == "Persisted turn"
    assert turn["final_text"] == "Jarvis mock response: Persisted turn"
    assert turn["brain_adapter"] == "mock"
    assert turn["brain_model"] == "mock-local"
    assert isinstance(turn["context_snapshot"], dict)
    assert turn["error"] is None
    assert turn["metadata"] == {}


def test_get_turns_default_order_is_chronological(app: DaemonApp) -> None:
    app.start()

    with running_server(app) as base_url:
        first = post_text(base_url, "First")
        conversation_id = str(first["conversation_id"])
        post_text(base_url, "Second", conversation_id=conversation_id)
        status, payload = request_json("GET", f"{base_url}/turns?conversation_id={conversation_id}")

    assert status == 200
    assert [turn["input_text"] for turn in payload["turns"]] == ["First", "Second"]


def test_get_turns_newest_first_returns_newest_first(app: DaemonApp) -> None:
    app.start()

    with running_server(app) as base_url:
        first = post_text(base_url, "Old")
        conversation_id = str(first["conversation_id"])
        post_text(base_url, "New", conversation_id=conversation_id)
        status, payload = request_json(
            "GET",
            f"{base_url}/turns?conversation_id={conversation_id}&newest_first=true",
        )

    assert status == 200
    assert payload["newest_first"] is True
    assert [turn["input_text"] for turn in payload["turns"]] == ["New", "Old"]


def test_get_conversations_excludes_archived_by_default(app: DaemonApp) -> None:
    app.start()

    with running_server(app) as base_url:
        active = post_text(base_url, "Active")
        archived = post_text(base_url, "Archived")
        assert app.conn is not None
        ConversationRepository(app.conn).archive(str(archived["conversation_id"]))
        status, payload = request_json("GET", f"{base_url}/conversations")

    assert status == 200
    ids = [conversation["id"] for conversation in payload["conversations"]]
    assert ids == [active["conversation_id"]]


def test_get_conversations_include_archived_includes_archived(app: DaemonApp) -> None:
    app.start()

    with running_server(app) as base_url:
        active = post_text(base_url, "Active")
        archived = post_text(base_url, "Archived")
        assert app.conn is not None
        ConversationRepository(app.conn).archive(str(archived["conversation_id"]))
        status, payload = request_json("GET", f"{base_url}/conversations?include_archived=yes")

    assert status == 200
    assert payload["include_archived"] is True
    ids = {conversation["id"] for conversation in payload["conversations"]}
    assert ids == {active["conversation_id"], archived["conversation_id"]}


@pytest.mark.parametrize(
    ("path", "query"),
    [
        ("/conversations", "limit=0"),
        ("/conversations", "limit=501"),
        ("/conversations", "limit=bad"),
        ("/turns", "conversation_id=abc&limit=0"),
        ("/turns", "conversation_id=abc&limit=501"),
        ("/turns", "conversation_id=abc&limit=bad"),
    ],
)
def test_history_invalid_limit_returns_json_400(app: DaemonApp, path: str, query: str) -> None:
    app.start()

    with running_server(app) as base_url:
        status, payload = request_json("GET", f"{base_url}{path}?{query}")

    assert status == 400
    assert payload["status"] == 400
    assert "limit" in str(payload["error"])


@pytest.mark.parametrize(
    ("path", "query"),
    [
        ("/conversations", "include_archived=maybe"),
        ("/turns", "conversation_id=abc&newest_first=maybe"),
    ],
)
def test_history_invalid_bool_query_returns_json_400(app: DaemonApp, path: str, query: str) -> None:
    app.start()

    with running_server(app) as base_url:
        status, payload = request_json("GET", f"{base_url}{path}?{query}")

    assert status == 400
    assert payload["status"] == 400


def test_history_endpoints_do_not_append_events(app: DaemonApp) -> None:
    app.start()

    with running_server(app) as base_url:
        created = post_text(base_url, "No event append")
        assert app.conn is not None
        before = event_count(app.conn)
        request_json("GET", f"{base_url}/conversations")
        request_json("GET", f"{base_url}/turns?conversation_id={created['conversation_id']}")
        after = event_count(app.conn)

    assert after == before


def test_history_endpoints_do_not_create_conversations_or_turns(app: DaemonApp) -> None:
    app.start()
    assert app.conn is not None
    before_conversations = table_count(app.conn, "conversations")
    before_turns = table_count(app.conn, "turns")

    with running_server(app) as base_url:
        request_json("GET", f"{base_url}/conversations")
        request_json("GET", f"{base_url}/turns?conversation_id=missing")

    assert table_count(app.conn, "conversations") == before_conversations
    assert table_count(app.conn, "turns") == before_turns


def test_history_endpoints_do_not_touch_voice_tools_or_workers(app: DaemonApp) -> None:
    app.start()

    with running_server(app) as base_url:
        created = post_text(base_url, "No side channels")
        assert app.conn is not None
        before = {
            "voice_queue": table_count(app.conn, "voice_queue"),
            "tool_runs": table_count(app.conn, "tool_runs"),
            "worker_jobs": table_count(app.conn, "worker_jobs"),
        }
        request_json("GET", f"{base_url}/conversations")
        request_json("GET", f"{base_url}/turns?conversation_id={created['conversation_id']}")
        after = {
            "voice_queue": table_count(app.conn, "voice_queue"),
            "tool_runs": table_count(app.conn, "tool_runs"),
            "worker_jobs": table_count(app.conn, "worker_jobs"),
        }

    assert after == before == {"voice_queue": 0, "tool_runs": 0, "worker_jobs": 0}


def test_no_real_home_is_touched_by_history_tests(tmp_path: Path) -> None:
    db_path = tmp_path / "home" / "jarvis.db"
    config = write_config(tmp_path / "jarvis.toml", db_path)

    daemon_app = create_daemon_app(config)
    try:
        assert str(daemon_app.paths.home).startswith(str(tmp_path))
        assert str(daemon_app.paths.db_path).startswith(str(tmp_path))
    finally:
        daemon_app.close()


def test_sqlite_schema_and_migrations_are_not_modified() -> None:
    assert_schema_and_migrations_unchanged(ROOT)


def test_runtime_history_files_do_not_contain_forbidden_legacy_strings() -> None:
    forbidden = (
        "/Users/n1_ozzy/Documents/dev/dan",
        "/tmp/dan",
        "afplay",
        "--dangerously-skip-permissions",
    )
    scanned = (
        ROOT / "jarvis" / "api" / "routes_history.py",
        ROOT / "jarvis" / "daemon" / "app.py",
        ROOT / "jarvis" / "daemon" / "lifecycle.py",
        ROOT / "jarvis" / "cli.py",
    )
    offenders: list[tuple[str, str]] = []

    for path in scanned:
        if not path.exists():
            continue
        source = path.read_text(encoding="utf-8")
        for snippet in forbidden:
            if snippet in source:
                offenders.append((str(path.relative_to(ROOT)), snippet))

    assert offenders == []
