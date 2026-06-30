"""OpenAI brain adapter placeholder."""

from __future__ import annotations

from jarvis.brain.base import BrainRequest, BrainResponse


class OpenAIAdapter:
    def respond(self, request: BrainRequest) -> BrainResponse:
        raise NotImplementedError("OpenAI provider integration is not implemented yet")
