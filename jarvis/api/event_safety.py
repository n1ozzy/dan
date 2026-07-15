"""Client-safe event payload shaping shared by REST and WebSocket."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from jarvis.events.models import Event
from jarvis.security.redaction import REDACTION_PLACEHOLDER, redact_secrets


_UNSAFE_CLIENT_PAYLOAD_KEYS = {
    "args",
    "arguments",
    "authorization",
    "auth",
    "cookie",
    "cookies",
    "content",
    "credentials",
    "credential",
    "env",
    "environment",
    "headers",
    "header",
    "input",
    "input_json",
    "log",
    "logs",
    "output",
    "output_json",
    "raw_args",
    "raw_output",
    "raw_payload",
    "raw_result",
    "request",
    "result",
    "result_summary",
    "screen",
    "stderr",
    "stdout",
    "token",
    "tokens",
    "window",
}

_UNSAFE_CLIENT_PAYLOAD_KEY_FRAGMENTS = (
    "api_key",
    "apikey",
    "auth",
    "cookie",
    "credential",
    "header",
    "password",
    "private_key",
    "secret",
    "token",
)

_SAFE_TOOL_RESULT_SUMMARY_KEYS = (
    "ok",
    "clicked",
    "focused",
    "pasted",
    "truncated",
    "replaced_existing",
    "element_count",
    "line_count",
    "chars_typed",
    "chars_pasted",
    "bytes_written",
    "size_bytes",
    "returned_bytes",
    "returncode",
    "status",
)
_MAX_TOOL_RESULT_SUMMARY_CHARS = 160


def safe_event_payload_for_client(event: Event) -> dict[str, Any]:
    """Return an event payload suitable for panel/API clients.

    EventStore keeps the durable payload for audit/debugging; client surfaces
    get a narrower projection so raw tool output, args, tokens, headers,
    cookies, env values, and similar high-risk fields do not cross the UI/API
    boundary. Unsafe keys are retained with redacted values so clients can see
    the field existed without leaking secrets. Finished tools additionally get
    a bounded summary built only from explicitly allowed boolean/numeric fields.
    """

    payload = _redact_unsafe_client_fields(event.payload)
    payload.pop("result_summary", None)
    if _has_output_key(event.payload):
        payload["output_omitted"] = True
    result_summary = _safe_tool_result_summary(event)
    if result_summary:
        payload["result_summary"] = result_summary
    return redact_secrets(payload)


def _safe_tool_result_summary(event: Event) -> str:
    if event.type != "tool.finished":
        return ""
    output = event.payload.get("output")
    if not isinstance(output, Mapping):
        return ""

    parts: list[str] = []
    for key in _SAFE_TOOL_RESULT_SUMMARY_KEYS:
        value = output.get(key)
        if isinstance(value, bool):
            rendered = "true" if value else "false"
        elif isinstance(value, int):
            rendered = str(value)
        elif isinstance(value, float) and math.isfinite(value):
            rendered = str(value)
        else:
            continue
        parts.append(f"{key}={rendered}")

    summary = " · ".join(parts)
    if len(summary) <= _MAX_TOOL_RESULT_SUMMARY_CHARS:
        return summary
    return f"{summary[: _MAX_TOOL_RESULT_SUMMARY_CHARS - 3]}..."


def _has_output_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key in value:
            if _normalize_key(key) == "output":
                return True
            if _has_output_key(value[key]):
                return True
    elif _sequence(value):
        for item in value:
            if _has_output_key(item):
                return True
    return False


def _redact_unsafe_client_fields(value: Any) -> Any:
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            normalized_key = _normalize_key(key_text)
            if _unsafe_client_payload_key(normalized_key):
                redacted[key_text] = REDACTION_PLACEHOLDER
            else:
                redacted[key_text] = _redact_unsafe_client_fields(item)
        return redacted
    if _sequence(value):
        return [_redact_unsafe_client_fields(item) for item in value]
    return value


def _unsafe_client_payload_key(normalized_key: str) -> bool:
    return normalized_key in _UNSAFE_CLIENT_PAYLOAD_KEYS or any(
        fragment in normalized_key for fragment in _UNSAFE_CLIENT_PAYLOAD_KEY_FRAGMENTS
    )


def _normalize_key(key: str) -> str:
    return key.strip().lower().replace("-", "_").replace(" ", "_")


def _sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray)


__all__ = ["safe_event_payload_for_client"]
