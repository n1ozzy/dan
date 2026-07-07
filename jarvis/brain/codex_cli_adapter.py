"""Safe subprocess Codex CLI brain adapter."""

from __future__ import annotations

from collections.abc import Sequence

from jarvis.brain.base import BrainAdapterError, BrainRequest, BrainResponse
from jarvis.brain.claude_cli_adapter import (
    CliRunner,
    default_subprocess_runner,
    generate_cli_response,
)
from jarvis.brain.codex_cli_contract import (
    CODEX_CLI_COMMAND,
    INTERNAL_MODEL_SENTINELS,
    CodexCliCommandSettings,
    build_codex_cli_command,
)


class CodexCliAdapter:
    name = "codex_cli"

    def __init__(
        self,
        *,
        command: str = CODEX_CLI_COMMAND,
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
        if self.default_model in INTERNAL_MODEL_SENTINELS:
            return []
        return [self.default_model]

    def command_settings(self) -> CodexCliCommandSettings:
        return CodexCliCommandSettings(
            command=self.command,
            args=list(self.args),
            model="" if self.default_model in INTERNAL_MODEL_SENTINELS else self.default_model,
        )

    def generate(self, request: BrainRequest, *, on_delta=None) -> BrainResponse:
        # Codex CLI has no wired streaming mode yet: on_delta is accepted and
        # ignored (G0 §2 degradation — the final text is chunked after the fact).
        command_contract = build_codex_cli_command(
            self.command_settings(),
            request_settings=request.settings,
        )
        return generate_cli_response(
            adapter_name=self.name,
            command_name=self.command,
            args=command_contract.argv[1:],
            default_model=command_contract.effective_model or self.default_model,
            timeout_seconds=self.timeout_seconds,
            runner=self._runner,
            request=request,
        )
