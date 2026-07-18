from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from dan.voice.assets import load_asset_manifest, load_voice_catalog
from dan.voice.pipelines.chatterbox_v3 import (
    ChatterboxV3ZanetaPipeline,
    PipelineCapabilityError,
    load_pipeline_manifest,
)
from dan.voice.resolver import AssetMetadata, EngineMetadata, VoiceResolver
from dan.voice.tts import build_tts_engine


ROOT = Path(__file__).resolve().parents[1]


def _decision_rows(path: Path) -> list[dict[str, str]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| `"):
            continue
        cells = [cell.strip().strip("`") for cell in line.strip("|").split("|")]
        if len(cells) == 8:
            rows.append(dict(zip(
                ("key", "sources", "reader", "old", "route", "asset", "evidence", "decision"),
                cells,
            )))
    return rows


def test_every_legacy_override_and_persona_has_final_decision() -> None:
    rows = _decision_rows(ROOT / "docs" / "migration" / "VOICE-DECISIONS.md")
    source_keys = {row["key"] for row in rows}
    catalog = load_voice_catalog(ROOT / "config" / "voice")

    assert rows
    assert all(row["decision"].lower() not in {"pending", "tbd", "todo"} for row in rows)
    assert "state/overrides.json:voice.jarvis_supertonic_voice" in source_keys
    assert "state/overrides.json:voice.jarvis_speed" in source_keys
    assert {f"persona:{name}" for name in catalog.personas} <= source_keys


def test_every_catalog_route_executes_through_real_runtime_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = load_voice_catalog(ROOT / "config" / "voice")
    engine_asset = tmp_path / "supertonic-model"
    engine_asset.write_bytes(b"pinned-supertonic")
    resolver = VoiceResolver(
        catalog.voice_catalog,
        {"voice": {"output_gain": 1.0}},
        {
            "supertonic": EngineMetadata(
                version="724fb5abbf5502583fb520898d45929e62f02c0b",
                assets={"model": AssetMetadata.from_path(engine_asset)},
            )
        },
    )
    current_persona = {"name": "dan"}
    config = SimpleNamespace(
        voice=SimpleNamespace(
            supertonic_binary="/usr/bin/true",
            supertonic_lang="pl",
            supertonic_steps=14,
            playback_binary="/usr/bin/true",
            tts_timeout_seconds=30,
            mastering_binary="/usr/bin/true",
            supertonic_custom_styles_manifest=str(
                ROOT / "config" / "voice" / "custom_styles" / "manifest.json"
            ),
        ),
        runtime=SimpleNamespace(runtime_dir=str(tmp_path / "runtime")),
    )
    external_calls: list[list[str]] = []

    def external_edge(argv: list[str], **kwargs: object):
        command = [str(value) for value in argv]
        external_calls.append(command)
        if len(command) > 1 and command[1] == "tts":
            output = Path(command[command.index("-o") + 1])
            output.write_bytes(b"\0" * 2_000)
        elif "-af" in command:
            Path(command[-1]).write_bytes(b"\0" * 2_000)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("dan.voice.tts.subprocess.run", external_edge)
    engine = build_tts_engine(
        "supertonic",
        config=config,
        persona_provider=lambda: current_persona["name"],
        resolver=resolver,
    )
    custom_styles = {
        asset.name
        for asset in load_asset_manifest(
            ROOT / "config" / "voice" / "custom_styles" / "manifest.json"
        ).assets
    }
    executed: set[str] = set()

    for name, spec in catalog.personas.items():
        assert spec["engine"] == "supertonic"
        current_persona["name"] = name
        external_calls.clear()

        chunk = engine.synthesize(f"route {name}")

        synthesis = next(command for command in external_calls if command[1] == "tts")
        assert synthesis[synthesis.index("--voice") + 1] == spec["voice"]
        assert synthesis[synthesis.index("--speed") + 1] == f"{float(spec['speed']):.2f}"
        if spec["voice"] in custom_styles:
            assert synthesis[synthesis.index("--custom-style-path") + 1] == str(
                ROOT / "config" / "voice" / "custom_styles" / f"{spec['voice']}.json"
            )
        else:
            assert "--custom-style-path" not in synthesis

        postprocess = [command for command in external_calls if "-af" in command]
        needs_postprocess = spec["mastering"] not in {"raw", "none"} or spec["dsp"] != "none"
        assert bool(postprocess) is needs_postprocess
        if spec["dsp"] != "none":
            assert spec["dsp"] in postprocess[0][postprocess[0].index("-af") + 1]
        assert len(chunk.audio) == 2_000
        executed.add(name)

    assert executed == set(catalog.personas)


def test_zaneta_local_only_chatterbox_fails_closed_then_live_route_executes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = load_voice_catalog(ROOT / "config" / "voice")
    zaneta = catalog.personas["zaneta"]

    assert zaneta["offline_pipeline"] == "chatterbox-v3-zaneta"
    assert ChatterboxV3ZanetaPipeline.live_capable is False
    with pytest.raises(PipelineCapabilityError, match="required local pipeline path"):
        load_pipeline_manifest(
            ROOT / "config" / "voice" / "pipelines" / "chatterbox-v3-zaneta.toml",
            environ={},
        )

    engine_asset = tmp_path / "supertonic-model"
    engine_asset.write_bytes(b"pinned-supertonic")
    resolver = VoiceResolver(
        catalog.voice_catalog,
        {"voice": {"output_gain": 1.0}},
        {
            "supertonic": EngineMetadata(
                version="724fb5abbf5502583fb520898d45929e62f02c0b",
                assets={"model": AssetMetadata.from_path(engine_asset)},
            )
        },
    )
    config = SimpleNamespace(
        voice=SimpleNamespace(
            supertonic_binary="/usr/bin/true",
            supertonic_lang="pl",
            supertonic_steps=14,
            playback_binary="/usr/bin/true",
            tts_timeout_seconds=30,
            mastering_binary="/usr/bin/true",
            supertonic_custom_styles_manifest=str(
                ROOT / "config" / "voice" / "custom_styles" / "manifest.json"
            ),
        ),
        runtime=SimpleNamespace(runtime_dir=str(tmp_path / "runtime")),
    )
    commands: list[list[str]] = []

    def external_edge(argv: list[str], **kwargs: object):
        command = [str(value) for value in argv]
        commands.append(command)
        if len(command) > 1 and command[1] == "tts":
            output = Path(command[command.index("-o") + 1])
            output.write_bytes(b"\0" * 2_000)
        elif "-af" in command:
            Path(command[-1]).write_bytes(b"\0" * 2_000)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("dan.voice.tts.subprocess.run", external_edge)
    engine = build_tts_engine(
        "supertonic",
        config=config,
        persona_provider=lambda: "zaneta",
        resolver=resolver,
    )

    engine.synthesize("Jawny live fallback")

    synthesis = next(command for command in commands if command[1] == "tts")
    assert synthesis[synthesis.index("--voice") + 1] == zaneta["voice"] == "F2"
