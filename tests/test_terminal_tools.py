"""FAZA D5: terminal bridge, sanitizer and tools on the fake backend.

The osascript path needs a running terminal app plus the Automation TCC
grant, so unit tests exercise the fake backend, the sanitizer contract,
paste validation, the fail-closed factory, and the *structure* of the
osascript invocation (fixed script sources, parameters via argv — never
interpolated). The live path is covered by the probe
(`python -m jarvis.macos.terminal`) and the live gate.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis.config import SecurityConfig
from jarvis.macos import terminal as terminal_module
from jarvis.macos.terminal import (
    MAX_PASTE_CHARS,
    MAX_TERMINAL_LINE_CHARS,
    MAX_TERMINAL_LINES,
    SUPPORTED_APPS,
    FakeTerminalBridge,
    OsascriptTerminalBridge,
    TerminalBridge,
    TerminalError,
    create_terminal_bridge,
    normalize_app,
    sanitize_terminal_snapshot,
    validate_paste_text,
)
from jarvis.tools.registry import ToolExecutionError
from jarvis.tools.terminal_tool import TerminalPasteTool, TerminalReadScreenTool
from tests.git_guards import assert_schema_and_migrations_unchanged


def test_d5_does_not_touch_db_schema() -> None:
    assert_schema_and_migrations_unchanged(Path(__file__).resolve().parents[1])


# ---------------------------------------------------------------------------
# App names: closed set, no free-form targets
# ---------------------------------------------------------------------------


def test_supported_apps_is_the_closed_two_app_set() -> None:
    assert set(SUPPORTED_APPS) == {"Terminal", "iTerm2"}


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("Terminal", "Terminal"),
        ("terminal", "Terminal"),
        ("iTerm2", "iTerm2"),
        ("iterm2", "iTerm2"),
        ("iTerm", "iTerm2"),
        (" iterm ", "iTerm2"),
    ],
)
def test_normalize_app_accepts_known_names(given: str, expected: str) -> None:
    assert normalize_app(given) == expected


@pytest.mark.parametrize("given", ["Safari", "iTerm3", "", "  ", 42, None])
def test_normalize_app_rejects_everything_else(given) -> None:
    with pytest.raises(TerminalError):
        normalize_app(given)


# ---------------------------------------------------------------------------
# Paste text validation (adapter layer; the tool re-checks independently)
# ---------------------------------------------------------------------------


def test_validate_paste_text_accepts_a_plain_command() -> None:
    validate_paste_text("git status --short")


def test_validate_paste_text_rejects_empty_and_non_string() -> None:
    for bad in ("", None, 42, b"ls"):
        with pytest.raises(TerminalError):
            validate_paste_text(bad)


def test_validate_paste_text_rejects_over_limit() -> None:
    with pytest.raises(TerminalError):
        validate_paste_text("x" * (MAX_PASTE_CHARS + 1))
    validate_paste_text("x" * MAX_PASTE_CHARS)


@pytest.mark.parametrize(
    "bad",
    [
        "ls\n",  # newline would submit despite `newline NO`
        "ls\rpwd",
        "echo hi\ttab",  # tab triggers shell completion
        "esc\x1b[201~",  # escape sequences / bracketed-paste games
        "nul\x00",
        "del\x7f",
    ],
)
def test_validate_paste_text_rejects_control_characters(bad: str) -> None:
    with pytest.raises(TerminalError):
        validate_paste_text(bad)


# ---------------------------------------------------------------------------
# Fake backend
# ---------------------------------------------------------------------------


def test_fake_bridge_reports_fake_backend() -> None:
    assert FakeTerminalBridge().backend == "fake"


def test_fake_bridge_is_a_terminal_bridge() -> None:
    assert isinstance(FakeTerminalBridge(), TerminalBridge)


def test_fake_bridge_fixture_contains_a_secret_shaped_token() -> None:
    # The fixture must smuggle a secret so every test/smoke run proves the
    # recorder redacts terminal output before it persists.
    snapshot = FakeTerminalBridge().read_screen("iTerm2")
    assert any("sk-" in line for line in snapshot["lines"])


def test_fake_bridge_read_screen_normalizes_the_app() -> None:
    snapshot = FakeTerminalBridge().read_screen("iterm")
    assert snapshot["app"] == "iTerm2"
    assert snapshot["source"] == "terminal"


def test_fake_bridge_read_screen_rejects_unknown_apps() -> None:
    with pytest.raises(TerminalError):
        FakeTerminalBridge().read_screen("Safari")


def test_fake_bridge_paste_records_the_paste() -> None:
    bridge = FakeTerminalBridge()
    result = bridge.paste_text("iTerm2", "echo smoke")
    assert result == {"pasted": True, "app": "iTerm2", "chars": len("echo smoke")}
    assert bridge.pasted == [("iTerm2", "echo smoke")]


def test_fake_bridge_paste_validates_text() -> None:
    bridge = FakeTerminalBridge()
    with pytest.raises(TerminalError):
        bridge.paste_text("iTerm2", "rm -rf /\n")
    assert bridge.pasted == []


def test_fake_bridge_paste_rejects_terminal_app_like_the_real_one() -> None:
    # Terminal.app has no paste-without-execute; the fake mirrors the real
    # constraint so smoke/tests cannot pass on a path osascript would refuse.
    bridge = FakeTerminalBridge()
    with pytest.raises(TerminalError):
        bridge.paste_text("Terminal", "echo hi")
    assert bridge.pasted == []


# ---------------------------------------------------------------------------
# Sanitizer: structural clipping at the tool boundary
# ---------------------------------------------------------------------------


def test_sanitize_clips_line_count_and_marks_truncated() -> None:
    raw = {"app": "iTerm2", "source": "terminal", "lines": ["x"] * (MAX_TERMINAL_LINES + 5)}
    snapshot = sanitize_terminal_snapshot(raw)
    assert snapshot["line_count"] == MAX_TERMINAL_LINES
    assert snapshot["truncated"] is True


def test_sanitize_clips_line_length() -> None:
    raw = {"app": "Terminal", "lines": ["y" * (MAX_TERMINAL_LINE_CHARS + 1)]}
    snapshot = sanitize_terminal_snapshot(raw)
    assert len(snapshot["lines"][0]) == MAX_TERMINAL_LINE_CHARS
    assert snapshot["truncated"] is True


def test_sanitize_survives_garbage_input() -> None:
    snapshot = sanitize_terminal_snapshot(None)
    assert snapshot["lines"] == []
    assert snapshot["line_count"] == 0
    assert snapshot["truncated"] is False


def test_sanitize_stringifies_non_string_lines() -> None:
    snapshot = sanitize_terminal_snapshot({"app": "Terminal", "lines": [1, None]})
    assert snapshot["lines"] == ["1", "None"]


# ---------------------------------------------------------------------------
# Factory: fail-closed on unknown backends
# ---------------------------------------------------------------------------


def test_create_terminal_bridge_builds_fake() -> None:
    assert create_terminal_bridge("fake").backend == "fake"


def test_create_terminal_bridge_builds_osascript() -> None:
    bridge = create_terminal_bridge("osascript")
    assert isinstance(bridge, OsascriptTerminalBridge)
    assert bridge.backend == "osascript"


@pytest.mark.parametrize("name", ["", "native", "applescript", None])
def test_create_terminal_bridge_rejects_unknown_backends(name) -> None:
    with pytest.raises(TerminalError):
        create_terminal_bridge(name)


def test_security_config_defaults_to_osascript_backend() -> None:
    assert SecurityConfig().terminal_backend == "osascript"


def test_create_terminal_bridge_accepts_case_variants_of_known_names() -> None:
    # D4 precedent: create_screen_reader strips and lowercases too.
    assert create_terminal_bridge("Fake").backend == "fake"
    assert create_terminal_bridge("OSASCRIPT ").backend == "osascript"


# ---------------------------------------------------------------------------
# Osascript invocation structure (no live osascript in unit tests)
# ---------------------------------------------------------------------------


class _RecordingRunner:
    def __init__(self, stdout: bytes = b"line one\nline two\n", returncode: int = 0,
                 stderr: bytes = b""):
        self.calls: list[list[str]] = []
        self._stdout = stdout
        self._returncode = returncode
        self._stderr = stderr

    def __call__(self, command, **kwargs):
        self.calls.append(list(command))

        class _Completed:
            returncode = self._returncode
            stdout = self._stdout
            stderr = self._stderr

        return _Completed()


def test_osascript_read_uses_a_fixed_script_for_the_named_app(monkeypatch) -> None:
    runner = _RecordingRunner()
    monkeypatch.setattr(terminal_module.subprocess, "run", runner)
    monkeypatch.setattr(terminal_module, "_app_is_running", lambda app: True)

    snapshot = OsascriptTerminalBridge().read_screen("iTerm2")

    assert snapshot["app"] == "iTerm2"
    assert snapshot["lines"] == ["line one", "line two"]
    (command,) = runner.calls
    assert command[0] == "/usr/bin/osascript"
    script = command[command.index("-e") + 1]
    assert 'tell application "iTerm2"' in script


def test_osascript_paste_passes_text_via_argv_never_in_the_script(monkeypatch) -> None:
    runner = _RecordingRunner(stdout=b"")
    monkeypatch.setattr(terminal_module.subprocess, "run", runner)
    monkeypatch.setattr(terminal_module, "_app_is_running", lambda app: True)

    payload = 'echo "quoted $(dangerous)"'
    result = OsascriptTerminalBridge().paste_text("iTerm2", payload)

    assert result == {"pasted": True, "app": "iTerm2", "chars": len(payload)}
    (command,) = runner.calls
    script = command[command.index("-e") + 1]
    assert payload not in script  # injection-proof: parameters ride argv
    assert "item 1 of argv" in script
    assert "newline NO" in script
    assert command[-1] == payload


def test_osascript_never_targets_an_app_that_is_not_running(monkeypatch) -> None:
    # `tell application` auto-launches its target; the bridge must refuse
    # before osascript ever runs.
    runner = _RecordingRunner()
    monkeypatch.setattr(terminal_module.subprocess, "run", runner)
    monkeypatch.setattr(terminal_module, "_app_is_running", lambda app: False)

    with pytest.raises(TerminalError, match="not running"):
        OsascriptTerminalBridge().read_screen("iTerm2")
    with pytest.raises(TerminalError, match="not running"):
        OsascriptTerminalBridge().paste_text("iTerm2", "echo hi")
    assert runner.calls == []


def test_osascript_paste_into_terminal_app_is_unsupported(monkeypatch) -> None:
    runner = _RecordingRunner()
    monkeypatch.setattr(terminal_module.subprocess, "run", runner)
    monkeypatch.setattr(terminal_module, "_app_is_running", lambda app: True)

    with pytest.raises(TerminalError, match="paste without executing"):
        OsascriptTerminalBridge().paste_text("Terminal", "echo hi")
    assert runner.calls == []


def test_osascript_tcc_denial_points_at_the_runbook(monkeypatch) -> None:
    runner = _RecordingRunner(
        returncode=1,
        stderr=b"execution error: Not authorized to send Apple events to iTerm2. (-1743)",
    )
    monkeypatch.setattr(terminal_module.subprocess, "run", runner)
    monkeypatch.setattr(terminal_module, "_app_is_running", lambda app: True)

    with pytest.raises(TerminalError, match="TERMINAL_AUTOMATION_TCC"):
        OsascriptTerminalBridge().read_screen("iTerm2")


def test_osascript_read_validates_the_app_before_anything_else(monkeypatch) -> None:
    runner = _RecordingRunner()
    monkeypatch.setattr(terminal_module.subprocess, "run", runner)

    with pytest.raises(TerminalError):
        OsascriptTerminalBridge().read_screen("Finder")
    assert runner.calls == []


# ---------------------------------------------------------------------------
# Tools: terminal_read_screen (terminal_read) / terminal_paste (terminal_write)
# ---------------------------------------------------------------------------


class _PermissiveBridge(TerminalBridge):
    """Records everything and validates nothing — proves the TOOL layer
    enforces its own checks instead of trusting the backend (the two-layer
    precedent from secure text fields in D2)."""

    backend = "permissive"

    def __init__(self):
        self.pasted: list[tuple[str, str]] = []

    def read_screen(self, app: str):
        return {"source": "terminal", "app": app, "lines": ["anything"]}

    def paste_text(self, app: str, text: str):
        self.pasted.append((app, text))
        return {"pasted": True, "app": app, "chars": len(text)}


class _ExplodingBridge(TerminalBridge):
    backend = "exploding"

    def read_screen(self, app: str):
        raise TerminalError("no automation grant")

    def paste_text(self, app: str, text: str):
        raise TerminalError("no automation grant")


def test_read_tool_declares_the_read_class() -> None:
    tool = TerminalReadScreenTool(FakeTerminalBridge())
    assert tool.name == "terminal_read_screen"
    assert tool.risk == "terminal_read"


def test_paste_tool_declares_the_write_class() -> None:
    tool = TerminalPasteTool(FakeTerminalBridge())
    assert tool.name == "terminal_paste"
    assert tool.risk == "terminal_write"


def test_read_and_paste_tools_never_share_a_risk_class() -> None:
    read = TerminalReadScreenTool(FakeTerminalBridge())
    paste = TerminalPasteTool(FakeTerminalBridge())
    assert read.risk != paste.risk


def test_read_tool_returns_a_sanitized_snapshot() -> None:
    result = TerminalReadScreenTool(FakeTerminalBridge()).run({"app": "iterm"})
    assert result["ok"] is True
    assert result["backend"] == "fake"
    assert result["screen"]["app"] == "iTerm2"
    assert result["line_count"] == result["screen"]["line_count"] > 0
    assert any("sk-" in line for line in result["screen"]["lines"])


def test_read_tool_clips_oversized_output() -> None:
    bridge = FakeTerminalBridge(lines=["x"] * (MAX_TERMINAL_LINES + 10))
    result = TerminalReadScreenTool(bridge).run({"app": "Terminal"})
    assert result["line_count"] == MAX_TERMINAL_LINES
    assert result["truncated"] is True


@pytest.mark.parametrize("arguments", [{}, {"app": ""}, {"app": "   "}, {"app": 7}])
def test_read_tool_requires_an_app_argument(arguments) -> None:
    with pytest.raises(ToolExecutionError):
        TerminalReadScreenTool(FakeTerminalBridge()).run(arguments)


def test_read_tool_wraps_bridge_errors() -> None:
    with pytest.raises(ToolExecutionError, match="terminal_read_screen"):
        TerminalReadScreenTool(_ExplodingBridge()).run({"app": "iTerm2"})


def test_paste_tool_pastes_and_reports_without_echoing_the_text() -> None:
    bridge = FakeTerminalBridge()
    payload = "echo D5-marker"
    result = TerminalPasteTool(bridge).run({"app": "iTerm2", "text": payload})
    assert result["ok"] is True
    assert result["pasted"] is True
    assert result["chars_pasted"] == len(payload)
    assert bridge.pasted == [("iTerm2", payload)]
    # The pasted text is not echoed back: it already lives (redacted) in the
    # tool input; duplicating it in output would double the exposure.
    assert payload not in json.dumps(result)


@pytest.mark.parametrize(
    "bad_text",
    ["", None, 42, "x" * (MAX_PASTE_CHARS + 1), "ls\n", "a\tb", "\x1b[A"],
)
def test_paste_tool_validates_text_independently_of_the_backend(bad_text) -> None:
    bridge = _PermissiveBridge()
    with pytest.raises(ToolExecutionError):
        TerminalPasteTool(bridge).run({"app": "iTerm2", "text": bad_text})
    assert bridge.pasted == []


@pytest.mark.parametrize("arguments", [{"text": "ls"}, {"app": "", "text": "ls"}])
def test_paste_tool_requires_an_app_argument(arguments) -> None:
    with pytest.raises(ToolExecutionError):
        TerminalPasteTool(FakeTerminalBridge()).run(arguments)


def test_paste_tool_wraps_bridge_errors() -> None:
    with pytest.raises(ToolExecutionError, match="terminal_paste"):
        TerminalPasteTool(_ExplodingBridge()).run({"app": "iTerm2", "text": "ls"})
