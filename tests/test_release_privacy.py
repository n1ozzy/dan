"""Task 13: the release worktree carries no private data, owner paths or secrets."""

from __future__ import annotations

import subprocess
from pathlib import Path

from dan.release_audit import audit_worktree

REPO_ROOT = Path(__file__).resolve().parents[1]

# Built by concatenation so this test file never contains the contiguous
# owner path itself (the audit under test scans raw file content).
OWNER_HOME = "/Users/" + "n1_ozzy"
FAKE_API_KEY = "sk-" + "a" * 32


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "--quiet", str(root)], check=True, capture_output=True)


def _track(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(root), "add", "--", relative], check=True, capture_output=True
    )


def test_repository_has_no_private_runtime_data() -> None:
    findings = audit_worktree(REPO_ROOT)
    assert findings.private_paths == []
    assert findings.absolute_owner_paths == []
    assert findings.secrets == []


def test_audit_reports_planted_owner_path_and_secret(tmp_path: Path) -> None:
    """Positive control: the auditor is not blind."""
    _init_repo(tmp_path)
    _track(tmp_path, "notes.md", f"backup lives in {OWNER_HOME}/Documents/dev/dan\n")
    _track(tmp_path, "config/settings.py", f'API_KEY = "{FAKE_API_KEY}"\n')
    _track(tmp_path, ".env", "TOKEN=whatever\n")

    findings = audit_worktree(tmp_path)
    assert any(f.path == "notes.md" for f in findings.absolute_owner_paths)
    assert any(f.path == "config/settings.py" for f in findings.secrets)
    assert any(f.path == ".env" for f in findings.private_paths)


def test_audit_ignores_untracked_ignored_files(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _track(tmp_path, ".gitignore", "*.log\n")
    (tmp_path / "debug.log").write_text(f"{OWNER_HOME}/tmp\n", encoding="utf-8")

    findings = audit_worktree(tmp_path)
    assert findings.absolute_owner_paths == []
    assert findings.private_paths == []
