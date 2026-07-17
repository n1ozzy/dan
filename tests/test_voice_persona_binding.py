"""Per-persona compatibility binding remains strict resolver-owned truth."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from dan.voice.models import SnapshotValidationError
from dan.voice.tts import SupertonicEngine, mastering_filter

from tests.test_voice_tts_supertonic import (  # reuse the fake-CLI harness
    build_strict_engine,
    fake_player,
    fake_supertonic,
)


def build_engine(
    tmp_path: Path,
    *,
    persona_provider=None,
    **voice_overrides,
) -> tuple[SupertonicEngine, Path]:
    binary, args_file = fake_supertonic(tmp_path)
    player, _played = fake_player(tmp_path)
    voice = {
        "default_tts": "supertonic",
        "supertonic_binary": str(binary),
        "supertonic_voice": "M1",
        "supertonic_lang": "pl",
        "supertonic_steps": 14,
        "supertonic_speed": 1.35,
        "playback_binary": str(player),
        "tts_timeout_seconds": 30,
    }
    voice.update(voice_overrides)
    config = SimpleNamespace(
        voice=SimpleNamespace(**voice),
        runtime=SimpleNamespace(runtime_dir=str(tmp_path / "runtime")),
    )
    persona_voices = voice.get("persona_voices", {}) or {}
    persona_speeds = voice.get("persona_speeds", {}) or {}
    persona_mastering = voice.get("persona_mastering", {}) or {}
    persona_names = set(persona_voices) | set(persona_speeds) | set(persona_mastering)
    personas = {
        "dan": {
            "voice": voice["supertonic_voice"],
            "speed": voice["supertonic_speed"],
            "mastering": voice.get("mastering_profile", "") or "raw",
            "dsp": "none",
        },
        **{
            name: {
                "voice": persona_voices.get(name, voice["supertonic_voice"]),
                "speed": persona_speeds.get(name, voice["supertonic_speed"]),
                "mastering": persona_mastering.get(
                    name, voice.get("mastering_profile", "") or "raw"
                ),
                "dsp": "none",
            }
            for name in persona_names
        },
    }
    engine = build_strict_engine(
        tmp_path,
        config,
        persona_provider=persona_provider,
        personas=personas,
    )
    return engine, args_file


def _voice_arg(args_file: Path) -> str:
    lines = args_file.read_text().splitlines()
    return lines[lines.index("--voice") + 1]


# -- voice binding -----------------------------------------------------------


def test_persona_profile_selects_mapped_voice(tmp_path: Path) -> None:
    engine, args_file = build_engine(
        tmp_path,
        persona_voices={"gangus-3": "M4"},
        persona_provider=lambda: "gangus-3",
    )
    engine.synthesize("cześć ziomek")
    assert _voice_arg(args_file) == "M4"


def test_unmapped_profile_fails_before_render(tmp_path: Path) -> None:
    engine, args_file = build_engine(
        tmp_path,
        persona_voices={"gangus-3": "M4"},
        persona_provider=lambda: "mentor",  # not in the map
    )
    with pytest.raises(SnapshotValidationError, match="unknown voice persona"):
        engine.synthesize("spokojnie")
    assert not args_file.exists()


def test_no_provider_uses_default_voice(tmp_path: Path) -> None:
    engine, args_file = build_engine(tmp_path, persona_voices={"gangus-3": "M4"})
    engine.synthesize("bez persony")
    assert _voice_arg(args_file) == "M1"


def test_failing_provider_is_fail_safe(tmp_path: Path) -> None:
    def boom() -> str:
        raise RuntimeError("settings DB down")

    engine, args_file = build_engine(
        tmp_path,
        persona_voices={"gangus-3": "M4"},
        persona_provider=boom,
    )
    engine.synthesize("nie milcz")
    assert _voice_arg(args_file) == "M1"


# -- mastering binding -------------------------------------------------------


def test_persona_profile_selects_mapped_mastering(tmp_path: Path) -> None:
    engine, _args = build_engine(
        tmp_path,
        mastering_profile="bastard",
        persona_mastering={"mentor": "clean"},
        persona_provider=lambda: "mentor",
    )
    assert engine._mastering_filter_for("mentor") == mastering_filter("clean")


def test_unmapped_profile_cannot_fall_back_to_global_mastering(tmp_path: Path) -> None:
    engine, _args = build_engine(
        tmp_path,
        mastering_profile="bastard",
        persona_mastering={"mentor": "clean"},
        persona_provider=lambda: "gangus-1",  # not in the map
    )
    with pytest.raises(SnapshotValidationError, match="unknown voice persona"):
        engine._mastering_filter_for("gangus-1")
