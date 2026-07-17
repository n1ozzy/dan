from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from dan.cli import main
from dan.config import load_config
from dan.config_registry import (
    IMPORTED_CONFIG_KEYS,
    REGISTRY,
    REJECTED_KEYS,
    ConfigOwner,
    ConfigStore,
    ConfigWriteRejected,
    discovered_runtime_config_keys,
)
from dan.voice.resolver import AssetMetadata, EngineMetadata, VoiceCatalog, VoiceResolver


def _voice_fixture(tmp_path: Path) -> tuple[VoiceCatalog, dict[str, EngineMetadata]]:
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
    model = voice_dir / "supertonic.onnx"
    model.write_bytes(b"fake-supertonic-model")
    asset = AssetMetadata.from_path(model)
    return (
        VoiceCatalog.from_directory(voice_dir),
        {"supertonic": EngineMetadata(version="1.3.1", assets={"model": asset})},
    )


def test_every_runtime_config_field_is_registered() -> None:
    assert discovered_runtime_config_keys() == set(REGISTRY)


def test_every_imported_key_has_an_explicit_registry_decision() -> None:
    assert "voice.jarvis_speed" in IMPORTED_CONFIG_KEYS
    assert IMPORTED_CONFIG_KEYS <= set(REGISTRY) | set(REJECTED_KEYS)


@pytest.mark.parametrize("key", ["jarvis_speed", "voice.unknown", "persona.dan.voice"])
def test_config_rejects_dead_unknown_or_versioned_key_without_write(
    key: str, tmp_path: Path
) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[voice]\noutput_gain = 1.0\n', encoding="utf-8")
    store = ConfigStore(path)
    before = path.read_bytes()

    with pytest.raises(ConfigWriteRejected):
        store.set(key, "M2")

    assert path.read_bytes() == before


def test_config_rejects_batch_before_any_write(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[voice]\noutput_gain = 1.0\n', encoding="utf-8")
    store = ConfigStore(path)
    before = path.read_bytes()

    with pytest.raises(ConfigWriteRejected):
        store.set_many({"voice.output_gain": 0.92, "voice.supertonic_voice": "M2"})

    assert path.read_bytes() == before


def test_atomic_config_write_is_owner_only_and_fsynced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[voice]\noutput_gain = 1.0\n', encoding="utf-8")
    events: list[str] = []
    real_fsync = os.fsync
    real_replace = os.replace

    def tracking_fsync(fd: int) -> None:
        mode = os.fstat(fd).st_mode
        events.append("fsync_dir" if stat.S_ISDIR(mode) else "fsync_file")
        real_fsync(fd)

    def tracking_replace(source: str | Path, destination: str | Path) -> None:
        events.append("replace")
        real_replace(source, destination)

    monkeypatch.setattr(os, "fsync", tracking_fsync)
    monkeypatch.setattr(os, "replace", tracking_replace)

    ConfigStore(path).set("voice.output_gain", 0.92)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert events == ["fsync_file", "replace", "fsync_dir"]
    assert not list(tmp_path.glob(".config.toml.*.tmp"))


def test_set_restart_explain_resolve_uses_one_value(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    source = Path(__file__).resolve().parents[1] / "config" / "dan.example.toml"
    path.write_bytes(source.read_bytes())
    ConfigStore(path).set("voice.output_gain", 0.92)
    restarted = ConfigStore(path)
    restarted_runtime = load_config(path)
    catalog, engines = _voice_fixture(tmp_path)

    explained = restarted.explain("voice.output_gain")
    snapshot = VoiceResolver(catalog, restarted, engines).resolve_mapping(
        {
            "text": "Test.",
            "persona": "dan",
            "source": "test",
            "session": "s1",
            "participant": "dan",
            "priority": 0,
            "lane": "normal",
            "interrupt_policy": "finish_current",
            "utterance_index": 0,
        }
    )

    assert explained.value == 0.92
    assert restarted_runtime.voice.output_gain == 0.92
    assert explained.owner is ConfigOwner.INSTALLATION
    assert explained.source_file == path
    assert explained.revision
    assert explained.consumers == ("VoiceResolver",)
    assert snapshot.gain == 0.92


def test_config_explain_cli_emits_the_registry_explanation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = Path(__file__).resolve().parents[1] / "config" / "dan.example.toml"
    config_path = tmp_path / "dan.toml"
    config_path.write_bytes(source.read_bytes())

    assert main(["--config", str(config_path), "config", "explain", "voice.output_gain"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["key"] == "voice.output_gain"
    assert payload["owner"] == "installation"
    assert payload["source_file"] == str(config_path)
    assert payload["revision"]
    assert payload["consumers"] == ["VoiceResolver"]
