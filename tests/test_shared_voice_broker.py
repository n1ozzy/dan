"""Shared broker transport tests; every filesystem write stays under tmp_path."""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path

import pytest

from dan.config import VoiceConfig
from dan.voice.models import SnapshotValidationError
from dan.voice.resolver import AssetMetadata, EngineMetadata, VoiceCatalog, VoiceResolver


def _shared_broker_module():
    try:
        return importlib.import_module("dan.voice.shared_broker")
    except ModuleNotFoundError:
        pytest.fail("shared broker transport is not implemented")


def _strict_resolver(tmp_path: Path, *, persona: str = "dan") -> VoiceResolver:
    voice_dir = tmp_path / f"voice-{persona}"
    voice_dir.mkdir()
    (voice_dir / "personas.toml").write_text(
        f'[{persona}]\nengine = "supertonic"\nvoice = "M3"\n'
        'speed = 1.35\nmastering = "clean"\ndsp = "none"\n',
        encoding="utf-8",
    )
    (voice_dir / "pronunciations.toml").write_text(
        'runtime = "rantajm"\n', encoding="utf-8"
    )
    model = voice_dir / "model.onnx"
    model.write_bytes(b"verified-model")
    return VoiceResolver(
        VoiceCatalog.from_directory(voice_dir),
        {"voice": {"output_gain": 1.0}},
        {
            "supertonic": EngineMetadata(
                version="1.3.1", assets={"model": AssetMetadata.from_path(model)}
            )
        },
    )


def test_client_requires_caller_supplied_resolver_before_publish(tmp_path: Path) -> None:
    module = _shared_broker_module()

    with pytest.raises(module.SharedBrokerError, match="VoiceResolver"):
        module.SharedBrokerClient(VoiceConfig(), request_dir=tmp_path / "req")

    assert not (tmp_path / "req").exists()


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
        persona_voices={"dan": "M3"},
        persona_speeds={"dan": 1.35},
        persona_mastering={"dan": "clean"},
    )
    client = module.SharedBrokerClient(
        config,
        resolver=_strict_resolver(tmp_path),
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
            persona_voices={"dan": "M3"},
            persona_speeds={"dan": 1.35},
            persona_mastering={"dan": "clean"},
        ),
        resolver=_strict_resolver(tmp_path),
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

    # DAN's shared broker orders equal-priority requests by file mtime. DAN
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


def test_client_delegates_to_resolver_and_resolution_failure_prevents_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _shared_broker_module()
    resolver = _strict_resolver(tmp_path, persona="someone-else")
    calls = 0
    original = VoiceResolver.resolve

    def recording_resolve(self: VoiceResolver, intent):
        nonlocal calls
        calls += 1
        return original(self, intent)

    monkeypatch.setattr(VoiceResolver, "resolve", recording_resolve)
    request_dir = tmp_path / "req"
    client = module.SharedBrokerClient(
        VoiceConfig(), resolver=resolver, request_dir=request_dir, persona="dan"
    )

    with pytest.raises(SnapshotValidationError, match="unknown voice persona"):
        client.enqueue(text="Nie publikuj.", session="s1")

    assert calls == 1
    assert not request_dir.exists()
