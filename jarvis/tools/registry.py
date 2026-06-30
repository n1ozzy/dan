"""Tool registry placeholder."""

from __future__ import annotations

from dataclasses import dataclass

from jarvis.tools.permissions import PermissionClass


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    permission: PermissionClass


class ToolRegistry:
    def get(self, name: str) -> ToolDefinition:
        raise NotImplementedError(f"tool is not registered yet: {name}")
