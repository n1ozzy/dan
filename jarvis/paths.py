"""Path declarations for Jarvis-owned local state."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class JarvisPaths:
    root: Path
    database: Path
    logs: Path
    runtime: Path
    pid_file: Path


def default_paths(home: Path | None = None) -> JarvisPaths:
    base_home = home or Path.home()
    root = base_home / ".jarvis"
    return JarvisPaths(
        root=root,
        database=root / "jarvis.db",
        logs=root / "logs",
        runtime=root / "runtime",
        pid_file=root / "runtime" / "jarvisd.pid",
    )
