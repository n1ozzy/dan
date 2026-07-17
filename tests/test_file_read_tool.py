"""FAZA C3: real read-only file tool tests.

Covers the execution-time containment re-check (defense in depth), size
limits, binary/UTF-8 handling, and the end-to-end daemon paths: direct user
requests execute immediately, model-originated requests stay approval-gated,
and secret-looking file content never persists raw in tool_runs.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dan.daemon.app import DaemonApp, create_daemon_app
from dan.tools.file_tool import (
    DEFAULT_MAX_BYTES,
    FileReadTool,
    HARD_MAX_BYTES,
)
from dan.tools.permissions import RequestSource
from dan.tools.registry import ToolExecutionError
from tests.git_guards import assert_schema_and_migrations_unchanged
from tests.test_api_smoke import ROOT, write_config


@pytest.fixture
def approved_root(tmp_path: Path) -> Path:
    root = tmp_path / "approved"
    root.mkdir()
    return root


@pytest.fixture
def tool(approved_root: Path) -> FileReadTool:
    return FileReadTool(approved_roots=[str(approved_root)])


def test_reads_utf8_file_inside_approved_root(tool: FileReadTool, approved_root: Path) -> None:
    target = approved_root / "notes.txt"
    target.write_text("zażółć gęślą jaźń\n", encoding="utf-8")

    output = tool.run({"path": str(target)})

    assert output["ok"] is True
    assert output["content"] == "zażółć gęślą jaźń\n"
    assert output["truncated"] is False
    assert output["path"] == str(target.resolve())


def test_expands_home_and_relative_markers(tool: FileReadTool, approved_root: Path) -> None:
    target = approved_root / "inner" / "file.txt"
    target.parent.mkdir()
    target.write_text("data", encoding="utf-8")
    dotted = approved_root / "inner" / ".." / "inner" / "file.txt"

    output = tool.run({"path": str(dotted)})

    assert output["ok"] is True
    assert output["content"] == "data"


def test_blocks_path_outside_approved_roots(tool: FileReadTool, tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")

    with pytest.raises(ToolExecutionError, match="outside approved roots"):
        tool.run({"path": str(outside)})


def test_blocks_symlink_escape_at_execution_time(
    tool: FileReadTool, approved_root: Path, tmp_path: Path
) -> None:
    outside_dir = tmp_path / "elsewhere"
    outside_dir.mkdir()
    (outside_dir / "secret.txt").write_text("secret", encoding="utf-8")
    escape = approved_root / "escape"
    escape.symlink_to(outside_dir)

    with pytest.raises(ToolExecutionError, match="outside approved roots"):
        tool.run({"path": str(escape / "secret.txt")})


def test_tool_with_no_roots_blocks_everything(tmp_path: Path) -> None:
    target = tmp_path / "file.txt"
    target.write_text("data", encoding="utf-8")
    tool = FileReadTool(approved_roots=[])

    with pytest.raises(ToolExecutionError, match="outside approved roots"):
        tool.run({"path": str(target)})


def test_missing_file_fails(tool: FileReadTool, approved_root: Path) -> None:
    with pytest.raises(ToolExecutionError, match="not a regular file"):
        tool.run({"path": str(approved_root / "missing.txt")})


def test_directory_fails(tool: FileReadTool, approved_root: Path) -> None:
    with pytest.raises(ToolExecutionError, match="not a regular file"):
        tool.run({"path": str(approved_root)})


def test_binary_content_is_refused(tool: FileReadTool, approved_root: Path) -> None:
    target = approved_root / "blob.bin"
    target.write_bytes(b"abc\x00def")

    with pytest.raises(ToolExecutionError, match="binary content"):
        tool.run({"path": str(target)})


def test_non_utf8_content_is_refused(tool: FileReadTool, approved_root: Path) -> None:
    target = approved_root / "latin.txt"
    target.write_bytes("zażółć".encode("iso-8859-2"))

    with pytest.raises(ToolExecutionError, match="non-UTF-8"):
        tool.run({"path": str(target)})


def test_truncates_to_max_bytes_without_splitting_characters(
    tool: FileReadTool, approved_root: Path
) -> None:
    target = approved_root / "long.txt"
    target.write_text("ą" * 100, encoding="utf-8")  # 2 bytes per char

    output = tool.run({"path": str(target), "max_bytes": 11})

    assert output["truncated"] is True
    assert output["content"] == "ą" * 5  # 11 bytes -> 5 whole chars, cut byte dropped
    assert output["size_bytes"] == 200


@pytest.mark.parametrize("bad_max", [0, -5, HARD_MAX_BYTES + 1, "big", True, None])
def test_invalid_max_bytes_is_rejected(
    tool: FileReadTool, approved_root: Path, bad_max: object
) -> None:
    target = approved_root / "file.txt"
    target.write_text("data", encoding="utf-8")
    arguments = {"path": str(target), "max_bytes": bad_max}

    with pytest.raises(ToolExecutionError, match="max_bytes"):
        tool.run(arguments)


@pytest.mark.parametrize("bad_path", [None, "", "   ", 7])
def test_invalid_path_is_rejected(tool: FileReadTool, bad_path: object) -> None:
    with pytest.raises(ToolExecutionError, match="path"):
        tool.run({"path": bad_path})


def test_default_max_bytes_is_sane() -> None:
    assert 0 < DEFAULT_MAX_BYTES <= HARD_MAX_BYTES


@pytest.fixture
def app(tmp_path: Path) -> DaemonApp:
    config_path = write_config(tmp_path / "dan.toml", tmp_path / "home" / "dan.db")
    daemon_app = create_daemon_app(config_path)
    daemon_app.start()
    try:
        yield daemon_app
    finally:
        daemon_app.stop(reason="test teardown")
        daemon_app.close()


def home_file(app: DaemonApp, name: str, content: str) -> Path:
    target = Path(app.paths.home) / name
    target.write_text(content, encoding="utf-8")
    return target


def test_daemon_registers_file_read_tool(app: DaemonApp) -> None:
    specs = {spec.name: spec for spec in app.list_tool_specs()}

    assert "file_read" in specs
    assert specs["file_read"].risk == "file_read"


def test_direct_user_request_reads_file_immediately(app: DaemonApp) -> None:
    target = home_file(app, "hello.txt", "hello dan")

    result = app.request_tool(
        tool_name="file_read",
        arguments={"path": str(target)},
        requested_by="cli",
        source=RequestSource.DIRECT_USER_COMMAND,
    )

    assert result.status == "finished"
    assert result.output is not None
    assert result.output["content"] == "hello dan"
    assert app.conn is not None
    run_count = int(app.conn.execute("SELECT COUNT(*) FROM tool_runs").fetchone()[0])
    assert run_count == 1


def test_model_originated_request_stays_approval_gated(app: DaemonApp) -> None:
    target = home_file(app, "gated.txt", "gated")

    result = app.request_tool(
        tool_name="file_read",
        arguments={"path": str(target)},
        requested_by="model",
        source=RequestSource.MODEL_ORIGINATED,
    )

    assert result.status == "approval_required"
    assert result.approval_id is not None
    assert app.conn is not None
    run_count = int(app.conn.execute("SELECT COUNT(*) FROM tool_runs").fetchone()[0])
    assert run_count == 0


def test_direct_request_outside_roots_is_blocked_without_execution(
    app: DaemonApp, tmp_path: Path
) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("nope", encoding="utf-8")

    result = app.request_tool(
        tool_name="file_read",
        arguments={"path": str(outside)},
        requested_by="cli",
        source=RequestSource.DIRECT_USER_COMMAND,
    )

    assert result.status == "blocked"
    assert app.conn is not None
    run_count = int(app.conn.execute("SELECT COUNT(*) FROM tool_runs").fetchone()[0])
    assert run_count == 0


def test_secretlike_file_content_is_redacted_in_tool_runs_and_events(app: DaemonApp) -> None:
    fake_secret = "sk-testfilesecret1234567890"
    target = home_file(app, "env.txt", f"OPENAI={fake_secret}\nplain=ok\n")

    result = app.request_tool(
        tool_name="file_read",
        arguments={"path": str(target)},
        requested_by="cli",
        source=RequestSource.DIRECT_USER_COMMAND,
    )

    assert result.status == "finished"
    assert app.conn is not None
    stored_rows = app.conn.execute("SELECT output_json FROM tool_runs").fetchall()
    stored = json.dumps([row[0] for row in stored_rows])
    assert fake_secret not in stored
    assert "plain=ok" in stored

    assert app.event_store is not None
    events_payloads = json.dumps(
        [event.payload for event in app.event_store.list_after(0, limit=200)],
        ensure_ascii=False,
    )
    assert fake_secret not in events_payloads


def test_config_approved_roots_override_default_home(tmp_path: Path) -> None:
    from tests.test_api_smoke import config_text

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "readme.md").write_text("workspace file", encoding="utf-8")
    raw = config_text(tmp_path / "home" / "dan.db")
    raw = raw.replace(
        "[security]",
        f'[security]\napproved_roots = ["{workspace}"]',
    )
    config_path = tmp_path / "dan.toml"
    config_path.write_text(raw, encoding="utf-8")

    app = create_daemon_app(config_path)
    app.start()
    try:
        result = app.request_tool(
            tool_name="file_read",
            arguments={"path": str(workspace / "readme.md")},
            requested_by="cli",
            source=RequestSource.DIRECT_USER_COMMAND,
        )
        assert result.status == "finished"

        home_target = Path(app.paths.home) / "not-approved.txt"
        home_target.write_text("home is not a root anymore", encoding="utf-8")
        blocked = app.request_tool(
            tool_name="file_read",
            arguments={"path": str(home_target)},
            requested_by="cli",
            source=RequestSource.DIRECT_USER_COMMAND,
        )
        assert blocked.status == "blocked"
    finally:
        app.stop(reason="test teardown")
        app.close()


def test_schema_and_migrations_unchanged() -> None:
    assert_schema_and_migrations_unchanged(ROOT)
