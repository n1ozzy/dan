"""Mock brain adapter for future tests."""

from __future__ import annotations

from jarvis.brain.base import BrainRequest, BrainResponse


class MockBrainAdapter:
    def respond(self, request: BrainRequest) -> BrainResponse:
        return BrainResponse(text="mock response", model=request.settings.get("model", "mock"))
