from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from dan.voice.assets import load_asset_manifest, load_voice_catalog
from dan.voice.models import SpeechIntent
from dan.voice.service import build_voice_resolver
from dan.voice.tts import build_tts_engine

ROOT = Path(__file__).resolve().parents[1]


def speech_intent(persona: str) -> SpeechIntent:
    return SpeechIntent(
        text=f"route {persona}",
        persona=persona,
        source="route-matrix",
        session=f"route-{persona}",
        participant=persona,
        priority=0,
        lane="normal",
        interrupt_policy="finish_current",
        utterance_index=0,
    )


def _decision_rows(path: Path) -> list[dict[str, str]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| `"):
            continue
        cells = [cell.strip().strip("`") for cell in line.strip("|").split("|")]
        if len(cells) == 8:
            rows.append(
                dict(
                    zip(
                        (
                            "key",
                            "sources",
                            "reader",
                            "old",
                            "route",
                            "asset",
                            "evidence",
                            "decision",
                        ),
                        cells,
                        strict=True,
                    )
                )
            )
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
    config = SimpleNamespace(
        voice=SimpleNamespace(
            output_gain=1.0,
            supertonic_binary="/usr/bin/true",
            supertonic_lang="pl",
            supertonic_steps=14,
            tts_timeout_seconds=30,
            mastering_binary="/usr/bin/true",
            supertonic_custom_styles_manifest=str(
                ROOT / "config" / "voice" / "custom_styles" / "manifest.json"
            ),
        ),
        runtime=SimpleNamespace(runtime_dir=str(tmp_path / "runtime")),
    )
    resolver = build_voice_resolver(config, repo_root=ROOT)
    external_calls: list[list[str]] = []

    def external_edge(argv: list[str], **kwargs: object):
        command = [str(value) for value in argv]
        external_calls.append(command)
        if len(command) > 1 and command[1] == "version":
            return subprocess.CompletedProcess(command, 0, "supertonic 1.3.1", "")
        if "render" in command and "-o" in command:
            output = Path(command[command.index("-o") + 1])
            output.write_bytes(b"\0" * 2_000)
        elif "-af" in command:
            Path(command[-1]).write_bytes(b"\0" * 2_000)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("dan.voice.tts.subprocess.run", external_edge)
    engine = build_tts_engine("supertonic", config=config)
    custom_styles = {
        asset.name
        for asset in load_asset_manifest(
            ROOT / "config" / "voice" / "custom_styles" / "manifest.json"
        ).assets
    }
    executed: set[str] = set()

    for name, spec in catalog.personas.items():
        assert spec["engine"] == "supertonic"
        external_calls.clear()
        snapshot = resolver.resolve(speech_intent(name))

        chunk = engine.synthesize(f"route {name}", snapshot)

        synthesis = next(command for command in external_calls if "render" in command)
        assert synthesis[synthesis.index("--voice") + 1] == spec["voice"]
        assert synthesis[synthesis.index("--speed") + 1] == repr(float(spec["speed"]))
        assert synthesis[synthesis.index("--seed") + 1] == str(spec["seed"])
        if spec["voice"] in custom_styles:
            assert synthesis[synthesis.index("--custom-style-path") + 1] == str(
                ROOT / "config" / "voice" / "custom_styles" / f"{spec['voice']}.json"
            )
        else:
            assert "--custom-style-path" not in synthesis

        postprocess = [command for command in external_calls if "-af" in command]
        # "raw" is loudness-normalized since 2026-07-22; only "none" skips ffmpeg.
        needs_postprocess = spec["mastering"] != "none" or spec["dsp"] != "none"
        assert bool(postprocess) is needs_postprocess
        if spec["dsp"] != "none":
            assert spec["dsp"] in postprocess[0][postprocess[0].index("-af") + 1]
        assert len(chunk.audio) == 2_000
        executed.add(name)

    assert executed == {"dan", "danusia"}
