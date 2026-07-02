"""Read-only screen OCR tools (FAZA D4, risk class screen_read).

The tools delegate to an injected ScreenReader backend and sanitize every
result at this layer — OCR text is clipped here and redacted downstream in
tool_runs/EventStore. Captured pixels never appear in tool output; the D3
stream never carries the OCR text either (ADR-019 omits bulk output).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from jarvis.macos.screen import (
    MAX_REGION_ORIGIN,
    MAX_REGION_SIZE,
    ScreenReader,
    ScreenReadError,
    sanitize_ocr_snapshot,
)
from jarvis.tools.registry import Tool, ToolExecutionError


class ScreenReadWindowTool(Tool):
    name = "screen_read_window"
    description = (
        "Capture the frontmost window and return its text via on-device OCR. "
        "The capture image is transient and never stored."
    )
    risk = "screen_read"
    input_schema = {"type": "object", "properties": {}}

    def __init__(self, reader: ScreenReader):
        self._reader = reader

    def run(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        try:
            raw = self._reader.read_window()
        except ScreenReadError as exc:
            raise ToolExecutionError(f"screen_read_window cannot capture: {exc}") from exc
        screen = sanitize_ocr_snapshot(raw)
        return {
            "ok": True,
            "backend": self._reader.backend,
            "screen": screen,
            "line_count": screen["line_count"],
            "truncated": screen["truncated"],
        }


class ScreenOcrRegionTool(Tool):
    name = "screen_ocr_region"
    description = (
        "Capture a named screen region (x, y, width, height in points) and "
        "return its text via on-device OCR. The capture image is transient "
        "and never stored."
    )
    risk = "screen_read"
    input_schema = {
        "type": "object",
        "properties": {
            "x": {"type": "integer", "description": "Region origin X (points)."},
            "y": {"type": "integer", "description": "Region origin Y (points)."},
            "width": {"type": "integer", "description": "Region width (points)."},
            "height": {"type": "integer", "description": "Region height (points)."},
        },
        "required": ["x", "y", "width", "height"],
    }

    def __init__(self, reader: ScreenReader):
        self._reader = reader

    def run(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        x = _bounded_int(arguments, "x", 0, MAX_REGION_ORIGIN)
        y = _bounded_int(arguments, "y", 0, MAX_REGION_ORIGIN)
        width = _bounded_int(arguments, "width", 1, MAX_REGION_SIZE)
        height = _bounded_int(arguments, "height", 1, MAX_REGION_SIZE)
        try:
            raw = self._reader.read_region(x=x, y=y, width=width, height=height)
        except ScreenReadError as exc:
            raise ToolExecutionError(f"screen_ocr_region cannot capture: {exc}") from exc
        screen = sanitize_ocr_snapshot(raw)
        return {
            "ok": True,
            "backend": self._reader.backend,
            "screen": screen,
            "line_count": screen["line_count"],
            "truncated": screen["truncated"],
        }


def _bounded_int(arguments: Mapping[str, Any], key: str, minimum: int, maximum: int) -> int:
    value = arguments.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolExecutionError(f"screen_ocr_region requires an integer {key}.")
    if value < minimum or value > maximum:
        raise ToolExecutionError(
            f"screen_ocr_region {key} must be between {minimum} and {maximum}."
        )
    return value


__all__ = ["ScreenOcrRegionTool", "ScreenReadWindowTool"]
