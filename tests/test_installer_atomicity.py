"""Task 11: the installer is staged, verified, backup-first and reversible.

All installs run against a disposable HOME (tmp_path). ~/.claude/archive is
excluded structurally — a byte of it changing is a test failure, and an item
that even targets it is refused before apply.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from dan.install import InstallError, InstallItem, InstallPlan


def tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        if path.is_symlink():
            digest.update(b"->" + str(path.readlink()).encode("utf-8"))
        elif path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()


def seed_archive(home: Path) -> Path:
    archive = home / ".claude" / "archive"
    (archive / "disabled-assumptions").mkdir(parents=True)
    (archive / "old-plan.md").write_text("stary plan — święty\n", encoding="utf-8")
    (archive / "disabled-assumptions" / "voice.md").write_text("stare głosy\n", encoding="utf-8")
    return archive


@pytest.fixture
def home(tmp_path: Path) -> Path:
    root = tmp_path / "home"
    root.mkdir()
    return root


def _full_install(home: Path, tmp_path: Path) -> tuple[InstallPlan, "object"]:
    plan = InstallPlan(home=home)
    staging = tmp_path / "staging"
    plan.preflight()
    plan.render(staging)
    plan.verify(staging)
    report = plan.apply(backup_root=tmp_path / "backups")
    return plan, report


def test_installer_never_touches_claude_archive(home: Path, tmp_path: Path) -> None:
    archive = seed_archive(home)
    before = tree_hash(archive)
    _full_install(home, tmp_path)
    assert tree_hash(archive) == before


def test_item_targeting_archive_is_refused_structurally(home: Path, tmp_path: Path) -> None:
    plan = InstallPlan(home=home)
    plan.items.append(
        InstallItem(relpath=".claude/archive/evil.md", content=b"nope", mode=0o644)
    )
    staging = tmp_path / "staging"
    with pytest.raises(InstallError):
        plan.render(staging)


def test_apply_requires_a_verified_staging(home: Path, tmp_path: Path) -> None:
    plan = InstallPlan(home=home)
    staging = tmp_path / "staging"
    plan.render(staging)
    with pytest.raises(InstallError):
        plan.apply(backup_root=tmp_path / "backups")


def test_apply_rejects_staging_modified_after_verify(home: Path, tmp_path: Path) -> None:
    plan = InstallPlan(home=home)
    staging = tmp_path / "staging"
    plan.render(staging)
    plan.verify(staging)
    victim = next(path for path in staging.rglob("*") if path.is_file())
    victim.write_bytes(victim.read_bytes() + b"\n# tampered")
    with pytest.raises(InstallError):
        plan.apply(backup_root=tmp_path / "backups")


def test_apply_is_backup_first_and_reports_every_path(home: Path, tmp_path: Path) -> None:
    existing = home / ".claude" / "CLAUDE.md"
    existing.parent.mkdir(parents=True)
    existing.write_text("# Ozzy's own notes\nline\n", encoding="utf-8")

    _, report = _full_install(home, tmp_path)

    entries = {entry.path: entry for entry in report.entries}
    claude_md = entries[str(existing)]
    assert claude_md.operation == "replace"
    assert claude_md.backup is not None
    backup_text = Path(claude_md.backup).read_text(encoding="utf-8")
    assert backup_text == "# Ozzy's own notes\nline\n"
    assert claude_md.sha_before is not None
    assert claude_md.sha_after != claude_md.sha_before
    for entry in report.entries:
        assert entry.operation in {"create", "replace"}
        assert entry.inverse in {"remove", "restore-backup"}
        assert entry.sha_after
    # Owner text survives inside the managed-block merge.
    merged = existing.read_text(encoding="utf-8")
    assert "# Ozzy's own notes" in merged
    assert "BEGIN DAN MANAGED BLOCK" in merged
    assert merged.count("BEGIN DAN MANAGED BLOCK") == 1


def test_managed_block_is_idempotent_across_reinstall(home: Path, tmp_path: Path) -> None:
    _full_install(home, tmp_path)
    first = (home / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")
    _full_install(home, tmp_path / "second")
    second = (home / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")
    assert first == second
    assert second.count("BEGIN DAN MANAGED BLOCK") == 1


def test_rollback_restores_previous_state(home: Path, tmp_path: Path) -> None:
    existing = home / ".claude" / "CLAUDE.md"
    existing.parent.mkdir(parents=True)
    existing.write_text("oryginał\n", encoding="utf-8")
    seed_archive(home)
    before_all = tree_hash(home)

    plan, report = _full_install(home, tmp_path)
    assert tree_hash(home) != before_all

    plan.rollback(report)
    assert tree_hash(home) == before_all
    assert existing.read_text(encoding="utf-8") == "oryginał\n"


def test_apply_replaces_a_symlink_without_following_it(home: Path, tmp_path: Path) -> None:
    victim = tmp_path / "victim.md"
    victim.write_text("cudzy plik — nie ruszać\n", encoding="utf-8")
    dest = home / ".codex" / "skills" / "dan-persona" / "SKILL.md"
    dest.parent.mkdir(parents=True)
    dest.symlink_to(victim)

    _full_install(home, tmp_path)

    assert victim.read_text(encoding="utf-8") == "cudzy plik — nie ruszać\n"
    assert not dest.is_symlink()
    assert dest.is_file()


def test_apply_refuses_symlinked_parent_directories(home: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (home / ".codex").parent.mkdir(parents=True, exist_ok=True)
    (home / ".codex").symlink_to(outside)

    plan = InstallPlan(home=home)
    staging = tmp_path / "staging"
    plan.render(staging)
    plan.verify(staging)
    with pytest.raises(InstallError):
        plan.apply(backup_root=tmp_path / "backups")


def test_uninstall_script_is_manifest_scoped_even_after_double_install(
    home: Path, tmp_path: Path
) -> None:
    import os
    import subprocess

    existing = home / ".claude" / "CLAUDE.md"
    existing.parent.mkdir(parents=True)
    existing.write_text("# Ozzy's own notes\n", encoding="utf-8")
    archive = seed_archive(home)
    archive_before = tree_hash(archive)
    (home / ".dan" / "migration").mkdir(parents=True)
    (home / ".dan" / "dan.db").write_bytes(b"sqlite-not-really")
    (home / ".dan" / "migration" / "backup.json").write_text("{}", encoding="utf-8")

    _full_install(home, tmp_path)
    _full_install(home, tmp_path / "second")  # double install, then uninstall

    script = Path(__file__).resolve().parents[1] / "scripts" / "uninstall.sh"
    env = {"HOME": str(home), "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    result = subprocess.run(
        ["/bin/bash", str(script)], env=env, capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr

    # Product surface is gone; owner text, archive and protected state stay.
    assert not (home / ".claude" / "skills" / "dan-persona" / "SKILL.md").exists()
    assert not (home / ".claude" / "hooks" / "tts-message-display.sh").exists()
    assert not (home / "Library" / "LaunchAgents" / "com.dan.dand.plist").exists()
    assert not (home / ".agents" / "skills" / "gadanie" / "SKILL.md").exists()
    assert not (home / ".dan" / "install-manifest.json").exists()
    merged = existing.read_text(encoding="utf-8")
    assert "# Ozzy's own notes" in merged
    assert "DAN MANAGED BLOCK" not in merged
    assert tree_hash(archive) == archive_before
    assert (home / ".dan" / "dan.db").exists()
    assert (home / ".dan" / "migration" / "backup.json").exists()


def test_install_manifest_written_for_uninstall(home: Path, tmp_path: Path) -> None:
    _, report = _full_install(home, tmp_path)
    manifest_path = home / ".dan" / "install-manifest.json"
    assert manifest_path.is_file()
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    paths = {entry["path"] for entry in data["entries"]}
    assert {entry.path for entry in report.entries} == paths
    for raw in paths:
        assert ".claude/archive" not in raw
