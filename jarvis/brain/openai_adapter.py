"""Unavailable OpenAI brain adapter placeholder."""

from __future__ import annotations

from jarvis.brain.base import BrainAdapterError, BrainRequest, BrainResponse


class OpenAIAdapter:
    name = "openai"
    default_model = "openai-unavailable"

    def available_models(self) -> list[str]:
        return []

    def generate(self, request: BrainRequest) -> BrainResponse:
        raise BrainAdapterError("OpenAI adapter is not implemented yet")
