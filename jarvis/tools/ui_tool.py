"""Read-only UI observation tools (FAZA D1, risk class ui_read).

The tools delegate to an injected AccessibilityReader backend and sanitize
every result at this layer — secure text field values never reach tool_runs
regardless of which backend produced the snapshot.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from jarvis.macos.accessibility import (
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


__all__ = ["UiActiveAppTool", "UiReadWindowTool"]
