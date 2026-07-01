"""Parse explicit Jarvis tool-call blocks from provider text output."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from jarvis.brain.base import BrainToolCall


TOOL_CALL_OPEN = "<jarvis_tool_call>"
TOOL_CALL_CLOSE = "</jarvis_tool_call>"
TOOL_CALL_PATTERN = re.compile(
    rf"{re.escape(TOOL_CALL_OPEN)}(.*?){re.escape(TOOL_CALL_CLOSE)}",
    re.DOTALL,
)
FALLBACK_TOOL_REQUEST_TEXT = "Jarvis requested tool approval."


@dataclass(frozen=True)
class ToolCallParseResult:
    text: str
    tool_calls: list[BrainToolCall] = field(default_factory=list)
    parse_errors: list[str] = field(default_factory=list)


def parse_tool_call_blocks(provider_output: str) -> ToolCallParseResult:
    """Extract explicit Jarvis tool-call blocks without executing anything."""

    if not isinstance(provider_output, str):
        provider_output = str(provider_output)

    matches = list(TOOL_CALL_PATTERN.finditer(provider_output))
    if not matches:
        return ToolCallParseResult(text=provider_output.strip())

    visible_parts: list[str] = []
    tool_calls: list[BrainToolCall] = []
    parse_errors: list[str] = []
    cursor = 0

    for block_index, match in enumerate(matches, start=1):
        visible_parts.append(provider_output[cursor : match.start()])
        cursor = match.end()
        call, error = _parse_single_block(match.group(1), block_index)
        if error is not None:
            parse_errors.append(error)
            continue
        if call is not None:
            tool_calls.append(call)

    visible_parts.append(provider_output[cursor:])
    visible_text = _clean_visible_text("".join(visible_parts))
    if not visible_text:
        visible_text = FALLBACK_TOOL_REQUEST_TEXT

    return ToolCallParseResult(
        text=visible_text,
        tool_calls=tool_calls,
        parse_errors=parse_errors,
    )


def _parse_single_block(payload_text: str, block_index: int) -> tuple[BrainToolCall | None, str | None]:
    try:
        payload = json.loads(payload_text.strip(), parse_constant=_reject_json_constant)
    except json.JSONDecodeError as exc:
        return None, f"tool call block {block_index}: invalid JSON: {exc.msg}"
    except ValueError as exc:
        return None, f"tool call block {block_index}: invalid JSON: {exc}"

    if not isinstance(payload, Mapping):
        return None, f"tool call block {block_index}: payload must be a JSON object"

    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        return None, f"tool call block {block_index}: name must be a non-empty string"

    arguments = payload.get("arguments", {})
    if not isinstance(arguments, Mapping):
        return None, f"tool call block {block_index}: arguments must be a JSON object"

    call_id, error = _optional_text(payload, "id", f"provider-tool-call-{block_index}", block_index)
    if error is not None:
        return None, error

    risk, error = _optional_text(payload, "risk", "safe_read", block_index)
    if error is not None:
        return None, error

    return (
        BrainToolCall(
            id=call_id,
            name=name.strip(),
            arguments=dict(arguments),
            risk=risk,
        ),
        None,
    )


def _optional_text(
    payload: Mapping[str, Any],
    key: str,
    default: str,
    block_index: int,
) -> tuple[str, str | None]:
    value = payload.get(key, default)
    if value is None:
        return default, None
    if not isinstance(value, str):
        return default, f"tool call block {block_index}: {key} must be a string"
    value = value.strip()
    return value or default, None


def _clean_visible_text(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        return ""
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n[ \t]+", "\n", cleaned)
    cleaned = re.sub(r"\n{2,}", "\n", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned.strip()


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


__all__ = [
    "FALLBACK_TOOL_REQUEST_TEXT",
    "ToolCallParseResult",
    "parse_tool_call_blocks",
]
