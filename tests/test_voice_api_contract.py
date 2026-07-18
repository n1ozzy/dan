"""Task 8: the one voice/config contract exposed through the local HTTP API."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from dan.daemon.app import DaemonApp, create_daemon_app
from dan.voice.resolver import VoiceResolverError

from tests.test_api_smoke import (
    request_json,
    rewrite_voice_section,
    running_server,
    write_config,
)
from tests.voice_helpers import render_snapshot, speech_intent


VOICE_TOML = """
enabled = true
speak_responses = false
broker_enabled = true
default_tts = "mock"
default_stt = "mock"
ptt_mode = "hold"
queue_persisted = true
recorder = "mock"
"""

SPEAK_TEXT = "Zażółć gęślą jaźń."


def make_voice_app(tmp_path: Path, *, resolver: Any | None = None) -> DaemonApp:
    config_path = write_config(tmp_path / "dan.toml", tmp_path / "home" / "dan.db")
    rewrite_voice_section(config_path, VOICE_TOML)
    app = create_daemon_app(config_path)
    app.voice_resolver = resolver or SimpleNamespace(
        resolve=lambda intent: render_snapshot()
    )
    app.start()
    # Rows must stay deterministically observable as 'queued': the broker
    # may not consume them while contract assertions run.
    assert app.voice_broker is not None
    app.voice_broker.pause()
    return app


@pytest.fixture
def voice_app(tmp_path: Path) -> Iterator[DaemonApp]:
    app = make_voice_app(tmp_path)
    try:
        yield app
    finally:
        app.close()


@pytest.fixture
def app_without_voice_asset(tmp_path: Path) -> Iterator[DaemonApp]:
    def broken_resolve(intent: Any) -> Any:
        raise VoiceResolverError("voice asset missing for persona 'dan'")

    app = make_voice_app(tmp_path, resolver=SimpleNamespace(resolve=broken_resolve))
    try:
        yield app
    finally:
        app.close()


def queue_rows(app: DaemonApp) -> list[dict[str, Any]]:
    conn = sqlite3.connect(app.config.database.path)
    try:
        rows = conn.execute(
            "SELECT id, text, status, session_id FROM voice_queue ORDER BY rowid ASC"
        ).fetchall()
    finally:
        conn.close()
    return [
        {"id": row[0], "text": row[1], "status": row[2], "session_id": row[3]}
        for row in rows
    ]


def voice_event_types(app: DaemonApp) -> list[str]:
    assert app.event_store is not None
    return [
        event.type
        for event in app.event_store.list_after(0, limit=500)
        if str(event.type).startswith("voice.")
    ]


def speak_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "text": SPEAK_TEXT,
        "persona": "dan",
        "session": "smoke",
        "source": "codex",
    }
    payload.update(overrides)
    return payload


# --- POST /voice/speak -------------------------------------------------------


def test_speak_returns_201_queued_and_commits_full_row(voice_app: DaemonApp) -> None:
    with running_server(voice_app) as base_url:
        status, body = request_json("POST", f"{base_url}/voice/speak", speak_payload())

    assert status == 201
    assert body["status"] == "queued"
    assert body["request_id"]

    rows = queue_rows(voice_app)
    assert len(rows) == 1
    assert rows[0]["id"] == body["request_id"]
    assert rows[0]["text"] == SPEAK_TEXT
    assert rows[0]["status"] == "queued"
    assert rows[0]["session_id"] == "smoke"
    assert "voice.speak.queued" in voice_event_types(voice_app)


def test_closed_intake_rejects_speak_before_queue_or_event_write(
    voice_app: DaemonApp,
) -> None:
    operation_id = voice_app.close_intake(
        operation_id="cutover-voice-1",
        reason="release cutover",
    )

    with running_server(voice_app) as base_url:
        status, body = request_json(
            "POST",
            f"{base_url}/voice/speak",
            speak_payload(),
        )

    assert status == 503
    assert body["code"] == "intake_closed"
    assert body["operation_id"] == operation_id
    assert body["reason"] == "release cutover"
    assert queue_rows(voice_app) == []
    assert voice_event_types(voice_app) == []


def test_speak_response_redacts_text_by_default(voice_app: DaemonApp) -> None:
    with running_server(voice_app) as base_url:
        status, body = request_json("POST", f"{base_url}/voice/speak", speak_payload())

    assert status == 201
    assert "text" not in body
    assert SPEAK_TEXT not in str(body)
    assert body["text_length"] == len(SPEAK_TEXT)


def test_speak_validation_failure_creates_no_row_and_no_event(
    voice_app: DaemonApp,
) -> None:
    with running_server(voice_app) as base_url:
        missing_text_status, missing_text_body = request_json(
            "POST", f"{base_url}/voice/speak", speak_payload(text="   ")
        )
        resolver_field_status, resolver_field_body = request_json(
            "POST", f"{base_url}/voice/speak", speak_payload(voice="M3")
        )
        non_object_status, _ = request_json(
            "POST", f"{base_url}/voice/speak", ["not-an-object"]
        )

    assert missing_text_status == 400
    assert "text" in str(missing_text_body["error"])
    assert resolver_field_status == 400
    assert "voice" in str(resolver_field_body["error"])
    assert non_object_status == 400
    assert queue_rows(voice_app) == []
    assert voice_event_types(voice_app) == []


def test_speak_resolver_failure_is_4xx_with_reason_and_empty_queue(
    app_without_voice_asset: DaemonApp,
) -> None:
    with running_server(app_without_voice_asset) as base_url:
        status, body = request_json(
            "POST", f"{base_url}/voice/speak", speak_payload()
        )

    assert 400 <= status < 500
    assert "voice asset missing" in str(body["error"])
    assert queue_rows(app_without_voice_asset) == []
    assert voice_event_types(app_without_voice_asset) == []


# --- queue endpoints ---------------------------------------------------------


def test_queue_flush_is_session_scoped(voice_app: DaemonApp) -> None:
    assert voice_app.voice_service is not None
    kept = voice_app.voice_service.submit(speech_intent("radio one", session="radio"))
    flushed = voice_app.voice_service.submit(
        speech_intent("standup one", session="standup")
    )

    with running_server(voice_app) as base_url:
        status, body = request_json(
            "POST", f"{base_url}/voice/queue/flush", {"session": "radio"}
        )

    assert status == 200
    assert body["cancelled"] == [kept.id]
    statuses = {row["id"]: row["status"] for row in queue_rows(voice_app)}
    assert statuses[kept.id] == "cancelled"
    assert statuses[flushed.id] == "queued"


def test_queue_flush_requires_session(voice_app: DaemonApp) -> None:
    with running_server(voice_app) as base_url:
        status, body = request_json("POST", f"{base_url}/voice/queue/flush", {})

    assert status == 400
    assert "session" in str(body["error"])


def test_queue_cancel_cancels_one_request(voice_app: DaemonApp) -> None:
    assert voice_app.voice_service is not None
    first = voice_app.voice_service.submit(speech_intent("first", session="cancel"))
    second = voice_app.voice_service.submit(
        speech_intent("second", session="cancel", utterance_index=1)
    )

    with running_server(voice_app) as base_url:
        status, body = request_json(
            "POST", f"{base_url}/voice/queue/{first.id}/cancel", {}
        )
        missing_status, _ = request_json(
            "POST", f"{base_url}/voice/queue/no-such-request/cancel", {}
        )

    assert status == 200
    assert body["request_id"] == first.id
    assert missing_status == 404
    statuses = {row["id"]: row["status"] for row in queue_rows(voice_app)}
    assert statuses[first.id] == "cancelled"
    assert statuses[second.id] == "queued"


def test_queue_listing_redacts_text(voice_app: DaemonApp) -> None:
    assert voice_app.voice_service is not None
    voice_app.voice_service.submit(speech_intent(SPEAK_TEXT, session="redact"))

    with running_server(voice_app) as base_url:
        status, body = request_json("GET", f"{base_url}/voice/queue")

    assert status == 200
    rows = body["voice_queue"]
    assert rows
    assert all("text" not in row for row in rows)
    assert rows[0]["text_length"] == len(SPEAK_TEXT)


# --- pause/resume ------------------------------------------------------------


def test_pause_and_resume_toggle_broker(voice_app: DaemonApp) -> None:
    broker = voice_app.voice_broker
    assert broker is not None
    broker.resume()
    assert broker.paused is False

    with running_server(voice_app) as base_url:
        pause_status, pause_body = request_json("POST", f"{base_url}/voice/pause", {})
        assert broker.paused is True
        resume_status, resume_body = request_json(
            "POST", f"{base_url}/voice/resume", {}
        )

    assert pause_status == 200
    assert pause_body["paused"] is True
    assert resume_status == 200
    assert resume_body["paused"] is False
    assert broker.paused is False
    broker.pause()


def test_paused_broker_claims_no_new_requests(voice_app: DaemonApp) -> None:
    broker = voice_app.voice_broker
    assert broker is not None
    assert broker.paused is True
    assert voice_app.voice_service is not None
    request = voice_app.voice_service.submit(speech_intent("hold it", session="pause"))

    assert broker.drain_all() == 0
    statuses = {row["id"]: row["status"] for row in queue_rows(voice_app)}
    assert statuses[request.id] == "queued"


# --- settings contract -------------------------------------------------------


def test_config_explain_names_owner_source_and_value(voice_app: DaemonApp) -> None:
    with running_server(voice_app) as base_url:
        status, body = request_json(
            "GET", f"{base_url}/settings/explain/voice.output_gain"
        )

    assert status == 200
    assert set(body) >= {"key", "value", "owner", "source", "revision", "consumers"}
    assert body["key"] == "voice.output_gain"
    assert body["value"] == 1.0
    assert body["owner"] == "installation"
    assert body["consumers"] == ["VoiceResolver"]
    assert body["revision"]


def test_config_explain_unknown_key_is_404(voice_app: DaemonApp) -> None:
    with running_server(voice_app) as base_url:
        status, body = request_json(
            "GET", f"{base_url}/settings/explain/voice.no_such_key"
        )

    assert status == 404
    assert "voice.no_such_key" in str(body["error"])


def test_put_settings_updates_registered_installation_key(
    voice_app: DaemonApp,
) -> None:
    with running_server(voice_app) as base_url:
        put_status, put_body = request_json(
            "PUT", f"{base_url}/settings/voice.hook_enabled", {"value": False}
        )
        explain_status, explain_body = request_json(
            "GET", f"{base_url}/settings/explain/voice.hook_enabled"
        )

    assert put_status == 200
    assert put_body["key"] == "voice.hook_enabled"
    assert put_body["value"] is False
    assert explain_status == 200
    assert explain_body["value"] is False


def test_put_settings_rejects_dead_key_without_write(voice_app: DaemonApp) -> None:
    source_before = Path(voice_app.config.source_path).read_bytes()

    with running_server(voice_app) as base_url:
        status, body = request_json(
            "PUT", f"{base_url}/settings/jarvis_speed", {"value": 1.2}
        )

    assert status == 400
    assert "jarvis_speed" in str(body["error"])
    assert Path(voice_app.config.source_path).read_bytes() == source_before
