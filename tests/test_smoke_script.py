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
TOOLS_SMOKE_SCRIPT = ROOT / "scripts" / "smoke-tools-approvals.sh"
MEMORY_SMOKE_SCRIPT = ROOT / "scripts" / "smoke-memory-runtime.sh"
CONTINUATION_SMOKE_SCRIPT = ROOT / "scripts" / "smoke-tool-continuation.sh"
FILE_READ_SMOKE_SCRIPT = ROOT / "scripts" / "smoke-file-read.sh"
STREAM_SMOKE_SCRIPT = ROOT / "scripts" / "smoke-stream.sh"
E2E_SMOKE_SCRIPT = ROOT / "scripts" / "smoke-e2e-mvp.sh"
E2E_RUNBOOK = ROOT / "docs" / "runbooks" / "E2E_MVP_SMOKE.md"
RUNBOOK = ROOT / "docs" / "runbooks" / "TEXT_RUNTIME_SMOKE.md"
PROVIDER_RUNBOOK = ROOT / "docs" / "runbooks" / "PROVIDER_SMOKE.md"
TOOLS_RUNBOOK = ROOT / "docs" / "runbooks" / "TOOLS_AND_APPROVALS.md"
MEMORY_RUNBOOK = ROOT / "docs" / "runbooks" / "MEMORY_API.md"
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

REQUIRED_TOOLS_SMOKE_SNIPPETS = (
    "python -m jarvis.cli",
    "daemon run",
    "/tools",
    "/tools/request",
    "/approvals",
    "/execute",
    "already executed",
    "events after",
    "approval_probe",
    "voice_queue",
    "worker_jobs",
)

REQUIRED_MEMORY_SMOKE_SNIPPETS = (
    "python -m jarvis.cli",
    "daemon run",
    "memory create",
    "memory list",
    "memory disable",
    "input text",
    "events after",
    "memory.updated",
    "context_snapshot",
    "memory_block_count",
    "voice_queue",
    "worker_jobs",
)

REQUIRED_CONTINUATION_SMOKE_SNIPPETS = (
    "python -m jarvis.cli",
    "daemon run",
    "/input/text",
    "awaiting_approval",
    "/approvals",
    "/execute",
    "already executed",
    "approval_probe",
    "jarvis_tool_call",
    "Continuation after approved tool execution",
    "tool_result_continuation",
    "pending_approval_count",
    "brain.requested",
    "voice_queue",
    "worker_jobs",
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


def test_tools_smoke_script_exists() -> None:
    assert TOOLS_SMOKE_SCRIPT.is_file()


def test_tools_smoke_script_is_executable() -> None:
    mode = TOOLS_SMOKE_SCRIPT.stat().st_mode
    assert mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    assert os.access(TOOLS_SMOKE_SCRIPT, os.X_OK)


def test_memory_smoke_script_exists() -> None:
    assert MEMORY_SMOKE_SCRIPT.is_file()


def test_memory_smoke_script_is_executable() -> None:
    mode = MEMORY_SMOKE_SCRIPT.stat().st_mode
    assert mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    assert os.access(MEMORY_SMOKE_SCRIPT, os.X_OK)


def test_continuation_smoke_script_exists() -> None:
    assert CONTINUATION_SMOKE_SCRIPT.is_file()


def test_continuation_smoke_script_is_executable() -> None:
    mode = CONTINUATION_SMOKE_SCRIPT.stat().st_mode
    assert mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    assert os.access(CONTINUATION_SMOKE_SCRIPT, os.X_OK)


def test_file_read_smoke_script_exists() -> None:
    assert FILE_READ_SMOKE_SCRIPT.is_file()


def test_file_read_smoke_script_is_executable() -> None:
    mode = FILE_READ_SMOKE_SCRIPT.stat().st_mode
    assert mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    assert os.access(FILE_READ_SMOKE_SCRIPT, os.X_OK)


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


def test_tools_smoke_script_passes_bash_syntax_check() -> None:
    result = subprocess.run(
        ["bash", "-n", str(TOOLS_SMOKE_SCRIPT)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_memory_smoke_script_passes_bash_syntax_check() -> None:
    result = subprocess.run(
        ["bash", "-n", str(MEMORY_SMOKE_SCRIPT)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_continuation_smoke_script_passes_bash_syntax_check() -> None:
    result = subprocess.run(
        ["bash", "-n", str(CONTINUATION_SMOKE_SCRIPT)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_stream_smoke_script_exists() -> None:
    assert STREAM_SMOKE_SCRIPT.is_file()


def test_stream_smoke_script_is_executable() -> None:
    mode = STREAM_SMOKE_SCRIPT.stat().st_mode
    assert mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    assert os.access(STREAM_SMOKE_SCRIPT, os.X_OK)


def test_stream_smoke_script_passes_bash_syntax_check() -> None:
    result = subprocess.run(
        ["bash", "-n", str(STREAM_SMOKE_SCRIPT)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_stream_smoke_script_avoids_forbidden_process_and_legacy_calls() -> None:
    text = STREAM_SMOKE_SCRIPT.read_text(encoding="utf-8")

    offenders = [snippet for snippet in FORBIDDEN_SCRIPT_SNIPPETS if snippet in text]
    assert offenders == []


def test_stream_smoke_script_exercises_stream_contract() -> None:
    text = STREAM_SMOKE_SCRIPT.read_text(encoding="utf-8")

    for snippet in (
        "GET /stream HTTP/1.1",
        "Sec-WebSocket-Key",
        "jarvis-token.",
        "output_omitted",
        "1003",
    ):
        assert snippet in text


def test_smoke_script_avoids_forbidden_process_and_legacy_calls() -> None:
    text = SMOKE_SCRIPT.read_text(encoding="utf-8")

    offenders = [snippet for snippet in FORBIDDEN_SCRIPT_SNIPPETS if snippet in text]
    assert offenders == []


def test_claude_smoke_script_avoids_forbidden_process_and_legacy_calls() -> None:
    text = CLAUDE_SMOKE_SCRIPT.read_text(encoding="utf-8")

    offenders = [snippet for snippet in FORBIDDEN_SCRIPT_SNIPPETS if snippet in text]
    assert offenders == []


def test_tools_smoke_script_avoids_forbidden_process_and_legacy_calls() -> None:
    text = TOOLS_SMOKE_SCRIPT.read_text(encoding="utf-8")

    offenders = [snippet for snippet in FORBIDDEN_SCRIPT_SNIPPETS if snippet in text]
    assert offenders == []


def test_memory_smoke_script_avoids_forbidden_process_and_legacy_calls() -> None:
    text = MEMORY_SMOKE_SCRIPT.read_text(encoding="utf-8")

    offenders = [snippet for snippet in FORBIDDEN_SCRIPT_SNIPPETS if snippet in text]
    assert offenders == []


def test_continuation_smoke_script_avoids_forbidden_process_and_legacy_calls() -> None:
    text = CONTINUATION_SMOKE_SCRIPT.read_text(encoding="utf-8")

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


def test_tools_smoke_script_references_required_tools_flow() -> None:
    text = TOOLS_SMOKE_SCRIPT.read_text(encoding="utf-8")

    missing = [snippet for snippet in REQUIRED_TOOLS_SMOKE_SNIPPETS if snippet not in text]
    assert missing == []


def test_memory_smoke_script_references_required_memory_flow() -> None:
    text = MEMORY_SMOKE_SCRIPT.read_text(encoding="utf-8")

    missing = [snippet for snippet in REQUIRED_MEMORY_SMOKE_SNIPPETS if snippet not in text]
    assert missing == []


def test_continuation_smoke_script_references_required_continuation_flow() -> None:
    text = CONTINUATION_SMOKE_SCRIPT.read_text(encoding="utf-8")

    missing = [snippet for snippet in REQUIRED_CONTINUATION_SMOKE_SNIPPETS if snippet not in text]
    assert missing == []


def test_continuation_smoke_script_uses_fake_local_cli_brain_only() -> None:
    text = CONTINUATION_SMOKE_SCRIPT.read_text(encoding="utf-8")

    for snippet in (
        'default_adapter = "claude_cli"',
        'command = "$FAKE_BRAIN"',
        'model = "fake-brain"',
        "fake-brain.sh",
    ):
        assert snippet in text
    assert 'command = "claude"' not in text
    assert 'command = "codex"' not in text


def test_tools_smoke_script_uses_temp_runtime_and_child_pid_cleanup_only() -> None:
    text = TOOLS_SMOKE_SCRIPT.read_text(encoding="utf-8")

    for snippet in (
        "mktemp",
        "SMOKE_KEEP_ARTIFACTS",
        "runtime.home",
        "runtime.logs_dir",
        "runtime.runtime_dir",
        "runtime.pid_file",
        "database.path",
        "DAEMON_PID=",
        "kill \"$DAEMON_PID\"",
    ):
        assert snippet in text
    assert "~/.jarvis" not in text


def test_memory_smoke_script_uses_temp_runtime_and_child_pid_cleanup_only() -> None:
    text = MEMORY_SMOKE_SCRIPT.read_text(encoding="utf-8")

    for snippet in (
        "mktemp",
        "SMOKE_KEEP_ARTIFACTS",
        "runtime.home",
        "runtime.logs_dir",
        "runtime.runtime_dir",
        "runtime.pid_file",
        "database.path",
        "DAEMON_PID=",
        "kill \"$DAEMON_PID\"",
        'default_adapter = "claude_cli"',
        'command = "claude"',
        'args = ["-p"]',
        'model = "claude-cli"',
        "voice.enabled = false",
        "launchd.enabled = false",
    ):
        assert snippet in text
    assert "~/.jarvis" not in text


def test_continuation_smoke_script_uses_temp_runtime_and_child_pid_cleanup_only() -> None:
    text = CONTINUATION_SMOKE_SCRIPT.read_text(encoding="utf-8")

    for snippet in (
        "mktemp",
        "SMOKE_KEEP_ARTIFACTS",
        "runtime.home",
        "runtime.logs_dir",
        "runtime.runtime_dir",
        "runtime.pid_file",
        "database.path",
        "DAEMON_PID=",
        "kill \"$DAEMON_PID\"",
    ):
        assert snippet in text
    assert "~/.jarvis" not in text


def test_pytest_only_syntax_checks_continuation_smoke_script() -> None:
    text = Path(__file__).read_text(encoding="utf-8")
    direct_exec = "subprocess.run([str(CONTINUATION_" + "SMOKE_SCRIPT)"

    assert direct_exec not in text


def test_pytest_only_syntax_checks_tools_smoke_script() -> None:
    text = Path(__file__).read_text(encoding="utf-8")
    direct_exec = "subprocess.run([str(TOOLS_" + "SMOKE_SCRIPT)"

    assert direct_exec not in text


def test_pytest_only_syntax_checks_memory_smoke_script() -> None:
    text = Path(__file__).read_text(encoding="utf-8")
    direct_exec = "subprocess.run([str(MEMORY_" + "SMOKE_SCRIPT)"

    assert direct_exec not in text


# F1 e2e MVP smoke: one daemon instance walks the operator scenario from
# MASTER_PLAN §6 end to end (fake CLI brain + fake backends, no providers).
REQUIRED_E2E_SMOKE_SNIPPETS = (
    "daemon run",
    "/health",
    "/input/text",
    "Restarting daemon",
    "<jarvis_tool_call>",
    "/approvals/",
    "/execute",
    "/reject",
    "409",
    "401",
    "/tools/request",
    "blocked",
    "/brain/switch",
    "/workers/jobs",
    "/runtime/processes",
    "/stream",
    "redact",
    "approved_roots",
)


def test_e2e_smoke_script_exists_and_is_executable() -> None:
    assert E2E_SMOKE_SCRIPT.is_file()
    assert os.access(E2E_SMOKE_SCRIPT, os.X_OK)
    mode = stat.S_IMODE(E2E_SMOKE_SCRIPT.stat().st_mode)
    assert mode & stat.S_IXUSR


def test_e2e_smoke_script_references_required_operator_flow() -> None:
    text = E2E_SMOKE_SCRIPT.read_text(encoding="utf-8")

    missing = [snippet for snippet in REQUIRED_E2E_SMOKE_SNIPPETS if snippet not in text]
    assert missing == []


def test_e2e_smoke_script_uses_fake_local_cli_brain_only() -> None:
    text = E2E_SMOKE_SCRIPT.read_text(encoding="utf-8")

    assert "fake-brain" in text
    assert "command -v claude" not in text
    assert 'command = "claude"' not in text
    assert "api.anthropic.com" not in text


def test_pytest_only_syntax_checks_e2e_smoke_script() -> None:
    result = subprocess.run(
        ["bash", "-n", str(E2E_SMOKE_SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_e2e_smoke_runbook_maps_acceptance_criteria() -> None:
    text = E2E_RUNBOOK.read_text(encoding="utf-8")
    lowered = text.lower()

    assert "smoke-e2e-mvp.sh" in text
    assert "master_plan" in lowered and "§6" in text
    # The runbook must say where every criterion is proven, including the
    # ones this harness does not cover itself.
    for marker in ("symlink", "accessibility", "screen", "launchd", "live gate"):
        assert marker in lowered, marker


def test_smoke_runbook_exists() -> None:
    assert RUNBOOK.is_file()


def test_provider_smoke_runbook_exists() -> None:
    assert PROVIDER_RUNBOOK.is_file()


def test_tools_smoke_runbook_exists() -> None:
    assert TOOLS_RUNBOOK.is_file()


def test_memory_smoke_runbook_exists() -> None:
    assert MEMORY_RUNBOOK.is_file()


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


def test_tools_smoke_runbook_documents_manual_harness_scope() -> None:
    text = TOOLS_RUNBOOK.read_text(encoding="utf-8")
    lowered = text.lower()

    assert "scripts/smoke-tools-approvals.sh" in text
    assert "SMOKE_KEEP_ARTIFACTS=1 scripts/smoke-tools-approvals.sh" in text
    assert "approval_probe" in text
    assert "explicit execute endpoint" in lowered
    assert "does not execute automatically" in lowered
    assert "duplicate execution prevention" in lowered
    for phrase in (
        "no real shell execution",
        "no file writing",
        "no network tools",
        "no worker replay",
        "no provider tool calling yet",
    ):
        assert phrase in lowered


def test_tools_smoke_runbook_documents_continuation_smoke_scope() -> None:
    text = TOOLS_RUNBOOK.read_text(encoding="utf-8")
    lowered = text.lower()

    assert "scripts/smoke-tool-continuation.sh" in text
    assert "SMOKE_KEEP_ARTIFACTS=1 scripts/smoke-tool-continuation.sh" in text
    assert "fake local cli brain" in lowered
    assert "no real providers" in lowered
    assert "approve does not execute" in lowered
    assert "duplicate execute" in lowered
    assert "tool_result_continuation" in text


def test_memory_smoke_runbook_documents_manual_harness_scope() -> None:
    text = MEMORY_RUNBOOK.read_text(encoding="utf-8")
    lowered = text.lower()

    assert "scripts/smoke-memory-runtime.sh" in text
    assert "SMOKE_KEEP_ARTIFACTS=1 scripts/smoke-memory-runtime.sh" in text
    assert "manual smoke" in lowered
    assert "real `~/.jarvis`" in lowered
    assert "does not use launchd" in lowered
    assert "does not use voice" in lowered
    assert "does not use workers" in lowered
    assert "does not use tools" in lowered
    assert "does not use the panel" in lowered


def test_memory_smoke_runbook_explains_disable_history_caveat() -> None:
    text = MEMORY_RUNBOOK.read_text(encoding="utf-8").lower()

    assert "recent conversation history" in text
    assert "memory_block_count = 0" in text
    assert "contextbuilder" in text


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
    assert "docs/runbooks/TOOLS_AND_APPROVALS.md" in text
    assert "docs/runbooks/MEMORY_API.md" in text


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
