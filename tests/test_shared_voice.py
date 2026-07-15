"""Wspólne źródło głosów/wymowy — loader po stronie Jarvis (2026-07-08).

Katalog ~/.config/voice/ z dwoma plikami (personas.toml + pronunciations.toml).
Sprawdza scalanie do VoiceConfig: wspólny plik jako baza, lokalny [voice] jako
override, oraz fail-safe gdy katalogu/plików brak.
"""

from __future__ import annotations

import dataclasses

import pytest

from jarvis.config import VoiceConfig
from jarvis.voice.shared_voice import (
    apply_shared_voices,
    load_personas,
    load_pronunciations,
)


def _voice_dir(tmp_path, *, personas="", pronunciations=""):
    if personas:
        (tmp_path / "personas.toml").write_text(personas, encoding="utf-8")
    if pronunciations:
        (tmp_path / "pronunciations.toml").write_text(pronunciations, encoding="utf-8")
    return tmp_path


def test_missing_dir_is_noop(tmp_path):
    cfg = VoiceConfig(persona_voices={"jarvis": "M1"}, tts_pronunciations={"bug": "bag"})
    out = apply_shared_voices(cfg, directory=tmp_path / "nope")
    assert out.persona_voices == {"jarvis": "M1"}
    assert out.tts_pronunciations == {"bug": "bag"}


def test_broken_toml_is_noop(tmp_path):
    _voice_dir(tmp_path, personas="personas = [this is not valid")
    cfg = VoiceConfig(persona_voices={"jarvis": "M1"})
    assert apply_shared_voices(cfg, directory=tmp_path).persona_voices == {"jarvis": "M1"}


def test_populates_personas_and_pronunciations(tmp_path):
    _voice_dir(
        tmp_path,
        personas="""
        [jarvis]
        voice = "M2"
        mastering = "clean"
        speed = 1.35
        [dan]
        voice = "M3"
        mastering = "raw"
        speed = 1.25
        """,
        pronunciations="""
        runtime = "rantajm"
        chatterbox = "czaterboks"
        """,
    )
    out = apply_shared_voices(VoiceConfig(), directory=tmp_path)
    assert out.persona_voices == {"jarvis": "M2", "dan": "M3"}
    # "raw" → pusty profil (Jarvis: surowy = brak łańcucha ffmpeg)
    assert out.persona_mastering == {"jarvis": "clean", "dan": ""}
    assert out.persona_speeds == {"jarvis": 1.35, "dan": 1.25}
    assert out.tts_pronunciations["runtime"] == "rantajm"
    assert out.tts_pronunciations["chatterbox"] == "czaterboks"


def test_local_config_overrides_shared(tmp_path):
    _voice_dir(
        tmp_path,
        personas='[jarvis]\nvoice = "M2"\nspeed = 1.35\n',
        pronunciations='bug = "bag"\nruntime = "rantajm"\n',
    )
    # Lokalny [voice] podał własny głos jarvisa i własną wymowę 'bug' — wygrywa.
    cfg = VoiceConfig(
        persona_voices={"jarvis": "F1"},
        persona_speeds={"jarvis": 1.1},
        tts_pronunciations={"bug": "ROBAK"},
    )
    out = apply_shared_voices(cfg, directory=tmp_path)
    assert out.persona_voices["jarvis"] == "F1"           # local wins
    assert out.persona_speeds["jarvis"] == 1.1             # local wins
    assert out.tts_pronunciations["bug"] == "ROBAK"        # local wins
    assert out.tts_pronunciations["runtime"] == "rantajm"  # shared fills the gap


def test_keys_are_lowercased(tmp_path):
    _voice_dir(tmp_path, pronunciations='RunTime = "rantajm"\n')
    out = apply_shared_voices(VoiceConfig(), directory=tmp_path)
    assert out.tts_pronunciations == {"runtime": "rantajm"}


def test_loaders_return_dicts(tmp_path):
    _voice_dir(
        tmp_path,
        personas='[jarvis]\nvoice = "M2"\n',
        pronunciations='runtime = "rantajm"\n',
    )
    assert load_personas(tmp_path)["jarvis"]["voice"] == "M2"
    assert load_pronunciations(tmp_path) == {"runtime": "rantajm"}


def test_result_is_still_frozen_voiceconfig(tmp_path):
    _voice_dir(tmp_path, pronunciations='bug = "bag"\n')
    out = apply_shared_voices(VoiceConfig(), directory=tmp_path)
    with pytest.raises(dataclasses.FrozenInstanceError):
        out.enabled = True  # type: ignore[misc]
