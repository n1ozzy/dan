"""Turn repository placeholder."""

from __future__ import annotations

from jarvis.turns.models import Turn


class TurnRepository:
    def create(self, turn: Turn) -> Turn:
        raise NotImplementedError("turn persistence is not implemented yet")
