"""Configuration value objects for the Jarvis scaffold."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


DEFAULT_CONFIG_PATH = Path("config/jarvis.example.toml")


@dataclass(frozen=True)
class JarvisConfig:
    """Minimal config handle; real TOML loading arrives in a later prompt."""

    path: Path = DEFAULT_CONFIG_PATH


def load_config(path: Path | None = None) -> JarvisConfig:
    return JarvisConfig(path=path or DEFAULT_CONFIG_PATH)
