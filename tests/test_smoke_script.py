"""Prompt 11D manual text runtime smoke harness tests."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

from tests.git_guards import assert_schema_and_migrations_unchanged


ROOT = Path(__file__).resolve().parents[1]
SMOKE_SCRIPT = ROOT / "scripts" / "smoke-text-runtime.sh"
CLAUDE_SMOKE_SCRIPT = ROOT / "scripts" / "smoke-claude-cli-brain.sh"
RUNBOOK = ROOT / "docs" / "runbooks" / "TEXT_RUNTIME_SMOKE.md"
PROVIDER_RUNBOOK = ROOT / "docs" / "runbooks" / "PROVIDER_SMOKE.md"
README = ROOT / "README.md"

FORBIDDEN_SCRIPT_SNIPPETS = (
    "launchctl",
    "pkill",
    "/tmp/dan",
    "/Users/n1_ozzy/Documents/dev/dan",
    "afplay",
    "--dangerously-skip-permissions",
)

REQUIRED_SCRIPT_SNIPPETS = (
    "python -m jarvis.cli",
    "daemon run",
    "input text",
    "conversations list",
    "turns list",
    "events after",
)

REQUIRED_CLAUDE_SMOKE_SNIPPETS = (
    "command -v claude",
    'command = "claude"',
    'args = ["-p"]',
    "daemon run",
    "input text",
    "--timeout 180",
    "events after",
    "brain.requested",
    "brain.responded",
    "turn.finished",
)

FORBIDDEN_RUNTIME_SNIPPETS = (
    "/Users/n1_ozzy/Documents/dev/dan",
    "/tmp/dan",
    "afplay",
    "--dangerously-skip-permissions",
)


def test_smoke_script_exists() -> None:
    assert SMOKE_SCRIPT.is_file()


def test_smoke_script_is_executable() -> None:
    mode = SMOKE_SCRIPT.stat().st_mode
    assert mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    assert os.access(SMOKE_SCRIPT, os.X_OK)


def test_claude_smoke_script_exists() -> None:
    assert CLAUDE_SMOKE_SCRIPT.is_file()


def test_claude_smoke_script_is_executable() -> None:
    mode = CLAUDE_SMOKE_SCRIPT.stat().st_mode
    assert mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    assert os.access(CLAUDE_SMOKE_SCRIPT, os.X_OK)


def test_smoke_script_passes_bash_syntax_check() -> None:
    result = subprocess.run(
        ["bash", "-n", str(SMOKE_SCRIPT)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_claude_smoke_script_passes_bash_syntax_check() -> None:
    result = subprocess.run(
        ["bash", "-n", str(CLAUDE_SMOKE_SCRIPT)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_smoke_script_avoids_forbidden_process_and_legacy_calls() -> None:
    text = SMOKE_SCRIPT.read_text(encoding="utf-8")

    offenders = [snippet for snippet in FORBIDDEN_SCRIPT_SNIPPETS if snippet in text]
    assert offenders == []


def test_claude_smoke_script_avoids_forbidden_process_and_legacy_calls() -> None:
    text = CLAUDE_SMOKE_SCRIPT.read_text(encoding="utf-8")

    offenders = [snippet for snippet in FORBIDDEN_SCRIPT_SNIPPETS if snippet in text]
    assert offenders == []


def test_smoke_script_references_required_cli_flow() -> None:
    text = SMOKE_SCRIPT.read_text(encoding="utf-8")

    missing = [snippet for snippet in REQUIRED_SCRIPT_SNIPPETS if snippet not in text]
    assert missing == []


def test_claude_smoke_script_references_required_provider_flow() -> None:
    text = CLAUDE_SMOKE_SCRIPT.read_text(encoding="utf-8")

    missing = [snippet for snippet in REQUIRED_CLAUDE_SMOKE_SNIPPETS if snippet not in text]
    assert missing == []


def test_smoke_runbook_exists() -> None:
    assert RUNBOOK.is_file()


def test_provider_smoke_runbook_exists() -> None:
    assert PROVIDER_RUNBOOK.is_file()


def test_smoke_runbook_documents_temp_database_and_runtime() -> None:
    text = RUNBOOK.read_text(encoding="utf-8").lower()

    assert "temporary db" in text
    assert "temporary runtime" in text
    assert "real ~/.jarvis" in text


def test_provider_smoke_runbook_documents_safe_claude_cli_smoke() -> None:
    text = PROVIDER_RUNBOOK.read_text(encoding="utf-8")
    lowered = text.lower()

    assert "temporary config" in lowered
    assert "temporary db" in lowered
    assert "real `~/.jarvis`" in lowered
    assert 'default_adapter = "claude_cli"' in text
    assert 'command = "claude"' in text
    assert 'args = ["-p"]' in text
    assert 'model = "claude-cli"' in text
    assert "--timeout 180" in text
    assert "--dangerously-skip-permissions" in text


def test_smoke_runbook_documents_excluded_runtime_surfaces() -> None:
    text = RUNBOOK.read_text(encoding="utf-8").lower()

    for phrase in (
        "does not use launchd",
        "does not use voice",
        "does not use tools",
        "does not use workers",
        "does not use real providers",
    ):
        assert phrase in text


def test_readme_points_to_smoke_runbook() -> None:
    text = README.read_text(encoding="utf-8")

    assert "docs/runbooks/TEXT_RUNTIME_SMOKE.md" in text
    assert "docs/runbooks/PROVIDER_SMOKE.md" in text


def test_schema_and_migrations_are_unchanged() -> None:
    assert_schema_and_migrations_unchanged(ROOT)


def test_runtime_files_avoid_forbidden_legacy_strings() -> None:
    scanned_roots = ("jarvis", "config", "scripts", "launchd", "README.md", "pyproject.toml")
    text_suffixes = {".py", ".sql", ".toml", ".md", ".sh", ".example", ".html", ".js", ".css", ""}
    offenders: list[tuple[str, str]] = []

    for relative_root in scanned_roots:
        root = ROOT / relative_root
        files = [root] if root.is_file() else [path for path in root.rglob("*") if path.is_file()]
        for path in files:
            if "__pycache__" in path.parts or path.suffix not in text_suffixes:
                continue
            text = path.read_text(encoding="utf-8")
            for snippet in FORBIDDEN_RUNTIME_SNIPPETS:
                if snippet in text:
                    offenders.append((str(path.relative_to(ROOT)), snippet))

    assert offenders == []
