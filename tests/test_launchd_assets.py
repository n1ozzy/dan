"""F2 launchd lifecycle asset contract tests (LAUNCH_SUPERVISION.md §5, FROZEN).

The build never installs or loads launchd. These tests only pin the shape of
the artifacts a human runs deliberately: one official label, plan printed
before anything happens, uninstall never deletes the database.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

from tests.git_guards import assert_schema_and_migrations_unchanged


ROOT = Path(__file__).resolve().parents[1]
PLIST_EXAMPLE = ROOT / "launchd" / "com.ozzy.jarvisd.plist.example"
WRAPPER = ROOT / "scripts" / "jarvisd"
INSTALL = ROOT / "scripts" / "install-launchd.sh"
UNINSTALL = ROOT / "scripts" / "uninstall-launchd.sh"
RUNBOOK = ROOT / "docs" / "runbooks" / "LAUNCHD.md"

OFFICIAL_LABEL = "com.ozzy.jarvisd"
LEGACY_LABELS = (
    "com.ozzy.jarvis</string>",
    "com.dan.voice-broker",
    "com.dan.xtts-server",
)
FORBIDDEN_SNIPPETS = (
    "/Users/n1_ozzy/Documents/dev/dan",
    "/tmp/dan",
    "afplay",
    "--dangerously-skip-permissions",
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def assert_executable(path: Path) -> None:
    assert path.is_file(), path
    assert os.access(path, os.X_OK), path
    assert stat.S_IMODE(path.stat().st_mode) & stat.S_IXUSR


def test_plist_example_uses_only_the_official_label() -> None:
    text = read(PLIST_EXAMPLE)

    assert f"<string>{OFFICIAL_LABEL}</string>" in text
    for legacy in LEGACY_LABELS:
        assert legacy not in text


def test_plist_example_logs_and_wrapper_live_outside_documents() -> None:
    # ADR-014: launchd cannot read scripts under ~/Documents (TCC trap).
    text = read(PLIST_EXAMPLE)

    assert ".jarvis/logs" in text
    assert ".jarvis/bin" in text
    assert "Documents" not in text


def test_wrapper_script_runs_the_real_daemon() -> None:
    assert_executable(WRAPPER)
    text = read(WRAPPER)

    assert "daemon run" in text
    assert "jarvis.cli" in text
    assert "not implemented" not in text


def test_wrapper_rejects_example_config_as_runtime() -> None:
    text = read(WRAPPER)
    install_text = read(INSTALL)

    assert "config/jarvis.example.toml is not a runtime config" in text
    assert "config/jarvis.example.toml is not a runtime config" in install_text


def test_install_script_prints_plan_and_requires_explicit_yes() -> None:
    assert_executable(INSTALL)
    text = read(INSTALL)

    assert "--yes" in text
    assert "launchctl bootstrap" in text or "launchctl load" in text
    assert "Dry run" in text or "dry run" in text
    assert OFFICIAL_LABEL in text
    # Fail-closed when the agent is already loaded instead of stacking.
    assert "already" in text.lower()
    assert "rm -rf" not in text


def test_uninstall_script_unloads_but_never_deletes_the_database() -> None:
    assert_executable(UNINSTALL)
    text = read(UNINSTALL)

    assert "launchctl bootout" in text or "launchctl unload" in text
    assert "--yes" in text
    assert OFFICIAL_LABEL in text
    assert "rm -rf" not in text
    lowered = text.lower()
    assert "does not delete" in lowered or "never deletes" in lowered
    for line in text.splitlines():
        if line.strip().startswith("rm "):
            assert "jarvis.db" not in line
            assert ".jarvis/logs" not in line


def test_lifecycle_scripts_pass_shebang_syntax_check() -> None:
    checks = (
        ("sh", WRAPPER),
        ("bash", INSTALL),
        ("bash", UNINSTALL),
    )
    for shell, script in checks:
        result = subprocess.run(
            [shell, "-n", str(script)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"{script}: {result.stderr}"


def test_install_is_never_run_by_smokes_or_build_steps() -> None:
    offenders: list[str] = []
    for path in sorted((ROOT / "scripts").glob("smoke-*.sh")):
        if "install-launchd" in read(path):
            offenders.append(path.name)
    assert offenders == []


def test_launchd_assets_avoid_forbidden_legacy_strings() -> None:
    offenders: list[tuple[str, str]] = []
    for path in (PLIST_EXAMPLE, WRAPPER, INSTALL, UNINSTALL, RUNBOOK):
        text = read(path)
        for snippet in FORBIDDEN_SNIPPETS:
            if snippet in text:
                offenders.append((path.name, snippet))
    assert offenders == []


def test_launchd_runbook_documents_the_frozen_rules() -> None:
    text = read(RUNBOOK)
    lowered = text.lower()

    assert OFFICIAL_LABEL in text
    assert "manual" in lowered
    assert "never" in lowered and "automatic" in lowered
    assert "adr-014" in lowered
    assert "tcc" in lowered
    assert "does not delete" in lowered or "never deletes" in lowered
    assert "install-launchd.sh" in text
    assert "uninstall-launchd.sh" in text


def test_schema_and_migrations_are_unchanged() -> None:
    assert_schema_and_migrations_unchanged(ROOT)
