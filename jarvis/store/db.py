"""Database connection placeholder."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Database:
    path: Path

    def connect(self) -> None:
        raise NotImplementedError("SQLite connection is not implemented yet")
