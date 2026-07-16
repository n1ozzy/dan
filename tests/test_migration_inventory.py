"""Release 1 source-inventory contract tests."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

from jarvis.migration.inventory import (
    InventoryRoots,
    build_inventory,
    inspect_path,
    write_manifest_atomic,
)


EXPECTED_SURFACES = {
    "repositories",
    "git_refs",
    "processes",
    "launchd",
    "databases",
    "voice_assets",
    "config_sources",
    "skills",
    "hooks",
    "symlinks",
    "producers",
    "request_formats",
    "runtime_paths",
    "input_materials",
}


def fixture_roots(tmp_path: Path) -> InventoryRoots:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    runtime = tmp_path / "tmp"
    home.mkdir()
    repo.mkdir()
    runtime.mkdir()
    (repo / "README.md").write_text("private fixture text", encoding="utf-8")
    return InventoryRoots(home=home, repo_root=repo, tmp_root=runtime)


def test_inventory_has_every_release1_surface(tmp_path: Path) -> None:
    manifest = build_inventory(fixture_roots(tmp_path))

    assert set(manifest["surfaces"]) == EXPECTED_SURFACES
    assert manifest["schema_version"] == 1
    assert "contents" not in json.dumps(manifest)


def test_inventory_records_symlink_target_and_sha256(tmp_path: Path) -> None:
    target = tmp_path / "target.sh"
    target.write_text("#!/bin/sh\n", encoding="utf-8")
    link = tmp_path / "link.sh"
    link.symlink_to(target)

    item = inspect_path(link)

    assert item.kind == "symlink"
    assert item.target == str(target)
    assert item.sha256 == hashlib.sha256(target.read_bytes()).hexdigest()


def test_manifest_write_is_atomic_and_owner_only(tmp_path: Path) -> None:
    destination = tmp_path / "migration" / "manifest.json"

    write_manifest_atomic({"schema_version": 1, "surfaces": {}}, destination)

    assert json.loads(destination.read_text(encoding="utf-8"))["schema_version"] == 1
    assert os.stat(destination).st_mode & 0o777 == 0o600
    assert list(destination.parent.glob(f".{destination.name}.*.tmp")) == []


def test_every_inventory_row_has_a_named_decision(tmp_path: Path) -> None:
    manifest = build_inventory(fixture_roots(tmp_path))

    missing = {
        surface: [row for row in rows if not str(row.get("decision", "")).strip()]
        for surface, rows in manifest["surfaces"].items()
    }

    assert {surface: rows for surface, rows in missing.items() if rows} == {}


def test_voice_lab_input_discovery_excludes_git_and_bytecode(tmp_path: Path) -> None:
    roots = fixture_roots(tmp_path)
    donor = roots.home / "Documents/dev/dan"
    live_voice_lab = donor / "audycja/voice-lab"
    git_voice_lab = donor / ".git/refs/heads/agent/voice-lab"
    bytecode_voice_lab = donor / "tests/__pycache__/test_voice_lab.pyc"
    live_voice_lab.mkdir(parents=True)
    git_voice_lab.parent.mkdir(parents=True)
    bytecode_voice_lab.parent.mkdir(parents=True)
    git_voice_lab.write_text("deadbeef\n", encoding="utf-8")
    bytecode_voice_lab.write_bytes(b"fixture")

    manifest = build_inventory(roots)
    paths = {row["path"] for row in manifest["surfaces"]["input_materials"]}

    assert str(live_voice_lab) in paths
    assert str(git_voice_lab) not in paths
    assert str(bytecode_voice_lab) not in paths


def test_launchd_inventory_excludes_unrelated_apple_voice_agents(tmp_path: Path) -> None:
    def runner(args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        if args == ["launchctl", "list"]:
            return subprocess.CompletedProcess(
                args,
                0,
                "-\t0\tcom.apple.voicebankingd\n123\t0\tcom.ozzy.jarvisd\n",
                "",
            )
        return subprocess.CompletedProcess(args, 1, "", "")

    manifest = build_inventory(fixture_roots(tmp_path), runner=runner)
    labels = {row.get("label") for row in manifest["surfaces"]["launchd"]}

    assert "com.ozzy.jarvisd" in labels
    assert "com.apple.voicebankingd" not in labels


def test_voice_asset_symlink_gets_asset_decision_not_adapter_decision(tmp_path: Path) -> None:
    roots = fixture_roots(tmp_path)
    target = roots.home / ".cache/model.bin"
    link = roots.home / "Documents/dev/dan/tools/jarvis/chatterbox/model.bin"
    target.parent.mkdir(parents=True)
    link.parent.mkdir(parents=True)
    target.write_bytes(b"model")
    link.symlink_to(target)

    manifest = build_inventory(roots)
    row = next(item for item in manifest["surfaces"]["symlinks"] if item["path"] == str(link))

    assert row["decision"] == "classify-license-and-version-or-fetch-in-task6"


def test_unborn_git_repository_has_no_fake_head(tmp_path: Path) -> None:
    roots = fixture_roots(tmp_path)
    subprocess.run(["git", "init", str(roots.repo_root)], check=True, capture_output=True)

    manifest = build_inventory(roots)
    row = next(
        item
        for item in manifest["surfaces"]["repositories"]
        if item["path"] == str(roots.repo_root)
    )

    assert row["metadata"]["head"] is None
