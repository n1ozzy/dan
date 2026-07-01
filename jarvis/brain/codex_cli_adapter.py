"""Unavailable Codex CLI brain adapter placeholder."""

from __future__ import annotations

from jarvis.brain.base import BrainAdapterError, BrainRequest, BrainResponse


class CodexCliAdapter:
    name = "codex-cli"
    default_model = "codex-cli-unavailable"

    def available_models(self) -> list[str]:
        return []

    def generate(self, request: BrainRequest) -> BrainResponse:
        raise BrainAdapterError("Codex CLI adapter is not implemented yet")
