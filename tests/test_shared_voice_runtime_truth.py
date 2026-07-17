"""Truthful shared-broker readiness and activity, without live audio/runtime."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from dan.api.routes_runtime import get_runtime_settings
from dan.api.routes_voice import get_voice_runtime
from dan.config import VoiceConfig
from dan.daemon.app import create_daemon_app
from dan.store.db import close_quietly, initialize_database
from dan.voice.shared_broker import SharedBrokerClient
from dan.voice.speech import SpeechPipeline
from tests.test_api_smoke import rewrite_voice_section, write_config
from tests.test_shared_voice_broker import _strict_resolver


def _shared_voice_config(tmp_path: Path) -> Path:
    return rewrite_voice_section(
        write_config(tmp_path / "dan.toml", tmp_path / "home" / "dan.db"),
        "\n".join(
            (
                "enabled = true",
                "speak_responses = true",
                "broker_enabled = true",
                "default_tts = 'supertonic'",
                "default_stt = 'mock'",
                "recorder = 'mock'",
                "supertonic_binary = '/definitely/missing/local-supertonic'",
                "playback_binary = '/definitely/missing/local-player'",
                "supertonic_voice = 'M3'",
                "supertonic_lang = 'pl'",
                "ptt_mode = 'hold'",
                "queue_persisted = true",
            )
        )
        + "\n",
    )


def test_shared_publisher_is_ready_without_claiming_local_tts_or_player(
    tmp_path: Path,
) -> None:
    resolver = _strict_resolver(tmp_path)
    app = create_daemon_app(
        _shared_voice_config(tmp_path), voice_resolver=resolver
    )
    try:
        app.start()

        activity_client = SharedBrokerClient(
            app.config.voice,
            resolver=resolver,
            request_dir=tmp_path / "activity-req",
            clock=lambda: 1_720_000_000.5,
            pid=lambda: 4321,
            nonce=lambda: "activity",
        )
        activity_pipeline = SpeechPipeline(
            app._connect_existing,
            config=app.config.voice,
            shared_broker=activity_client,
        )
        assert activity_pipeline.speak_text(
            turn_id="turn-activity",
            text="Sprawdzam stan.",
            lane="commentary",
        ) == 1

        voice_runtime = get_voice_runtime(app)["voice_runtime"]
        runtime_settings = get_runtime_settings(app)

        tts = voice_runtime["groups"]["tts_voice_model"]
        playback = voice_runtime["groups"]["playback"]
        queue = voice_runtime["groups"]["queue_barge_in"]
        assert tts["readiness"] == "ok"
        assert tts["effective"]["publisher_mode"] == "external_shared"
        assert playback["readiness"] == "ok"
        assert playback["effective"]["publisher_mode"] == "external_shared"
        assert playback["effective"]["acknowledgement"] == "unavailable"
        assert queue["effective"]["interrupt_policy"] == "uninterruptible"
        assert queue["effective"]["cancel_supported"] is False
        assert not any(
            "playback" in warning.lower() and "unavailable" in warning.lower()
            for warning in voice_runtime["warnings"]
        )

        tts_layer = runtime_settings["voice_tts_voice_model"]
        playback_layer = runtime_settings["voice_playback"]
        queue_layer = runtime_settings["voice_queue_barge_in"]
        assert tts_layer["readiness"]["value"] == "ok"
        assert tts_layer["effective_value"]["value"]["publisher_mode"] == "external_shared"
        assert playback_layer["readiness"]["value"] == "ok"
        assert playback_layer["effective_value"]["value"]["publisher_mode"] == "external_shared"
        activity = playback_layer["effective_value"]["value"]["latest_activity"]
        assert activity == {
            "event_type": "voice.speak.queued",
            "request_id": "1720000000.500000-4321-activity",
            "turn_id": "turn-activity",
            "lane": "commentary",
            "transport": "external_shared_broker",
            "delivery_state": "published",
            "interrupt_policy": "uninterruptible",
            "acknowledgement": "unavailable",
            "cancel_supported": False,
        }
        assert queue_layer["effective_value"]["value"]["interrupt_policy"] == "uninterruptible"

        capabilities = runtime_settings["capability_graph"]["voice_capabilities"]
        assert capabilities["publisher_mode"] == "external_shared"
        assert capabilities["publisher_ready"] is True
        assert capabilities["acknowledgement_support"] is False
        assert capabilities["cancellation_support"] is False
        assert capabilities["interrupt_policy"] == "uninterruptible"

        preview = runtime_settings["settings_preview"]["sections"]["queue_barge_in"]["fields"]
        assert preview["cancel_support"]["current"] is False
        assert preview["manual_cancel_available"]["current"] is False
        assert preview["queue_status"]["current"]["publisher_mode"] == "external_shared"
        assert preview["queue_status"]["current"]["delivery_observation"] == "published_without_ack"

        readiness = runtime_settings["runtime_readiness"]
        assert readiness["tts_provider"]["status"] == "ok"
        assert readiness["playback_command"]["status"] == "ok"
        assert readiness["playback_command"]["value"]["mode"] == "external_shared"
    finally:
        app.close()


def test_shared_publisher_shutdown_never_controls_the_external_broker(
    tmp_path: Path,
) -> None:
    app = create_daemon_app(
        _shared_voice_config(tmp_path), voice_resolver=_strict_resolver(tmp_path)
    )
    control_calls: list[str] = []

    class ExternalBrokerControlTrap:
        def stop(self) -> None:
            control_calls.append("stop")

        def flush(self) -> None:
            control_calls.append("flush")

        def restart(self) -> None:
            control_calls.append("restart")

    try:
        app.start()
        app.voice_publisher = ExternalBrokerControlTrap()

        app.close()

        assert control_calls == []
        assert app.voice_publisher is None
    finally:
        app.close()


def test_shared_publish_emits_truthful_backend_lifecycle_without_fake_ack(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "voice-events.db"
    close_quietly(initialize_database(db_path))
    request_dir = tmp_path / "shared-req"
    nonces = iter(("commentary", "final"))
    config = VoiceConfig(
        enabled=True,
        speak_responses=True,
        broker_enabled=True,
        default_tts="supertonic",
        persona_voices={"dan": "M3"},
        persona_speeds={"dan": 1.35},
        persona_mastering={"dan": "clean"},
    )
    client = SharedBrokerClient(
        config,
        resolver=_strict_resolver(tmp_path),
        request_dir=request_dir,
        clock=lambda: 1_720_000_000.125,
        pid=lambda: 1234,
        nonce=lambda: next(nonces),
    )
    pipeline = SpeechPipeline(
        lambda: sqlite3.connect(db_path),
        config=config,
        shared_broker=client,
    )

    assert pipeline.speak_text(
        turn_id="turn-shared",
        text="Najpierw sprawdzam.",
        lane="commentary",
    ) == 1
    assert pipeline.speak_text(
        turn_id="turn-shared",
        text="Gotowe.",
        lane="final",
    ) == 1

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT type, source, turn_id, payload_json FROM events ORDER BY id"
        ).fetchall()
    finally:
        close_quietly(conn)

    assert [row[0] for row in rows] == ["voice.speak.queued", "voice.speak.queued"]
    assert [row[1] for row in rows] == ["voice.shared_publisher", "voice.shared_publisher"]
    assert [row[2] for row in rows] == ["turn-shared", "turn-shared"]

    payloads = [json.loads(row[3]) for row in rows]
    assert [payload["lane"] for payload in payloads] == ["commentary", "final"]
    for payload in payloads:
        assert payload["transport"] == "external_shared_broker"
        assert payload["delivery_state"] == "published"
        assert payload["interrupt_policy"] == "uninterruptible"
        assert payload["acknowledgement"] == "unavailable"
        assert payload["cancel_supported"] is False

    assert not any(
        row[0] in {"voice.speak.started", "voice.speak.finished", "voice.speak.cancelled"}
        for row in rows
    )
