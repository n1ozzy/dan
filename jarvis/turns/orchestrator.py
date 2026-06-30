"""Single input pipeline orchestrator placeholder."""

from __future__ import annotations

from jarvis.turns.models import Turn


class TurnOrchestrator:
    def run_text_turn(self, text: str) -> Turn:
        raise NotImplementedError("turn orchestration is not implemented yet")
