"""Shared broker transport tests; every filesystem write stays under tmp_path."""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path

import pytest

from jarvis.config import VoiceConfig


def _shared_broker_module():
    try:
        return importlib.import_module("jarvis.voice.shared_broker")
    except ModuleNotFoundError:
        pytest.fail("shared broker transport is not implemented")


def test_client_writes_exact_dan_request_atomically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _shared_broker_module()
    request_dir = tmp_path / "req"
    request_dir.mkdir()
    config = VoiceConfig(
        broker_enabled=True,
        default_tts="supertonic",
        supertonic_lang="pl",
        persona_voices={"jarvis": "M3"},
        persona_speeds={"jarvis": 1.35},
        persona_mastering={"jarvis": "clean"},
    )
    client = module.SharedBrokerClient(
        config,
        request_dir=request_dir,
        clock=lambda: 1_720_000_000.125,
        pid=lambda: 1234,
    )
    replacements: list[tuple[Path, Path]] = []
    real_replace = os.replace

    def record_replace(source, destination) -> None:
        replacements.append((Path(source), Path(destination)))
        real_replace(source, destination)

    monkeypatch.setattr(module.os, "replace", record_replace)

    path = client.enqueue(
        text="Jedna kompletna wypowiedź. Z naturalnym rytmem.",
        session="turn-abcdef",
        lane="commentary",
    )

    assert replacements == [(Path(f"{path}.tmp"), path)]
    assert list(request_dir.glob("*.tmp")) == []
    assert list(request_dir.glob("*.json")) == [path]
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "text": "Jedna kompletna wypowiedź. Z naturalnym rytmem.",
        "engine": "supertonic",
        "session": "turn-abc",
        "voice": "M3",
        "speed": 1.35,
        "priority": 0,
        "profile": "clean",
        "language": "pl",
        "lane": "commentary",
    }


def test_multiple_response_lanes_never_overwrite_each_other_at_the_same_tick(
    tmp_path: Path,
) -> None:
    module = _shared_broker_module()
    request_dir = tmp_path / "req"
    nonces = iter(("commentary-one", "commentary-two", "final"))
    client = module.SharedBrokerClient(
        VoiceConfig(
            broker_enabled=True,
            default_tts="supertonic",
            persona_voices={"jarvis": "M3"},
            persona_speeds={"jarvis": 1.35},
            persona_mastering={"jarvis": "clean"},
        ),
        request_dir=request_dir,
        clock=lambda: 1_720_000_000.125,
        pid=lambda: 1234,
        nonce=lambda: next(nonces),
    )

    paths = [
        client.enqueue(text="Komentarz pierwszy.", session="turn-1", lane="commentary"),
        client.enqueue(text="Komentarz drugi.", session="turn-1", lane="commentary"),
        client.enqueue(text="Final głosowy.", session="turn-1", lane="final"),
    ]

    # DAN's shared broker orders equal-priority requests by file mtime. Jarvis
    # must therefore publish strictly increasing mtimes even when its injected
    # wall clock returns the exact same tick for commentary and final.
    base_ns = int(1_720_000_000.125 * 1_000_000_000)
    assert [path.stat().st_mtime_ns for path in paths] == [
        base_ns,
        base_ns + 1,
        base_ns + 2,
    ]
    broker_order = sorted(paths, key=lambda path: (path.stat().st_mtime_ns, path.name))
    requests = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in broker_order
    ]
    assert len(requests) == 3
    assert [request["text"] for request in requests] == [
        "Komentarz pierwszy.",
        "Komentarz drugi.",
        "Final głosowy.",
    ]
