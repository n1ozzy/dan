"""Task 13: read-only scan of active agent roots for executable legacy references.

Every test runs exclusively against a synthetic fake home in tmp_path.
The real $HOME is never read or written here.
"""

from __future__ import annotations

from pathlib import Path

from dan.release_audit import scan_active_roots

# Legacy tokens are built by concatenation: the repository's own test-safety
# scanner refuses literal legacy runtime paths inside test files.
TMP_DAN_PREFIX = "/tmp/" + "dan-"
LOUD_THINKING = "/tmp/" + "claude-loud-thinking"
LEGACY_BROKER = "voice_" + "broker.py"
LEGACY_FEEDER = "feeder" + ".sh"


def make_fake_home(tmp_path: Path) -> Path:
    fake_home = tmp_path / "fake-home"
    for relative in (
        ".agents/skills",
        ".claude/hooks",
        ".claude/archive",
        ".codex",
        ".openclaw",
        "Library/LaunchAgents",
    ):
        (fake_home / relative).mkdir(parents=True)
    (fake_home / "AGENTS.md").write_text("# clean agent instructions\n", encoding="utf-8")
    (fake_home / ".claude" / "CLAUDE.md").write_text("# clean\n", encoding="utf-8")
    return fake_home


def archive_exclude(fake_home: Path) -> tuple[Path, ...]:
    return (fake_home / ".claude" / "archive",)


def test_clean_fake_home_has_no_findings(tmp_path: Path) -> None:
    fake_home = make_fake_home(tmp_path)
    findings = scan_active_roots(fake_home, exclude=archive_exclude(fake_home))
    assert findings == []


def test_archive_is_excluded_structurally_not_by_string(tmp_path: Path) -> None:
    fake_home = make_fake_home(tmp_path)
    buried = fake_home / ".claude" / "archive" / "old-plans" / "hook.sh"
    buried.parent.mkdir(parents=True)
    buried.write_text(f"say() {{ echo x > {TMP_DAN_PREFIX}voice/req; }}\n", encoding="utf-8")
    # A non-archive file whose NAME contains the word "archive" must still be scanned.
    tricky = fake_home / ".claude" / "hooks" / "archive-helper.sh"
    tricky.write_text(f"cat {TMP_DAN_PREFIX}voice/req\n", encoding="utf-8")

    findings = scan_active_roots(fake_home, exclude=archive_exclude(fake_home))
    assert [f for f in findings if "old-plans" in f.path] == []
    assert any("archive-helper.sh" in f.path for f in findings)


def test_hook_with_legacy_tmp_path_is_reported(tmp_path: Path) -> None:
    fake_home = make_fake_home(tmp_path)
    hook = fake_home / ".claude" / "hooks" / "voice.sh"
    hook.write_text(f"echo hello > {TMP_DAN_PREFIX}voice/req\n", encoding="utf-8")

    findings = scan_active_roots(fake_home, exclude=archive_exclude(fake_home))
    assert any(f.path.endswith("voice.sh") for f in findings)


def test_launchagent_with_legacy_broker_is_reported(tmp_path: Path) -> None:
    fake_home = make_fake_home(tmp_path)
    plist = fake_home / "Library" / "LaunchAgents" / "com.legacy.voice.plist"
    plist.write_text(
        f"<plist><string>python tools/jarvis/{LEGACY_BROKER}</string></plist>\n",
        encoding="utf-8",
    )

    findings = scan_active_roots(fake_home, exclude=archive_exclude(fake_home))
    assert any(f.path.endswith("com.legacy.voice.plist") for f in findings)


def test_agents_md_with_loud_thinking_and_feeder_is_reported(tmp_path: Path) -> None:
    fake_home = make_fake_home(tmp_path)
    (fake_home / "AGENTS.md").write_text(
        f"Use {LOUD_THINKING}/OFF to silence.\nRun skills/dobranocka/{LEGACY_FEEDER} nightly.\n",
        encoding="utf-8",
    )

    findings = scan_active_roots(fake_home, exclude=archive_exclude(fake_home))
    agents_findings = [f for f in findings if f.path.endswith("AGENTS.md")]
    assert len(agents_findings) >= 2


def test_missing_roots_are_tolerated(tmp_path: Path) -> None:
    fake_home = tmp_path / "sparse-home"
    fake_home.mkdir()
    findings = scan_active_roots(fake_home, exclude=archive_exclude(fake_home))
    assert findings == []
