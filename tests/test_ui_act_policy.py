"""FAZA D2: ui_act cells of the source-sensitive permission matrix.

docs/MACOS_PERMISSION_MODEL.md §3: `ui_act` | user AP | model AP | auto B.
No plain allow for anyone — clicking and typing always cross ApprovalGate;
per-surface earned trust is a §6 future, not D2.
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
    result = policy.decide("ui_act", source=source, tool_name="matrix", payload={})
    return ToolDecision(result.decision)


def test_ui_act_is_a_known_permission_class() -> None:
    assert PermissionClass("ui_act") == PermissionClass.UI_ACT


@pytest.mark.parametrize("source", USER + MODEL)
def test_ui_act_requires_approval_for_user_and_model(source: RequestSource) -> None:
    assert decide(source) == ToolDecision.APPROVAL_REQUIRED


@pytest.mark.parametrize("source", AUTO)
def test_ui_act_is_blocked_for_auto_sources(source: RequestSource) -> None:
    assert decide(source) == ToolDecision.BLOCKED


def test_ui_act_with_unknown_source_is_blocked() -> None:
    assert decide("carrier_pigeon") == ToolDecision.BLOCKED
