"""Release 1 source-inventory contract tests."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from jarvis.migration.inventory import (
    InventoryRoots,
    build_inventory,
    check_manifest,
    inspect_path,
    main,
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


def unavailable_runner(
    args: list[str], **_: object
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args, 127, "", "unavailable in fixture")


def git_only_runner(
    args: list[str], **kwargs: object
) -> subprocess.CompletedProcess[str]:
    if args and args[0] == "git":
        return subprocess.run(args, **kwargs)  # type: ignore[arg-type]
    return unavailable_runner(args)


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


def test_manifest_write_refuses_insecure_existing_directory_without_chmod(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "shared"
    directory.mkdir(mode=0o755)
    directory.chmod(0o755)
    destination = directory / "manifest.json"

    with pytest.raises(PermissionError, match="manifest directory mode must be 0700"):
        write_manifest_atomic({"schema_version": 1, "surfaces": {}}, destination)

    assert os.stat(directory).st_mode & 0o777 == 0o755
    assert not destination.exists()


def test_manifest_write_refuses_symlink_directory_without_mutating_target(
    tmp_path: Path,
) -> None:
    real_directory = tmp_path / "real"
    real_directory.mkdir(mode=0o755)
    real_directory.chmod(0o755)
    linked_directory = tmp_path / "linked"
    linked_directory.symlink_to(real_directory, target_is_directory=True)

    with pytest.raises(ValueError, match="manifest directory must not be a symlink"):
        write_manifest_atomic(
            {"schema_version": 1, "surfaces": {}},
            linked_directory / "manifest.json",
        )

    assert os.stat(real_directory).st_mode & 0o777 == 0o755
    assert not (real_directory / "manifest.json").exists()


def test_every_inventory_row_has_a_named_decision(tmp_path: Path) -> None:
    manifest = build_inventory(fixture_roots(tmp_path))

    missing = {
        surface: [row for row in rows if not str(row.get("decision", "")).strip()]
        for surface, rows in manifest["surfaces"].items()
    }

    assert {surface: rows for surface, rows in missing.items() if rows} == {}


def test_missing_canonical_dan_targets_keep_actionable_decisions(tmp_path: Path) -> None:
    roots = fixture_roots(tmp_path)

    manifest = build_inventory(roots, runner=unavailable_runner)
    databases = {row["path"]: row for row in manifest["surfaces"]["databases"]}
    configs = {row["path"]: row for row in manifest["surfaces"]["config_sources"]}

    assert databases[str(roots.home / ".dan/dan.db")]["decision"] == (
        "create-and-verify-through-versioned-migration"
    )
    assert configs[str(roots.home / ".dan/config.toml")]["decision"] == (
        "create-installation-config-in-task5"
    )
    assert configs[str(roots.home / ".dan/owner.toml")]["decision"] == (
        "create-private-owner-config-in-task5"
    )
    assert configs[str(roots.home / ".dan/secrets.env")]["decision"] == (
        "create-private-secrets-config-mode-0600-in-task5"
    )


def test_voice_lab_input_discovery_excludes_git_and_bytecode(tmp_path: Path) -> None:
    roots = fixture_roots(tmp_path)
    donor = roots.home / "Documents/dev/dan"
    live_voice_lab = donor / "audycja/voice-lab"
    git_voice_lab = donor / ".git/refs/heads/agent/voice-lab"
    bytecode_voice_lab = donor / "tests/__pycache__/test_voice_lab.pyc"
    venv_voice_lab = donor / "tools/cb3-venv/share/voice-lab-hidden"
    live_voice_lab.mkdir(parents=True)
    git_voice_lab.parent.mkdir(parents=True)
    bytecode_voice_lab.parent.mkdir(parents=True)
    venv_voice_lab.parent.mkdir(parents=True)
    git_voice_lab.write_text("deadbeef\n", encoding="utf-8")
    bytecode_voice_lab.write_bytes(b"fixture")
    venv_voice_lab.write_text("fixture\n", encoding="utf-8")

    manifest = build_inventory(roots)
    paths = {row["path"] for row in manifest["surfaces"]["input_materials"]}

    assert str(live_voice_lab) in paths
    assert str(git_voice_lab) not in paths
    assert str(bytecode_voice_lab) not in paths
    assert str(venv_voice_lab) not in paths


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


def test_ephemeral_dan_runtime_symlink_gets_runtime_decision(tmp_path: Path) -> None:
    roots = fixture_roots(tmp_path)
    runtime = roots.tmp_root / "dan-atlas-cdp.fixture"
    runtime.mkdir()
    link = runtime / "SingletonSocket"
    link.symlink_to(roots.tmp_root / "socket")

    manifest = build_inventory(roots, runner=unavailable_runner)
    row = next(
        item for item in manifest["surfaces"]["symlinks"] if item["path"] == str(link)
    )

    assert row["decision"] == "observe-ephemeral-link-and-retire-with-runtime-in-task12"


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


def test_existing_output_manifest_is_excluded_from_runtime_inventory(tmp_path: Path) -> None:
    roots = fixture_roots(tmp_path)
    output = roots.home / ".dan/migration/release1-source-manifest.json"
    output.parent.mkdir(parents=True)
    output.write_text('{"old": true}\n', encoding="utf-8")

    result = main(
        [
            "--output",
            str(output),
            "--repo-root",
            str(roots.repo_root),
            "--home",
            str(roots.home),
            "--tmp-root",
            str(roots.tmp_root),
        ]
    )

    assert result == 0
    assert os.stat(output.parent).st_mode & 0o777 == 0o700
    manifest = json.loads(output.read_text(encoding="utf-8"))
    runtime_paths = {row["path"] for row in manifest["surfaces"]["runtime_paths"]}
    assert str(output.absolute()) not in runtime_paths


def test_check_rejects_manifest_that_inventories_itself(tmp_path: Path) -> None:
    destination = tmp_path / "release1-source-manifest.json"
    surfaces = {surface: [] for surface in EXPECTED_SURFACES}
    surfaces["runtime_paths"] = [
        {
            "path": str(destination.absolute()),
            "status": "present",
            "decision": "preserve-private-state-and-migrate-with-backup",
        }
    ]
    write_manifest_atomic({"schema_version": 1, "surfaces": surfaces}, destination)

    _, errors = check_manifest(destination)

    assert "manifest must not inventory its own destination" in errors


def test_dirty_repository_records_content_free_wip_hashes(tmp_path: Path) -> None:
    roots = fixture_roots(tmp_path)
    tracked = roots.repo_root / "tracked.txt"
    untracked = roots.repo_root / "untracked.txt"
    subprocess.run(["git", "init", str(roots.repo_root)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(roots.repo_root), "config", "user.email", "fixture@example.test"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(roots.repo_root), "config", "user.name", "Fixture"],
        check=True,
    )
    subprocess.run(["git", "-C", str(roots.repo_root), "add", "README.md"], check=True)
    subprocess.run(
        ["git", "-C", str(roots.repo_root), "commit", "-m", "fixture"],
        check=True,
        capture_output=True,
    )
    tracked.write_text("tracked wip\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(roots.repo_root), "add", "tracked.txt"], check=True)
    tracked.write_text("tracked wip changed again\n", encoding="utf-8")
    untracked.write_text("untracked wip\n", encoding="utf-8")

    manifest = build_inventory(roots, runner=git_only_runner)
    row = next(
        item
        for item in manifest["surfaces"]["repositories"]
        if item["path"] == str(roots.repo_root)
    )
    metadata = row["metadata"]
    entries = {entry["path"]: entry for entry in metadata["wip_entries"]}

    assert metadata["tracked_diff_sha256"] == hashlib.sha256(
        subprocess.run(
            [
                "git",
                "-C",
                str(roots.repo_root),
                "diff",
                "--binary",
                "--full-index",
                "HEAD",
                "--",
            ],
            check=True,
            capture_output=True,
        ).stdout
    ).hexdigest()
    assert len(metadata["staged_diff_sha256"]) == 64
    assert len(metadata["unstaged_diff_sha256"]) == 64
    assert len(metadata["untracked_tree_sha256"]) == 64
    assert entries["tracked.txt"]["sha256"] == hashlib.sha256(tracked.read_bytes()).hexdigest()
    assert entries["untracked.txt"]["sha256"] == hashlib.sha256(
        untracked.read_bytes()
    ).hexdigest()
    assert "contents" not in json.dumps(metadata)


def test_wip_fingerprint_excludes_superpowers_and_generated_caches(tmp_path: Path) -> None:
    roots = fixture_roots(tmp_path)
    subprocess.run(["git", "init", str(roots.repo_root)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(roots.repo_root), "config", "user.email", "fixture@example.test"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(roots.repo_root), "config", "user.name", "Fixture"],
        check=True,
    )
    subprocess.run(["git", "-C", str(roots.repo_root), "add", "README.md"], check=True)
    subprocess.run(
        ["git", "-C", str(roots.repo_root), "commit", "-m", "fixture"],
        check=True,
        capture_output=True,
    )
    excluded = (
        roots.repo_root / ".superpowers/private.txt",
        roots.repo_root / "pkg/__pycache__/module.pyc",
        roots.repo_root / "tools/cb3-venv/cache.bin",
    )
    included = roots.repo_root / "source.py"
    for path in (*excluded, included):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"fixture:{path.name}".encode())

    manifest = build_inventory(roots, runner=git_only_runner)
    row = next(
        item
        for item in manifest["surfaces"]["repositories"]
        if item["path"] == str(roots.repo_root)
    )
    paths = {entry["path"] for entry in row["metadata"]["wip_entries"]}

    assert paths == {"source.py"}


def test_git_inventory_probes_disable_optional_locks(tmp_path: Path) -> None:
    roots = fixture_roots(tmp_path)
    calls: list[list[str]] = []

    def runner(args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        if args and args[0] == "git":
            calls.append(args)
        return unavailable_runner(args)

    build_inventory(roots, runner=runner)

    assert calls
    assert all(call[1] == "--no-optional-locks" for call in calls)


def test_git_status_probe_failure_is_not_reported_as_clean(tmp_path: Path) -> None:
    roots = fixture_roots(tmp_path)

    def runner(args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        if args[:4] == ["git", "--no-optional-locks", "-C", str(roots.repo_root)]:
            git_args = args[4:]
            if git_args == ["rev-parse", "--show-toplevel"]:
                return subprocess.CompletedProcess(args, 0, f"{roots.repo_root}\n", "")
            if git_args == ["rev-parse", "HEAD"]:
                return subprocess.CompletedProcess(args, 0, f"{'a' * 40}\n", "")
            if git_args == ["branch", "--show-current"]:
                return subprocess.CompletedProcess(args, 0, "main\n", "")
            if git_args and git_args[0] == "status":
                return subprocess.CompletedProcess(args, 1, "", "status failed")
        return unavailable_runner(args)

    manifest = build_inventory(roots, runner=runner)
    row = next(
        item
        for item in manifest["surfaces"]["repositories"]
        if item["path"] == str(roots.repo_root)
    )

    assert row["status"] == "git-status-probe-error"
    assert row["decision"] == "record-probe-failure-and-recheck-at-review-gate"


def test_git_ref_probe_failure_is_recorded_instead_of_silently_omitted(tmp_path: Path) -> None:
    roots = fixture_roots(tmp_path)

    def runner(args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        if args[:4] == ["git", "--no-optional-locks", "-C", str(roots.repo_root)]:
            git_args = args[4:]
            if git_args == ["rev-parse", "--show-toplevel"]:
                return subprocess.CompletedProcess(args, 0, f"{roots.repo_root}\n", "")
            if git_args == ["rev-parse", "--git-dir"]:
                return subprocess.CompletedProcess(args, 0, ".git\n", "")
            if git_args == ["rev-parse", "HEAD"]:
                return subprocess.CompletedProcess(args, 0, f"{'a' * 40}\n", "")
            if git_args == ["branch", "--show-current"]:
                return subprocess.CompletedProcess(args, 0, "main\n", "")
            if git_args and git_args[0] == "status":
                return subprocess.CompletedProcess(args, 0, "", "")
            if git_args and git_args[0] == "diff":
                return subprocess.CompletedProcess(args, 0, "", "")
            if git_args and git_args[0] == "for-each-ref":
                return subprocess.CompletedProcess(args, 1, "", "ref probe failed")
        return unavailable_runner(args)

    manifest = build_inventory(roots, runner=runner)
    row = next(
        item
        for item in manifest["surfaces"]["git_refs"]
        if item.get("repository") == str(roots.repo_root)
    )

    assert row["status"] == "git-ref-probe-error"
    assert row["decision"] == "record-probe-failure-and-recheck-at-review-gate"


def test_production_scan_roots_cover_active_host_roots(tmp_path: Path) -> None:
    roots = fixture_roots(tmp_path)
    production = InventoryRoots.production(roots.repo_root, home=roots.home)

    scan_roots = set(production.active_scan_roots())

    assert {
        roots.home / ".agents",
        roots.home / ".claude",
        roots.home / ".codex",
        roots.home / ".openclaw",
    } <= scan_roots


def test_production_exclusions_are_canonical_and_unique(tmp_path: Path) -> None:
    roots = fixture_roots(tmp_path)
    archive = roots.home / ".claude/archive"

    production = InventoryRoots.production(
        roots.repo_root,
        home=roots.home,
        excludes=(archive, archive),
    )

    assert production.excludes == (archive,)


def test_inventory_includes_active_host_config_sources(tmp_path: Path) -> None:
    roots = fixture_roots(tmp_path)
    paths = (
        roots.home / ".claude/settings.json",
        roots.home / ".claude/settings.local.json",
        roots.home / ".claude/agents/voice-regression.md",
        roots.home / ".claude/plugins/installed_plugins.json",
        roots.home / ".codex/.codex-global-state.json",
        roots.home / ".codex/config.toml",
        roots.home / ".codex/AGENTS.md",
        roots.home / ".codex/memories/MEMORY.md",
        roots.home / ".openclaw/openclaw.json",
        roots.home / ".openclaw/workspace/AGENTS.md",
        roots.home / ".openclaw/workspace/DREAMS.md",
        roots.home / ".openclaw/workspace/MEMORY.md",
        roots.home / ".openclaw/workspace/USER.md",
        roots.home / "Documents/dev/dan/.claude/settings.json",
    )
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n", encoding="utf-8")

    manifest = build_inventory(roots, runner=unavailable_runner)
    config_paths = {row["path"] for row in manifest["surfaces"]["config_sources"]}

    assert {str(path) for path in paths} <= config_paths


def test_inventory_includes_repo_local_skill_trees(tmp_path: Path) -> None:
    roots = fixture_roots(tmp_path)
    skills = (
        roots.home / "Documents/dev/dan/.claude/skills/voice-report/SKILL.md",
        roots.home / "Documents/dev/dan/.agents/skills/voice-report/SKILL.md",
        roots.home / "Documents/dev/dan/skills/trio-live/SKILL.md",
    )
    for path in skills:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n", encoding="utf-8")

    manifest = build_inventory(roots, runner=unavailable_runner)
    skill_paths = {row["path"] for row in manifest["surfaces"]["skills"]}

    assert {str(path) for path in skills} <= skill_paths


def test_inventory_includes_plugin_provided_skill_trees(tmp_path: Path) -> None:
    roots = fixture_roots(tmp_path)
    skills = (
        roots.home / ".codex/plugins/cache/vendor/plugin/1.0.0/skills/tool/SKILL.md",
        roots.home / ".claude/plugins/cache/vendor/plugin/1.0.0/skills/tool/SKILL.md",
    )
    for path in skills:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n", encoding="utf-8")

    manifest = build_inventory(roots, runner=unavailable_runner)
    skill_rows = {row["path"]: row for row in manifest["surfaces"]["skills"]}

    assert {str(path) for path in skills} <= set(skill_rows)
    assert {
        skill_rows[str(path)]["decision"] for path in skills
    } == {"classify-installed-plugin-version-and-migrate-or-disable-in-task11"}


def test_openclaw_live_and_disabled_skills_have_distinct_decisions(
    tmp_path: Path,
) -> None:
    roots = fixture_roots(tmp_path)
    radio = roots.home / ".openclaw/workspace/skills/radio-dan/SKILL.md"
    disabled = roots.home / ".openclaw/workspace/skills/danv2-enhanced/SKILL.md"
    for path in (radio, disabled):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n", encoding="utf-8")

    manifest = build_inventory(roots, runner=unavailable_runner)
    skill_rows = {row["path"]: row for row in manifest["surfaces"]["skills"]}

    assert skill_rows[str(radio)]["decision"] == (
        "replace-live-openclaw-skill-with-thin-dan-adapter-in-task11"
    )
    assert skill_rows[str(disabled)]["decision"] == (
        "retain-disabled-openclaw-skill-and-retire-after-task11-audit"
    )


def test_voice_lab_input_materials_are_hashed_recursively(tmp_path: Path) -> None:
    roots = fixture_roots(tmp_path)
    voice_lab = roots.home / "Documents/dev/dan/audycja/voice-lab"
    manifest_file = voice_lab / "manifest.json"
    sample = voice_lab / "wav/sample.wav"
    manifest_file.parent.mkdir(parents=True)
    sample.parent.mkdir(parents=True)
    manifest_file.write_text('{"fixture": true}\n', encoding="utf-8")
    sample.write_bytes(b"RIFF fixture")

    manifest = build_inventory(roots, runner=unavailable_runner)
    materials = {row["path"]: row for row in manifest["surfaces"]["input_materials"]}

    assert materials[str(manifest_file)]["sha256"] == hashlib.sha256(
        manifest_file.read_bytes()
    ).hexdigest()
    assert materials[str(sample)]["sha256"] == hashlib.sha256(sample.read_bytes()).hexdigest()


def test_desktop_visualizer_is_recorded_as_input_material(tmp_path: Path) -> None:
    roots = fixture_roots(tmp_path)
    visualizer = roots.home / "Desktop/djdan-visualizer.html"
    visualizer.parent.mkdir(parents=True)
    visualizer.write_text("<main>fixture</main>\n", encoding="utf-8")

    manifest = build_inventory(roots, runner=unavailable_runner)
    materials = {row["path"]: row for row in manifest["surfaces"]["input_materials"]}

    assert materials[str(visualizer)]["sha256"] == hashlib.sha256(
        visualizer.read_bytes()
    ).hexdigest()


def test_quarantine_consumer_search_includes_live_processes(tmp_path: Path) -> None:
    roots = fixture_roots(tmp_path)
    quarantine = roots.home / "Documents/dev/dan/_quarantine-continuity-fix-2026-07-08"
    quarantine.mkdir(parents=True)

    def runner(args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        if args == ["ps", "-axo", "pid=,ppid=,command="]:
            command = f"python voice_broker.py --source {quarantine}"
            return subprocess.CompletedProcess(args, 0, f"123 1 {command}\n", "")
        return unavailable_runner(args)

    manifest = build_inventory(roots, runner=runner)
    row = next(
        item
        for item in manifest["surfaces"]["input_materials"]
        if item["path"] == str(quarantine)
    )

    assert "process:123" in row["consumers"]
    assert row["decision"] == "active-source"


def test_inventory_collector_is_not_a_producer_or_quarantine_consumer(
    tmp_path: Path,
) -> None:
    roots = fixture_roots(tmp_path)
    collector = roots.repo_root / "jarvis/migration/inventory.py"
    collector.parent.mkdir(parents=True)
    collector.write_text(
        'SIGNATURE = "dan-voice/req"\n'
        'CANDIDATE = "_quarantine-continuity-fix-2026-07-08"\n',
        encoding="utf-8",
    )
    quarantine = roots.home / "Documents/dev/dan/_quarantine-continuity-fix-2026-07-08"
    quarantine.mkdir(parents=True)

    manifest = build_inventory(roots, runner=unavailable_runner)
    producer_paths = {row["path"] for row in manifest["surfaces"]["producers"]}
    quarantine_row = next(
        row
        for row in manifest["surfaces"]["input_materials"]
        if row["path"] == str(quarantine)
    )

    assert str(collector) not in producer_paths
    assert quarantine_row["consumers"] == ()
    assert quarantine_row["decision"] == "archive/do-not-copy"


def test_process_inventory_excludes_the_running_collector(tmp_path: Path) -> None:
    roots = fixture_roots(tmp_path)
    collector_pid = os.getpid()

    def runner(args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        if args == ["ps", "-axo", "pid=,ppid=,command="]:
            rows = (
                f"{collector_pid} 1 python -m jarvis.migration.inventory --output manifest.json\n"
                "123 1 python voice_broker.py\n"
            )
            return subprocess.CompletedProcess(args, 0, rows, "")
        return unavailable_runner(args)

    manifest = build_inventory(roots, runner=runner)
    process_ids = {row["pid"] for row in manifest["surfaces"]["processes"]}

    assert collector_pid not in process_ids
    assert 123 in process_ids


def test_operational_rows_are_deterministic_for_same_snapshot(tmp_path: Path) -> None:
    roots = fixture_roots(tmp_path)

    def runner(order: tuple[int, int]):
        def run(args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            if args == ["ps", "-axo", "pid=,ppid=,command="]:
                rows = {
                    10: "10 1 python voice_broker.py\n",
                    20: "20 1 node openclaw gateway\n",
                }
                return subprocess.CompletedProcess(args, 0, "".join(rows[key] for key in order), "")
            if args == ["launchctl", "list"]:
                rows = {
                    10: "10\t0\tcom.ozzy.jarvisd\n",
                    20: "20\t0\tai.openclaw.gateway\n",
                }
                return subprocess.CompletedProcess(args, 0, "".join(rows[key] for key in order), "")
            return unavailable_runner(args)

        return run

    first = build_inventory(roots, runner=runner((20, 10)))
    second = build_inventory(roots, runner=runner((10, 20)))

    assert first["surfaces"]["processes"] == second["surfaces"]["processes"]
    assert first["surfaces"]["launchd"] == second["surfaces"]["launchd"]


def test_ref_ledger_accounts_for_divergent_dan_shared_voice_ref() -> None:
    ledger = (
        Path(__file__).resolve().parents[1] / "docs/migration/REF-DECISIONS.md"
    ).read_text(encoding="utf-8")

    assert "refs/remotes/origin/feat/shared-voice-source" in ledger
    assert "34cbe7c746ec96d3344e44fd3fdc7075eb626e65" in ledger
