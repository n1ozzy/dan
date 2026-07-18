from __future__ import annotations

import time
from pathlib import Path

from dan.daemon.app import create_daemon_app
from dan.voice.models import RenderSnapshot
from dan.voice.speech import SpeechPipeline
from tests.test_api_smoke import rewrite_voice_section, write_config


class Resolver:
    def __init__(self) -> None:
        self.calls = []

    def resolve(self, intent):
        self.calls.append(intent)
        return RenderSnapshot(
            engine="mock",
            engine_version="1",
            voice_or_style="M3",
            speed=1.25,
            mastering_profile="raw",
            dsp="none",
            pronunciations={},
            pronunciations_sha256="a" * 64,
            gain=1.0,
            asset_sha256={"mock": "b" * 64},
            config_revision="voice-catalog-v1",
        )


def native_voice_config(tmp_path: Path) -> Path:
    return rewrite_voice_section(
        write_config(tmp_path / "dan.toml", tmp_path / "home" / "dan.db"),
        "\n".join(
            (
                "enabled = true",
                "speak_responses = true",
                "broker_enabled = true",
                "default_tts = 'mock'",
                "default_stt = 'mock'",
                "recorder = 'mock'",
                "ptt_mode = 'hold'",
                "queue_persisted = true",
            )
        )
        + "\n",
    )


def test_daemon_owns_one_service_broker_engine_and_player(tmp_path: Path) -> None:
    resolver = Resolver()
    app = create_daemon_app(native_voice_config(tmp_path), voice_resolver=resolver)
    try:
        app.start()

        assert app.voice_service is not None
        assert app.voice_broker is not None
        assert app.voice_player is not None
        assert app.voice_broker._player is app.voice_player
        assert app.voice_broker._engine is app.voice_engine
        assert not hasattr(app.voice_engine, "play")
    finally:
        app.close()


def test_speech_pipeline_submits_snapshot_and_native_broker_confirms_playback(
    tmp_path: Path,
) -> None:
    resolver = Resolver()
    app = create_daemon_app(native_voice_config(tmp_path), voice_resolver=resolver)
    try:
        app.start()
        pipeline = SpeechPipeline(
            app._connect_existing,
            config=app.config.voice,
            voice_service=app.voice_service,
        )

        assert pipeline.speak_text(turn_id="turn-native", text="Jedyny natywny tor.") == 1

        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            row = app.conn.execute(
                """
                SELECT status, playback_confirmed, render_snapshot_json
                FROM voice_queue WHERE session_id = 'turn-native'
                """
            ).fetchone()
            if row is not None and row[0] == "done":
                break
            time.sleep(0.01)
        assert row is not None
        assert row[0:2] == ("done", 1)
        assert '"voice_or_style":"M3"' in row[2]
        assert len(resolver.calls) == 1
    finally:
        app.close()
