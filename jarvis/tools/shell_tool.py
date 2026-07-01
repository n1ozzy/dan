"""Safe shell tool placeholders.

Prompt 13 intentionally does not implement shell execution.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from jarvis.tools.registry import Tool


class ShellReadPlaceholderTool(Tool):
    name = "shell_read_placeholder"
    description = "Placeholder for future read-only shell commands; does not execute."
    risk = "shell_read"
    input_schema = {"type": "object"}

    def run(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"ok": False, "message": "Shell execution is not implemented."}


class ShellWritePlaceholderTool(Tool):
    name = "shell_write_placeholder"
    description = "Placeholder for future mutating shell commands; does not execute."
    risk = "shell_write"
    input_schema = {"type": "object"}

    def run(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"ok": False, "message": "Shell execution is not implemented."}


class ShellTool(ShellReadPlaceholderTool):
    """Backward-compatible placeholder name for the initial scaffold."""


__all__ = ["ShellReadPlaceholderTool", "ShellTool", "ShellWritePlaceholderTool"]
