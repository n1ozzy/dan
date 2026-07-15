"""Tests for git-dependent schema guard helpers."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.git_guards import assert_schema_and_migrations_unchanged


def completed(args: list[str], returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=args, returncode=returncode, stdout=stdout, stderr=stderr)


def test_schema_guard_skips_outside_git_work_tree(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return completed(args, 128, stderr="not a git repository")

    monkeypatch.setattr("tests.git_guards.subprocess.run", fake_run)

    with pytest.raises(pytest.skip.Exception):
        assert_schema_and_migrations_unchanged(tmp_path)

    assert calls == [["git", "rev-parse", "--is-inside-work-tree"]]


def test_schema_guard_fails_inside_git_work_tree_when_schema_patch_is_malformed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[:3] == ["git", "rev-parse", "--is-inside-work-tree"]:
            return completed(args, 0, stdout="true\n")
        return completed(args, 1, stdout="jarvis/store/schema.sql:10: trailing whitespace.\n")

    monkeypatch.setattr("tests.git_guards.subprocess.run", fake_run)

    with pytest.raises(AssertionError):
        assert_schema_and_migrations_unchanged(tmp_path)

    assert calls == [
        ["git", "rev-parse", "--is-inside-work-tree"],
        ["git", "diff", "--check", "--", "jarvis/store/schema.sql", "jarvis/store/migrations.py"],
    ]
