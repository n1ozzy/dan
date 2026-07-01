"""Safe subprocess Claude CLI brain adapter."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from typing import Any

from jarvis.brain.base import BrainAdapterError, BrainRequest, BrainResponse
from jarvis.logging import redact_secrets


CliRunner = Callable[[list[str], str, float], subprocess.CompletedProcess[str]]
DEFAULT_STDERR_PREVIEW_CHARS = 800


def format_cli_prompt(request: BrainRequest) -> str:
    """Build a deterministic, stateless prompt for provider CLI stdin."""

    lines = [
        "Jarvis v4.1 stateless brain request.",
        "",
        "Rules:",
        "- Answer as Jarvis using only the context in this request.",
        "- Do not expose hidden chain-of-thought; provide the final answer only.",
        "- Provider sessions are not Jarvis memory.",
        "- Tools are not executable in this call; tool requests remain pending approval.",
        "",
        f"Conversation: {request.conversation_id}",
        f"Turn: {request.turn_id}",
        "",
        "System context:",
    ]
    system_messages = [message for message in request.context_messages if message.role == "system"]
    lines.extend(_format_messages(system_messages))

    lines.extend(["", "Memory blocks:"])
    lines.extend(_format_memory_blocks(request))

    lines.extend(["", "Recent context:"])
    recent_messages = [message for message in request.context_messages if message.role != "system"]
    lines.extend(_format_messages(recent_messages))

    lines.extend(["", "Available tools:"])
    lines.extend(_format_tools(request))

    lines.extend(
        [
            "",
            "Current user input:",
            _clean_text(request.input_text),
            "",
            "Respond now as Jarvis.",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def default_subprocess_runner(
    command: list[str],
    input_text: str,
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        input=input_text,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def generate_cli_response(
    *,
    adapter_name: str,
    command_name: str,
    args: Sequence[str],
    default_model: str,
    timeout_seconds: float,
    runner: CliRunner,
    request: BrainRequest,
) -> BrainResponse:
    command = [command_name, *list(args)]
    _reject_unsafe_args(command)
    prompt = format_cli_prompt(request)
    try:
        result = runner(command, prompt, timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        raise BrainAdapterError(
            f"{adapter_name} timed out after {timeout_seconds:g}s"
        ) from exc
    except FileNotFoundError as exc:
        raise BrainAdapterError(f"{adapter_name} executable not found: {command_name}") from exc
    except OSError as exc:
        raise BrainAdapterError(
            f"{adapter_name} failed to run: {redact_secrets(str(exc))}"
        ) from exc

    if result.returncode != 0:
        stderr = _stderr_preview(result.stderr)
        raise BrainAdapterError(
            f"{adapter_name} exited with code {result.returncode}: {stderr}"
        )

    stdout = (result.stdout or "").strip()
    if not stdout:
        raise BrainAdapterError(f"{adapter_name} returned empty stdout")

    return BrainResponse(
        text=stdout,
        model=default_model,
        raw_metadata={
            "adapter": adapter_name,
            "command_name": command_name,
            "stateless": True,
        },
    )


class ClaudeCliAdapter:
    name = "claude_cli"

    def __init__(
        self,
        *,
        command: str = "claude",
        args: Sequence[str] | None = None,
        model: str = "",
        timeout_seconds: float = 120,
        runner: CliRunner | None = None,
    ) -> None:
        self.command = _required_text(command, "command")
        self.args = list(args) if args is not None else ["-p"]
        self.default_model = model.strip() or "claude-cli"
        self.timeout_seconds = float(timeout_seconds)
        self._runner = runner or default_subprocess_runner

    def available_models(self) -> list[str]:
        return [self.default_model]

    def generate(self, request: BrainRequest) -> BrainResponse:
        return generate_cli_response(
            adapter_name=self.name,
            command_name=self.command,
            args=self.args,
            default_model=self.default_model,
            timeout_seconds=self.timeout_seconds,
            runner=self._runner,
            request=request,
        )


def _format_messages(messages: list[Any]) -> list[str]:
    if not messages:
        return ["- none"]
    lines: list[str] = []
    for message in messages:
        role = _clean_text(str(getattr(message, "role", "message"))) or "message"
        name = getattr(message, "name", None)
        label = role if not name else f"{role}/{_clean_text(str(name))}"
        content = _clean_text(str(getattr(message, "content", "")))
        if content:
            lines.append(f"- {label}: {content}")
    return lines or ["- none"]


def _format_memory_blocks(request: BrainRequest) -> list[str]:
    if not request.memory_blocks:
        return ["- none"]
    lines: list[str] = []
    for block in request.memory_blocks:
        title = _clean_text(block.title)
        kind = _clean_text(block.kind)
        body = _clean_text(block.body)
        lines.append(f"- {title} [{kind}, priority {block.priority}]: {body}")
    return lines


def _format_tools(request: BrainRequest) -> list[str]:
    if not request.available_tools:
        return ["- none"]
    lines: list[str] = []
    for tool in request.available_tools:
        name = _clean_text(tool.name)
        risk = _clean_text(tool.risk)
        description = _clean_text(tool.description)
        lines.append(f"- {name} [{risk}]: {description} (unavailable; pending approval)")
    return lines


def _stderr_preview(stderr: str | None) -> str:
    redacted = redact_secrets((stderr or "").strip())
    if not redacted:
        return "no stderr"
    if len(redacted) <= DEFAULT_STDERR_PREVIEW_CHARS:
        return redacted
    return f"{redacted[:DEFAULT_STDERR_PREVIEW_CHARS]}..."


def _clean_text(value: str) -> str:
    return " ".join(value.strip().split())


def _required_text(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BrainAdapterError(f"{label} must be a non-empty string")
    return value.strip()


def _reject_unsafe_args(command: list[str]) -> None:
    forbidden = "".join(("--", "dangerously", "-skip-permissions"))
    if forbidden in command:
        raise BrainAdapterError("unsafe CLI argument is not allowed")
