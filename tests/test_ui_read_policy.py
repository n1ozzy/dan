"""FAZA D1: ui_read cells of the source-sensitive permission matrix.

docs/MACOS_PERMISSION_MODEL.md §3: `ui_read` | user A | model AP | auto B.
Approved surfaces in D1 are the frontmost app and its focused window — the
tools expose nothing broader, so the policy row carries no payload check.
"""

from __future__ import annotations

import pytest

from dan.tools.permissions import (
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


def decide(source: RequestSource | str) -> ToolDecision:
    policy = ToolPermissionPolicy()
    result = policy.decide("ui_read", source=source, tool_name="matrix", payload={})
    return ToolDecision(result.decision)


def test_ui_read_is_a_known_permission_class() -> None:
    assert PermissionClass("ui_read") == PermissionClass.UI_READ


@pytest.mark.parametrize("source", USER)
def test_ui_read_allows_for_user_sources(source: RequestSource) -> None:
    assert decide(source) == ToolDecision.ALLOW


@pytest.mark.parametrize("source", MODEL)
def test_ui_read_requires_approval_for_model(source: RequestSource) -> None:
    assert decide(source) == ToolDecision.APPROVAL_REQUIRED


@pytest.mark.parametrize("source", AUTO)
def test_ui_read_is_blocked_for_auto_sources(source: RequestSource) -> None:
    assert decide(source) == ToolDecision.BLOCKED


def test_ui_read_with_unknown_source_is_blocked() -> None:
    assert decide("carrier_pigeon") == ToolDecision.BLOCKED
