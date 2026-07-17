"""Terminal/iTerm operator bridge (FAZA D5, ADR-021).

Reading FROM a terminal (`terminal_read`) and writing TO a terminal
(`terminal_write`) are distinct risk classes and never share a code path
decision — the ui_read / ui_act precedent. This adapter implements both
sides of the D5 profile:

- read: the visible contents of the front window / current session of an
  explicitly named terminal app, from the closed set {Terminal, iTerm2}.
- write: paste WITHOUT submitting (iTerm2 `write text ... newline NO`);
  pressing Enter stays with the human. Terminal.app has no
  paste-without-execute verb, so pasting there is unsupported — a
  submit/execute path would be a new ADR, not a flag.

Safety model:
- AppleScript is "a shell in a trenchcoat" (MACOS_CAPABILITIES.md §9), so
  the bridge runs only FIXED script constants; parameters travel via the
  osascript `run` handler argv and are never interpolated into script
  source — there is no injection surface.
- `tell application` auto-launches its target; the bridge refuses to talk
  to an app that is not running (checked before osascript ever spawns).
- Paste text is bounded (MAX_PASTE_CHARS) and must be a single printable
  line: control characters (newline, tab, escape, ...) are rejected here
  AND at the tool layer — an embedded "\\n" would submit the command
  despite `newline NO`.
- Terminal contents routinely include secrets: output is clipped by
  `sanitize_terminal_snapshot` at the tool layer, redacted by
  ToolRunRecorder/EventStore like every tool output, and never carried by
  the D3 stream (ADR-019 omits bulk output).

Backends:
- `osascript` — real bridge via /usr/bin/osascript; requires the
  Automation (Apple Events) TCC grant per target app, see
  docs/runbooks/TERMINAL_AUTOMATION_TCC.md.
- `fake` — deterministic fixture for tests and the smoke harness; its
  lines include a secret-shaped token so every run proves redaction, and
  it mirrors the real constraints (closed app set, no paste into
  Terminal.app). Unknown backend names fail the daemon at startup.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Mapping
from typing import Any


MAX_TERMINAL_LINES = 240
MAX_TERMINAL_LINE_CHARS = 512
MAX_PASTE_CHARS = 4096

SUPPORTED_APPS = ("Terminal", "iTerm2")

_OSASCRIPT_BINARY = "/usr/bin/osascript"
_PGREP_BINARY = "/usr/bin/pgrep"
_OSASCRIPT_TIMEOUT_SECONDS = 15

_APP_ALIASES = {
    "terminal": "Terminal",
    "iterm": "iTerm2",
    "iterm2": "iTerm2",
}

# Fixed script sources (ADR-021): the only AppleScript dand will ever
# execute. Parameters arrive through the `run` handler argv.
_READ_SCRIPTS = {
    "Terminal": (
        'tell application "Terminal"\n'
        'if (count of windows) is 0 then error "no Terminal window"\n'
        "return contents of selected tab of front window\n"
        "end tell"
    ),
    "iTerm2": (
        'tell application "iTerm2"\n'
        'if (count of windows) is 0 then error "no iTerm2 window"\n'
        "return contents of current session of current window\n"
        "end tell"
    ),
}

_PASTE_SCRIPTS = {
    "iTerm2": (
        "on run argv\n"
        'tell application "iTerm2"\n'
        'if (count of windows) is 0 then error "no iTerm2 window"\n'
        "tell current session of current window to write text (item 1 of argv) newline NO\n"
        "end tell\n"
        "end run"
    ),
}


class TerminalError(Exception):
    """Raised when the bridge cannot observe or paste (bad app, no TCC, ...)."""


class TerminalBridge:
    """Backend interface: observe and paste into a named terminal app."""

    backend = "abstract"

    def read_screen(self, app: str) -> Mapping[str, Any]:
        raise NotImplementedError

    def paste_text(self, app: str, text: str) -> Mapping[str, Any]:
        raise NotImplementedError


def normalize_app(app: Any) -> str:
    """Map a user-facing name onto the closed app set; anything else fails."""

    if not isinstance(app, str):
        raise TerminalError(f"Terminal app must be a string, got {type(app).__name__}.")
    normalized = _APP_ALIASES.get(app.strip().lower())
    if normalized is None:
        raise TerminalError(
            f"Unsupported terminal app: {app!r}. Supported: {', '.join(SUPPORTED_APPS)}."
        )
    return normalized


def validate_paste_text(text: Any) -> str:
    """Paste payloads are one bounded printable line — enforced fail-closed."""

    if not isinstance(text, str) or not text:
        raise TerminalError("Paste text must be a non-empty string.")
    if len(text) > MAX_PASTE_CHARS:
        raise TerminalError(f"Paste text exceeds {MAX_PASTE_CHARS} characters.")
    for ch in text:
        if ord(ch) < 0x20 or ord(ch) == 0x7F:
            raise TerminalError(
                "Paste text may not contain control characters "
                f"(found {ch!r}); a newline would submit the command."
            )
    return text


# The fixture intentionally contains a secret-looking token, so every
# test/smoke run proves terminal output is redacted before it persists.
_DEFAULT_FIXTURE_LINES = [
    "DAN FAKE TERMINAL — D5 smoke fixture",
    "$ export API_KEY=sk-faketerminalsecret1234567890",
    "$ make build",
    "Build finished in 2.7s",
]


class FakeTerminalBridge(TerminalBridge):
    """Deterministic backend for tests and smoke runs."""

    backend = "fake"

    def __init__(self, lines: list[str] | None = None):
        self._lines = list(lines) if lines is not None else list(_DEFAULT_FIXTURE_LINES)
        self.pasted: list[tuple[str, str]] = []

    def read_screen(self, app: str) -> Mapping[str, Any]:
        normalized = normalize_app(app)
        return {
            "source": "terminal",
            "app": normalized,
            "lines": list(self._lines),
        }

    def paste_text(self, app: str, text: str) -> Mapping[str, Any]:
        normalized = normalize_app(app)
        validate_paste_text(text)
        # Mirror the real constraint: Terminal.app cannot paste without
        # executing, so the fake refuses too — smoke/tests can never pass
        # on a path the osascript backend would reject.
        if normalized not in _PASTE_SCRIPTS:
            raise TerminalError(
                f"{normalized} cannot paste without executing; "
                "ADR-021 supports paste into iTerm2 only."
            )
        self.pasted.append((normalized, text))
        return {"pasted": True, "app": normalized, "chars": len(text)}


class OsascriptTerminalBridge(TerminalBridge):
    """Fixed-script osascript backend (Automation TCC required per app)."""

    backend = "osascript"

    def read_screen(self, app: str) -> Mapping[str, Any]:
        normalized = normalize_app(app)
        self._require_running(normalized)
        stdout = self._run_fixed_script(normalized, _READ_SCRIPTS[normalized], [])
        lines = stdout.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        while lines and not lines[-1].strip():
            lines.pop()
        return {
            "source": "terminal",
            "app": normalized,
            "lines": lines,
        }

    def paste_text(self, app: str, text: str) -> Mapping[str, Any]:
        normalized = normalize_app(app)
        validate_paste_text(text)
        script = _PASTE_SCRIPTS.get(normalized)
        if script is None:
            raise TerminalError(
                f"{normalized} cannot paste without executing; "
                "ADR-021 supports paste into iTerm2 only."
            )
        self._require_running(normalized)
        self._run_fixed_script(normalized, script, [text])
        return {"pasted": True, "app": normalized, "chars": len(text)}

    def _require_running(self, app: str) -> None:
        # `tell application` launches its target when absent; never do that.
        if not _app_is_running(app):
            raise TerminalError(
                f"{app} is not running; the terminal bridge never launches apps."
            )

    def _run_fixed_script(self, app: str, script: str, args: list[str]) -> str:
        command = [_OSASCRIPT_BINARY, "-e", script, *args]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                timeout=_OSASCRIPT_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise TerminalError(f"osascript did not run: {exc}") from exc

        if completed.returncode != 0:
            stderr = completed.stderr.decode("utf-8", errors="replace").strip()[:300]
            if "-1743" in stderr:
                raise TerminalError(
                    f"Automation (Apple Events) TCC grant is missing for {app}. "
                    "See docs/runbooks/TERMINAL_AUTOMATION_TCC.md. "
                    f"({stderr})"
                )
            raise TerminalError(
                f"osascript failed for {app} (rc={completed.returncode}): "
                f"{stderr or 'no error output'}"
            )
        return completed.stdout.decode("utf-8", errors="replace")


def _app_is_running(app: str) -> bool:
    """True when a process with the app's exact name exists (no TCC needed)."""

    try:
        completed = subprocess.run(
            [_PGREP_BINARY, "-qx", app],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TerminalError(f"Cannot check whether {app} is running: {exc}") from exc
    return completed.returncode == 0


def create_terminal_bridge(backend: Any) -> TerminalBridge:
    """Build the configured backend; unknown names fail closed."""

    normalized = str(backend).strip().lower() if isinstance(backend, str) else ""
    if normalized == "fake":
        return FakeTerminalBridge()
    if normalized == "osascript":
        return OsascriptTerminalBridge()
    raise TerminalError(f"Unknown terminal backend: {backend!r}")


def sanitize_terminal_snapshot(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    """Clip terminal output at the tool layer: bounded line count and length.

    Sanitization here is structural (JSON-safe strings, hard caps); secret
    redaction happens where it always does — ToolRunRecorder/EventStore.
    """

    snapshot = raw if isinstance(raw, Mapping) else {}
    raw_lines = snapshot.get("lines")
    source_lines = list(raw_lines) if isinstance(raw_lines, (list, tuple)) else []

    truncated = len(source_lines) > MAX_TERMINAL_LINES
    lines: list[str] = []
    for value in source_lines[:MAX_TERMINAL_LINES]:
        text = value if isinstance(value, str) else str(value)
        if len(text) > MAX_TERMINAL_LINE_CHARS:
            text = text[:MAX_TERMINAL_LINE_CHARS]
            truncated = True
        lines.append(text)

    sanitized: dict[str, Any] = {
        "source": str(snapshot.get("source") or "terminal"),
        "lines": lines,
        "line_count": len(lines),
        "truncated": truncated,
    }
    app = snapshot.get("app")
    if app is not None:
        sanitized["app"] = app if isinstance(app, str) else str(app)
    return sanitized


def _probe() -> int:
    """Manual TCC/onboarding probe: ``python -m dan.macos.terminal [app]``.

    Attempts a real read of the given app (default: the first supported app
    found running) through the osascript backend. Exit codes: 0 read OK,
    2 nothing to probe or Automation TCC missing/failed.
    """

    requested = sys.argv[1] if len(sys.argv) >= 2 else None
    report: dict[str, Any] = {"backend": "osascript"}
    try:
        if requested is not None:
            app = normalize_app(requested)
        else:
            running = [name for name in SUPPORTED_APPS if _app_is_running(name)]
            if not running:
                report["error"] = (
                    "No supported terminal app is running "
                    f"({', '.join(SUPPORTED_APPS)}); start one and re-run."
                )
                print(json.dumps(report, ensure_ascii=False, indent=2))
                return 2
            app = running[0]
        report["app"] = app
        snapshot = sanitize_terminal_snapshot(OsascriptTerminalBridge().read_screen(app))
        report["automation"] = True
        report["screen"] = {
            "line_count": snapshot["line_count"],
            "truncated": snapshot["truncated"],
            "lines_preview": snapshot["lines"][-5:],
        }
    except TerminalError as exc:
        report["automation"] = False
        report["error"] = str(exc)
        report["hint"] = (
            "Grant Automation (Apple Events) from the process hosting dand "
            "to the terminal app: System Settings -> Privacy & Security -> "
            "Automation. See docs/runbooks/TERMINAL_AUTOMATION_TCC.md"
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("automation") else 2


if __name__ == "__main__":
    # `python -m` executes this file as __main__ while the daemon imports it
    # as dan.macos.terminal — two distinct TerminalError classes otherwise
    # (the D2 probe lesson). Delegate to the canonical module instance.
    from dan.macos import terminal as _canonical

    raise SystemExit(_canonical._probe())


__all__ = [
    "MAX_PASTE_CHARS",
    "MAX_TERMINAL_LINE_CHARS",
    "MAX_TERMINAL_LINES",
    "SUPPORTED_APPS",
    "FakeTerminalBridge",
    "OsascriptTerminalBridge",
    "TerminalBridge",
    "TerminalError",
    "create_terminal_bridge",
    "normalize_app",
    "sanitize_terminal_snapshot",
    "validate_paste_text",
]
