"""Shell tools.

The active runtime exposes the read-only shell tool directly to DAN.
State-changing shell remains a separate placeholder.

Safety model for shell_read:
- Model-originated calls execute directly and are recorded in tool_runs/events.
- Only normalized whitelist matches execute.
- Scrubbed environment, bounded runtime, bounded output, optional cwd that
  must stay inside the approved roots.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from collections.abc import Iterable, Mapping
from typing import Any

from dan.tools.registry import Tool, ToolExecutionError


DEFAULT_SHELL_READ_WHITELIST: tuple[str, ...] = (
    "pwd",
    "ls",
    "ls -la",
    "date",
    "whoami",
    "id",
    "uname -a",
    "sw_vers",
    "uptime",
    "df -h",
    "git status --short",
    "git log --oneline -10",
    "git diff --stat",
)

SHELL_TIMEOUT_SECONDS = 15
MAX_OUTPUT_CHARS = 65_536

_SCRUBBED_ENV = {
    "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
}

# git honours repo-local .git/config, so a malicious repo can turn a
# whitelisted read-only command into code execution (core.fsmonitor,
# core.hooksPath). Neutralize config-driven exec paths for every git run.
_GIT_ENV_HARDENING = {
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
}
_GIT_ARGV_HARDENING = (
    "-c",
    "core.fsmonitor=",
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "protocol.ext.allow=never",
)


class ShellReadTool(Tool):
    name = "shell_read"
    description = (
        "Run one configured read-only allowlisted command and return bounded "
        "stdout/stderr; optional cwd must stay inside DAN-approved roots."
    )
    risk = "shell_read"
    input_schema = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Exact command from the runtime's read-only allowlist.",
            },
            "cwd": {
                "type": "string",
                "description": "Optional working directory.",
            },
        },
        "required": ["command"],
    }

    def __init__(
        self,
        *,
        whitelist: Iterable[str] | None = None,
        approved_roots: Iterable[str] | None = None,
    ):
        self.whitelist = tuple(
            _normalize_command(entry) for entry in (whitelist or DEFAULT_SHELL_READ_WHITELIST)
        )
        self.approved_roots = tuple(_normalize_path(root) for root in (approved_roots or ()))

    def run(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        command = _required_command_argument(arguments)
        normalized = _normalize_command(command)

        cwd = self._resolve_cwd(arguments)
        env = dict(_SCRUBBED_ENV)
        # For git commands inject hardening flags; parse argv only to detect git
        argv_check = shlex.split(normalized)
        if argv_check and argv_check[0] == "git":
            hardening = " ".join(_GIT_ARGV_HARDENING)
            normalized = f"git {hardening} {' '.join(argv_check[1:])}"
            env.update(_GIT_ENV_HARDENING)

        try:
            completed = subprocess.run(
                normalized,
                capture_output=True,
                timeout=SHELL_TIMEOUT_SECONDS,
                shell=True,
                env=env,
                cwd=cwd,
            )
        except subprocess.TimeoutExpired as exc:
            raise ToolExecutionError(
                f"shell_read command timed out after {SHELL_TIMEOUT_SECONDS}s."
            ) from exc
        except OSError as exc:
            raise ToolExecutionError(f"shell_read cannot execute command: {exc}") from exc

        stdout, stdout_truncated = _clip_output(completed.stdout)
        stderr, stderr_truncated = _clip_output(completed.stderr)
        return {
            "ok": completed.returncode == 0,
            "command": normalized,
            "cwd": cwd,
            "returncode": completed.returncode,
            "stdout": stdout,
            "stdout_truncated": stdout_truncated,
            "stderr": stderr,
            "stderr_truncated": stderr_truncated,
        }

    def _resolve_cwd(self, arguments: Mapping[str, Any]) -> str:
        raw_cwd = arguments.get("cwd")
        if raw_cwd is None:
            return os.getcwd()
        if not isinstance(raw_cwd, str) or not raw_cwd.strip():
            raise ToolExecutionError("shell_read cwd must be a non-empty string.")

        resolved = _normalize_path(raw_cwd.strip())
        if not os.path.isdir(resolved):
            raise ToolExecutionError(f"shell_read cwd is not a directory: {resolved}")
        return resolved


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


def _required_command_argument(arguments: Mapping[str, Any]) -> str:
    raw_command = arguments.get("command")
    if not isinstance(raw_command, str) or not raw_command.strip():
        raise ToolExecutionError("shell_read requires a non-empty command argument.")
    return raw_command


def _normalize_command(command: str) -> str:
    return " ".join(command.split())


def _clip_output(raw: bytes) -> tuple[str, bool]:
    text = raw.decode("utf-8", errors="replace")
    if len(text) > MAX_OUTPUT_CHARS:
        return text[:MAX_OUTPUT_CHARS], True
    return text, False


def _normalize_path(path: str) -> str:
    return os.path.realpath(os.path.abspath(os.path.expanduser(path)))


def _is_within_root(candidate: str, approved_root: str) -> bool:
    try:
        return os.path.commonpath([candidate, approved_root]) == approved_root
    except ValueError:
        return False


__all__ = [
    "DEFAULT_SHELL_READ_WHITELIST",
    "MAX_OUTPUT_CHARS",
    "SHELL_TIMEOUT_SECONDS",
    "ShellReadPlaceholderTool",
    "ShellReadTool",
    "ShellTool",
    "ShellWritePlaceholderTool",
]
