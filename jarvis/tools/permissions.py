"""Deterministic permission decisions for Jarvis tools."""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ToolPermissionError(Exception):
    """Raised when a permission policy cannot make a safe decision."""


class ToolDecision(StrEnum):
    ALLOW = "allow"
    APPROVAL_REQUIRED = "approval_required"
    BLOCKED = "blocked"


class PermissionClass(StrEnum):
    SAFE_READ = "safe_read"
    SAFE_STATUS = "safe_status"
    FILE_READ = "file_read"
    FILE_WRITE = "file_write"
    SHELL_READ = "shell_read"
    SHELL_WRITE = "shell_write"
    NETWORK = "network"
    DESTRUCTIVE = "destructive"


@dataclass(frozen=True)
class ToolPermissionResult:
    decision: str
    risk: str
    reason: str
    approval_required: bool = False
    blocked: bool = False


class ToolPermissionPolicy:
    """Default Jarvis v4.1 tool safety policy.

    This policy only classifies requests. It does not execute commands, inspect
    processes, mutate files, or make network calls.
    """

    def __init__(
        self,
        *,
        destructive_tools_enabled: bool = False,
        approved_roots: Iterable[str] | None = None,
    ):
        self.destructive_tools_enabled = destructive_tools_enabled
        self.approved_roots = tuple(_normalize_root(root) for root in approved_roots or ())

    def decide(
        self,
        risk: str,
        *,
        tool_name: str,
        payload: Mapping[str, Any] | None = None,
    ) -> ToolPermissionResult:
        normalized_risk = str(risk)

        if normalized_risk in {PermissionClass.SAFE_READ, PermissionClass.SAFE_STATUS}:
            return _allow(normalized_risk, f"{tool_name} is classified as {normalized_risk}.")

        if normalized_risk == PermissionClass.FILE_READ:
            return self._decide_file_read(tool_name=tool_name, payload=payload)

        if normalized_risk in {
            PermissionClass.FILE_WRITE,
            PermissionClass.SHELL_READ,
            PermissionClass.SHELL_WRITE,
            PermissionClass.NETWORK,
        }:
            return _approval_required(
                normalized_risk,
                f"{tool_name} requires human approval for {normalized_risk}.",
            )

        if normalized_risk == PermissionClass.DESTRUCTIVE:
            if self.destructive_tools_enabled:
                return _approval_required(
                    normalized_risk,
                    f"{tool_name} is destructive and requires explicit approval.",
                )
            return _blocked(
                normalized_risk,
                f"{tool_name} is destructive and destructive tools are disabled.",
            )

        return _blocked(normalized_risk, f"{tool_name} has unknown risk: {normalized_risk}.")

    def _decide_file_read(
        self,
        *,
        tool_name: str,
        payload: Mapping[str, Any] | None,
    ) -> ToolPermissionResult:
        if not self.approved_roots:
            return _allow("file_read", f"{tool_name} file_read allowed by placeholder root policy.")

        path_value = payload.get("path") if isinstance(payload, Mapping) else None
        if not isinstance(path_value, str) or not path_value.strip():
            return _blocked("file_read", f"{tool_name} file_read requires a path.")

        candidate = _normalize_root(path_value)
        for approved_root in self.approved_roots:
            if _is_within_root(candidate, approved_root):
                return _allow("file_read", f"{tool_name} file_read path is under an approved root.")

        return _blocked("file_read", f"{tool_name} file_read path is outside approved roots.")


class PermissionPolicy(ToolPermissionPolicy):
    """Backward-compatible alias for the initial scaffold policy."""

    def requires_approval(self, permission: PermissionClass) -> bool:
        result = self.decide(str(permission), tool_name="legacy_permission_check", payload={})
        return result.decision == ToolDecision.APPROVAL_REQUIRED


def _allow(risk: str, reason: str) -> ToolPermissionResult:
    return ToolPermissionResult(decision=ToolDecision.ALLOW, risk=risk, reason=reason)


def _approval_required(risk: str, reason: str) -> ToolPermissionResult:
    return ToolPermissionResult(
        decision=ToolDecision.APPROVAL_REQUIRED,
        risk=risk,
        reason=reason,
        approval_required=True,
    )


def _blocked(risk: str, reason: str) -> ToolPermissionResult:
    return ToolPermissionResult(
        decision=ToolDecision.BLOCKED,
        risk=risk,
        reason=reason,
        blocked=True,
    )


def _normalize_root(path: str) -> str:
    return os.path.abspath(os.path.expanduser(path))


def _is_within_root(candidate: str, approved_root: str) -> bool:
    try:
        return os.path.commonpath([candidate, approved_root]) == approved_root
    except ValueError:
        return False


__all__ = [
    "PermissionClass",
    "PermissionPolicy",
    "ToolDecision",
    "ToolPermissionError",
    "ToolPermissionPolicy",
    "ToolPermissionResult",
]
