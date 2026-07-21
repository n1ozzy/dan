from __future__ import annotations

import dataclasses
import hashlib
import json
import unicodedata
from pathlib import Path

import pytest

from dan.config_registry import ConfigStore
from dan.voice.models import IntentValidationError, SpeechIntent
from dan.voice.resolver import (
    AssetMetadata,
    EngineMetadata,
    SnapshotValidationError,
    VoiceCatalog,
    VoiceResolver,
)


@pytest.fixture
def catalog(tmp_path: Path) -> VoiceCatalog:
    voice_dir = tmp_path / "voice"
    voice_dir.mkdir()
    (voice_dir / "personas.toml").write_text(
        '[dan]\nengine = "supertonic"\nvoice = "M3"\nmastering = "raw"\n'
        'speed = 1.25\ndsp = "none"\n',
        encoding="utf-8",
    )
    (voice_dir / "pronunciations.toml").write_text(
        'runtime = "rantajm"\n', encoding="utf-8"
    )
    return VoiceCatalog.from_directory(voice_dir)


@pytest.fixture
def installation_config(tmp_path: Path) -> ConfigStore:
    path = tmp_path / "config.toml"
    path.write_text('[voice]\noutput_gain = 0.92\n', encoding="utf-8")
    return ConfigStore(path)


@pytest.fixture
def engines(tmp_path: Path) -> dict[str, EngineMetadata]:
    model = tmp_path / "supertonic.onnx"
    model.write_bytes(b"fake-supertonic-model")
    return {
        "supertonic": EngineMetadata(
            version="1.3.1", assets={"model": AssetMetadata.from_path(model)}
        )
    }


def speech_intent(persona: str = "dan") -> SpeechIntent:
    return SpeechIntent(
        text="Zażółć gęślą jaźń.",
        persona=persona,
        source="codex",
        session="smoke",
        participant="dan",
        priority=0,
        lane="live",
        interrupt_policy="interruptible",
        utterance_index=0,
    )


def test_resolver_creates_complete_snapshot_once(
    catalog: VoiceCatalog,
    installation_config: ConfigStore,
    engines: dict[str, EngineMetadata],
) -> None:
    snapshot = VoiceResolver(catalog, installation_config, engines).resolve(speech_intent())

    assert snapshot.engine == "supertonic"
    assert snapshot.engine_version and snapshot.voice_or_style == "M3"
    assert snapshot.speed == 1.25 and snapshot.mastering_profile == "raw"
    assert snapshot.dsp == "none" and snapshot.pronunciations["runtime"] == "rantajm"
    assert snapshot.pronunciations_sha256
    assert snapshot.gain == 0.92 and snapshot.asset_sha256 and snapshot.config_revision
    snapshot.validate_complete()


def test_intent_cannot_override_resolver_fields() -> None:
    with pytest.raises(IntentValidationError, match="voice"):
        SpeechIntent.from_mapping(
            {"text": "Nie oszukuj.", "persona": "dan", "voice": "M1"},
            source="hook",
            session="s1",
        )


def test_intent_normalizes_text_to_utf8_nfc() -> None:
    decomposed = unicodedata.normalize("NFD", "gęślą")

    intent = SpeechIntent.from_mapping(
        {"text": decomposed, "persona": "dan"}, source="hook", session="s1"
    )

    assert intent.text == "gęślą"
    assert intent.text.encode("utf-8").decode("utf-8") == intent.text


def test_snapshot_is_frozen_and_canonical_json_is_stable(
    catalog: VoiceCatalog,
    installation_config: ConfigStore,
    engines: dict[str, EngineMetadata],
) -> None:
    snapshot = VoiceResolver(catalog, installation_config, engines).resolve(speech_intent())

    with pytest.raises(dataclasses.FrozenInstanceError):
        snapshot.gain = 2.0  # type: ignore[misc]
    payload = json.loads(snapshot.canonical_json())
    assert payload["asset_sha256"] == dict(sorted(snapshot.asset_sha256.items()))


def test_resolver_rejects_asset_hash_mismatch(
    catalog: VoiceCatalog,
    installation_config: ConfigStore,
    tmp_path: Path,
) -> None:
    model = tmp_path / "changed.onnx"
    model.write_bytes(b"changed")
    engines = {
        "supertonic": EngineMetadata(
            version="1.3.1",
            assets={"model": AssetMetadata(path=model, sha256=hashlib.sha256(b"other").hexdigest())},
        )
    }

    with pytest.raises(SnapshotValidationError, match="SHA-256"):
        VoiceResolver(catalog, installation_config, engines).resolve(speech_intent())


@pytest.mark.parametrize("missing_field", ["engine", "voice", "speed", "mastering", "dsp"])
def test_resolver_requires_every_persona_render_field(
    tmp_path: Path,
    installation_config: ConfigStore,
    engines: dict[str, EngineMetadata],
    missing_field: str,
) -> None:
    fields = {
        "engine": 'engine = "supertonic"',
        "voice": 'voice = "M3"',
        "speed": "speed = 1.25",
        "mastering": 'mastering = "raw"',
        "dsp": 'dsp = "none"',
    }
    voice_dir = tmp_path / "strict-voice"
    voice_dir.mkdir()
    persona_lines = [line for name, line in fields.items() if name != missing_field]
    (voice_dir / "personas.toml").write_text(
        "[dan]\n" + "\n".join(persona_lines) + "\n",
        encoding="utf-8",
    )
    (voice_dir / "pronunciations.toml").write_text(
        'runtime = "rantajm"\n', encoding="utf-8"
    )

    with pytest.raises(SnapshotValidationError, match=missing_field):
        VoiceResolver(
            VoiceCatalog.from_directory(voice_dir), installation_config, engines
        ).resolve(speech_intent())


def test_resolver_reverifies_every_catalog_asset_before_snapshot(
    catalog: VoiceCatalog,
    installation_config: ConfigStore,
    engines: dict[str, EngineMetadata],
) -> None:
    personas_path = next(
        asset.path
        for name, asset in catalog.assets.items()
        if name == "voice.personas"
    )
    personas_path.write_text("[dan]\nvoice = \"M1\"\n", encoding="utf-8")

    with pytest.raises(SnapshotValidationError, match="voice.personas"):
        VoiceResolver(catalog, installation_config, engines).resolve(speech_intent())


def test_resolver_freezes_custom_style_as_verified_absolute_path(
    tmp_path: Path,
    installation_config: ConfigStore,
) -> None:
    voice_dir = tmp_path / "custom-voice"
    voice_dir.mkdir()
    (voice_dir / "personas.toml").write_text(
        '[dan]\nengine = "supertonic"\nvoice = "M2M1"\n'
        'mastering = "raw"\nspeed = 1.25\ndsp = "none"\n',
        encoding="utf-8",
    )
    (voice_dir / "pronunciations.toml").write_text("", encoding="utf-8")
    style = tmp_path / "M2M1.json"
    style.write_text('{"style": []}\n', encoding="utf-8")
    resolver = VoiceResolver(
        VoiceCatalog.from_directory(voice_dir),
        installation_config,
        {
            "supertonic": EngineMetadata(
                version="1.3.1",
                assets={"voice:M2M1": AssetMetadata.from_path(style)},
            )
        },
    )

    snapshot = resolver.resolve(speech_intent())

    assert snapshot.voice_or_style == str(style.resolve())
    assert snapshot.asset_sha256["engine.supertonic.voice:M2M1"] == hashlib.sha256(
        style.read_bytes()
    ).hexdigest()


def test_catalog_and_snapshot_mappings_are_immutable(
    catalog: VoiceCatalog,
    installation_config: ConfigStore,
    engines: dict[str, EngineMetadata],
) -> None:
    snapshot = VoiceResolver(catalog, installation_config, engines).resolve(speech_intent())

    with pytest.raises(TypeError):
        catalog.personas["dan"] = {}  # type: ignore[index]
    with pytest.raises(TypeError):
        snapshot.pronunciations["runtime"] = "changed"  # type: ignore[index]


class TestCatalogHashMatchesParsedBytes:
    """Content and hash must come from one read of personas.toml.

    Reading the file twice let a write land between the two reads: the catalog
    then carried routes from one revision and the frozen SHA of another, so the
    integrity check passed forever while the daemon served stale routes.
    """

    def test_write_between_reads_cannot_desync_content_and_hash(
        self, tmp_path, monkeypatch
    ) -> None:
        import hashlib
        import pathlib

        from dan.voice.resolver import VoiceCatalog

        first = '[dan]\nengine = "supertonic"\nvoice = "M3"\n'
        second = '[dan]\nengine = "supertonic"\nvoice = "M1"\n'
        personas = tmp_path / "personas.toml"
        personas.write_text(first, encoding="utf-8")
        (tmp_path / "pronunciations.toml").write_text("", encoding="utf-8")

        real_read_bytes = pathlib.Path.read_bytes

        def patched_read_bytes(self):  # type: ignore[no-untyped-def]
            # A concurrent writer landing right before the hashing read.
            if self == personas:
                personas.write_text(second, encoding="utf-8")
            return real_read_bytes(self)

        monkeypatch.setattr(pathlib.Path, "read_bytes", patched_read_bytes)
        catalog = VoiceCatalog.from_directory(tmp_path)

        parsed_voice = catalog.personas["dan"]["voice"]
        expected_source = first if parsed_voice == "M3" else second
        assert catalog.asset_sha256["voice.personas"] == hashlib.sha256(
            expected_source.encode("utf-8")
        ).hexdigest()
