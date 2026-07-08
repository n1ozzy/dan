"""Executable Claude CLI command contract shared by adapter and projections."""

from __future__ import annotations

import os
import re
import shlex
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from jarvis.security.redaction import redact_secrets


CLAUDE_CLI_COMMAND = "claude"


class ClaudeCliEffortLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"


class ClaudeCliPermissionMode(StrEnum):
    DEFAULT = "default"
    MANUAL = "manual"
    ACCEPT_EDITS = "acceptEdits"
    PLAN = "plan"
    AUTO = "auto"
    DONT_ASK = "dontAsk"
    BYPASS_PERMISSIONS = "bypassPermissions"


class ClaudeCliOutputFormat(StrEnum):
    TEXT = "text"
    JSON = "json"
    STREAM_JSON = "stream-json"


class ClaudeCliInputFormat(StrEnum):
    TEXT = "text"
    STREAM_JSON = "stream-json"


CLAUDE_CLI_EFFORTS = tuple(ClaudeCliEffortLevel)
CLAUDE_CLI_PERMISSION_MODES = tuple(ClaudeCliPermissionMode)
CLAUDE_CLI_OUTPUT_FORMATS = tuple(ClaudeCliOutputFormat)
CLAUDE_CLI_INPUT_FORMATS = tuple(ClaudeCliInputFormat)
UNKNOWN_CLAUDE_CLI_VALUE = "unknown"
DEFAULT_STREAM_ARGS = (
    "--output-format",
    ClaudeCliOutputFormat.STREAM_JSON.value,
    "--verbose",
    "--include-partial-messages",
)
INTERNAL_MODEL_SENTINELS = frozenset({"claude-cli"})

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
    flag_metadata: list[dict[str, Any]]
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
    runtime_model = runtime_model if runtime_model is not None else _request_model(request_settings)
    runtime_effort = runtime_effort if runtime_effort is not None else request_settings.get("effort")

    args_model = _cli_arg_value(args, "--model")
    requested_model = _non_sentinel_model(_optional_text(runtime_model))
    configured_model = _non_sentinel_model(_optional_text(settings.model))
    args_model = _non_sentinel_model(args_model)
    selected_model = requested_model or args_model or configured_model
    effective_model = selected_model
    model_source = "jarvis_explicit" if selected_model else "claude_default"

    args_effort = _cli_arg_value(args, "--effort")
    configured_effort = _optional_text(settings.effort)
    requested_effort = _optional_text(runtime_effort)
    selected_effort = requested_effort or configured_effort or args_effort
    effective_effort_enum = _parse_claude_effort(selected_effort)
    effective_effort = effective_effort_enum.value if effective_effort_enum is not None else None
    effort_source = (
        "jarvis_explicit"
        if effective_effort
        else ("model_default" if CLAUDE_CLI_EFFORTS else "unsupported")
    )

    permission_raw = _optional_text(settings.permission_mode) or _cli_arg_value(args, "--permission-mode")
    permission_mode = normalize_claude_permission_mode(permission_raw)
    tools = _configured_or_arg_tools(settings.tools, _cli_arg_value(args, "--tools"))
    allowed_tools = _configured_or_arg_tool_selectors(
        settings.allowed_tools,
        _cli_arg_value(args, "--allowedTools", "--allowed-tools"),
    )
    disallowed_tools = _configured_or_arg_tool_selectors(
        settings.disallowed_tools,
        _cli_arg_value(args, "--disallowedTools", "--disallowed-tools"),
    )
    mcp_config_path = _optional_text(settings.mcp_config_path) or _cli_arg_value(args, "--mcp-config")
    mcp_config_status = _mcp_config_status(mcp_config_path)
    strict_mcp_config = _strict_mcp_config(settings.strict_mcp_config, args)

    args_output_format = _cli_arg_value(args, "--output-format")
    if streaming:
        output_format = ClaudeCliOutputFormat.STREAM_JSON.value
    else:
        output_format = normalize_claude_output_format(
            _optional_text(settings.output_format)
            or args_output_format
            or ClaudeCliOutputFormat.TEXT.value
        )
    args_input_format = _cli_arg_value(args, "--input-format")
    stream_input_format = _cli_arg_value(stream_args, "--input-format")
    input_format = normalize_claude_input_format(
        _optional_text(settings.input_format)
        or (stream_input_format if streaming else None)
        or args_input_format
        or ClaudeCliInputFormat.TEXT.value
    )

    argv = [command, *_strip_managed_options(args)]
    _append_value(argv, "--model", selected_model)
    _append_value(argv, "--effort", effective_effort)
    _append_value(
        argv,
        "--permission-mode",
        permission_mode
        if permission_mode
        not in {ClaudeCliPermissionMode.DEFAULT.value, UNKNOWN_CLAUDE_CLI_VALUE}
        else None,
    )
    _append_value(argv, "--tools", _join_tools_value(tools), allow_empty=True)
    _append_value(argv, "--allowedTools", ",".join(allowed_tools) if allowed_tools else None)
    _append_value(argv, "--disallowedTools", ",".join(disallowed_tools) if disallowed_tools else None)
    _append_value(argv, "--mcp-config", mcp_config_path)
    if strict_mcp_config is True:
        _append_flag(argv, "--strict-mcp-config")

    output_format_explicit = bool(
        streaming or settings.output_format or args_output_format
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
    streaming_supported = "yes" if output_format == ClaudeCliOutputFormat.STREAM_JSON.value else "no"
    return ClaudeCliCommandContract(
        argv=argv,
        command_preview=_redacted_command_preview(argv),
        flag_metadata=_flag_metadata(
            selected_model=selected_model,
            requested_model=requested_model,
            args_model=args_model,
            configured_model=configured_model,
            selected_effort=selected_effort,
            effective_effort=effective_effort,
            requested_effort=requested_effort,
            args_effort=args_effort,
            configured_effort=configured_effort,
            permission_mode=permission_mode,
            permission_raw=permission_raw,
            tools=tools,
            allowed_tools=allowed_tools,
            disallowed_tools=disallowed_tools,
            mcp_config_path=mcp_config_path,
            strict_mcp_config=strict_mcp_config,
            output_format=output_format,
            output_format_explicit=output_format_explicit,
            input_format=input_format,
            input_format_explicit=input_format_explicit,
            streaming=streaming,
            partial_messages_supported=partial_messages_supported,
        ),
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
        return ClaudeCliPermissionMode.DEFAULT.value
    aliases = {
        "accept-edits": ClaudeCliPermissionMode.ACCEPT_EDITS.value,
        "acceptedits": ClaudeCliPermissionMode.ACCEPT_EDITS.value,
        "dont-ask": ClaudeCliPermissionMode.DONT_ASK.value,
        "dontask": ClaudeCliPermissionMode.DONT_ASK.value,
        "bypass-permissions": ClaudeCliPermissionMode.BYPASS_PERMISSIONS.value,
        "bypasspermissions": ClaudeCliPermissionMode.BYPASS_PERMISSIONS.value,
    }
    normalized = aliases.get(raw, aliases.get(raw.lower(), raw))
    try:
        return ClaudeCliPermissionMode(normalized).value
    except ValueError:
        return UNKNOWN_CLAUDE_CLI_VALUE


def normalize_claude_output_format(value: str | None) -> str:
    raw = str(value or "").strip()
    try:
        return ClaudeCliOutputFormat(raw).value
    except ValueError:
        return ClaudeCliOutputFormat.TEXT.value


def normalize_claude_input_format(value: str | None) -> str:
    raw = str(value or "").strip()
    try:
        return ClaudeCliInputFormat(raw).value
    except ValueError:
        return ClaudeCliInputFormat.TEXT.value


def _parse_claude_effort(value: str | None) -> ClaudeCliEffortLevel | None:
    raw = _optional_text(value)
    if raw is None:
        return None
    try:
        return ClaudeCliEffortLevel(raw)
    except ValueError:
        return None


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


def _configured_or_arg_tool_selectors(
    configured: Sequence[str],
    arg_value: str | None,
) -> list[str]:
    values = [str(item).strip() for item in configured if str(item).strip()]
    if values:
        return values
    if arg_value is None:
        return []
    normalized = str(arg_value).strip()
    return [normalized] if normalized else []


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


def _flag_metadata(
    *,
    selected_model: str | None,
    requested_model: str | None,
    args_model: str | None,
    configured_model: str | None,
    selected_effort: str | None,
    effective_effort: str | None,
    requested_effort: str | None,
    args_effort: str | None,
    configured_effort: str | None,
    permission_mode: str,
    permission_raw: str | None,
    tools: Sequence[str],
    allowed_tools: Sequence[str],
    disallowed_tools: Sequence[str],
    mcp_config_path: str | None,
    strict_mcp_config: bool | str,
    output_format: str,
    output_format_explicit: bool,
    input_format: str,
    input_format_explicit: bool,
    streaming: bool,
    partial_messages_supported: str,
) -> list[dict[str, Any]]:
    metadata: list[dict[str, Any]] = []
    _append_flag_metadata(
        metadata,
        "--model",
        included=bool(selected_model),
        source=_source_for_first(
            ("request_settings", requested_model),
            ("args", args_model),
            ("config", configured_model),
        ),
        reason=(
            "Jarvis-selected model is explicit."
            if requested_model
            else (
                "Model is configured on the Claude CLI contract."
                if configured_model
                else "No explicit Jarvis model; Claude CLI default is allowed."
            )
        ),
    )
    _append_flag_metadata(
        metadata,
        "--effort",
        included=bool(effective_effort),
        source=_source_for_first(
            ("request_settings", requested_effort),
            ("config", configured_effort),
            ("args", args_effort),
        ),
        reason=(
            "Effort is known and supported."
            if effective_effort
            else (
                "Effort is unknown or unsupported; no explicit effort flag is emitted."
                if selected_effort
                else "No explicit effort selected."
            )
        ),
    )
    _append_flag_metadata(
        metadata,
        "--permission-mode",
        included=permission_mode not in {"default", "unknown"},
        source="config_or_args" if permission_raw else "default",
        reason=(
            "Permission mode is configured."
            if permission_mode not in {"default", "unknown"}
            else "No known permission mode is configured."
        ),
    )
    _append_flag_metadata(
        metadata,
        "--tools",
        included=bool(tools),
        source="config_or_args" if tools else "default",
        reason="Tool allow mode is explicit." if tools else "No tool allow mode configured.",
    )
    _append_flag_metadata(
        metadata,
        "--allowedTools",
        included=bool(allowed_tools),
        source="config_or_args" if allowed_tools else "default",
        reason="Allowed tool selectors are configured." if allowed_tools else "No allowed tool selectors configured.",
    )
    _append_flag_metadata(
        metadata,
        "--disallowedTools",
        included=bool(disallowed_tools),
        source="config_or_args" if disallowed_tools else "default",
        reason="Disallowed tool selectors are configured." if disallowed_tools else "No disallowed tool selectors configured.",
    )
    _append_flag_metadata(
        metadata,
        "--mcp-config",
        included=bool(mcp_config_path),
        source="config_or_args" if mcp_config_path else "default",
        reason="MCP config path is explicit." if mcp_config_path else "No MCP config path configured.",
    )
    _append_flag_metadata(
        metadata,
        "--strict-mcp-config",
        included=strict_mcp_config is True,
        source="config_or_args" if strict_mcp_config != "unknown" else "unknown",
        reason=(
            "Strict MCP config is enabled."
            if strict_mcp_config is True
            else "Strict MCP config is not enabled."
        ),
    )
    _append_flag_metadata(
        metadata,
        "--output-format",
        included=output_format_explicit,
        source="streaming" if streaming else ("config_or_args" if output_format_explicit else "default"),
        reason=(
            "Streaming requires stream-json output."
            if streaming
            else (
                "Output format is explicitly configured."
                if output_format_explicit
                else "No output format flag is needed for Claude CLI text default."
            )
        ),
    )
    _append_flag_metadata(
        metadata,
        "--input-format",
        included=input_format_explicit,
        source="config_or_args" if input_format_explicit else "default",
        reason=(
            "Input format is explicitly configured."
            if input_format_explicit
            else "No input format flag is needed for Claude CLI text default."
        ),
    )
    _append_flag_metadata(
        metadata,
        "--include-partial-messages",
        included=partial_messages_supported == "yes",
        source="streaming" if partial_messages_supported == "yes" else "default",
        reason=(
            "Streaming partial messages are enabled."
            if partial_messages_supported == "yes"
            else "Partial messages are not enabled."
        ),
    )
    return metadata


def _append_flag_metadata(
    metadata: list[dict[str, Any]],
    flag: str,
    *,
    included: bool,
    source: str,
    reason: str,
) -> None:
    metadata.append(
        {
            "flag": flag,
            "included": bool(included),
            "source": source,
            "reason": reason,
        }
    )


def _source_for_first(*candidates: tuple[str, str | None]) -> str:
    for source, value in candidates:
        if value:
            return source
    return "default"


def _redacted_command_preview(tokens: Sequence[str]) -> str:
    rendered = " ".join(shlex.quote(str(token)) for token in tokens)
    return str(redact_secrets(rendered))
