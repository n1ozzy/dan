"""Wspólne źródło głosów i wymowy — czytnik katalogu ~/.config/voice/.

Dwa pliki, dwie sprawy:
  personas.toml       — kto jakim głosem gada (voice / mastering / speed)
  pronunciations.toml — słownik wymowy anglicyzmów (słowo -> polska fonetyka)

To samo źródło dla Jarvis daemon, DAN i skilli (standup) — ale KAŻDY projekt ma
własny, niezależny czytnik; współdzielony jest tylko plik danych w ~/.config
(poza repo, jak ~/.jarvis/jarvis.toml). Ten moduł czyta pliki i scala je do
VoiceConfig demona; NIE dotyka silnika syntezy (jarvis/voice/tts.py).

Semantyka: wspólny plik = BAZA, lokalny ~/.jarvis/jarvis.toml = OVERRIDE
(user-local wygrywa). Katalog nadpisuje env VOICE_CONFIG_DIR (testy / inna
lokalizacja). Fail-safe: brak katalogu / pliku / zły TOML → zwraca wejściowy
VoiceConfig bez zmian (systemy jadą na swoich wbudowanych mapach).
"""

from __future__ import annotations

import dataclasses
import os
import tomllib
from pathlib import Path
from typing import Any, TypeVar

_VOICE_CFG = TypeVar("_VOICE_CFG")

# Domyślny katalog; VOICE_CONFIG_DIR nadpisuje (neutralna nazwa — bez brandu
# projektu, bo plik jest wspólny dla Jarvisa i DAN-a).
DEFAULT_VOICE_DIR = Path.home() / ".config" / "voice"
PERSONAS_FILE = "personas.toml"
PRONUNCIATIONS_FILE = "pronunciations.toml"

# "raw"/"none"/"" → surowy głos = pusty profil masteringu (brak łańcucha ffmpeg).
_RAW_ALIASES = {"raw", "none", ""}


def _resolve_dir(directory: str | Path | None) -> Path:
    if directory is not None:
        return Path(directory).expanduser()
    env = os.environ.get("VOICE_CONFIG_DIR")
    return Path(env).expanduser() if env else DEFAULT_VOICE_DIR


def _load_toml(path: Path) -> dict[str, Any]:
    try:
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
    except (FileNotFoundError, IsADirectoryError, PermissionError):
        return {}
    except (tomllib.TOMLDecodeError, ValueError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def load_personas(directory: str | Path | None = None) -> dict[str, dict]:
    """{'jarvis': {'voice': 'M2', 'mastering': 'clean', ...}, ...}; {} gdy brak."""
    data = _load_toml(_resolve_dir(directory) / PERSONAS_FILE)
    return {n: s for n, s in data.items() if isinstance(n, str) and isinstance(s, dict)}


def load_pronunciations(directory: str | Path | None = None) -> dict[str, str]:
    """{'runtime': 'rantajm', ...}, klucze lowercase; {} gdy brak."""
    data = _load_toml(_resolve_dir(directory) / PRONUNCIATIONS_FILE)
    return {k.lower(): v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}


def _normalize_mastering(value: str) -> str:
    return "" if value.strip().lower() in _RAW_ALIASES else value.strip()


def apply_shared_voices(voice_cfg: _VOICE_CFG, directory: str | Path | None = None) -> _VOICE_CFG:
    """Zwróć VoiceConfig wzbogacony o wspólny katalog (baza), z lokalnym override.

    Scala trzy pola: persona_voices, persona_mastering, tts_pronunciations.
    Nic innego nie rusza — to warstwa danych person + wymowy, nie silnik.
    """
    personas = load_personas(directory)
    pron = load_pronunciations(directory)
    if not personas and not pron:
        return voice_cfg

    # ── wymowa: wspólny plik jako baza, lokalne wpisy nadpisują ──
    merged_pron: dict[str, str] = dict(pron)
    for key, val in (getattr(voice_cfg, "tts_pronunciations", None) or {}).items():
        if isinstance(key, str) and isinstance(val, str):
            merged_pron[key.lower()] = val

    # ── persony: wspólny plik jako baza, lokalne mapy nadpisują ──
    shared_voices: dict[str, str] = {}
    shared_master: dict[str, str] = {}
    for name, spec in personas.items():
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


__all__ = [
    "load_personas",
    "load_pronunciations",
    "apply_shared_voices",
    "DEFAULT_VOICE_DIR",
]
