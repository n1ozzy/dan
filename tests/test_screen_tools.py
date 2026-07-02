"""FAZA D4: screen_read adapter, sanitizer and tools on the fake backend.

Native capture/OCR needs the Screen Recording TCC grant and a live screen,
so unit tests exercise the fake backend, the sanitizer contract, argument
validation and the fail-closed backend factory. The native path is covered
by its probe (`python -m jarvis.macos.screen`) and the live gate.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.config import SecurityConfig
from jarvis.macos.screen import (
    MAX_OCR_LINE_CHARS,
    MAX_OCR_LINES,
    FakeScreenReader,
    NativeScreenReader,
    ScreenReader,
    ScreenReadError,
    create_screen_reader,
    sanitize_ocr_snapshot,
)
from jarvis.tools.permissions import RequestSource, ToolPermissionPolicy
from jarvis.tools.registry import ToolExecutionError
from jarvis.tools.screen_tool import ScreenOcrRegionTool, ScreenReadWindowTool
from tests.git_guards import assert_schema_and_migrations_unchanged


class ExplodingReader(ScreenReader):
    backend = "exploding"

    def read_window(self):
        raise ScreenReadError("no screen recording grant")

    def read_region(self, *, x: int, y: int, width: int, height: int):
        raise ScreenReadError("no screen recording grant")


# ---------------------------------------------------------------------------
# Fake backend
# ---------------------------------------------------------------------------


def test_fake_reader_reports_fake_backend() -> None:
    assert FakeScreenReader().backend == "fake"


def test_fake_reader_fixture_contains_a_secret_looking_line() -> None:
    # The fixture must keep a secret-shaped token so smoke/tests prove the
    # recorder redacts OCR text before it persists.
    lines = FakeScreenReader().read_window()["lines"]
    assert any("sk-" in line for line in lines)


def test_fake_reader_window_and_region_shapes() -> None:
    reader = FakeScreenReader(["one", "two"])

    window = reader.read_window()
    assert window["source"] == "window"
    assert window["lines"] == ["one", "two"]

    region = reader.read_region(x=10, y=20, width=300, height=200)
    assert region["source"] == "region"
    assert region["region"] == {"x": 10, "y": 20, "width": 300, "height": 200}
    assert region["lines"] == ["one", "two"]


# ---------------------------------------------------------------------------
# Sanitizer
# ---------------------------------------------------------------------------


def test_sanitizer_clips_line_count() -> None:
    raw = {"source": "window", "lines": [f"line {i}" for i in range(MAX_OCR_LINES + 50)]}
    snapshot = sanitize_ocr_snapshot(raw)
    assert snapshot["line_count"] == MAX_OCR_LINES
    assert snapshot["truncated"] is True


def test_sanitizer_clips_line_length() -> None:
    raw = {"source": "region", "lines": ["x" * (MAX_OCR_LINE_CHARS + 100)]}
    snapshot = sanitize_ocr_snapshot(raw)
    assert len(snapshot["lines"][0]) == MAX_OCR_LINE_CHARS
    assert snapshot["truncated"] is True


def test_sanitizer_is_json_safe_for_weird_backend_types() -> None:
    raw = {
        "source": object(),
        "lines": [b"bytes-line", 42, None],
        "app_name": object(),
        "pid": 4242,
        "region": {"x": 1, "y": 2, "width": "bad", "height": 4},
    }
    snapshot = sanitize_ocr_snapshot(raw)
    assert all(isinstance(line, str) for line in snapshot["lines"])
    assert isinstance(snapshot["source"], str)
    assert isinstance(snapshot["app_name"], str)
    assert snapshot["pid"] == 4242
    assert "width" not in snapshot["region"]
    assert snapshot["region"]["x"] == 1


def test_sanitizer_handles_missing_or_malformed_input() -> None:
    assert sanitize_ocr_snapshot(None)["lines"] == []
    assert sanitize_ocr_snapshot({})["line_count"] == 0
    assert sanitize_ocr_snapshot({"lines": "not-a-list"})["lines"] == []


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def test_screen_tools_declare_screen_read_risk() -> None:
    reader = FakeScreenReader()
    assert ScreenReadWindowTool(reader).risk == "screen_read"
    assert ScreenOcrRegionTool(reader).risk == "screen_read"


def test_screen_read_window_returns_sanitized_snapshot() -> None:
    tool = ScreenReadWindowTool(FakeScreenReader(["hello screen"]))
    output = tool.run({})
    assert output["ok"] is True
    assert output["backend"] == "fake"
    assert output["screen"]["source"] == "window"
    assert output["screen"]["lines"] == ["hello screen"]
    assert output["line_count"] == 1
    assert output["truncated"] is False


def test_screen_ocr_region_echoes_the_region() -> None:
    tool = ScreenOcrRegionTool(FakeScreenReader(["region text"]))
    output = tool.run({"x": 0, "y": 0, "width": 800, "height": 600})
    assert output["screen"]["region"] == {"x": 0, "y": 0, "width": 800, "height": 600}
    assert output["screen"]["lines"] == ["region text"]


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"x": 0, "y": 0, "width": 100},
        {"x": "0", "y": 0, "width": 100, "height": 100},
        {"x": True, "y": 0, "width": 100, "height": 100},
        {"x": -1, "y": 0, "width": 100, "height": 100},
        {"x": 0, "y": 0, "width": 0, "height": 100},
        {"x": 0, "y": 0, "width": 100, "height": 100_000},
    ],
)
def test_screen_ocr_region_rejects_bad_arguments(arguments: dict) -> None:
    tool = ScreenOcrRegionTool(FakeScreenReader())
    with pytest.raises(ToolExecutionError):
        tool.run(arguments)


def test_screen_tools_surface_backend_errors_as_failures() -> None:
    with pytest.raises(ToolExecutionError):
        ScreenReadWindowTool(ExplodingReader()).run({})
    with pytest.raises(ToolExecutionError):
        ScreenOcrRegionTool(ExplodingReader()).run({"x": 0, "y": 0, "width": 10, "height": 10})


def test_screen_tool_output_never_contains_capture_paths() -> None:
    output = ScreenReadWindowTool(FakeScreenReader()).run({})
    flattened = str(output)
    assert ".png" not in flattened
    assert "/tmp" not in flattened


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (RequestSource.DIRECT_USER_COMMAND, "allow"),
        (RequestSource.MODEL_ORIGINATED, "approval_required"),
        (RequestSource.SCHEDULED_WORKER, "blocked"),
    ],
)
def test_screen_read_end_to_end_matrix(source: RequestSource, expected: str) -> None:
    policy = ToolPermissionPolicy()
    tool = ScreenReadWindowTool(FakeScreenReader())
    result = policy.decide(tool.risk, source=source, tool_name=tool.name, payload={})
    assert result.decision == expected


# ---------------------------------------------------------------------------
# Factory and config
# ---------------------------------------------------------------------------


def test_create_screen_reader_builds_fake_backend(tmp_path: Path) -> None:
    reader = create_screen_reader("fake", work_dir=tmp_path)
    assert isinstance(reader, FakeScreenReader)


def test_create_screen_reader_builds_native_backend(tmp_path: Path) -> None:
    reader = create_screen_reader("native", work_dir=tmp_path)
    assert isinstance(reader, NativeScreenReader)


def test_create_screen_reader_fails_closed_on_unknown_backend(tmp_path: Path) -> None:
    with pytest.raises(ScreenReadError):
        create_screen_reader("screenshotd", work_dir=tmp_path)


def test_security_config_defaults_to_native_backend() -> None:
    assert SecurityConfig().screen_read_backend == "native"


def test_schema_and_migrations_are_unchanged() -> None:
    assert_schema_and_migrations_unchanged(Path(__file__).resolve().parents[1])
