"""Safe system status placeholder tool."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from jarvis.tools.registry import Tool


class SystemStatusTool(Tool):
    name = "system_status"
    description = "Return a static Jarvis system status placeholder."
    risk = "safe_status"
    input_schema = {"type": "object"}

    def run(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"ok": True, "message": "Jarvis system status placeholder"}


class SystemTool(SystemStatusTool):
    """Backward-compatible placeholder name for the initial scaffold."""


__all__ = ["SystemStatusTool", "SystemTool"]
