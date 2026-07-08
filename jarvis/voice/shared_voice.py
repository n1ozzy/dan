"""Wspólne źródło głosów i wymowy — cienki loader ~/.config/jarvis-voice/voices.toml.

Jedno miejsce dla Jarvis + DAN + skille (patrz sam plik voices.toml). Ten moduł
CZYTA plik i scala jego dane do VoiceConfig demona. Świadomie NIE dotyka silnika
syntezy (jarvis/voice/tts.py) — działa wyłącznie na danych konfiguracji.

Semantyka scalania: wspólny plik jest BAZĄ, lokalny ~/.jarvis/jarvis.toml
NADPISUJE (user-local wygrywa). Dzięki temu nowe słowo dopisane do voices.toml
gada od razu, a ręczne poprawki w ~/.jarvis nadal mają pierwszeństwo.

Fail-safe: brak pliku / zły TOML / złe typy → zwraca wejściowy VoiceConfig bez
zmian. Nigdy nie rzuca — brak wspólnego pliku = zachowanie sprzed bindingu.
"""

from __future__ import annotations

import dataclasses
import os
import tomllib
from pathlib import Path
from typing import Any, TypeVar

_VOICE_CFG = TypeVar("_VOICE_CFG")

# Domyślna ścieżka; JARVIS_VOICES_FILE nadpisuje (testy, alternatywna lokalizacja).
DEFAULT_VOICES_PATH = Path.home() / ".config" / "jarvis-voice" / "voices.toml"

# "raw"/"none"/"" → surowy głos. Jarvis reprezentuje surowy jako pusty profil
# masteringu (mastering_profile == "" → brak łańcucha ffmpeg). Reszta przechodzi
# jak jest ("clean"/"bastard"/"gritty"); nieznany profil i tak fail-safe'uje w tts.
_RAW_ALIASES = {"raw", "none", ""}


def _resolve_path(path: str | Path | None) -> Path:
    if path is not None:
        return Path(path).expanduser()
    env = os.environ.get("JARVIS_VOICES_FILE")
    if env:
        return Path(env).expanduser()
    return DEFAULT_VOICES_PATH


def load_shared_voices(path: str | Path | None = None) -> dict[str, Any]:
    """Surowy dict z voices.toml, albo {} gdy pliku brak/uszkodzony."""
    target = _resolve_path(path)
    try:
        with open(target, "rb") as handle:
            data = tomllib.load(handle)
    except (FileNotFoundError, IsADirectoryError, PermissionError):
        return {}
    except (tomllib.TOMLDecodeError, ValueError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _normalize_mastering(value: str) -> str:
    return "" if value.strip().lower() in _RAW_ALIASES else value.strip()


def apply_shared_voices(voice_cfg: _VOICE_CFG, path: str | Path | None = None) -> _VOICE_CFG:
    """Zwróć VoiceConfig wzbogacony o wspólny plik (baza), z lokalnym override.

    Scala trzy pola: persona_voices, persona_mastering, tts_pronunciations.
    Wszystko inne (głos globalny, tempo silnika itd.) zostaje bez zmian —
    to warstwa danych person + wymowy, nie przełącznik silnika.
    """
    data = load_shared_voices(path)
    if not data:
        return voice_cfg

    personas = data.get("personas")
    pron = data.get("pronunciations")

    # ── wymowa: wspólny plik jako baza, lokalne wpisy nadpisują ──
    merged_pron: dict[str, str] = {}
    if isinstance(pron, dict):
        for key, val in pron.items():
            if isinstance(key, str) and isinstance(val, str):
                merged_pron[key.lower()] = val
    for key, val in (getattr(voice_cfg, "tts_pronunciations", None) or {}).items():
        if isinstance(key, str) and isinstance(val, str):
            merged_pron[key.lower()] = val

    # ── persony: wspólny plik jako baza, lokalne mapy nadpisują ──
    shared_voices: dict[str, str] = {}
    shared_master: dict[str, str] = {}
    if isinstance(personas, dict):
        for name, spec in personas.items():
            if not isinstance(name, str) or not isinstance(spec, dict):
                continue
            voice = spec.get("voice")
            master = spec.get("mastering")
            if isinstance(voice, str) and voice.strip():
                shared_voices[name] = voice.strip()
            if isinstance(master, str):
                shared_master[name] = _normalize_mastering(master)

    merged_voices = {**shared_voices, **(getattr(voice_cfg, "persona_voices", None) or {})}
    merged_master = {**shared_master, **(getattr(voice_cfg, "persona_mastering", None) or {})}

    return dataclasses.replace(
        voice_cfg,
        tts_pronunciations=merged_pron,
        persona_voices=merged_voices,
        persona_mastering=merged_master,
    )


__all__ = ["load_shared_voices", "apply_shared_voices", "DEFAULT_VOICES_PATH"]
