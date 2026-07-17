from __future__ import annotations

import json
from pathlib import Path

from dan.voice.assets import load_voice_catalog
from dan.voice.models import SpeechIntent
from dan.voice.resolver import AssetMetadata, EngineMetadata, VoiceResolver


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


def test_route_matrix_matches_snapshot_and_playback(tmp_path: Path) -> None:
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

    for name, spec in catalog.personas.items():
        if spec["engine"] != "supertonic":
            continue
        snapshot = resolver.resolve(
            SpeechIntent(
                text="test",
                persona=name,
                source="task-6",
                session="route-matrix",
                participant=name,
                priority=0,
                lane="normal",
                interrupt_policy="finish_current",
                utterance_index=0,
            )
        )
        playback = json.loads(snapshot.canonical_json())
        assert (snapshot.voice_or_style, snapshot.speed, snapshot.dsp) == (
            playback["voice_or_style"],
            playback["speed"],
            playback["dsp"],
        )
        assert (snapshot.voice_or_style, snapshot.speed, snapshot.dsp) == (
            spec["voice"],
            spec["speed"],
            spec["dsp"],
        )
