"""Per-persona voice + mastering binding (2026-07-08).

Switching `persona.profile` (panel dropdown / settings) must change how Jarvis
SOUNDS, not only his text tone: the mapped supertonic voice and mastering
profile are resolved per-chunk from a lightweight persona provider, so a live
persona switch takes effect on the next spoken chunk without a daemon restart.

Everything is fail-safe: an unmapped profile, an empty/failing provider, or no
provider at all falls back to the global `supertonic_voice` / `mastering_profile`
— the pre-binding behavior, so this can never cause silence or a wrong-default
regression.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from jarvis.voice.tts import SupertonicEngine, mastering_filter

from tests.test_voice_tts_supertonic import (  # reuse the fake-CLI harness
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
    engine = SupertonicEngine(config=config, persona_provider=persona_provider)
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


def test_unmapped_profile_falls_back_to_default_voice(tmp_path: Path) -> None:
    engine, args_file = build_engine(
        tmp_path,
        persona_voices={"gangus-3": "M4"},
        persona_provider=lambda: "mentor",  # not in the map
    )
    engine.synthesize("spokojnie")
    assert _voice_arg(args_file) == "M1"


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


def test_unmapped_profile_falls_back_to_global_mastering(tmp_path: Path) -> None:
    engine, _args = build_engine(
        tmp_path,
        mastering_profile="bastard",
        persona_mastering={"mentor": "clean"},
        persona_provider=lambda: "gangus-1",  # not in the map
    )
    assert engine._mastering_filter_for("gangus-1") == mastering_filter("bastard")
