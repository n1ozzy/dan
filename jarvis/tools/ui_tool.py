"""Read-only UI observation tools (FAZA D1, risk class ui_read).

The tools delegate to an injected AccessibilityReader backend and sanitize
every result at this layer — secure text field values never reach tool_runs
regardless of which backend produced the snapshot.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from jarvis.macos.accessibility import (
    MAX_TYPE_CHARS,
    AccessibilityActor,
    AccessibilityError,
    AccessibilityReader,
    sanitize_app_snapshot,
    sanitize_window_snapshot,
)
from jarvis.tools.registry import Tool, ToolExecutionError


class UiActiveAppTool(Tool):
    name = "ui_active_app"
    description = "Report the frontmost application (name, bundle id, pid)."
    risk = "ui_read"
    input_schema = {"type": "object", "properties": {}}

    def __init__(self, reader: AccessibilityReader):
        self._reader = reader

    def run(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        try:
            raw = self._reader.active_app()
        except AccessibilityError as exc:
            raise ToolExecutionError(f"ui_active_app cannot observe the UI: {exc}") from exc
        return {
            "ok": True,
            "backend": self._reader.backend,
            "app": sanitize_app_snapshot(raw),
        }


class UiReadWindowTool(Tool):
    name = "ui_read_window"
    description = (
        "Read the focused window of the frontmost application: title and "
        "visible controls. Secure text field values are never returned."
    )
    risk = "ui_read"
    input_schema = {"type": "object", "properties": {}}

    def __init__(self, reader: AccessibilityReader):
        self._reader = reader

    def run(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        try:
            raw = self._reader.focused_window()
        except AccessibilityError as exc:
            raise ToolExecutionError(f"ui_read_window cannot observe the UI: {exc}") from exc
        window = sanitize_window_snapshot(raw)
        return {
            "ok": True,
            "backend": self._reader.backend,
            "window": window,
            "element_count": len(window["elements"]),
            "truncated": window["truncated"],
        }


class UiClickTool(Tool):
    name = "ui_click"
    description = (
        "Click a control in the focused window of the frontmost application, "
        "matched by its visible label (approval-gated)."
    )
    risk = "ui_act"
    input_schema = {
        "type": "object",
        "properties": {
            "label": {"type": "string", "description": "Visible label of the control."},
            "role": {
                "type": "string",
                "description": "Optional AX role filter, e.g. AXButton.",
            },
        },
        "required": ["label"],
    }

    def __init__(self, actor: AccessibilityActor):
        self._actor = actor

    def run(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        label = _required_text(arguments, "label", "ui_click")
        role = arguments.get("role")
        if role is not None and (not isinstance(role, str) or not role.strip()):
            raise ToolExecutionError("ui_click role must be a non-empty string when given.")
        try:
            result = self._actor.click(label=label, role=role.strip() if role else None)
        except AccessibilityError as exc:
            raise ToolExecutionError(f"ui_click failed: {exc}") from exc
        return {
            "ok": True,
            "backend": self._actor.backend,
            "clicked": bool(result.get("clicked")),
            "label": label,
        }


class UiTypeTool(Tool):
    name = "ui_type"
    description = (
        "Type text into the focused element of the frontmost application "
        "(approval-gated). Refuses secure text fields; never types passwords."
    )
    risk = "ui_act"
    input_schema = {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": f"Text to type (max {MAX_TYPE_CHARS} chars).",
            },
        },
        "required": ["text"],
    }

    def __init__(self, actor: AccessibilityActor):
        self._actor = actor

    def run(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        text = arguments.get("text")
        if not isinstance(text, str) or not text:
            raise ToolExecutionError("ui_type requires non-empty string text.")
        if len(text) > MAX_TYPE_CHARS:
            raise ToolExecutionError(f"ui_type text exceeds {MAX_TYPE_CHARS} characters.")
        try:
            self._actor.type_text(text)
        except AccessibilityError as exc:
            raise ToolExecutionError(f"ui_type failed: {exc}") from exc
        # The typed text is not echoed back: it already lives (redacted) in
        # the tool input; duplicating it in output would double the exposure.
        return {
            "ok": True,
            "backend": self._actor.backend,
            "chars_typed": len(text),
        }


class UiFocusAppTool(Tool):
    name = "ui_focus_app"
    description = "Bring a running application to the front by name (approval-gated)."
    risk = "ui_act"
    input_schema = {
        "type": "object",
        "properties": {
            "app_name": {"type": "string", "description": "Exact application name."},
        },
        "required": ["app_name"],
    }

    def __init__(self, actor: AccessibilityActor):
        self._actor = actor

    def run(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        app_name = _required_text(arguments, "app_name", "ui_focus_app")
        try:
            result = self._actor.focus_app(app_name)
        except AccessibilityError as exc:
            raise ToolExecutionError(f"ui_focus_app failed: {exc}") from exc
        return {
            "ok": True,
            "backend": self._actor.backend,
            "focused": bool(result.get("focused")),
            "app_name": app_name,
        }


def _required_text(arguments: Mapping[str, Any], key: str, tool_name: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ToolExecutionError(f"{tool_name} requires a non-empty {key} argument.")
    return value.strip()


__all__ = [
    "UiActiveAppTool",
    "UiClickTool",
    "UiFocusAppTool",
    "UiReadWindowTool",
    "UiTypeTool",
]
