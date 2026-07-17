"""Deprecated shared-voice readers and a strict resolver compatibility projection.

Raw readers remain for migration diagnostics. Runtime projection requires a
caller-supplied ``VoiceResolver``; local voice, speed, mastering and pronunciation
values never override its resolved snapshot.
"""

from __future__ import annotations

import dataclasses
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


def apply_shared_voices(
    voice_cfg: _VOICE_CFG,
    *,
    resolver: Any = None,
    persona: str = "dan",
) -> _VOICE_CFG:
    """Temporary compatibility projection delegated to ``VoiceResolver``."""

    from dan.voice.models import SpeechIntent
    from dan.voice.resolver import VoiceResolverError

    if resolver is None:
        raise VoiceResolverError(
            "apply_shared_voices requires a caller-supplied VoiceResolver"
        )

    warnings.warn(
        "apply_shared_voices is a compatibility caller; resolution belongs to VoiceResolver",
        DeprecationWarning,
        stacklevel=2,
    )
    snapshot = resolver.resolve(
        SpeechIntent(
            text="compatibility projection",
            persona=persona,
            source="shared_voice_compat",
            session="config",
            participant=persona,
            priority=0,
            lane="normal",
            interrupt_policy="finish_current",
            utterance_index=0,
        )
    )
    return dataclasses.replace(
        voice_cfg,
        default_tts=snapshot.engine,
        supertonic_voice=snapshot.voice_or_style,
        supertonic_speed=snapshot.speed,
        mastering_profile=snapshot.mastering_profile,
        tts_pronunciations=dict(snapshot.pronunciations),
        persona_voices={persona: snapshot.voice_or_style},
        persona_mastering={persona: snapshot.mastering_profile},
        persona_speeds={persona: snapshot.speed},
    )


__all__ = [
    "load_personas",
    "load_pronunciations",
    "apply_shared_voices",
    "DEFAULT_VOICE_DIR",
]
