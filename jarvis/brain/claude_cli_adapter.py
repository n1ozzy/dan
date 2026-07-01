"""Unavailable Claude CLI brain adapter placeholder."""

from __future__ import annotations

from jarvis.brain.base import BrainAdapterError, BrainRequest, BrainResponse


class ClaudeCliAdapter:
    name = "claude-cli"
    default_model = "claude-cli-unavailable"

    def available_models(self) -> list[str]:
        return []

    def generate(self, request: BrainRequest) -> BrainResponse:
        raise BrainAdapterError("Claude CLI adapter is not implemented yet")
