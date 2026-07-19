"""file_write and whitelisted shell_read tool tests.

Model-originated calls execute directly on the active branch. The tool layer
still enforces approved-root containment and the exact shell allowlist.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dan.daemon.app import DaemonApp, create_daemon_app
from dan.tools.file_tool import FileWriteTool, HARD_MAX_BYTES
from dan.tools.permissions import RequestSource
from dan.tools.registry import ToolExecutionError
from dan.tools.shell_tool import (
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


def test_write_refuses_preplanted_symlink_at_temp_path(
    write_tool: FileWriteTool, approved_root: Path, tmp_path: Path
) -> None:
    # FIX-08 (LOW): the temp file must be created without following a symlink
    # (O_NOFOLLOW|O_EXCL). An attacker who pre-plants a symlink at the
    # predictable temp path must not be able to redirect the write through it to
    # a file outside the approved root (a TOCTOU on the write step).
    import os

    outside = tmp_path / "outside_target.txt"
    outside.write_text("original", encoding="utf-8")
    target = approved_root / "out.txt"
    temp_path = f"{target.resolve()}.dan-write-{os.getpid()}.tmp"
    os.symlink(outside, temp_path)

    with pytest.raises(ToolExecutionError):
        write_tool.run({"path": str(target), "content": "attacker-controlled"})

    # the write never followed the symlink out of the root
    assert outside.read_text(encoding="utf-8") == "original"
    assert not target.exists()


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


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True, capture_output=True)


def _make_sentinel_script(path: Path, sentinel: Path) -> Path:
    script = path / "evil.sh"
    script.write_text(f"#!/bin/sh\ntouch {sentinel}\n", encoding="utf-8")
    script.chmod(0o755)
    return script


def test_git_status_does_not_execute_repo_local_fsmonitor(
    shell_tool: ShellReadTool, approved_root: Path
) -> None:
    repo = approved_root / "evil-repo"
    repo.mkdir()
    _init_git_repo(repo)
    sentinel = approved_root / "fsmonitor-sentinel"
    script = _make_sentinel_script(approved_root, sentinel)
    subprocess.run(
        ["git", "-C", str(repo), "config", "core.fsmonitor", str(script)],
        check=True,
        capture_output=True,
    )

    output = shell_tool.run({"command": "git status --short", "cwd": str(repo)})

    assert output["ok"] is True
    assert not sentinel.exists(), "repo-local core.fsmonitor was executed"


def test_git_status_ignores_repo_local_hooks_path(
    shell_tool: ShellReadTool, approved_root: Path
) -> None:
    repo = approved_root / "hooked-repo"
    repo.mkdir()
    _init_git_repo(repo)
    hooks = approved_root / "evil-hooks"
    hooks.mkdir()
    sentinel = approved_root / "hooks-sentinel"
    _make_sentinel_script(hooks, sentinel).rename(hooks / "post-index-change")
    subprocess.run(
        ["git", "-C", str(repo), "config", "core.hooksPath", str(hooks)],
        check=True,
        capture_output=True,
    )

    output = shell_tool.run({"command": "git status --short", "cwd": str(repo)})

    assert output["ok"] is True
    assert not sentinel.exists(), "repo-local core.hooksPath hook was executed"


def test_git_status_still_works_in_legit_repo(
    shell_tool: ShellReadTool, approved_root: Path
) -> None:
    repo = approved_root / "legit-repo"
    repo.mkdir()
    _init_git_repo(repo)
    (repo / "file.txt").write_text("hello\n", encoding="utf-8")

    output = shell_tool.run({"command": "git status --short", "cwd": str(repo)})

    assert output["ok"] is True
    assert "file.txt" in output["stdout"]


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


# --- daemon integration: direct execution with tool-layer guards ---


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


def test_daemon_registers_write_and_shell_tools(app: DaemonApp) -> None:
    specs = {spec.name: spec for spec in app.list_tool_specs()}

    assert specs["file_write"].risk == "file_write"
    assert specs["shell_read"].risk == "shell_read"


def test_file_write_executes_directly_without_approval_row(app: DaemonApp) -> None:
    target = Path(app.paths.home) / "written-by-dan.txt"

    requested = app.request_tool(
        tool_name="file_write",
        arguments={"path": str(target), "content": "direct write"},
        requested_by="model",
        source=RequestSource.MODEL_ORIGINATED,
    )
    assert requested.status == "finished"
    assert requested.approval_id is None
    assert requested.output is not None
    assert requested.output["ok"] is True
    assert target.read_text(encoding="utf-8") == "direct write"
    assert app.conn is not None
    assert int(app.conn.execute("SELECT COUNT(*) FROM approvals").fetchone()[0]) == 0


def test_shell_read_executes_directly_without_approval_row(app: DaemonApp) -> None:
    requested = app.request_tool(
        tool_name="shell_read",
        arguments={"command": "pwd"},
        requested_by="model",
        source=RequestSource.MODEL_ORIGINATED,
    )
    assert requested.status == "finished"
    assert requested.approval_id is None
    assert requested.output is not None
    assert requested.output["returncode"] == 0
    assert requested.output["stdout"].strip() == str(Path(app.paths.home).resolve())
    assert app.conn is not None
    assert int(app.conn.execute("SELECT COUNT(*) FROM approvals").fetchone()[0]) == 0


def test_out_of_root_file_write_fails_without_touching_disk(
    app: DaemonApp, tmp_path: Path
) -> None:
    target = tmp_path / "outside.txt"

    requested = app.request_tool(
        tool_name="file_write",
        arguments={"path": str(target), "content": "nope"},
        requested_by="model",
        source=RequestSource.MODEL_ORIGINATED,
    )
    assert requested.status == "failed"
    assert requested.approval_id is None
    assert requested.error is not None
    assert "outside approved roots" in requested.error
    assert not target.exists()
    assert app.conn is not None
    assert int(app.conn.execute("SELECT COUNT(*) FROM approvals").fetchone()[0]) == 0


def test_non_whitelisted_shell_command_fails_directly_without_side_effects(
    app: DaemonApp, tmp_path: Path
) -> None:
    sentinel = tmp_path / "must-not-exist"
    requested = app.request_tool(
        tool_name="shell_read",
        arguments={"command": f"touch {sentinel}"},
        requested_by="model",
        source=RequestSource.MODEL_ORIGINATED,
    )
    assert requested.status == "failed"
    assert requested.approval_id is None
    assert requested.error is not None
    assert "not whitelisted" in requested.error
    assert not sentinel.exists()


def test_schema_and_migrations_unchanged() -> None:
    assert_schema_and_migrations_unchanged(ROOT)
