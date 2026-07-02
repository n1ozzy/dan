"""FAZA C4: file_write and whitelisted shell_read tool tests.

Both classes are approval-required for every source (matrix §3), so the
integration paths assert the full request -> approval -> explicit execute
lifecycle, and that nothing runs before approval.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.daemon.app import DaemonApp, create_daemon_app
from jarvis.tools.file_tool import FileWriteTool, HARD_MAX_BYTES
from jarvis.tools.permissions import RequestSource
from jarvis.tools.registry import ToolExecutionError
from jarvis.tools.shell_tool import (
    DEFAULT_SHELL_READ_WHITELIST,
    MAX_OUTPUT_CHARS,
    ShellReadTool,
)
from tests.git_guards import assert_schema_and_migrations_unchanged
from tests.test_api_smoke import ROOT, write_config


@pytest.fixture
def approved_root(tmp_path: Path) -> Path:
    root = tmp_path / "approved"
    root.mkdir()
    return root


@pytest.fixture
def write_tool(approved_root: Path) -> FileWriteTool:
    return FileWriteTool(approved_roots=[str(approved_root)])


@pytest.fixture
def shell_tool(approved_root: Path) -> ShellReadTool:
    return ShellReadTool(approved_roots=[str(approved_root)])


# --- file_write unit ---


def test_writes_new_file(write_tool: FileWriteTool, approved_root: Path) -> None:
    target = approved_root / "out.txt"

    output = write_tool.run({"path": str(target), "content": "zażółć\n"})

    assert output["ok"] is True
    assert output["replaced_existing"] is False
    assert target.read_text(encoding="utf-8") == "zażółć\n"


def test_refuses_overwrite_without_flag(write_tool: FileWriteTool, approved_root: Path) -> None:
    target = approved_root / "out.txt"
    target.write_text("old", encoding="utf-8")

    with pytest.raises(ToolExecutionError, match="overwrite=true"):
        write_tool.run({"path": str(target), "content": "new"})
    assert target.read_text(encoding="utf-8") == "old"


def test_overwrites_with_flag(write_tool: FileWriteTool, approved_root: Path) -> None:
    target = approved_root / "out.txt"
    target.write_text("old", encoding="utf-8")

    output = write_tool.run({"path": str(target), "content": "new", "overwrite": True})

    assert output["replaced_existing"] is True
    assert target.read_text(encoding="utf-8") == "new"


def test_write_blocks_outside_approved_roots(write_tool: FileWriteTool, tmp_path: Path) -> None:
    with pytest.raises(ToolExecutionError, match="outside approved roots"):
        write_tool.run({"path": str(tmp_path / "evil.txt"), "content": "x"})
    assert not (tmp_path / "evil.txt").exists()


def test_write_blocks_symlink_escape(
    write_tool: FileWriteTool, approved_root: Path, tmp_path: Path
) -> None:
    outside_dir = tmp_path / "elsewhere"
    outside_dir.mkdir()
    escape = approved_root / "escape"
    escape.symlink_to(outside_dir)

    with pytest.raises(ToolExecutionError, match="outside approved roots"):
        write_tool.run({"path": str(escape / "evil.txt"), "content": "x"})
    assert not (outside_dir / "evil.txt").exists()


def test_write_requires_existing_parent(write_tool: FileWriteTool, approved_root: Path) -> None:
    with pytest.raises(ToolExecutionError, match="parent directory"):
        write_tool.run({"path": str(approved_root / "missing" / "out.txt"), "content": "x"})


def test_write_refuses_replacing_non_regular_file(
    write_tool: FileWriteTool, approved_root: Path
) -> None:
    target = approved_root / "adir"
    target.mkdir()

    with pytest.raises(ToolExecutionError, match="not a regular file"):
        write_tool.run({"path": str(target), "content": "x", "overwrite": True})


def test_write_rejects_oversized_content(write_tool: FileWriteTool, approved_root: Path) -> None:
    with pytest.raises(ToolExecutionError, match="exceeds"):
        write_tool.run(
            {"path": str(approved_root / "big.txt"), "content": "a" * (HARD_MAX_BYTES + 1)}
        )


@pytest.mark.parametrize("bad_content", [None, 7, ["x"]])
def test_write_rejects_non_string_content(
    write_tool: FileWriteTool, approved_root: Path, bad_content: object
) -> None:
    with pytest.raises(ToolExecutionError, match="string content"):
        write_tool.run({"path": str(approved_root / "out.txt"), "content": bad_content})


def test_write_with_no_roots_blocks_everything(tmp_path: Path) -> None:
    tool = FileWriteTool(approved_roots=[])

    with pytest.raises(ToolExecutionError, match="outside approved roots"):
        tool.run({"path": str(tmp_path / "out.txt"), "content": "x"})


# --- shell_read unit ---


def test_runs_whitelisted_command(shell_tool: ShellReadTool, approved_root: Path) -> None:
    output = shell_tool.run({"command": "pwd"})

    assert output["ok"] is True
    assert output["returncode"] == 0
    assert output["stdout"].strip() == str(approved_root.resolve())


def test_normalizes_whitespace_before_matching(shell_tool: ShellReadTool) -> None:
    output = shell_tool.run({"command": "  uname    -a "})

    assert output["ok"] is True
    assert output["command"] == "uname -a"


def test_refuses_non_whitelisted_command(shell_tool: ShellReadTool) -> None:
    with pytest.raises(ToolExecutionError, match="not whitelisted"):
        shell_tool.run({"command": "rm -rf /"})


def test_refuses_whitelisted_prefix_with_extra_args(shell_tool: ShellReadTool) -> None:
    with pytest.raises(ToolExecutionError, match="not whitelisted"):
        shell_tool.run({"command": "ls -la /etc"})


def test_refuses_shell_metacharacters(shell_tool: ShellReadTool) -> None:
    with pytest.raises(ToolExecutionError, match="not whitelisted"):
        shell_tool.run({"command": "pwd; rm -rf /"})


def test_custom_whitelist_replaces_default(approved_root: Path) -> None:
    tool = ShellReadTool(whitelist=["echo hello"], approved_roots=[str(approved_root)])

    output = tool.run({"command": "echo hello"})
    assert output["stdout"].strip() == "hello"

    with pytest.raises(ToolExecutionError, match="not whitelisted"):
        tool.run({"command": "pwd"})


def test_cwd_must_be_inside_approved_roots(shell_tool: ShellReadTool, tmp_path: Path) -> None:
    with pytest.raises(ToolExecutionError, match="outside approved roots"):
        shell_tool.run({"command": "pwd", "cwd": str(tmp_path)})


def test_cwd_inside_roots_is_used(shell_tool: ShellReadTool, approved_root: Path) -> None:
    inner = approved_root / "inner"
    inner.mkdir()

    output = shell_tool.run({"command": "pwd", "cwd": str(inner)})

    assert output["stdout"].strip() == str(inner.resolve())


def test_no_roots_and_no_cwd_fails(tmp_path: Path) -> None:
    tool = ShellReadTool(approved_roots=[])

    with pytest.raises(ToolExecutionError, match="no approved roots"):
        tool.run({"command": "pwd"})


def test_output_is_clipped(approved_root: Path) -> None:
    tool = ShellReadTool(
        whitelist=[f"head -c {MAX_OUTPUT_CHARS * 2} /dev/zero"],
        approved_roots=[str(approved_root)],
    )

    output = tool.run({"command": f"head -c {MAX_OUTPUT_CHARS * 2} /dev/zero"})

    assert output["stdout_truncated"] is True
    assert len(output["stdout"]) == MAX_OUTPUT_CHARS


def test_default_whitelist_contains_no_mutating_commands() -> None:
    forbidden_binaries = {"rm", "mv", "cp", "chmod", "chown", "kill", "sudo", "cat", "curl"}
    for entry in DEFAULT_SHELL_READ_WHITELIST:
        assert entry.split()[0] not in forbidden_binaries, entry


# --- daemon integration: approval lifecycle for both tools ---


@pytest.fixture
def app(tmp_path: Path) -> DaemonApp:
    config_path = write_config(tmp_path / "jarvis.toml", tmp_path / "home" / "jarvis.db")
    daemon_app = create_daemon_app(config_path)
    daemon_app.start()
    try:
        yield daemon_app
    finally:
        daemon_app.stop(reason="test teardown")
        daemon_app.close()


def test_daemon_registers_write_and_shell_tools(app: DaemonApp) -> None:
    specs = {spec.name: spec for spec in app.list_tool_specs()}

    assert specs["file_write"].risk == "file_write"
    assert specs["shell_read"].risk == "shell_read"


def test_file_write_full_approval_lifecycle(app: DaemonApp) -> None:
    target = Path(app.paths.home) / "written-by-jarvis.txt"

    requested = app.request_tool(
        tool_name="file_write",
        arguments={"path": str(target), "content": "approved write"},
        requested_by="cli",
        source=RequestSource.DIRECT_USER_COMMAND,
    )
    assert requested.status == "approval_required"
    assert not target.exists()

    app.approve(str(requested.approval_id), reason="ok")
    assert not target.exists()  # approve alone never executes

    response = app.execute_approved_tool(str(requested.approval_id))

    assert response["ok"] is True
    assert target.read_text(encoding="utf-8") == "approved write"


def test_shell_read_full_approval_lifecycle(app: DaemonApp) -> None:
    requested = app.request_tool(
        tool_name="shell_read",
        arguments={"command": "pwd"},
        requested_by="cli",
        source=RequestSource.DIRECT_USER_COMMAND,
    )
    assert requested.status == "approval_required"

    app.approve(str(requested.approval_id), reason="ok")
    response = app.execute_approved_tool(str(requested.approval_id))

    assert response["ok"] is True
    assert response["result"]["returncode"] == 0
    assert response["result"]["stdout"].strip() == str(Path(app.paths.home).resolve())


def test_rejected_file_write_never_touches_disk(app: DaemonApp) -> None:
    target = Path(app.paths.home) / "never-written.txt"

    requested = app.request_tool(
        tool_name="file_write",
        arguments={"path": str(target), "content": "nope"},
        requested_by="cli",
        source=RequestSource.DIRECT_USER_COMMAND,
    )
    app.reject(str(requested.approval_id), reason="no")

    assert not target.exists()
    assert app.conn is not None
    run_count = int(app.conn.execute("SELECT COUNT(*) FROM tool_runs").fetchone()[0])
    assert run_count == 0


def test_non_whitelisted_shell_command_fails_at_execute_without_side_effects(
    app: DaemonApp,
) -> None:
    requested = app.request_tool(
        tool_name="shell_read",
        arguments={"command": "rm -rf /"},
        requested_by="cli",
        source=RequestSource.DIRECT_USER_COMMAND,
    )
    assert requested.status == "approval_required"

    app.approve(str(requested.approval_id), reason="testing the guard")
    response = app.execute_approved_tool(str(requested.approval_id))

    assert response["ok"] is False
    assert "not whitelisted" in response["tool_run"]["error"]


def test_schema_and_migrations_unchanged() -> None:
    assert_schema_and_migrations_unchanged(ROOT)
