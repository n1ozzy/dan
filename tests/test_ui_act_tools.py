"""FAZA D2: UI action tools over a pluggable actor backend.

Contract (docs/MACOS_OPERATOR_CONTRACT.md + ADR-018): actions always cross
ApprovalGate, Jarvis never owns or extracts credentials — typing into a
secure text field is refused at the tool layer no matter what the backend
would accept, and typed text is never echoed back in tool output.
"""

from __future__ import annotations

import json

import pytest

from jarvis.macos.accessibility import (
    AccessibilityActor,
    AccessibilityError,
    FakeAccessibilityActor,
    MAX_TYPE_CHARS,
    create_actor,
)
from jarvis.tools.permissions import RequestSource, ToolPermissionPolicy
from jarvis.tools.registry import ToolRegistry, ToolRequest
from jarvis.tools.ui_tool import UiClickTool, UiFocusAppTool, UiTypeTool


def make_request(tool_name: str, arguments: dict | None = None) -> ToolRequest:
    return ToolRequest(
        id="run-1",
        tool_name=tool_name,
        arguments=arguments or {},
        requested_by="tests",
    )


# --- fake actor --------------------------------------------------------------


def test_fake_actor_reports_fake_backend_and_starts_idle() -> None:
    actor = FakeAccessibilityActor()
    assert actor.backend == "fake"
    assert actor.performed == []


def test_fake_actor_click_records_action() -> None:
    actor = FakeAccessibilityActor()
    result = actor.click(label="Zaloguj")
    assert result["clicked"] is True
    assert actor.performed == [{"action": "click", "label": "Zaloguj"}]


def test_fake_actor_click_unknown_label_raises() -> None:
    actor = FakeAccessibilityActor()
    with pytest.raises(AccessibilityError):
        actor.click(label="Nie ma takiego przycisku")
    assert actor.performed == []


def test_fake_actor_type_refuses_secure_focused_element() -> None:
    actor = FakeAccessibilityActor(
        focused_element={"role": "AXTextField", "subrole": "AXSecureTextField", "secure": True}
    )
    with pytest.raises(AccessibilityError):
        actor.type_text("tajne")
    assert actor.performed == []


def test_fake_actor_focus_app_records_action() -> None:
    actor = FakeAccessibilityActor()
    result = actor.focus_app("FakePad")
    assert result["focused"] is True
    assert actor.performed == [{"action": "focus_app", "app_name": "FakePad"}]


def test_create_actor_builds_fake_and_fails_closed() -> None:
    assert isinstance(create_actor("fake"), FakeAccessibilityActor)
    with pytest.raises(AccessibilityError):
        create_actor("bogus")


# --- tools -------------------------------------------------------------------


def test_ui_act_tools_declare_ui_act_risk() -> None:
    actor = FakeAccessibilityActor()
    assert UiClickTool(actor).risk == "ui_act"
    assert UiTypeTool(actor).risk == "ui_act"
    assert UiFocusAppTool(actor).risk == "ui_act"


def test_ui_click_runs_and_reports_backend() -> None:
    actor = FakeAccessibilityActor()
    output = UiClickTool(actor).run({"label": "Zaloguj"})
    assert output["ok"] is True
    assert output["backend"] == "fake"
    assert actor.performed[-1]["action"] == "click"


def test_ui_click_requires_label() -> None:
    from jarvis.tools.registry import ToolExecutionError

    with pytest.raises(ToolExecutionError):
        UiClickTool(FakeAccessibilityActor()).run({})


def test_ui_type_types_text_without_echoing_it() -> None:
    actor = FakeAccessibilityActor()
    output = UiTypeTool(actor).run({"text": "notatka od jarvisa"})
    assert output["ok"] is True
    assert output["chars_typed"] == len("notatka od jarvisa")
    assert "notatka od jarvisa" not in json.dumps(output)
    assert actor.performed[-1]["action"] == "type_text"


def test_ui_type_refuses_secure_focused_element_via_tool() -> None:
    from jarvis.tools.registry import ToolExecutionError

    actor = FakeAccessibilityActor(
        focused_element={"role": "AXTextField", "subrole": "AXSecureTextField", "secure": True}
    )
    with pytest.raises(ToolExecutionError):
        UiTypeTool(actor).run({"text": "haslo123"})
    assert actor.performed == []


def test_ui_type_rejects_oversized_text() -> None:
    from jarvis.tools.registry import ToolExecutionError

    with pytest.raises(ToolExecutionError):
        UiTypeTool(FakeAccessibilityActor()).run({"text": "x" * (MAX_TYPE_CHARS + 1)})


def test_ui_focus_app_requires_app_name() -> None:
    from jarvis.tools.registry import ToolExecutionError

    with pytest.raises(ToolExecutionError):
        UiFocusAppTool(FakeAccessibilityActor()).run({"app_name": "   "})


# --- registry integration ----------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [RequestSource.DIRECT_USER_COMMAND, RequestSource.MODEL_ORIGINATED],
)
def test_ui_click_never_executes_without_approval(source: RequestSource) -> None:
    actor = FakeAccessibilityActor()
    registry = ToolRegistry()
    registry.register(UiClickTool(actor))
    result = registry.request_tool(
        make_request("ui_click", {"label": "Zaloguj"}),
        permission_policy=ToolPermissionPolicy(),
        source=source,
    )
    assert result.status == "approval_required"
    assert actor.performed == []


def test_ui_click_blocked_for_auto_sources() -> None:
    actor = FakeAccessibilityActor()
    registry = ToolRegistry()
    registry.register(UiClickTool(actor))
    result = registry.request_tool(
        make_request("ui_click", {"label": "Zaloguj"}),
        permission_policy=ToolPermissionPolicy(),
        source=RequestSource.SCHEDULED_WORKER,
    )
    assert result.status == "blocked"
    assert actor.performed == []


def test_ui_click_executes_after_explicit_execute_step() -> None:
    actor = FakeAccessibilityActor()
    registry = ToolRegistry()
    registry.register(UiClickTool(actor))
    result = registry.execute_tool(make_request("ui_click", {"label": "Zaloguj"}))
    assert result.status == "finished"
    assert actor.performed == [{"action": "click", "label": "Zaloguj"}]


class ExplodingActor(AccessibilityActor):
    backend = "exploding"

    def click(self, *, label, role=None):
        raise AccessibilityError("AX actor unavailable")

    def type_text(self, text):
        raise AccessibilityError("AX actor unavailable")

    def focus_app(self, app_name):
        raise AccessibilityError("AX actor unavailable")


def test_ui_act_tools_surface_backend_errors_as_failures() -> None:
    registry = ToolRegistry()
    registry.register(UiFocusAppTool(ExplodingActor()))
    result = registry.execute_tool(make_request("ui_focus_app", {"app_name": "Safari"}))
    assert result.status == "failed"
    assert "AX actor unavailable" in (result.error or "")
