from __future__ import annotations

import json
from pathlib import Path

from dan.daemon.app import DaemonApp, create_daemon_app
from tests.test_api_smoke import (
    request_json,
    rewrite_voice_section,
    running_server,
    write_config,
)


VOICE_TOML = """
enabled = true
speak_responses = true
broker_enabled = true
default_tts = "mock"
default_stt = "mock"
ptt_mode = "hold"
queue_persisted = true
recorder = "mock"
"""


def make_voice_app_without_cancellation(tmp_path: Path) -> DaemonApp:
    config_path = write_config(tmp_path / "dan.toml", tmp_path / "home" / "dan.db")
    rewrite_voice_section(config_path, VOICE_TOML)
    app = create_daemon_app(config_path)
    app.start()
    app.voice_cancellation = None
    return app


def test_voice_runtime_reports_local_cancellation_unavailable_without_coordinator(
    tmp_path: Path,
) -> None:
    app = make_voice_app_without_cancellation(tmp_path)
    try:
        assert app.voice_cancellation is None
        with running_server(app) as base_url:
            status, payload = request_json("GET", f"{base_url}/voice/runtime")
    finally:
        app.close()

    assert status == 200
    groups = payload["voice_runtime"]["groups"]
    assert groups["tts_voice_model"]["effective"]["publisher_mode"] == "local"
    assert groups["playback"]["effective"]["publisher_mode"] == "local"
    assert groups["queue_barge_in"]["effective"]["publisher_mode"] == "local"
    assert (
        groups["playback"]["effective"]["interrupt_policy"]
        == "local_cancellation_unavailable"
    )
    assert (
        groups["queue_barge_in"]["effective"]["interrupt_policy"]
        == "local_cancellation_unavailable"
    )
    assert groups["queue_barge_in"]["effective"]["cancel_supported"] is False
    serialized = json.dumps(payload)
    assert "external_shared" not in serialized
    assert "External shared playback" not in serialized


def test_runtime_settings_describes_missing_local_cancellation_without_shared_playback(
    tmp_path: Path,
) -> None:
    app = make_voice_app_without_cancellation(tmp_path)
    try:
        assert app.voice_cancellation is None
        with running_server(app) as base_url:
            status, payload = request_json("GET", f"{base_url}/runtime/settings")
    finally:
        app.close()

    assert status == 200
    capabilities = payload["capability_graph"]["voice_capabilities"]
    assert capabilities["publisher_mode"] == "local"
    assert capabilities["cancellation_support"] is False
    assert capabilities["interrupt_policy"] == "local_cancellation_unavailable"

    preview = payload["settings_preview"]["sections"]
    assert (
        preview["endpointing_ptt"]["fields"]["interrupt_policy"]["current"]
        == "local_cancellation_unavailable"
    )
    queue_status = preview["queue_barge_in"]["fields"]["queue_status"]["current"]
    assert queue_status["publisher_mode"] == "local"
    assert queue_status["interrupt_policy"] == "local_cancellation_unavailable"

    warning = next(
        item
        for item in payload["compatibility_warnings"]
        if item["id"] == "barge_in_cancel_unavailable"
    )
    warning_text = " ".join(
        (warning["message"], warning["reason"], warning["suggested_action"])
    ).lower()
    assert "local cancellation" in warning_text
    assert "shared" not in warning_text
    assert "external" not in warning_text
