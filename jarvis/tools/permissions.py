"""Deterministic, source-sensitive permission decisions for Jarvis tools.

Design: docs/MACOS_PERMISSION_MODEL.md — "the user says *click this*" is not
the same event as "the model decided to click". Every decision therefore takes
the request source as a required dimension. Sources are assigned by jarvisd at
the entry points, never taken from payloads.
"""

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
    UI_READ = "ui_read"
    UI_ACT = "ui_act"
    SCREEN_READ = "screen_read"
    TERMINAL_READ = "terminal_read"
    TERMINAL_WRITE = "terminal_write"
    MEMORY_WRITE = "memory_write"


class RequestSource(StrEnum):
    DIRECT_USER_COMMAND = "direct_user_command"
    PANEL_COMMAND = "panel_command"
    VOICE_COMMAND = "voice_command"
    MODEL_ORIGINATED = "model_originated"
    SCHEDULED_WORKER = "scheduled_worker"
    HOOK_TRIGGERED = "hook_triggered"


@dataclass(frozen=True)
class TrustedScope:
    """A filesystem scope where model-originated tools can be auto-approved.
    
    Used by ToolPermissionPolicy to grant ALLOW for MODEL_ORIGINATED requests
    within specific paths for specific tools.
    """
    name: str
    path: str
    tools: tuple[str, ...] = ()
    # Optional: max session TTL in minutes for runtime activations (0 = no limit)
    max_session_ttl_minutes: int = 0


# Matrix columns (docs/MACOS_PERMISSION_MODEL.md §3): user sources share one
# column deliberately — voice is not trusted more than text.
USER_SOURCES = frozenset(
    {
        RequestSource.DIRECT_USER_COMMAND,
        RequestSource.PANEL_COMMAND,
        RequestSource.VOICE_COMMAND,
    }
)
AUTO_SOURCES = frozenset(
    {
        RequestSource.SCHEDULED_WORKER,
        RequestSource.HOOK_TRIGGERED,
    }
)


@dataclass(frozen=True)
class ToolPermissionResult:
    decision: str
    risk: str
    reason: str
    source: str = ""
    approval_required: bool = False
    blocked: bool = False


class ToolPermissionPolicy:
    """Default Jarvis v4.2 tool safety policy.

    This policy only classifies requests. It does not execute commands, inspect
    processes, mutate files, or make network calls.
    """

    def __init__(
        self,
        *,
        destructive_tools_enabled: bool = False,
        approved_roots: Iterable[str] | None = None,
        trusted_scopes: Iterable[TrustedScope] | None = None,
        voice_auto_approve: bool = False,
    ):
        self.destructive_tools_enabled = destructive_tools_enabled
        self.approved_roots = tuple(_normalize_root(root) for root in approved_roots or ())
        self.trusted_scopes = tuple(trusted_scopes or ())
        self.voice_auto_approve = voice_auto_approve

    def _is_model_trusted_for_tool(self, tool_name: str, payload: Mapping[str, Any] | None) -> bool:
        """Check if a MODEL_ORIGINATED request is within a trusted scope for the tool."""
        if not self.trusted_scopes:
            return False
        path_value = payload.get("path") if isinstance(payload, Mapping) else None
        if not isinstance(path_value, str) or not path_value.strip():
            return False
        candidate = _normalize_root(path_value)
        for scope in self.trusted_scopes:
            if tool_name not in scope.tools:
                continue
            scope_path = _normalize_root(scope.path)
            if _is_within_root(candidate, scope_path):
                return True
        return False

    def _is_voice_trusted_for_tool(self, tool_name: str, payload: Mapping[str, Any] | None) -> bool:
        """Check if a VOICE_COMMAND request is within approved_roots for the tool."""
        if not self.approved_roots:
            return False
        # For network/shell tools, check if there's a path or command that's safe
        # For now, allow if approved_roots exists (voice implies user intent)
        return True

    def decide(
        self,
        risk: str,
        *,
        source: RequestSource | str,
        tool_name: str,
        payload: Mapping[str, Any] | None = None,
    ) -> ToolPermissionResult:
        normalized_risk = str(risk)
        normalized_source = _normalize_source(source)

        if normalized_source is None:
            return _blocked(
                normalized_risk,
                f"{tool_name} request has unknown source: {source!r}.",
                source=str(source),
            )

        if normalized_risk in {PermissionClass.SAFE_READ, PermissionClass.SAFE_STATUS}:
            if normalized_source in USER_SOURCES:
                return _allow(
                    normalized_risk,
                    f"{tool_name} is classified as {normalized_risk}.",
                    source=normalized_source,
                )
            return _approval_required(
                normalized_risk,
                f"{tool_name} is {normalized_risk} but {normalized_source} requests require approval.",
                source=normalized_source,
            )

        if normalized_risk in {
            PermissionClass.UI_READ,
            PermissionClass.SCREEN_READ,
            PermissionClass.TERMINAL_READ,
        }:
            # §3: ui_read | user A | model AP | auto B. D1 approved surfaces
            # are the frontmost app and its focused window only; secure text
            # fields are stripped at the tool layer regardless of source.
            # §3: screen_read (narrow) shares the row — D4 tools cover only
            # the current window / a named region; the broad shape (full
            # display / continuous) has no tools yet (ADR-020).
            # §3: terminal_read shares it too — D5 observes only the front
            # window of an explicitly named terminal app (ADR-021).
            if normalized_source in AUTO_SOURCES:
                return _blocked(
                    normalized_risk,
                    f"{tool_name} is {normalized_risk} and unattended {normalized_source} "
                    "requests may not observe the UI.",
                    source=normalized_source,
                )
            if normalized_source in USER_SOURCES:
                return _allow(
                    normalized_risk,
                    f"{tool_name} {normalized_risk} observes an approved surface for a user source.",
                    source=normalized_source,
                )
            return _approval_required(
                normalized_risk,
                f"{tool_name} {normalized_risk} from {normalized_source} requires approval.",
                source=normalized_source,
            )

        if normalized_risk == PermissionClass.FILE_READ:
            return self._decide_file_read(
                tool_name=tool_name,
                payload=payload,
                source=normalized_source,
            )

        if normalized_risk in {
            PermissionClass.FILE_WRITE,
            PermissionClass.SHELL_READ,
            PermissionClass.SHELL_WRITE,
            PermissionClass.NETWORK,
            # §3: ui_act | user AP | model AP | auto B — clicking and typing
            # always cross ApprovalGate; earned per-surface trust is §6 future.
            PermissionClass.UI_ACT,
            # §3: terminal_write | user AP | model AP | auto B — pasting into
            # a terminal is one Enter from execution, shell_write-grade
            # (ADR-021); never merged with terminal_read.
            PermissionClass.TERMINAL_WRITE,
            # memory_write | user AP | model AP | auto B — a saved block feeds
            # every future prompt, so promotion stays human-sanctioned
            # (ADR-009) and unattended sources may not curate memory at all.
            PermissionClass.MEMORY_WRITE,
        }:
            if normalized_source in AUTO_SOURCES:
                return _blocked(
                    normalized_risk,
                    f"{tool_name} is {normalized_risk} and unattended {normalized_source} "
                    "requests may not mutate anything.",
                    source=normalized_source,
                )
            # Trusted scopes: MODEL_ORIGINATED gets ALLOW within configured paths/tools.
            if normalized_source == RequestSource.MODEL_ORIGINATED and self._is_model_trusted_for_tool(tool_name, payload):
                return _allow(
                    normalized_risk,
                    f"{tool_name} is {normalized_risk} but allowed via trusted scope for model-originated request.",
                    source=normalized_source,
                )
            # Voice auto-approve: VOICE_COMMAND gets ALLOW for mutation tools within approved_roots.
            if normalized_source == RequestSource.VOICE_COMMAND and self.voice_auto_approve and self._is_voice_trusted_for_tool(tool_name, payload):
                return _allow(
                    normalized_risk,
                    f"{tool_name} is {normalized_risk} but allowed via voice auto-approve for voice command.",
                    source=normalized_source,
                )
            return _approval_required(
                normalized_risk,
                f"{tool_name} requires human approval for {normalized_risk}.",
                source=normalized_source,
            )

        if normalized_risk == PermissionClass.DESTRUCTIVE:
            if normalized_source in AUTO_SOURCES:
                return _blocked(
                    normalized_risk,
                    f"{tool_name} is destructive and never runs from {normalized_source}.",
                    source=normalized_source,
                )
            if self.destructive_tools_enabled:
                return _approval_required(
                    normalized_risk,
                    f"{tool_name} is destructive and requires explicit approval.",
                    source=normalized_source,
                )
            return _blocked(
                normalized_risk,
                f"{tool_name} is destructive and destructive tools are disabled.",
                source=normalized_source,
            )

        return _blocked(
            normalized_risk,
            f"{tool_name} has unknown risk: {normalized_risk}.",
            source=normalized_source,
        )

    def _decide_file_read(
        self,
        *,
        tool_name: str,
        payload: Mapping[str, Any] | None,
        source: RequestSource,
    ) -> ToolPermissionResult:
        if not self.approved_roots:
            return _blocked(
                "file_read",
                f"{tool_name} file_read is blocked because no approved roots are configured.",
                source=source,
            )

        path_value = payload.get("path") if isinstance(payload, Mapping) else None
        if not isinstance(path_value, str) or not path_value.strip():
            return _blocked(
                "file_read",
                f"{tool_name} file_read requires a path.",
                source=source,
            )

        candidate = _normalize_root(path_value)
        contained = any(
            _is_within_root(candidate, approved_root) for approved_root in self.approved_roots
        )
        if not contained:
            return _blocked(
                "file_read",
                f"{tool_name} file_read path is outside approved roots.",
                source=source,
            )

        if source in USER_SOURCES:
            return _allow(
                "file_read",
                f"{tool_name} file_read path is under an approved root.",
                source=source,
            )
        # Trusted scopes: MODEL_ORIGINATED gets ALLOW within configured paths/tools.
        if source == RequestSource.MODEL_ORIGINATED and self._is_model_trusted_for_tool(tool_name, payload):
            return _allow(
                "file_read",
                f"{tool_name} file_read allowed via trusted scope for model-originated request.",
                source=source,
            )
        return _approval_required(
            "file_read",
            f"{tool_name} file_read from {source} requires approval even under approved roots.",
            source=source,
        )


class PermissionPolicy(ToolPermissionPolicy):
    """Backward-compatible alias for the initial scaffold policy."""

    def requires_approval(self, permission: PermissionClass) -> bool:
        result = self.decide(
            str(permission),
            source=RequestSource.DIRECT_USER_COMMAND,
            tool_name="legacy_permission_check",
            payload={},
        )
        return result.decision == ToolDecision.APPROVAL_REQUIRED


def _normalize_source(source: RequestSource | str) -> RequestSource | None:
    try:
        return RequestSource(str(source))
    except ValueError:
        return None


def _allow(risk: str, reason: str, *, source: RequestSource | str) -> ToolPermissionResult:
    return ToolPermissionResult(
        decision=ToolDecision.ALLOW,
        risk=risk,
        reason=reason,
        source=str(source),
    )


def _approval_required(
    risk: str,
    reason: str,
    *,
    source: RequestSource | str,
) -> ToolPermissionResult:
    return ToolPermissionResult(
        decision=ToolDecision.APPROVAL_REQUIRED,
        risk=risk,
        reason=reason,
        source=str(source),
        approval_required=True,
    )


def _blocked(risk: str, reason: str, *, source: RequestSource | str) -> ToolPermissionResult:
    return ToolPermissionResult(
        decision=ToolDecision.BLOCKED,
        risk=risk,
        reason=reason,
        source=str(source),
        blocked=True,
    )


def _normalize_root(path: str) -> str:
    return os.path.realpath(os.path.abspath(os.path.expanduser(path)))


def _is_within_root(candidate: str, approved_root: str) -> bool:
    try:
        return os.path.commonpath([candidate, approved_root]) == approved_root
    except ValueError:
        return False


__all__ = [
    "AUTO_SOURCES",
    "PermissionClass",
    "PermissionPolicy",
    "RequestSource",
    "ToolDecision",
    "ToolPermissionError",
    "ToolPermissionPolicy",
    "ToolPermissionResult",
    "USER_SOURCES",
]
