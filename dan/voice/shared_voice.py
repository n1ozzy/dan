"""Wspólne źródło głosów i wymowy — czytnik katalogu ~/.config/voice/.

Dwa pliki, dwie sprawy:
  personas.toml       — kto jakim głosem gada (voice / mastering / speed)
  pronunciations.toml — słownik wymowy anglicyzmów (słowo -> polska fonetyka)

To samo źródło dla DAN daemon, DAN i skilli (standup) — ale KAŻDY projekt ma
własny, niezależny czytnik; współdzielony jest tylko plik danych w ~/.config
(poza repo, jak ~/.dan/config.toml). Ten moduł czyta pliki i scala je do
VoiceConfig demona; NIE dotyka silnika syntezy (dan/voice/tts.py).

Semantyka: wspólny plik = BAZA, lokalny ~/.dan/config.toml = OVERRIDE
(user-local wygrywa). Katalog nadpisuje env VOICE_CONFIG_DIR (testy / inna
lokalizacja). Fail-safe: brak katalogu / pliku / zły TOML → zwraca wejściowy
VoiceConfig bez zmian (systemy jadą na swoich wbudowanych mapach).
"""

from __future__ import annotations

import os
import tomllib
import warnings
from pathlib import Path
from typing import Any, TypeVar

_VOICE_CFG = TypeVar("_VOICE_CFG")

# Domyślny katalog; VOICE_CONFIG_DIR nadpisuje (neutralna nazwa — bez brandu
# projektu, bo plik jest wspólny dla DANa i DAN-a).
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
    """{'dan': {'voice': 'M2', 'mastering': 'clean', ...}, ...}; {} gdy brak."""
    data = _load_toml(_resolve_dir(directory) / PERSONAS_FILE)
    return {n: s for n, s in data.items() if isinstance(n, str) and isinstance(s, dict)}


def load_pronunciations(directory: str | Path | None = None) -> dict[str, str]:
    """{'runtime': 'rantajm', ...}, klucze lowercase; {} gdy brak."""
    data = _load_toml(_resolve_dir(directory) / PRONUNCIATIONS_FILE)
    return {k.lower(): v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}


def _normalize_mastering(value: str) -> str:
    return "" if value.strip().lower() in _RAW_ALIASES else value.strip()


def apply_shared_voices(voice_cfg: _VOICE_CFG, directory: str | Path | None = None) -> _VOICE_CFG:
    """Temporary compatibility projection delegated to ``VoiceResolver``."""

    from dan.voice.resolver import VoiceCatalog, VoiceResolver

    warnings.warn(
        "apply_shared_voices is a compatibility caller; resolution belongs to VoiceResolver",
        DeprecationWarning,
        stacklevel=2,
    )
    catalog = VoiceCatalog.from_directory(_resolve_dir(directory), strict=False)
    return VoiceResolver.compatibility_voice_config(catalog, voice_cfg)


__all__ = [
    "load_personas",
    "load_pronunciations",
    "apply_shared_voices",
    "DEFAULT_VOICE_DIR",
]
