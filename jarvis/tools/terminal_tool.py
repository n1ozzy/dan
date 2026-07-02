"""Terminal/iTerm operator tools (FAZA D5, ADR-021).

Two tools, two risk classes, never merged (the ui_read / ui_act precedent):
- `terminal_read_screen` (`terminal_read`) observes the front window /
  current session of a named terminal app.
- `terminal_paste` (`terminal_write`) pastes a prepared command WITHOUT
  submitting it — pressing Enter stays with the human.

The tools delegate to an injected TerminalBridge backend and re-validate at
this layer: paste text limits and the control-character ban are enforced
here AND in the bridge (two-layer, like the secure-field ban in D2), and
read output is clipped by `sanitize_terminal_snapshot` before it leaves.
Redaction happens downstream in tool_runs/EventStore; the D3 stream never
carries the terminal text (ADR-019 omits bulk output).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from jarvis.macos.terminal import (
    MAX_PASTE_CHARS,
    SUPPORTED_APPS,
    TerminalBridge,
    TerminalError,
    sanitize_terminal_snapshot,
    validate_paste_text,
)
from jarvis.tools.registry import Tool, ToolExecutionError


class TerminalReadScreenTool(Tool):
    name = "terminal_read_screen"
    description = (
        "Read the visible contents of the front window of a named terminal "
        f"app ({', '.join(SUPPORTED_APPS)}). Never launches the app."
    )
    risk = "terminal_read"
    input_schema = {
        "type": "object",
        "properties": {
            "app": {
                "type": "string",
                "enum": list(SUPPORTED_APPS),
                "description": "Terminal application to observe.",
            },
        },
        "required": ["app"],
    }

    def __init__(self, bridge: TerminalBridge):
        self._bridge = bridge

    def run(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        app = _required_text(arguments, "app", "terminal_read_screen")
        try:
            raw = self._bridge.read_screen(app)
        except TerminalError as exc:
            raise ToolExecutionError(f"terminal_read_screen cannot observe: {exc}") from exc
        screen = sanitize_terminal_snapshot(raw)
        return {
            "ok": True,
            "backend": self._bridge.backend,
            "screen": screen,
            "line_count": screen["line_count"],
            "truncated": screen["truncated"],
        }


class TerminalPasteTool(Tool):
    name = "terminal_paste"
    description = (
        "Paste a prepared single-line command into the current session of a "
        "named terminal app WITHOUT executing it (approval-gated); pressing "
        "Enter stays with the user. Refuses control characters."
    )
    risk = "terminal_write"
    input_schema = {
        "type": "object",
        "properties": {
            "app": {
                "type": "string",
                "enum": list(SUPPORTED_APPS),
                "description": "Terminal application to paste into.",
            },
            "text": {
                "type": "string",
                "description": (
                    f"Single printable line to paste (max {MAX_PASTE_CHARS} "
                    "chars, no control characters — it is not submitted)."
                ),
            },
        },
        "required": ["app", "text"],
    }

    def __init__(self, bridge: TerminalBridge):
        self._bridge = bridge

    def run(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        app = _required_text(arguments, "app", "terminal_paste")
        text = arguments.get("text")
        # Second validation layer: the tool never trusts the backend to
        # enforce the paste contract (two-layer, the D2 precedent).
        try:
            validate_paste_text(text)
        except TerminalError as exc:
            raise ToolExecutionError(f"terminal_paste rejects the text: {exc}") from exc
        try:
            result = self._bridge.paste_text(app, text)
        except TerminalError as exc:
            raise ToolExecutionError(f"terminal_paste failed: {exc}") from exc
        # The pasted text is not echoed back: it already lives (redacted) in
        # the tool input; duplicating it in output would double the exposure.
        return {
            "ok": True,
            "backend": self._bridge.backend,
            "pasted": bool(result.get("pasted")),
            "app": str(result.get("app") or app),
            "chars_pasted": len(text),
        }


def _required_text(arguments: Mapping[str, Any], key: str, tool_name: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ToolExecutionError(f"{tool_name} requires a non-empty {key} argument.")
    return value.strip()


__all__ = ["TerminalPasteTool", "TerminalReadScreenTool"]
