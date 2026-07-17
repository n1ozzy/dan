"""Compatibility voice loader delegates only to strict resolver truth."""

from __future__ import annotations

import dataclasses
import warnings
from pathlib import Path

import pytest

from dan.config import VoiceConfig
from dan.voice.shared_voice import (
    apply_shared_voices,
    load_personas,
    load_pronunciations,
)
from dan.voice.resolver import (
    AssetMetadata,
    EngineMetadata,
    VoiceCatalog,
    VoiceResolver,
    VoiceResolverError,
)


def _voice_dir(tmp_path, *, personas="", pronunciations=""):
    if personas:
        (tmp_path / "personas.toml").write_text(personas, encoding="utf-8")
    if pronunciations:
        (tmp_path / "pronunciations.toml").write_text(pronunciations, encoding="utf-8")
    return tmp_path


def _resolver(tmp_path: Path) -> VoiceResolver:
    (tmp_path / "voice").mkdir()
    voice_dir = _voice_dir(
        tmp_path / "voice",
        personas=(
            '[dan]\nengine = "supertonic"\nvoice = "M3"\n'
            'mastering = "raw"\nspeed = 1.25\ndsp = "none"\n'
        ),
        pronunciations='runtime = "rantajm"\nbug = "bag"\n',
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


def test_missing_or_broken_catalog_fails_strictly(tmp_path):
    with pytest.raises(VoiceResolverError, match="does not exist"):
        VoiceCatalog.from_directory(tmp_path / "nope")

    _voice_dir(tmp_path, personas="personas = [this is not valid")
    with pytest.raises(VoiceResolverError, match="could not load"):
        VoiceCatalog.from_directory(tmp_path)


def test_compatibility_projection_ignores_local_resolver_owned_overrides(tmp_path):
    cfg = VoiceConfig(
        persona_voices={"dan": "F1"},
        persona_speeds={"dan": 1.1},
        tts_pronunciations={"bug": "ROBAK"},
    )
    out = apply_shared_voices(cfg, resolver=_resolver(tmp_path))

    assert out.persona_voices == {"dan": "M3"}
    assert out.persona_speeds == {"dan": 1.25}
    assert out.persona_mastering == {"dan": "raw"}
    assert out.tts_pronunciations == {"bug": "bag", "runtime": "rantajm"}


def test_loaders_return_dicts(tmp_path):
    _voice_dir(
        tmp_path,
        personas='[dan]\nvoice = "M2"\n',
        pronunciations='runtime = "rantajm"\n',
    )
    assert load_personas(tmp_path)["dan"]["voice"] == "M2"
    assert load_pronunciations(tmp_path) == {"runtime": "rantajm"}


def test_result_is_still_frozen_voiceconfig(tmp_path):
    out = apply_shared_voices(VoiceConfig(), resolver=_resolver(tmp_path))
    with pytest.raises(dataclasses.FrozenInstanceError):
        out.enabled = True  # type: ignore[misc]


def test_compatibility_loader_delegates_to_authoritative_resolver(
    tmp_path, monkeypatch
):
    cfg = VoiceConfig()
    resolver = _resolver(tmp_path)
    calls = []
    original = VoiceResolver.resolve

    def recording_resolve(self, intent):
        calls.append(intent)
        return original(self, intent)

    monkeypatch.setattr(VoiceResolver, "resolve", recording_resolve)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        out = apply_shared_voices(cfg, resolver=resolver)

    assert len(calls) == 1
    assert calls[0].persona == "dan"
    assert out.supertonic_voice == "M3"
    assert any("VoiceResolver" in str(item.message) for item in caught)


def test_compatibility_loader_requires_caller_supplied_resolver() -> None:
    with pytest.raises(VoiceResolverError, match="caller-supplied VoiceResolver"):
        apply_shared_voices(VoiceConfig())
