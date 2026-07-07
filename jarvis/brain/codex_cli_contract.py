"""Executable Codex CLI command contract shared by adapter and projections."""

from __future__ import annotations

import shlex
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from jarvis.security.redaction import redact_secrets


CODEX_CLI_COMMAND = "codex"
CODEX_EXEC_SUBCOMMAND = "exec"
INTERNAL_MODEL_SENTINELS = frozenset({"codex-cli"})

_MANAGED_VALUE_FLAGS = frozenset({"--model"})
_GLOBAL_VALUE_FLAGS = frozenset({"--ask-for-approval"})
_GLOBAL_BARE_FLAGS = frozenset({"--search"})
_DANGEROUS_FLAGS = frozenset(
    {
        "--dangerously-bypass-approvals-and-sandbox",
        "--dangerously-bypass-hook-trust",
        "--full-auto",
        "--dangerously-auto-approve-everything",
    }
)


@dataclass(frozen=True)
class CodexCliCommandSettings:
    command: str = CODEX_CLI_COMMAND
    args: list[str] = field(default_factory=list)
    model: str = ""


@dataclass(frozen=True)
class CodexCliCommandContract:
    argv: list[str]
    command_preview: str
    flag_metadata: list[dict[str, Any]]
    command: str
    args: list[str]
    selected_model: str | None
    effective_model: str | None
    model_source: str


def build_codex_cli_command(
    settings: CodexCliCommandSettings,
    *,
    runtime_model: Any = None,
    request_settings: Mapping[str, Any] | None = None,
) -> CodexCliCommandContract:
    """Build a deterministic non-interactive ``codex exec`` command."""

    command = _required_command(settings.command)
    args = [str(item) for item in settings.args]
    _reject_dangerous_flags(args)

    request_settings = request_settings or {}
    runtime_model = runtime_model if runtime_model is not None else _request_model(request_settings)
    args_model = _non_sentinel_model(_cli_arg_value(args, "--model"))
    requested_model = _non_sentinel_model(_optional_text(runtime_model))
    configured_model = _non_sentinel_model(_optional_text(settings.model))
    selected_model = requested_model or args_model or configured_model
    effective_model = selected_model
    model_source = "jarvis_explicit" if selected_model else "codex_default"

    global_args, exec_args = _split_operator_options(args)
    argv = [command, *global_args, CODEX_EXEC_SUBCOMMAND]
    _append_value(argv, "--model", selected_model)
    argv.extend(exec_args)

    return CodexCliCommandContract(
        argv=argv,
        command_preview=_redacted_command_preview(argv),
        flag_metadata=_flag_metadata(
            selected_model=selected_model,
            requested_model=requested_model,
            args_model=args_model,
            configured_model=configured_model,
        ),
        command=command,
        args=args,
        selected_model=selected_model,
        effective_model=effective_model,
        model_source=model_source,
    )


def _required_command(value: str) -> str:
    command = str(value or "").strip()
    return command or CODEX_CLI_COMMAND


def _optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _request_model(settings: Mapping[str, Any]) -> str | None:
    model = _optional_text(settings.get("model"))
    if model is None:
        return None
    source = _optional_text(settings.get("model_source")) or _optional_text(
        settings.get("brain.model_source")
    )
    if source in {"settings", "jarvis_explicit", "runtime"}:
        return model
    return None


def _non_sentinel_model(value: str | None) -> str | None:
    if value in INTERNAL_MODEL_SENTINELS:
        return None
    return value


def _reject_dangerous_flags(tokens: Sequence[str]) -> None:
    for token in tokens:
        raw = str(token)
        if not raw.startswith("-"):
            continue
        flag = raw.split("=", 1)[0]
        if flag in _DANGEROUS_FLAGS:
            raise ValueError(f"dangerous Codex CLI flag is not allowed: {flag}")


def _cli_arg_value(args: Sequence[str], *flags: str) -> str | None:
    flag_set = set(flags)
    index = 0
    while index < len(args):
        token = str(args[index])
        for flag in flag_set:
            if token == flag and index + 1 < len(args):
                return str(args[index + 1])
            prefix = f"{flag}="
            if token.startswith(prefix):
                return token[len(prefix) :]
        index += 1
    return None


def _split_operator_options(tokens: Sequence[str]) -> tuple[list[str], list[str]]:
    global_args: list[str] = []
    exec_args: list[str] = []
    index = 0
    while index < len(tokens):
        token = str(tokens[index])
        flag = token.split("=", 1)[0]
        if token == CODEX_EXEC_SUBCOMMAND:
            index += 1
            continue
        if flag in _MANAGED_VALUE_FLAGS:
            index += _managed_option_width(tokens, index, token)
            continue
        if flag in _GLOBAL_BARE_FLAGS:
            global_args.append(token)
            index += 1
            continue
        if flag in _GLOBAL_VALUE_FLAGS:
            width = _managed_option_width(tokens, index, token)
            global_args.extend(str(item) for item in tokens[index : index + width])
            index += width
            continue
        exec_args.append(token)
        index += 1
    return global_args, exec_args


def _managed_option_width(tokens: Sequence[str], index: int, token: str) -> int:
    if "=" in token:
        return 1
    if index + 1 < len(tokens) and not str(tokens[index + 1]).startswith("-"):
        return 2
    return 1


def _append_value(argv: list[str], flag: str, value: str | None) -> None:
    if value is None:
        return
    argv.extend([flag, value])


def _redacted_command_preview(argv: Sequence[str]) -> str:
    return redact_secrets(shlex.join([str(item) for item in argv]))


def _flag_metadata(
    *,
    selected_model: str | None,
    requested_model: str | None,
    args_model: str | None,
    configured_model: str | None,
) -> list[dict[str, Any]]:
    if requested_model:
        source = "request_settings"
    elif args_model:
        source = "operator_args"
    elif configured_model:
        source = "config"
    else:
        source = "codex_default"
    return [
        {
            "flag": "--model",
            "included": bool(selected_model),
            "source": source,
            "reason": "Jarvis-selected model is explicit."
            if selected_model
            else "No Codex model was selected; Codex CLI default applies.",
        }
    ]
