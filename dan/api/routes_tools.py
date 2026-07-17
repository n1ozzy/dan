"""Tool registry route payloads."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from typing import Any

from dan.daemon.app import DaemonApp
from dan.tools.permissions import RequestSource
from dan.tools.registry import ToolResult


ROUTE_GROUP = "tools"


class ToolRequestValidationError(ValueError):
    """Raised when a tool request payload is malformed."""


def get_tools(app: DaemonApp) -> dict[str, object]:
    return {"tools": [asdict(spec) for spec in app.list_tool_specs()]}


def post_tool_request(app: DaemonApp, request_payload: Any) -> dict[str, object]:
    payload = _validate_request_payload(request_payload)
    # Source is backend-owned audit metadata, never caller-controlled.
    result = app.request_tool(
        tool_name=payload["tool_name"],
        arguments=payload["arguments"],
        requested_by=payload["requested_by"],
        source=RequestSource.DIRECT_USER_COMMAND,
        turn_id=payload.get("turn_id"),
        metadata=payload.get("metadata"),
    )
    return tool_result_to_dict(result)


def tool_result_to_dict(result: ToolResult) -> dict[str, object]:
    return {
        "id": result.id,
        "tool_name": result.tool_name,
        "status": result.status,
        "output": result.output,
        "error": result.error,
    }


def _validate_request_payload(request_payload: Any) -> dict[str, Any]:
    if not isinstance(request_payload, Mapping):
        raise ToolRequestValidationError("Request JSON must be an object.")

    raw_tool_name = request_payload.get("tool_name")
    if not isinstance(raw_tool_name, str) or not raw_tool_name.strip():
        raise ToolRequestValidationError("tool_name must be a non-empty string.")

    raw_arguments = request_payload.get("arguments", {})
    if not isinstance(raw_arguments, Mapping):
        raise ToolRequestValidationError("arguments must be a JSON object.")

    raw_requested_by = request_payload.get("requested_by", "api")
    if not isinstance(raw_requested_by, str) or not raw_requested_by.strip():
        raise ToolRequestValidationError("requested_by must be a non-empty string.")

    payload: dict[str, Any] = {
        "tool_name": raw_tool_name.strip(),
        "arguments": dict(raw_arguments),
        "requested_by": raw_requested_by.strip(),
    }

    if "turn_id" in request_payload and request_payload["turn_id"] is not None:
        turn_id = request_payload["turn_id"]
        if not isinstance(turn_id, str) or not turn_id.strip():
            raise ToolRequestValidationError("turn_id must be a non-empty string.")
        payload["turn_id"] = turn_id.strip()

    if "metadata" in request_payload and request_payload["metadata"] is not None:
        metadata = request_payload["metadata"]
        if not isinstance(metadata, Mapping):
            raise ToolRequestValidationError("metadata must be a JSON object.")
        payload["metadata"] = dict(metadata)
    else:
        payload["metadata"] = None

    return payload


def register_routes(app: object) -> None:
    return None


__all__ = [
    "ROUTE_GROUP",
    "ToolRequestValidationError",
    "get_tools",
    "post_tool_request",
    "register_routes",
    "tool_result_to_dict",
]
