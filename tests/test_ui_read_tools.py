"""FAZA D1: read-only Accessibility tools over a pluggable reader backend.

Safety model mirrors file_read defense in depth: whatever the backend
returns, the tool-side sanitizer strips secure text field values, clips
element counts and text lengths, and keeps output JSON-safe. A backend can
therefore never leak a password into tool_runs, not even a buggy one.
"""

from __future__ import annotations

import json

import pytest

from jarvis.macos.accessibility import (
    AccessibilityError,
    AccessibilityReader,
    FakeAccessibilityReader,
    MAX_ELEMENTS,
    MAX_TEXT_CHARS,
    sanitize_window_snapshot,
)
from jarvis.tools.registry import ToolRegistry, ToolRequest
from jarvis.tools.permissions import RequestSource, ToolPermissionPolicy
from jarvis.tools.ui_tool import UiActiveAppTool, UiReadWindowTool


class ExplodingReader(AccessibilityReader):
    backend = "exploding"

    def active_app(self):
        raise AccessibilityError("AX backend unavailable")

    def focused_window(self):
        raise AccessibilityError("AX backend unavailable")


def make_request(tool_name: str, arguments: dict | None = None) -> ToolRequest:
    return ToolRequest(
        id="run-1",
        tool_name=tool_name,
        arguments=arguments or {},
        requested_by="tests",
    )


# --- fake reader -----------------------------------------------------------


def test_fake_reader_reports_fake_backend() -> None:
    assert FakeAccessibilityReader().backend == "fake"


def test_fake_reader_default_fixture_has_app_and_secure_field() -> None:
    reader = FakeAccessibilityReader()
    app = reader.active_app()
    window = reader.focused_window()
    assert app["app_name"]
    assert window["title"]
    assert any(element.get("secure") for element in window["elements"])


def test_fake_reader_accepts_custom_fixture() -> None:
    reader = FakeAccessibilityReader(
        active_app={"app_name": "TestApp", "bundle_id": "com.test.app", "pid": 42},
        focused_window={"app_name": "TestApp", "title": "Okno", "elements": []},
    )
    assert reader.active_app()["app_name"] == "TestApp"
    assert reader.focused_window()["title"] == "Okno"


# --- sanitizer -------------------------------------------------------------


def test_sanitizer_strips_secure_field_values() -> None:
    snapshot = sanitize_window_snapshot(
        {
            "app_name": "Safari",
            "title": "Logowanie",
            "elements": [
                {"role": "AXTextField", "label": "Login", "value": "ozzy"},
                {
                    "role": "AXTextField",
                    "subrole": "AXSecureTextField",
                    "label": "Hasło",
                    "value": "hunter2-super-tajne",
                },
            ],
        }
    )
    dumped = json.dumps(snapshot, ensure_ascii=False)
    assert "hunter2-super-tajne" not in dumped
    secure = snapshot["elements"][1]
    assert secure["secure"] is True
    assert secure["value"] is None


def test_sanitizer_strips_values_when_backend_sets_secure_flag_only() -> None:
    snapshot = sanitize_window_snapshot(
        {
            "app_name": "App",
            "title": "T",
            "elements": [{"role": "AXTextField", "secure": True, "value": "sekret"}],
        }
    )
    assert "sekret" not in json.dumps(snapshot)


def test_sanitizer_clips_element_count() -> None:
    elements = [{"role": "AXStaticText", "value": f"e{i}"} for i in range(MAX_ELEMENTS + 50)]
    snapshot = sanitize_window_snapshot({"app_name": "A", "title": "T", "elements": elements})
    assert len(snapshot["elements"]) == MAX_ELEMENTS
    assert snapshot["truncated"] is True


def test_sanitizer_clips_text_length() -> None:
    long_text = "x" * (MAX_TEXT_CHARS + 100)
    snapshot = sanitize_window_snapshot(
        {"app_name": "A", "title": long_text, "elements": [{"role": "AXStaticText", "value": long_text}]}
    )
    assert len(snapshot["title"]) == MAX_TEXT_CHARS
    assert len(snapshot["elements"][0]["value"]) == MAX_TEXT_CHARS
    assert snapshot["truncated"] is True


def test_sanitizer_is_json_safe_for_weird_backend_types() -> None:
    snapshot = sanitize_window_snapshot(
        {
            "app_name": object(),
            "title": 123,
            "elements": [{"role": None, "value": ["not", "a", "string"]}],
        }
    )
    json.dumps(snapshot)


# --- tools -----------------------------------------------------------------


def test_ui_tools_declare_ui_read_risk() -> None:
    reader = FakeAccessibilityReader()
    assert UiActiveAppTool(reader).risk == "ui_read"
    assert UiReadWindowTool(reader).risk == "ui_read"


def test_ui_active_app_returns_app_info_and_backend() -> None:
    tool = UiActiveAppTool(FakeAccessibilityReader())
    output = tool.run({})
    assert output["ok"] is True
    assert output["backend"] == "fake"
    assert output["app"]["app_name"]


def test_ui_read_window_never_returns_secure_values() -> None:
    tool = UiReadWindowTool(FakeAccessibilityReader())
    output = tool.run({})
    dumped = json.dumps(output, ensure_ascii=False)
    assert output["ok"] is True
    assert "fake-secure-value" not in dumped
    assert any(element["secure"] for element in output["window"]["elements"])


def test_ui_tools_surface_backend_errors_as_failures() -> None:
    registry = ToolRegistry()
    registry.register(UiActiveAppTool(ExplodingReader()))
    result = registry.execute_tool(make_request("ui_active_app"))
    assert result.status == "failed"
    assert "AX backend unavailable" in (result.error or "")


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (RequestSource.DIRECT_USER_COMMAND, "finished"),
        (RequestSource.MODEL_ORIGINATED, "approval_required"),
        (RequestSource.SCHEDULED_WORKER, "blocked"),
    ],
)
def test_ui_read_window_end_to_end_matrix(source: RequestSource, expected: str) -> None:
    registry = ToolRegistry()
    registry.register(UiReadWindowTool(FakeAccessibilityReader()))
    result = registry.request_tool(
        make_request("ui_read_window"),
        permission_policy=ToolPermissionPolicy(),
        source=source,
    )
    assert result.status == expected


# --- backend selection and config -------------------------------------------


def test_create_reader_builds_fake_backend() -> None:
    from jarvis.macos.accessibility import create_reader

    assert isinstance(create_reader("fake"), FakeAccessibilityReader)


def test_create_reader_fails_closed_on_unknown_backend() -> None:
    from jarvis.macos.accessibility import create_reader

    with pytest.raises(AccessibilityError):
        create_reader("bogus")


def test_security_config_defaults_to_ax_backend() -> None:
    from jarvis.config import SecurityConfig

    assert SecurityConfig().ui_read_backend == "ax"
