"""Read-only panel contract for the canonical DAN/Danusia voice catalog."""

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


PERSONAS_SAMPLE = """# canonical read-only fixture

[dan]
engine = "supertonic"
voice = "M3"
mastering = "default"
speed = 1.0
seed = 1
dsp = "none"

[danusia]
engine = "supertonic"
voice = "F4"
mastering = "default"
speed = 1.0
seed = 1
dsp = "none"
"""


@pytest.fixture()
def catalog_dir(tmp_path: Path) -> Path:
    catalog = tmp_path / "voice-catalog"
    catalog.mkdir()
    (catalog / "personas.toml").write_text(PERSONAS_SAMPLE, encoding="utf-8")
    (catalog / "pronunciations.toml").write_text(
        'DAN = "Dan"\n',
        encoding="utf-8",
    )
    (catalog / "gains.json").write_text(
        '{"F4|default": 7.97, "M3|default": 8.98}\n',
        encoding="utf-8",
    )
    return catalog


@pytest.fixture()
def persona_app(tmp_path: Path, catalog_dir: Path) -> Any:
    app = make_voice_app(tmp_path)
    app.voice_catalog_dir = catalog_dir
    try:
        yield app
    finally:
        app.close()


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


def test_get_voice_personas_is_read_only_runtime_truth(
    persona_app: DaemonApp,
) -> None:
    with running_server(persona_app) as base_url:
        status, body = request_json("GET", f"{base_url}/voice/personas")

    assert status == 200
    assert set(body) == {"personas", "source"}
    assert body["source"] == "config/voice/personas.toml"
    assert body["personas"] == tomllib.loads(PERSONAS_SAMPLE)


@pytest.mark.parametrize(
    "payload",
    (
        {"persona": "dan", "voice": "M1"},
        {"persona": "danusia", "voice": "F2"},
        {"persona": "dan", "speed": 1.1},
        {"persona": "danusia", "mastering": "raw"},
    ),
)
def test_runtime_has_no_persona_catalog_mutation_route(
    persona_app: DaemonApp,
    catalog_dir: Path,
    payload: dict[str, object],
) -> None:
    before = read_catalog_raw(catalog_dir)
    with running_server(persona_app) as base_url:
        status, _body = request_json(
            "POST",
            f"{base_url}/voice/personas/apply",
            payload,
        )

    assert status == 404
    assert read_catalog_raw(catalog_dir) == before
