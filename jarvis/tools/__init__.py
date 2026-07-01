"""Jarvis tool registry and safe placeholder tools."""

from __future__ import annotations

from jarvis.tools.permissions import (
    PermissionClass,
    PermissionPolicy,
    ToolDecision,
    ToolPermissionError,
    ToolPermissionPolicy,
    ToolPermissionResult,
)
from jarvis.tools.registry import (
    ApprovalGate,
    EchoTool,
    Tool,
    ToolExecutionError,
    ToolRegistry,
    ToolRegistryError,
    ToolRequest,
    ToolResult,
    ToolRunRecorder,
    ToolSpec,
)
from jarvis.tools.system_tool import SystemStatusTool


def create_default_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(EchoTool())
    registry.register(SystemStatusTool())
    return registry


__all__ = [
    "ApprovalGate",
    "EchoTool",
    "PermissionClass",
    "PermissionPolicy",
    "SystemStatusTool",
    "Tool",
    "ToolDecision",
    "ToolExecutionError",
    "ToolPermissionError",
    "ToolPermissionPolicy",
    "ToolPermissionResult",
    "ToolRegistry",
    "ToolRegistryError",
    "ToolRequest",
    "ToolResult",
    "ToolRunRecorder",
    "ToolSpec",
    "create_default_tool_registry",
]
