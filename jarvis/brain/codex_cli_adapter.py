"""Safe subprocess Codex CLI brain adapter."""

from __future__ import annotations

from collections.abc import Sequence

from jarvis.brain.base import BrainAdapterError, BrainRequest, BrainResponse
from jarvis.brain.claude_cli_adapter import (
    CliRunner,
    default_subprocess_runner,
    generate_cli_response,
)


class CodexCliAdapter:
    name = "codex_cli"

    def __init__(
        self,
        *,
        command: str = "codex",
        args: Sequence[str] | None = None,
        model: str = "",
        timeout_seconds: float = 120,
        runner: CliRunner | None = None,
    ) -> None:
        self.command = command.strip()
        if not self.command:
            raise BrainAdapterError("command must be a non-empty string")
        self.args = list(args) if args is not None else []
        self.default_model = model.strip() or "codex-cli"
        self.timeout_seconds = float(timeout_seconds)
        self._runner = runner or default_subprocess_runner

    def available_models(self) -> list[str]:
        return [self.default_model]

    def generate(self, request: BrainRequest, *, on_delta=None) -> BrainResponse:
        # Codex CLI has no wired streaming mode yet: on_delta is accepted and
        # ignored (G0 §2 degradation — the final text is chunked after the fact).
        return generate_cli_response(
            adapter_name=self.name,
            command_name=self.command,
            args=self.args,
            default_model=self.default_model,
            timeout_seconds=self.timeout_seconds,
            runner=self._runner,
            request=request,
        )
