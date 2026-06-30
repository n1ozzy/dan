"""BrainRequest builder placeholder."""

from __future__ import annotations

from jarvis.brain.base import BrainRequest


class ContextBuilder:
    def build(self, turn_id: str) -> BrainRequest:
        raise NotImplementedError("brain context building is not implemented yet")
