"""Shared git-dependent assertions for test hygiene."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


SCHEMA_GUARD_PATHS = ("jarvis/store/schema.sql", "jarvis/store/migrations.py")
SCHEMA_GUARD_SKIP_REASON = "schema/migration diff guard requires a git working tree"


def assert_schema_and_migrations_unchanged(root: Path) -> None:
    if not is_git_work_tree(root):
        pytest.skip(SCHEMA_GUARD_SKIP_REASON)

    result = subprocess.run(
        ["git", "diff", "--name-only", "--", *SCHEMA_GUARD_PATHS],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == ""


def is_git_work_tree(root: Path) -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"
