"""Tool risk classification for DAN. NOT an enforcement layer.

READ THIS BEFORE TRUSTING ANYTHING HERE (accurate as of 2026-07-21):

`ToolPermissionPolicy.decide()` returns ALLOW unconditionally — every risk
class, every source. It gates nothing. The constructor still accepts
`approved_roots`, `trusted_scopes`, `destructive_tools_enabled`,
`auto_approve_mode` and the `require_approval_for_*` flags, and dand still
passes them, but they are stored and rendered as runtime state only. No code
path reads them to make a decision.

The real containment lives INSIDE the individual tools and nowhere else:
approved-root scoping (`file_tool`), the scrubbed environment and the per-tool
runtime/output bounds (`shell_tool`). If you weaken a check inside a tool,
nothing behind it catches the mistake. Do NOT add the `shell_read` allowlist to
that list: `security.shell_read_unrestricted` switches it off, and it is off on
this machine. The git hardening does belong on it again — since 2026-07-21 it
rides in the environment and applies to every command, rather than only to ones
starting with the word `git`. See the module docstring of `shell_tool`.

`RequestSource` is recorded for audit — it is copied into the result's
`source` field — but it does not change any decision. The source-sensitive
matrix in docs/MACOS_PERMISSION_MODEL.md is an UNIMPLEMENTED design, not a
description of this file.

Secret redaction (`dan.security.redaction`) is separate from this module and
IS active.
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
    """INERT — `decide()` never consults `trusted_scopes`. See `dan.config`,
    which declares the identical shape this one duplicates; neither is read.
    """
    name: str
    path: str
    tools: tuple[str, ...] = ()
    max_session_ttl_minutes: int = 0


# Source groupings kept for audit/reporting and for the day the matrix in
# docs/MACOS_PERMISSION_MODEL.md is actually built. `decide()` does not branch
# on them today.
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
    """Classifier only — allows everything. See the module docstring.

    Nothing here blocks, gates, or scopes. It does not execute commands,
    inspect processes, mutate files, or make network calls either.
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
        # Retained as configuration compatibility fields while Release 1 uses
        # direct tool execution. They are rendered as effective runtime state,
        # but deliberately do not create approval rows or block execution.
        self.require_approval_for_shell = require_approval_for_shell
        self.require_approval_for_file_write = require_approval_for_file_write
        self.require_approval_for_network = require_approval_for_network
        self.require_approval_for_ui = require_approval_for_ui
        self.require_approval_for_terminal = require_approval_for_terminal
        # ADR-009 wanted memory_write to require human promotion. That gate is
        # not implemented here either — this flag is reported, never enforced.
        self.require_approval_for_memory = require_approval_for_memory

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
        so the rest of DAN has one API, but it no longer blocks, requests
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

class PermissionPolicy(ToolPermissionPolicy):
    """Backward-compatible alias for the initial scaffold policy."""

    def requires_approval(self, permission: PermissionClass) -> bool:
        return False


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


def _normalize_root(path: str) -> str:
    return os.path.realpath(os.path.abspath(os.path.expanduser(path)))


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
