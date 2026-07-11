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
        destructive_tools_enabled: bool = True,
        approved_roots: Iterable[str] | None = None,
        trusted_scopes: Iterable[TrustedScope] | None = None,
        voice_auto_approve: bool = True,
        auto_approve_mode: str = "all",
        require_approval_for_shell: bool = False,
        require_approval_for_file_write: bool = False,
        require_approval_for_network: bool = False,
        require_approval_for_ui: bool = False,
        require_approval_for_terminal: bool = False,
        require_approval_for_memory: bool = False,
    ):
        self.destructive_tools_enabled = destructive_tools_enabled
        self.approved_roots = tuple(_normalize_root(root) for root in approved_roots or ())
        self.trusted_scopes = tuple(trusted_scopes or ())
        self.voice_auto_approve = voice_auto_approve
        self.auto_approve_mode = auto_approve_mode  # "off", "model", "voice", "all"
        # Per-class approval switches — the panel's "require approval for
        # shell / file / network" toggles. These are the PRIMARY, single knob:
        # when a class is switched off, every attended source (user AND model)
        # runs it without an approval click. Unattended AUTO_SOURCES stay
        # blocked; destructive keeps its own gate. Default True preserves the
        # fail-closed posture when nothing is configured.
        self.require_approval_for_shell = require_approval_for_shell
        self.require_approval_for_file_write = require_approval_for_file_write
        self.require_approval_for_network = require_approval_for_network
        self.require_approval_for_ui = require_approval_for_ui
        self.require_approval_for_terminal = require_approval_for_terminal
        # memory_write defaults to human promotion (ADR-009); switching this
        # off is the operator explicitly overriding that default for himself.
        self.require_approval_for_memory = require_approval_for_memory

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
        if payload is None:
            return False
        # Check path-based tools
        path_value = payload.get("path")
        if isinstance(path_value, str) and path_value.strip():
            candidate = _normalize_root(path_value)
            return any(
                _is_within_root(candidate, approved_root) for approved_root in self.approved_roots
            )
        # For shell_read, check cwd
        cwd_value = payload.get("cwd")
        if isinstance(cwd_value, str) and cwd_value.strip():
            candidate = _normalize_root(cwd_value)
            return any(
                _is_within_root(candidate, approved_root) for approved_root in self.approved_roots
            )
        # For network, no path to check — conservative: require explicit config
        return False

    def _class_requires_approval(self, risk: str) -> bool:

        return False
        # TODO: CLEAN THIS MESS 
        """Whether an attended request of this mutation class still needs an
        approval click, given the operator's per-class switches.

        Every switchable mutation class has a panel grant: shell, file_write,
        network, ui_act, terminal_write and memory_write. ``destructive`` is
        deliberately NOT here — it keeps its own always-gated branch."""
        if risk in {PermissionClass.SHELL_READ, PermissionClass.SHELL_WRITE}:
            return self.require_approval_for_shell
        if risk == PermissionClass.FILE_WRITE:
            return self.require_approval_for_file_write
        if risk == PermissionClass.NETWORK:
            return self.require_approval_for_network
        if risk == PermissionClass.UI_ACT:
            return self.require_approval_for_ui
        if risk == PermissionClass.TERMINAL_WRITE:
            return self.require_approval_for_terminal
        if risk == PermissionClass.MEMORY_WRITE:
            return self.require_approval_for_memory
        return True

    def decide(
        self,
        risk: str,
        *,
        source: RequestSource | str,
        tool_name: str,
        payload: Mapping[str, Any] | None = None,
    ) -> ToolPermissionResult:
        """Runtime-lab policy: tools run without approval gates.

        This branch is owner-controlled and local. The permission object stays
        so the rest of Jarvis has one API, but it no longer blocks, requests
        approval, or performs source/root/destructive gating. Runtime failures
        still surface from the tool itself.
        """

        normalized_risk = str(risk)
        normalized_source = _normalize_source(source)
        source_text = str(normalized_source or source or "unknown")
        return _allow(
            normalized_risk,
            f"{tool_name} allowed by runtime-lab policy.",
            source=source_text,
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
        if self.auto_approve_mode == "all":
            return _allow(
                "file_read",
                f"{tool_name} file_read path is under an approved root and allowed by auto_approve_mode=all.",
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
        return False
        # TODO: Clean this shit 
        # result = self.decide(
        #     str(permission),
        #     source=RequestSource.DIRECT_USER_COMMAND,
        #     tool_name="legacy_permission_check",
        #     payload={},
        # )
        # return result.decision == ToolDecision.APPROVAL_REQUIRED


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
    return False
    # return ToolPermissionResult(
    #     decision=ToolDecision.APPROVAL_REQUIRED,
    #     risk=risk,
    #     reason=reason,
    #     source=str(source),
    #     approval_required=True,
    # )


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
