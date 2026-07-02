"""FAZA D5: terminal_read / terminal_write cells of the permission matrix.

docs/MACOS_PERMISSION_MODEL.md §3 (ADR-021):
`terminal_read`  | user A  | model AP | auto B — same shape as ui_read /
screen_read (narrow): observing the front window of a named terminal app.
`terminal_write` | user AP | model AP | auto B — shell_write-grade
(MACOS_CAPABILITIES.md §9): pasting into a terminal is one Enter away from
execution, so no source ever gets a plain allow.

Reading FROM a terminal and writing TO a terminal are distinct classes and
must never share a row (the ui_read / ui_act precedent).
"""

from __future__ import annotations

import pytest

from jarvis.tools.permissions import (
    AUTO_SOURCES,
    PermissionClass,
    RequestSource,
    ToolDecision,
    ToolPermissionPolicy,
    USER_SOURCES,
)


USER = sorted(USER_SOURCES)
MODEL = [RequestSource.MODEL_ORIGINATED]
AUTO = sorted(AUTO_SOURCES)


def decide(risk: str, source: RequestSource | str) -> ToolDecision:
    policy = ToolPermissionPolicy()
    result = policy.decide(risk, source=source, tool_name="matrix", payload={})
    return ToolDecision(result.decision)


def test_terminal_read_is_a_known_permission_class() -> None:
    assert PermissionClass("terminal_read") == PermissionClass.TERMINAL_READ


def test_terminal_write_is_a_known_permission_class() -> None:
    assert PermissionClass("terminal_write") == PermissionClass.TERMINAL_WRITE


# ---------------------------------------------------------------------------
# terminal_read | user A | model AP | auto B
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("source", USER)
def test_terminal_read_allows_for_user_sources(source: RequestSource) -> None:
    assert decide("terminal_read", source) == ToolDecision.ALLOW


@pytest.mark.parametrize("source", MODEL)
def test_terminal_read_requires_approval_for_model(source: RequestSource) -> None:
    assert decide("terminal_read", source) == ToolDecision.APPROVAL_REQUIRED


@pytest.mark.parametrize("source", AUTO)
def test_terminal_read_is_blocked_for_auto_sources(source: RequestSource) -> None:
    assert decide("terminal_read", source) == ToolDecision.BLOCKED


def test_terminal_read_with_unknown_source_is_blocked() -> None:
    assert decide("terminal_read", "carrier_pigeon") == ToolDecision.BLOCKED


# ---------------------------------------------------------------------------
# terminal_write | user AP | model AP | auto B
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("source", USER)
def test_terminal_write_requires_approval_for_user_sources(source: RequestSource) -> None:
    assert decide("terminal_write", source) == ToolDecision.APPROVAL_REQUIRED


@pytest.mark.parametrize("source", MODEL)
def test_terminal_write_requires_approval_for_model(source: RequestSource) -> None:
    assert decide("terminal_write", source) == ToolDecision.APPROVAL_REQUIRED


@pytest.mark.parametrize("source", AUTO)
def test_terminal_write_is_blocked_for_auto_sources(source: RequestSource) -> None:
    assert decide("terminal_write", source) == ToolDecision.BLOCKED


def test_terminal_write_with_unknown_source_is_blocked() -> None:
    assert decide("terminal_write", "carrier_pigeon") == ToolDecision.BLOCKED


# ---------------------------------------------------------------------------
# The read/write split itself is an invariant, not an accident.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("source", USER)
def test_terminal_read_and_write_rows_differ_for_user_sources(
    source: RequestSource,
) -> None:
    assert decide("terminal_read", source) != decide("terminal_write", source)
