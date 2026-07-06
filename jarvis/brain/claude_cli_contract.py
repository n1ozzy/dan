"""Executable Claude CLI command contract shared by adapter and projections."""

from __future__ import annotations

import os
import re
import shlex
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jarvis.security.redaction import redact_secrets


CLAUDE_CLI_COMMAND = "claude"
CLAUDE_CLI_EFFORTS = ("low", "medium", "high", "xhigh", "max")
CLAUDE_CLI_PERMISSION_MODES = (
    "default",
    "manual",
    "acceptEdits",
    "plan",
    "auto",
    "dontAsk",
    "bypassPermissions",
)
CLAUDE_CLI_OUTPUT_FORMATS = ("text", "json", "stream-json")
CLAUDE_CLI_INPUT_FORMATS = ("text", "stream-json")
DEFAULT_STREAM_ARGS = (
    "--output-format",
    "stream-json",
    "--verbose",
    "--include-partial-messages",
)

_MANAGED_VALUE_FLAGS = frozenset(
    {
        "--model",
        "--effort",
        "--permission-mode",
        "--tools",
        "--allowedTools",
        "--allowed-tools",
        "--disallowedTools",
        "--disallowed-tools",
        "--mcp-config",
        "--output-format",
        "--input-format",
    }
)
_MANAGED_BARE_FLAGS = frozenset({"--strict-mcp-config"})
_MANAGED_FLAGS = _MANAGED_VALUE_FLAGS | _MANAGED_BARE_FLAGS


@dataclass(frozen=True)
class ClaudeCliCommandSettings:
    command: str = CLAUDE_CLI_COMMAND
    args: list[str] = field(default_factory=lambda: ["-p"])
    model: str = ""
    effort: str = ""
    permission_mode: str = ""
    output_format: str = ""
    input_format: str = ""
    tools: list[str] = field(default_factory=list)
    allowed_tools: list[str] = field(default_factory=list)
    disallowed_tools: list[str] = field(default_factory=list)
    mcp_config_path: str = ""
    strict_mcp_config: bool | None = None
    stream_args: list[str] | None = None


@dataclass(frozen=True)
class ClaudeCliCommandContract:
    argv: list[str]
    command_preview: str
    command: str
    args: list[str]
    stream_args: list[str]
    selected_model: str | None
    effective_model: str | None
    model_source: str
    selected_effort: str | None
    effective_effort: str | None
    effort_source: str
    permission_mode: str
    tools: list[str]
    allowed_tools: list[str]
    disallowed_tools: list[str]
    mcp_config_path: str | None
    mcp_config_status: str
    strict_mcp_config: bool | str
    output_format: str
    input_format: str
    streaming_supported: str
    partial_messages_supported: str


def build_claude_cli_command(
    settings: ClaudeCliCommandSettings,
    *,
    runtime_model: Any = None,
    runtime_effort: Any = None,
    request_settings: Mapping[str, Any] | None = None,
    streaming: bool = False,
) -> ClaudeCliCommandContract:
    """Build the executable argv and redacted preview for one Claude CLI turn."""

    command = _required_command(settings.command)
    args = [str(item) for item in settings.args]
    stream_args = (
        [str(item) for item in settings.stream_args]
        if settings.stream_args is not None
        else list(DEFAULT_STREAM_ARGS)
    )
    request_settings = request_settings or {}
    runtime_effort = runtime_effort if runtime_effort is not None else request_settings.get("effort")

    args_model = _cli_arg_value(args, "--model")
    requested_model = _optional_text(runtime_model)
    configured_model = _optional_text(settings.model)
    selected_model = requested_model or args_model or configured_model
    effective_model = selected_model
    model_source = "jarvis_explicit" if selected_model else "claude_default"

    args_effort = _cli_arg_value(args, "--effort")
    configured_effort = _optional_text(settings.effort)
    requested_effort = _optional_text(runtime_effort)
    selected_effort = requested_effort or configured_effort or args_effort
    effort_valid = selected_effort in CLAUDE_CLI_EFFORTS if selected_effort else False
    effective_effort = selected_effort if effort_valid else None
    effort_source = (
        "jarvis_explicit"
        if effective_effort
        else ("model_default" if CLAUDE_CLI_EFFORTS else "unsupported")
    )

    permission_raw = _optional_text(settings.permission_mode) or _cli_arg_value(args, "--permission-mode")
    permission_mode = normalize_claude_permission_mode(permission_raw)
    tools = _configured_or_arg_tools(settings.tools, _cli_arg_value(args, "--tools"))
    allowed_tools = _configured_or_arg_list(
        settings.allowed_tools,
        _cli_arg_value(args, "--allowedTools", "--allowed-tools"),
    )
    disallowed_tools = _configured_or_arg_list(
        settings.disallowed_tools,
        _cli_arg_value(args, "--disallowedTools", "--disallowed-tools"),
    )
    mcp_config_path = _optional_text(settings.mcp_config_path) or _cli_arg_value(args, "--mcp-config")
    mcp_config_status = _mcp_config_status(mcp_config_path)
    strict_mcp_config = _strict_mcp_config(settings.strict_mcp_config, args)

    args_output_format = _cli_arg_value(args, "--output-format")
    stream_output_format = _cli_arg_value(stream_args, "--output-format")
    output_format = normalize_claude_output_format(
        _optional_text(settings.output_format)
        or (stream_output_format if streaming else None)
        or args_output_format
        or "text"
    )
    args_input_format = _cli_arg_value(args, "--input-format")
    stream_input_format = _cli_arg_value(stream_args, "--input-format")
    input_format = normalize_claude_input_format(
        _optional_text(settings.input_format)
        or (stream_input_format if streaming else None)
        or args_input_format
        or "text"
    )

    argv = [command, *_strip_managed_options(args)]
    _append_value(argv, "--model", selected_model if selected_model != "claude-cli" else None)
    _append_value(argv, "--effort", effective_effort)
    _append_value(
        argv,
        "--permission-mode",
        permission_mode if permission_mode not in {"default", "unknown"} else None,
    )
    _append_value(argv, "--tools", _join_tools_value(tools), allow_empty=True)
    _append_value(argv, "--allowedTools", ",".join(allowed_tools) if allowed_tools else None)
    _append_value(argv, "--disallowedTools", ",".join(disallowed_tools) if disallowed_tools else None)
    _append_value(argv, "--mcp-config", mcp_config_path)
    if strict_mcp_config is True:
        _append_flag(argv, "--strict-mcp-config")
    elif strict_mcp_config is False and settings.strict_mcp_config is False:
        _append_value(argv, "--strict-mcp-config", "false")

    output_format_explicit = bool(
        settings.output_format or args_output_format or (streaming and stream_output_format)
    )
    input_format_explicit = bool(
        settings.input_format or args_input_format or (streaming and stream_input_format)
    )
    _append_value(argv, "--output-format", output_format if output_format_explicit else None)
    _append_value(argv, "--input-format", input_format if input_format_explicit else None)
    if streaming:
        argv.extend(
            _strip_managed_options(
                stream_args,
                keep_flags={"--verbose", "--include-partial-messages"},
            )
        )

    partial_messages_supported = "yes" if _cli_arg_present(argv, "--include-partial-messages") else "no"
    streaming_supported = "yes" if output_format == "stream-json" else "no"
    return ClaudeCliCommandContract(
        argv=argv,
        command_preview=_redacted_command_preview(argv),
        command=command,
        args=args,
        stream_args=stream_args,
        selected_model=selected_model,
        effective_model=effective_model,
        model_source=model_source,
        selected_effort=selected_effort,
        effective_effort=effective_effort,
        effort_source=effort_source,
        permission_mode=permission_mode,
        tools=tools,
        allowed_tools=allowed_tools,
        disallowed_tools=disallowed_tools,
        mcp_config_path=mcp_config_path,
        mcp_config_status=mcp_config_status,
        strict_mcp_config=strict_mcp_config,
        output_format=output_format,
        input_format=input_format,
        streaming_supported=streaming_supported,
        partial_messages_supported=partial_messages_supported,
    )


def normalize_claude_permission_mode(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "default"
    aliases = {
        "accept-edits": "acceptEdits",
        "acceptedits": "acceptEdits",
        "dont-ask": "dontAsk",
        "dontask": "dontAsk",
        "bypass-permissions": "bypassPermissions",
        "bypasspermissions": "bypassPermissions",
    }
    normalized = aliases.get(raw, aliases.get(raw.lower(), raw))
    return normalized if normalized in CLAUDE_CLI_PERMISSION_MODES else "unknown"


def normalize_claude_output_format(value: str | None) -> str:
    raw = str(value or "").strip()
    return raw if raw in CLAUDE_CLI_OUTPUT_FORMATS else "text"


def normalize_claude_input_format(value: str | None) -> str:
    raw = str(value or "").strip()
    return raw if raw in CLAUDE_CLI_INPUT_FORMATS else "text"


def _required_command(value: str) -> str:
    command = str(value or "").strip()
    if not command:
        return CLAUDE_CLI_COMMAND
    return command


def _optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


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


def _cli_arg_present(args: Sequence[str], *flags: str) -> bool:
    flag_set = set(flags)
    for token in args:
        raw = str(token)
        if raw in flag_set:
            return True
        if any(raw.startswith(f"{flag}=") for flag in flag_set):
            return True
    return False


def _configured_or_arg_list(configured: Sequence[str], arg_value: str | None) -> list[str]:
    values = [str(item).strip() for item in configured if str(item).strip()]
    if values:
        return values
    return _split_cli_list(arg_value)


def _configured_or_arg_tools(configured: Sequence[str], arg_value: str | None) -> list[str]:
    if configured:
        values = [str(item).strip() for item in configured]
        if values == [""]:
            return [""]
        return [item for item in values if item]
    if arg_value is None:
        return []
    if not str(arg_value).strip():
        return [""]
    return _split_cli_list(arg_value)


def _split_cli_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [item for item in (part.strip() for part in re.split(r"[,\s]+", value)) if item]


def _join_tools_value(tools: Sequence[str]) -> str | None:
    if not tools:
        return None
    if list(tools) == [""]:
        return ""
    return ",".join(tools)


def _mcp_config_status(path: str | None) -> str:
    if not path:
        return "missing"
    try:
        return "configured" if Path(os.path.expanduser(path)).is_file() else "missing"
    except OSError:
        return "missing"


def _strict_mcp_config(configured: bool | None, args: Sequence[str]) -> bool | str:
    if isinstance(configured, bool):
        return configured
    if not _cli_arg_present(args, "--strict-mcp-config"):
        return "unknown"
    return _normalize_bool_or_unknown(_cli_arg_value(args, "--strict-mcp-config") or "true")


def _normalize_bool_or_unknown(value: str | None) -> bool | str:
    if value is None:
        return "unknown"
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return "unknown"


def _strip_managed_options(tokens: Sequence[str], *, keep_flags: set[str] | None = None) -> list[str]:
    keep_flags = keep_flags or set()
    stripped: list[str] = []
    index = 0
    while index < len(tokens):
        token = str(tokens[index])
        flag = token.split("=", 1)[0]
        if flag in _MANAGED_FLAGS and flag not in keep_flags:
            index += _managed_option_width(tokens, index, flag, token)
            continue
        if token and token not in stripped:
            stripped.append(token)
        index += 1
    return stripped


def _managed_option_width(tokens: Sequence[str], index: int, flag: str, token: str) -> int:
    if "=" in token:
        return 1
    if flag in _MANAGED_VALUE_FLAGS and index + 1 < len(tokens):
        next_token = str(tokens[index + 1])
        if not next_token.startswith("-"):
            return 2
    if flag == "--strict-mcp-config" and index + 1 < len(tokens):
        next_token = str(tokens[index + 1]).strip().lower()
        if next_token in {"1", "0", "true", "false", "yes", "no", "on", "off"}:
            return 2
    return 1


def _append_value(
    argv: list[str],
    flag: str,
    value: str | None,
    *,
    allow_empty: bool = False,
) -> None:
    if value is None or (not allow_empty and not value) or _cli_arg_present(argv, flag):
        return
    argv.extend([flag, value])


def _append_flag(argv: list[str], flag: str) -> None:
    if not _cli_arg_present(argv, flag):
        argv.append(flag)


def _redacted_command_preview(tokens: Sequence[str]) -> str:
    rendered = " ".join(shlex.quote(str(token)) for token in tokens)
    return str(redact_secrets(rendered))
