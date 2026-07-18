from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_obsolete_shared_voice_modules_are_removed() -> None:
    assert importlib.util.find_spec("dan.voice.shared_broker") is None
    assert importlib.util.find_spec("dan.voice.shared_voice") is None


def test_voice_service_is_the_only_runtime_resolver_caller() -> None:
    callers: list[str] = []
    for path in sorted((ROOT / "dan").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if "._resolver.resolve(" in text:
            callers.append(str(path.relative_to(ROOT)))

    assert callers == ["dan/voice/service.py"]


def test_broker_is_the_only_audio_player_caller() -> None:
    callers: list[str] = []
    for path in sorted((ROOT / "dan").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if "._player.play(" in text:
            callers.append(str(path.relative_to(ROOT)))

    assert callers == ["dan/voice/broker.py"]
