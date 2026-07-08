"""Wspólne źródło głosów/wymowy — loader po stronie Jarvis (2026-07-08).

Sprawdza scalanie voices.toml do VoiceConfig: wspólny plik jako baza, lokalny
[voice] jako override, oraz fail-safe gdy pliku brak / jest uszkodzony.
"""

from __future__ import annotations

import dataclasses

import pytest

from jarvis.config import VoiceConfig
from jarvis.voice.shared_voice import apply_shared_voices, load_shared_voices


def _write(tmp_path, text):
    p = tmp_path / "voices.toml"
    p.write_text(text, encoding="utf-8")
    return p


def test_missing_file_is_noop(tmp_path):
    cfg = VoiceConfig(persona_voices={"jarvis": "M1"}, tts_pronunciations={"bug": "bag"})
    out = apply_shared_voices(cfg, path=tmp_path / "nope.toml")
    assert out.persona_voices == {"jarvis": "M1"}
    assert out.tts_pronunciations == {"bug": "bag"}


def test_broken_toml_is_noop(tmp_path):
    bad = _write(tmp_path, "personas = [this is not valid")
    cfg = VoiceConfig(persona_voices={"jarvis": "M1"})
    assert apply_shared_voices(cfg, path=bad).persona_voices == {"jarvis": "M1"}


def test_shared_file_populates_personas_and_pronunciations(tmp_path):
    p = _write(
        tmp_path,
        """
        [personas.jarvis]
        voice = "M2"
        mastering = "clean"
        [personas.dan]
        voice = "M3"
        mastering = "raw"
        [pronunciations]
        runtime = "rantajm"
        chatterbox = "czaterboks"
        """,
    )
    out = apply_shared_voices(VoiceConfig(), path=p)
    assert out.persona_voices == {"jarvis": "M2", "dan": "M3"}
    # "raw" → pusty profil (Jarvis: surowy = brak łańcucha ffmpeg)
    assert out.persona_mastering == {"jarvis": "clean", "dan": ""}
    assert out.tts_pronunciations["runtime"] == "rantajm"
    assert out.tts_pronunciations["chatterbox"] == "czaterboks"


def test_local_config_overrides_shared(tmp_path):
    p = _write(
        tmp_path,
        """
        [personas.jarvis]
        voice = "M2"
        [pronunciations]
        bug = "bag"
        runtime = "rantajm"
        """,
    )
    # Lokalny [voice] podał własny głos jarvisa i własną wymowę 'bug' — wygrywa.
    cfg = VoiceConfig(
        persona_voices={"jarvis": "F1"},
        tts_pronunciations={"bug": "ROBAK"},
    )
    out = apply_shared_voices(cfg, path=p)
    assert out.persona_voices["jarvis"] == "F1"          # local wins
    assert out.tts_pronunciations["bug"] == "ROBAK"       # local wins
    assert out.tts_pronunciations["runtime"] == "rantajm"  # shared fills the gap


def test_keys_are_lowercased(tmp_path):
    p = _write(tmp_path, '[pronunciations]\nRunTime = "rantajm"\n')
    out = apply_shared_voices(VoiceConfig(), path=p)
    assert out.tts_pronunciations == {"runtime": "rantajm"}


def test_load_shared_voices_returns_dict(tmp_path):
    p = _write(tmp_path, "schema_version = 1\n[personas.jarvis]\nvoice = \"M2\"\n")
    data = load_shared_voices(path=p)
    assert data["schema_version"] == 1
    assert data["personas"]["jarvis"]["voice"] == "M2"


def test_result_is_still_frozen_voiceconfig(tmp_path):
    p = _write(tmp_path, '[pronunciations]\nbug = "bag"\n')
    out = apply_shared_voices(VoiceConfig(), path=p)
    with pytest.raises(dataclasses.FrozenInstanceError):
        out.enabled = True  # type: ignore[misc]
