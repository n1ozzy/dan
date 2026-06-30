"""Brain adapter manager placeholder."""

from __future__ import annotations

from jarvis.brain.base import BrainAdapter, BrainRequest, BrainResponse


class BrainManager:
    def __init__(self, adapter: BrainAdapter) -> None:
        self.adapter = adapter

    def respond(self, request: BrainRequest) -> BrainResponse:
        return self.adapter.respond(request)
