"""Central secret redaction tests."""

from __future__ import annotations

import json
import sqlite3
from copy import deepcopy
from pathlib import Path

from jarvis.api.routes_events import event_to_dict
from jarvis.daemon.app import create_daemon_app
from jarvis.events.types import EventType
from jarvis.security.redaction import REDACTION_PLACEHOLDER, redact_secrets
from jarvis.store.db import close_quietly, initialize_database
from jarvis.store.event_store import EventStore, create_event_store
from tests.git_guards import assert_schema_and_migrations_unchanged
from tests.test_api_smoke import request_json, running_server, write_config


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_RUNTIME_STRINGS = (
    "/Users/n1_ozzy/Documents/dev/" "dan",
    "/tmp/" "dan",
    "af" "play",
    "--dangerously-" "skip-permissions",
)


def make_store(tmp_path: Path) -> tuple[sqlite3.Connection, EventStore]:
    conn = initialize_database(tmp_path / "jarvis.db")
    return conn, create_event_store(conn)


def test_redacts_dict_values_by_sensitive_keys() -> None:
    redacted = redact_secrets({"password": "hunter2", "safe": "ordinary"})

    assert redacted == {"password": REDACTION_PLACEHOLDER, "safe": "ordinary"}


def test_redacts_nested_dict_list_and_tuple_sensitive_keys() -> None:
    payload = {
        "items": [
            {"Access_Token": "access-raw"},
            {"metadata": {"set-cookie": "session=raw"}},
        ],
        "tuple": ({"private_key": "raw-key"}, {"visible": "ok"}),
        "count": 3,
        "enabled": True,
        "nothing": None,
    }

    redacted = redact_secrets(payload)

    assert redacted["items"][0]["Access_Token"] == REDACTION_PLACEHOLDER
    assert redacted["items"][1]["metadata"]["set-cookie"] == REDACTION_PLACEHOLDER
    assert redacted["tuple"][0]["private_key"] == REDACTION_PLACEHOLDER
    assert redacted["tuple"][1]["visible"] == "ok"
    assert redacted["count"] == 3
    assert redacted["enabled"] is True
    assert redacted["nothing"] is None


def test_sensitive_key_context_redacts_non_string_scalars() -> None:
    redacted = redact_secrets({"token": 123, "secret": False, "cookie": None})

    assert redacted == {
        "token": REDACTION_PLACEHOLDER,
        "secret": REDACTION_PLACEHOLDER,
        "cookie": REDACTION_PLACEHOLDER,
    }


def test_redacts_token_looking_strings_under_harmless_keys() -> None:
    raw_token = "sk-ant-abc123"

    redacted = redact_secrets(
        {
            "stdout": f"token is {raw_token}",
            "message": "project key sk-proj-abc123 and generic sk-abc123",
        }
    )

    rendered = json.dumps(redacted, sort_keys=True)
    assert raw_token not in rendered
    assert "sk-proj-abc123" not in rendered
    assert "sk-abc123" not in rendered
    assert redacted["stdout"] == f"token is {REDACTION_PLACEHOLDER}"


def test_redacts_bearer_tokens_in_strings() -> None:
    raw_token = "abc.def-ghi"

    redacted = redact_secrets({"note": f"Authorization: Bearer {raw_token}"})

    assert raw_token not in redacted["note"]
    assert f"Bearer {REDACTION_PLACEHOLDER}" in redacted["note"]


def test_redacts_basic_auth_values_in_strings() -> None:
    raw_token = "dXNlcjpwYXNz"

    redacted = redact_secrets({"header": f"Authorization: Basic {raw_token}"})

    assert raw_token not in redacted["header"]
    assert f"Basic {REDACTION_PLACEHOLDER}" in redacted["header"]


def test_redacts_github_token_patterns() -> None:
    raw_classic = "ghp_1234567890abcdef"
    raw_fine_grained = "github_pat_1234567890abcdef"

    redacted = redact_secrets({"stdout": f"{raw_classic} {raw_fine_grained}"})

    rendered = json.dumps(redacted, sort_keys=True)
    assert raw_classic not in rendered
    assert raw_fine_grained not in rendered
    assert rendered.count(REDACTION_PLACEHOLDER) == 2


def test_redaction_does_not_mutate_original_payload_object() -> None:
    payload = {
        "password": "hunter2",
        "nested": [{"stdout": "sk-ant-original"}],
    }
    original = deepcopy(payload)

    redacted = redact_secrets(payload)

    assert payload == original
    assert redacted is not payload
    assert redacted["nested"] is not payload["nested"]
    assert redacted["nested"][0] is not payload["nested"][0]


def test_preserves_ordinary_non_secret_text() -> None:
    payload = {"message": "ordinary text with no credential material", "status": "ok"}

    assert redact_secrets(payload) == payload


def test_event_store_append_redacts_before_persistence(tmp_path: Path) -> None:
    conn, store = make_store(tmp_path)
    raw_payload = {
        "password": "hunter2",
        "stdout": "token is sk-ant-eventstore123",
        "note": "Authorization: Bearer bearer-secret",
    }
    original = deepcopy(raw_payload)
    try:
        event = store.append(EventType.ERROR_RAISED, "tests", raw_payload)
        payload_json = conn.execute(
            "SELECT payload_json FROM events WHERE id = ?",
            (event.id,),
        ).fetchone()[0]
        stored = store.list_after(0, limit=10)[0]
    finally:
        close_quietly(conn)

    rendered_event = json.dumps(event_to_dict(event), sort_keys=True)
    rendered_stored = json.dumps(stored.payload, sort_keys=True)
    assert raw_payload == original
    assert "hunter2" not in payload_json
    assert "sk-ant-eventstore123" not in payload_json
    assert "bearer-secret" not in payload_json
    assert "hunter2" not in rendered_event
    assert "sk-ant-eventstore123" not in rendered_stored
    assert "bearer-secret" not in rendered_stored
    assert event.payload["password"] == REDACTION_PLACEHOLDER
    assert stored.payload["password"] == REDACTION_PLACEHOLDER


def test_events_api_response_cannot_expose_secret_from_event_payload(tmp_path: Path) -> None:
    raw_secret = "sk-ant-apiresponse123"
    config_path = write_config(tmp_path / "jarvis.toml", tmp_path / "home" / "jarvis.db")
    app = create_daemon_app(config_path)
    try:
        assert app.event_store is not None
        app.event_store.append(EventType.ERROR_RAISED, "tests", {"stdout": raw_secret})

        with running_server(app) as base_url:
            status, payload = request_json("GET", f"{base_url}/events?after_id=0&limit=10")
    finally:
        app.close()

    rendered = json.dumps(payload, sort_keys=True)
    assert status == 200
    assert raw_secret not in rendered
    assert REDACTION_PLACEHOLDER in rendered


def test_event_store_ordering_and_list_after_are_unchanged(tmp_path: Path) -> None:
    conn, store = make_store(tmp_path)
    try:
        first = store.append(EventType.TURN_STARTED, "tests", {"index": 1})
        second = store.append(EventType.TURN_STARTED, "tests", {"index": 2})
        third = store.append(EventType.TURN_STARTED, "tests", {"index": 3})
        after_first = store.list_after(first.id, limit=10)
    finally:
        close_quietly(conn)

    assert [event.id for event in after_first] == [second.id, third.id]
    assert [event.payload["index"] for event in after_first] == [2, 3]


def test_schema_and_migrations_were_not_changed() -> None:
    assert_schema_and_migrations_unchanged(ROOT)


def test_forbidden_legacy_strings_are_absent_from_runtime_code_and_scripts() -> None:
    findings: list[str] = []
    for root_name in ("jarvis", "scripts"):
        for path in (ROOT / root_name).rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for forbidden in FORBIDDEN_RUNTIME_STRINGS:
                if forbidden in text:
                    findings.append(f"{path.relative_to(ROOT)} contains {forbidden}")

    assert findings == []
