"""FAZA D4: screen_read cells of the source-sensitive permission matrix.

docs/MACOS_PERMISSION_MODEL.md §3: `screen_read` (narrow) | user A |
model AP | auto B. D4 implements the narrow shape only — the current
window or a named region. The broad shape (full display / continuous)
has no tools and therefore no policy cell to exercise; adding it needs
a new ADR (docs/DECISIONS.md ADR-020).
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


def decide(source: RequestSource | str) -> ToolDecision:
    policy = ToolPermissionPolicy()
    result = policy.decide("screen_read", source=source, tool_name="matrix", payload={})
    return ToolDecision(result.decision)


def test_screen_read_is_a_known_permission_class() -> None:
    assert PermissionClass("screen_read") == PermissionClass.SCREEN_READ


@pytest.mark.parametrize("source", USER)
def test_screen_read_allows_for_user_sources(source: RequestSource) -> None:
    assert decide(source) == ToolDecision.ALLOW


@pytest.mark.parametrize("source", MODEL)
def test_screen_read_requires_approval_for_model(source: RequestSource) -> None:
    assert decide(source) == ToolDecision.APPROVAL_REQUIRED


@pytest.mark.parametrize("source", AUTO)
def test_screen_read_is_blocked_for_auto_sources(source: RequestSource) -> None:
    assert decide(source) == ToolDecision.BLOCKED


def test_screen_read_with_unknown_source_is_blocked() -> None:
    assert decide("carrier_pigeon") == ToolDecision.BLOCKED
