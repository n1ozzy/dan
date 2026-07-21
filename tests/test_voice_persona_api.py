"""Panel persona-voice contract: GET /voice/personas + POST /voice/personas/apply.

The panel edits ``config/voice/personas.toml`` through the daemon
(``dan.voice.persona_editor``) and hot-swaps the resolver in-process
(``DaemonApp.reload_voice_catalog``) instead of requiring a launchd restart.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from dan.daemon.app import DaemonApp
from dan.voice.service import VoiceService

from tests.test_api_smoke import request_json, running_server
from tests.test_voice_api_contract import make_voice_app
from tests.voice_helpers import render_snapshot, speech_intent


PERSONAS_SAMPLE = """# panel edit fixture

[dan]
engine = "supertonic"
voice = "M3"
mastering = "raw"
speed = 1.28
dsp = "none"

[danusia]
engine = "supertonic"
voice = "F4"
mastering = "clean"
speed = 1.28
dsp = "none"

[komentator]
engine = "supertonic"
voice = "M2M1"
mastering = "raw"
speed = 1.45
dsp = "none"
"""


@pytest.fixture()
def catalog_dir(tmp_path: Path) -> Path:
    catalog = tmp_path / "voice-catalog"
    catalog.mkdir()
    (catalog / "personas.toml").write_text(PERSONAS_SAMPLE, encoding="utf-8")
    return catalog


@pytest.fixture()
def persona_app(tmp_path: Path, catalog_dir: Path) -> Any:
    app = make_voice_app(tmp_path)
    app.voice_catalog_dir = catalog_dir
    try:
        yield app
    finally:
        app.close()


def read_catalog(catalog_dir: Path) -> dict:
    text = (catalog_dir / "personas.toml").read_text(encoding="utf-8")
    return tomllib.loads(text)


def read_catalog_raw(catalog_dir: Path) -> str:
    return (catalog_dir / "personas.toml").read_text(encoding="utf-8")


class RecordingQueue:
    def __init__(self) -> None:
        self.rows: list[Any] = []

    def enqueue(self, intent: Any, snapshot: Any) -> Any:
        row = SimpleNamespace(intent=intent, snapshot=snapshot)
        self.rows.append(row)
        return row


class TestReplaceResolver:
    def test_swaps_resolution(self) -> None:
        service = VoiceService(
            RecordingQueue(),
            SimpleNamespace(resolve=lambda intent: render_snapshot()),
        )
        marker = render_snapshot(voice_or_style="tests/marker-voice")
        service.replace_resolver(SimpleNamespace(resolve=lambda intent: marker))
        submitted = service.submit(speech_intent("cześć"))
        assert submitted.snapshot is marker

    def test_rejects_resolver_without_resolve(self) -> None:
        service = VoiceService(
            RecordingQueue(),
            SimpleNamespace(resolve=lambda intent: render_snapshot()),
        )
        with pytest.raises(TypeError):
            service.replace_resolver(None)
        with pytest.raises(TypeError):
            service.replace_resolver(SimpleNamespace())


class TestReloadVoiceCatalog:
    def test_reload_swaps_resolver_and_service(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app = make_voice_app(tmp_path)
        try:
            sentinel = SimpleNamespace(resolve=lambda intent: render_snapshot())
            monkeypatch.setattr(
                "dan.voice.service.build_voice_resolver",
                lambda config, **kwargs: sentinel,
            )
            app.reload_voice_catalog()
            assert app.voice_resolver is sentinel
            assert app.voice_service._resolver is sentinel
        finally:
            app.close()

    def test_reload_passes_voice_catalog_dir_override(
        self,
        tmp_path: Path,
        catalog_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        app = make_voice_app(tmp_path)
        try:
            app.voice_catalog_dir = catalog_dir
            captured: list[dict[str, Any]] = []

            def fake_build(config: Any, **kwargs: Any) -> Any:
                captured.append(kwargs)
                return SimpleNamespace(resolve=lambda intent: render_snapshot())

            monkeypatch.setattr("dan.voice.service.build_voice_resolver", fake_build)
            app.reload_voice_catalog()
            assert captured and captured[0].get("voice_root") == catalog_dir
        finally:
            app.close()


class TestGetVoicePersonas:
    def test_lists_personas_and_allowed_values(self, persona_app: DaemonApp) -> None:
        with running_server(persona_app) as base_url:
            status, body = request_json("GET", f"{base_url}/voice/personas")

        assert status == 200
        personas = body["personas"]
        assert set(personas) == {"dan", "danusia", "komentator"}
        assert personas["dan"]["voice"] == "M3"
        assert personas["danusia"]["mastering"] == "clean"

        allowed = body["allowed_voices"]
        for builtin in ("M1", "M3", "F4"):
            assert builtin in allowed
        assert "M2M1" in allowed
        assert allowed == sorted(allowed)

        assert body["allowed_mastering"] == [
            "bastard",
            "clean",
            "gritty",
            "raport",
            "raw",
        ]
        assert body["speed_min"] == pytest.approx(0.5)
        assert body["speed_max"] == pytest.approx(2.0)


class TestApplyPersonaVoice:
    @staticmethod
    def _reload_counter(app: DaemonApp) -> list[int]:
        calls: list[int] = []
        app.reload_voice_catalog = lambda: calls.append(1)
        return calls

    def test_apply_writes_file_reloads_and_reports_changes(
        self, persona_app: DaemonApp, catalog_dir: Path
    ) -> None:
        calls = self._reload_counter(persona_app)
        with running_server(persona_app) as base_url:
            status, body = request_json(
                "POST",
                f"{base_url}/voice/personas/apply",
                {"persona": "dan", "voice": "M1", "speed": 1.1},
            )

        assert status == 200
        assert body["ok"] is True
        assert body["persona"] == "dan"
        assert body["changes"]["voice"] == ["M3", "M1"]

        data = read_catalog(catalog_dir)
        assert data["dan"]["voice"] == "M1"
        assert data["dan"]["speed"] == pytest.approx(1.1)
        assert calls == [1]

    def test_unknown_persona_is_400_and_skips_reload(
        self, persona_app: DaemonApp, catalog_dir: Path
    ) -> None:
        calls = self._reload_counter(persona_app)
        before = read_catalog_raw(catalog_dir)
        with running_server(persona_app) as base_url:
            status, _body = request_json(
                "POST",
                f"{base_url}/voice/personas/apply",
                {"persona": "nikt_taki", "voice": "M1"},
            )
        assert status == 400
        assert read_catalog_raw(catalog_dir) == before
        assert calls == []

    def test_voice_outside_allowed_is_400(
        self, persona_app: DaemonApp, catalog_dir: Path
    ) -> None:
        before = read_catalog_raw(catalog_dir)
        with running_server(persona_app) as base_url:
            status, _body = request_json(
                "POST",
                f"{base_url}/voice/personas/apply",
                {"persona": "dan", "voice": "X9"},
            )
        assert status == 400
        assert read_catalog_raw(catalog_dir) == before

    def test_missing_fields_is_400(self, persona_app: DaemonApp) -> None:
        with running_server(persona_app) as base_url:
            status, _body = request_json(
                "POST",
                f"{base_url}/voice/personas/apply",
                {"persona": "dan"},
            )
        assert status == 400

    def test_reload_failure_rolls_back_file_and_returns_500(
        self, persona_app: DaemonApp, catalog_dir: Path
    ) -> None:
        before = read_catalog_raw(catalog_dir)

        def boom() -> None:
            raise RuntimeError("kaboom")

        persona_app.reload_voice_catalog = boom
        with running_server(persona_app) as base_url:
            status, body = request_json(
                "POST",
                f"{base_url}/voice/personas/apply",
                {"persona": "dan", "voice": "M1"},
            )
        assert status == 500
        assert body["status"] == 500
        assert read_catalog_raw(catalog_dir) == before
