"""Adversarial FIX FIRST regressions for the Task 1 inventory."""

from __future__ import annotations

import copy
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

import jarvis.migration.inventory as inventory
from jarvis.migration.inventory import (
    InventoryRoots,
    build_inventory,
    check_manifest,
    inspect_path,
    validate_manifest,
    write_manifest_atomic,
)


SURFACES = (
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
)


def fixture_roots(tmp_path: Path) -> InventoryRoots:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    runtime = tmp_path / "tmp"
    home.mkdir()
    repo.mkdir()
    runtime.mkdir()
    return InventoryRoots(home=home, repo_root=repo, tmp_root=runtime)


def quiet_runner(
    args: list[str], **_: object
) -> subprocess.CompletedProcess[str]:
    if args == ["ps", "-axo", "pid=,ppid=,command="]:
        return subprocess.CompletedProcess(args, 0, "", "")
    if args == ["launchctl", "list"]:
        return subprocess.CompletedProcess(args, 0, "", "")
    return subprocess.CompletedProcess(args, 1, "", "fixture probe unavailable")


def producer_rows(manifest: dict[str, object]) -> dict[str, dict[str, object]]:
    surfaces = manifest["surfaces"]
    assert isinstance(surfaces, dict)
    rows = surfaces["producers"]
    assert isinstance(rows, list)
    return {str(row["path"]): row for row in rows if "path" in row}


def manifest_shell() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "generated_at": "2026-07-17T00:00:00+00:00",
        "selected_base": {
            "repository": "/fixture/repo",
            "ref": "main",
            "head": "a" * 40,
            "head_state": "resolved",
            "required": True,
        },
        "roots": {
            "home": "/fixture/home",
            "repo_root": "/fixture/repo",
            "tmp_root": "/fixture/tmp",
            "excluded": [],
            "production": [],
        },
        "surfaces": {name: [] for name in SURFACES},
    }


def process_row(**extra: object) -> dict[str, object]:
    return {
        "kind": "process",
        "pid": 123,
        "ppid": 1,
        "role": "legacy-broker",
        "executable": "python3",
        "runtime_signature": "b" * 64,
        "status": "running",
        "decision": "observe-only-in-task1-stop-only-during-journaled-cutover",
        **extra,
    }


def structural_carrier_manifest() -> dict[str, Any]:
    manifest = manifest_shell()
    manifest["surfaces"]["repositories"] = [
        {
            "path": "/fixture/repo",
            "kind": "directory",
            "status": "dirty",
            "required": False,
            "metadata": {
                "branch": "main",
                "head": "a" * 40,
                "head_state": "resolved",
                "toplevel": "/fixture/repo",
                "probe": "git status --porcelain=v1 -z",
                "returncode": 0,
                "dirty_entry_count": 1,
                "wip_entries": [
                    {
                        "status": "??",
                        "path_status": "present",
                        "path": "voice.sh",
                        "kind": "file",
                        "sha256": "b" * 64,
                        "original_path": "old-voice.sh",
                        "target": "/fixture/repo/voice.sh",
                        "error": {
                            "type": "PermissionError",
                            "operation": "git-wip-inspect",
                            "resolved": True,
                        },
                        "symlink": {
                            "raw_target": "target.txt",
                            "normalized_target": "/fixture/repo/target.txt",
                            "target_state": "existing",
                            "target_kind": "file",
                            "target_is_absolute": False,
                            "inside_allowed_roots": True,
                            "scope_decision": "hash-allowed-regular-target",
                            "target_size_bytes": 10,
                        },
                    }
                ],
                "tracked_diff_sha256": "c" * 64,
                "tracked_diff_basis": "HEAD",
                "staged_diff_sha256": "d" * 64,
                "unstaged_diff_sha256": "e" * 64,
                "untracked_tree_sha256": "f" * 64,
            },
            "decision": "use-as-release1-integration-worktree",
        }
    ]
    manifest["surfaces"]["git_refs"] = [
        {
            "repository": "/fixture/repo",
            "ref": "refs/heads/main",
            "head": "a" * 40,
            "upstream": "origin/main",
            "chosen_base": "b" * 40,
            "unreachable_from_base": [],
            "decision": "retain-ref-unchanged-and-apply-ref-decision-ledger",
        }
    ]
    manifest["surfaces"]["processes"] = [
        process_row(
            probe="ps",
            error={
                "type": "NonZeroExit",
                "operation": "ps",
                "resolved": True,
            },
        )
    ]
    manifest["surfaces"]["launchd"] = [
        {
            "kind": "launchd",
            "pid": 123,
            "last_exit_status": 0,
            "label": "com.dan.dand",
            "status": "loaded",
            "decision": "replace-or-disable-during-task11-and-cutover",
        }
    ]
    manifest["surfaces"]["databases"] = [
        {
            "path": "/fixture/private.db",
            "kind": "database",
            "status": "present",
            "required": False,
            "user_version": 1,
            "schema_version": 2,
            "journal_mode": "wal",
            "tables": ["memories"],
            "record_counts": {"memories": 1},
            "decision": "backup-and-import-with-lineage-in-task3",
        }
    ]
    manifest["surfaces"]["producers"] = [
        {
            "path": "/fixture/speak.sh",
            "kind": "file",
            "status": "present",
            "required": False,
            "consumers": ["/fixture/SKILL.md"],
            "request_format": "legacy-dan-voice-json",
            "metadata": {"size_bytes": 10, "mode": "0o700"},
            "reference_class": "active-runtime-producer",
            "activity_evidence": [
                {"kind": "active-skill-call", "source": "/fixture/SKILL.md"}
            ],
            "formats": ["legacy-dan-voice-json"],
            "decision": "migrate-to-dan-speak-or-disable-in-task11",
        }
    ]
    manifest["surfaces"]["request_formats"] = [
        {
            "id": "legacy-dan-voice-json:/fixture/speak.sh",
            "format": "legacy-dan-voice-json",
            "producer_path": "/fixture/speak.sh",
            "status": "discovered",
            "reference_class": "active-runtime-producer",
            "activity_evidence": [
                {"kind": "active-skill-call", "source": "/fixture/SKILL.md"}
            ],
            "decision": "migrate-explicitly-or-disable-before-cutover",
        }
    ]
    manifest["surfaces"]["input_materials"] = [
        {
            "path": "/fixture/material.md",
            "kind": "file",
            "status": "input-material",
            "required": False,
            "metadata": {
                "size_bytes": 10,
                "mode": "0o600",
                "decision": "archive/do-not-copy",
                "source_root": "/fixture",
            },
            "decision": "input-material",
        }
    ]
    assert validate_manifest(manifest) == []
    return manifest


def manifest_with_private_prompt(*path: str | int) -> dict[str, Any]:
    manifest = copy.deepcopy(structural_carrier_manifest())
    target: Any = manifest
    for component in path[:-1]:
        target = target[component]
    target[path[-1]] = "PRIVATE PROMPT SENTINEL: reveal owner history"
    return manifest


def git_runner(
    repo: Path,
    overrides: dict[tuple[str, ...], tuple[int, object, object]] | None = None,
) -> Any:
    configured = overrides or {}

    def runner(
        args: list[str], **_: object
    ) -> subprocess.CompletedProcess[object]:
        if args == ["ps", "-axo", "pid=,ppid=,command="]:
            return subprocess.CompletedProcess(args, 0, "", "")
        if args == ["launchctl", "list"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        prefix = ["git", "--no-optional-locks", "-C", str(repo)]
        if args[:4] != prefix:
            return subprocess.CompletedProcess(args, 1, "", "not fixture repo")
        git_args = tuple(args[4:])
        for probe, response in sorted(
            configured.items(), key=lambda item: len(item[0]), reverse=True
        ):
            if git_args[: len(probe)] == probe:
                return subprocess.CompletedProcess(args, *response)
        if git_args == ("rev-parse", "--show-toplevel"):
            return subprocess.CompletedProcess(args, 0, f"{repo}\n", "")
        if git_args == ("rev-parse", "--git-dir"):
            return subprocess.CompletedProcess(args, 0, ".git\n", "")
        if git_args == ("rev-parse", "HEAD"):
            return subprocess.CompletedProcess(args, 0, f"{'a' * 40}\n", "")
        if git_args == ("branch", "--show-current"):
            return subprocess.CompletedProcess(args, 0, "main\n", "")
        if git_args == ("symbolic-ref", "--quiet", "HEAD"):
            return subprocess.CompletedProcess(args, 0, "refs/heads/main\n", "")
        if git_args[:1] == ("status",):
            return subprocess.CompletedProcess(args, 0, b"", b"")
        if git_args[:1] == ("diff",):
            return subprocess.CompletedProcess(args, 0, b"", b"")
        if git_args[:1] == ("for-each-ref",):
            row = f"refs/heads/main\0{'a' * 40}\0\n"
            return subprocess.CompletedProcess(args, 0, row, "")
        if git_args[:2] == ("rev-parse", "--verify"):
            return subprocess.CompletedProcess(args, 0, f"{'a' * 40}\n", "")
        if git_args[:1] == ("rev-list",):
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, 1, "", "unknown git fixture probe")

    return runner


def test_claude_project_memory_uses_real_slug_for_underscored_username() -> None:
    roots = InventoryRoots(
        home=Path("/Users/n1_ozzy"),
        repo_root=Path("/Users/n1_ozzy/Documents/dev/jarvis"),
    )

    assert Path(
        "/Users/n1_ozzy/.claude/projects/"
        "-Users-n1-ozzy-Documents-dev-jarvis/memory"
    ) in roots.claude_project_memory_roots()


def test_skill_activity_never_spreads_by_skill_md_basename(tmp_path: Path) -> None:
    roots = fixture_roots(tmp_path)
    invoked = roots.home / ".agents/skills/invoked/SKILL.md"
    untouched = roots.home / ".agents/skills/untouched/SKILL.md"
    caller = roots.home / ".claude/hooks/call-skill.sh"
    for skill in (invoked, untouched):
        skill.parent.mkdir(parents=True)
        skill.write_text("# fixture voice_broker.py reference\n", encoding="utf-8")
    caller.parent.mkdir(parents=True)
    caller.write_text(f"python {invoked}\n", encoding="utf-8")
    caller.chmod(0o700)

    rows = producer_rows(build_inventory(roots, runner=quiet_runner))

    assert rows[str(invoked)]["reference_class"] == "active-runtime-producer"
    assert rows[str(untouched)]["reference_class"] != "active-runtime-producer"
    assert rows[str(untouched)]["activity_evidence"] == [
        {"kind": "active-skill", "source": str(untouched)}
    ]


def test_active_skill_local_basename_call_activates_only_its_neighbor(
    tmp_path: Path,
) -> None:
    roots = fixture_roots(tmp_path)
    invoked_skill = roots.home / ".agents/skills/invoked/SKILL.md"
    invoked_producer = invoked_skill.parent / "speak.sh"
    untouched_skill = roots.home / ".agents/skills/untouched/SKILL.md"
    untouched_producer = untouched_skill.parent / "speak.sh"
    for skill, producer in (
        (invoked_skill, invoked_producer),
        (untouched_skill, untouched_producer),
    ):
        skill.parent.mkdir(parents=True)
        skill.write_text("# fixture skill\n", encoding="utf-8")
        producer.write_text("#!/bin/sh\n# dan-voice/req\n", encoding="utf-8")
        producer.chmod(0o700)
    invoked_skill.write_text("Run: bash speak.sh\n", encoding="utf-8")

    rows = producer_rows(build_inventory(roots, runner=quiet_runner))

    assert rows[str(invoked_producer)]["reference_class"] == "active-runtime-producer"
    assert rows[str(invoked_producer)]["activity_evidence"] == [
        {"kind": "active-skill-call", "source": str(invoked_skill)}
    ]
    assert rows[str(untouched_producer)]["reference_class"] == (
        "unproven-runtime-reference"
    )
    assert rows[str(untouched_producer)]["activity_evidence"] == []


def test_inactive_plugin_cache_skill_cannot_activate_its_sibling_producer(
    tmp_path: Path,
) -> None:
    roots = fixture_roots(tmp_path)
    active_skill = roots.home / ".agents/skills/voice/SKILL.md"
    active_producer = active_skill.parent / "speak.sh"
    cached_skill = (
        roots.home
        / ".codex/plugins/cache/vendor/plugin/1.0.0/skills/voice/SKILL.md"
    )
    cached_producer = cached_skill.parent / "speak.sh"
    for skill, producer in (
        (active_skill, active_producer),
        (cached_skill, cached_producer),
    ):
        skill.parent.mkdir(parents=True)
        skill.write_text("Run: bash speak.sh\n", encoding="utf-8")
        producer.write_text("#!/bin/sh\n# dan-voice/req\n", encoding="utf-8")
        producer.chmod(0o700)

    rows = producer_rows(build_inventory(roots, runner=quiet_runner))

    assert rows[str(active_producer)]["reference_class"] == "active-runtime-producer"
    assert rows[str(active_producer)]["activity_evidence"] == [
        {"kind": "active-skill-call", "source": str(active_skill)}
    ]
    assert rows[str(cached_producer)]["reference_class"] == (
        "unproven-runtime-reference"
    )
    assert rows[str(cached_producer)]["activity_evidence"] == []


def test_malformed_git_porcelain_is_never_reported_clean(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    runner = git_runner(repo, {("status",): (0, b"??", b"")})

    row = inventory._repository_record(runner, repo, required=True)

    assert row["status"] == "git-status-probe-error"
    assert row["error"]["type"] == "MalformedGitStatusRecord"


def test_disappearing_wip_path_is_an_unresolved_git_error(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    runner = git_runner(repo, {("status",): (0, b"?? disappearing.txt\0", b"")})

    row = inventory._repository_record(runner, repo, required=True)
    entry = row["metadata"]["wip_entries"][0]

    assert row["status"] == "git-wip-inspection-error"
    assert entry["path_status"] == "path-error"
    assert entry["error"]["type"] == "WipPathMissing"

    deleted_runner = git_runner(repo, {("status",): (0, b" D deleted.txt\0", b"")})
    deleted_row = inventory._repository_record(deleted_runner, repo, required=True)
    deleted_entry = deleted_row["metadata"]["wip_entries"][0]

    assert deleted_row["status"] == "dirty"
    assert deleted_entry["path_status"] == "deleted"
    assert "error" not in deleted_entry


def test_wip_symlink_keeps_full_inspect_path_evidence(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "target.txt"
    target.write_text("fixture\n", encoding="utf-8")
    link = repo / "link.txt"
    link.symlink_to(target.name)
    runner = git_runner(repo, {("status",): (0, b"?? link.txt\0", b"")})

    row = inventory._repository_record(runner, repo, required=True)
    entry = row["metadata"]["wip_entries"][0]

    assert entry["path_status"] == "present"
    assert entry["symlink"]["raw_target"] == "target.txt"
    assert entry["symlink"]["normalized_target"] == str(target)


def test_undecodable_git_head_is_a_required_probe_error(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    runner = git_runner(repo, {("rev-parse", "HEAD"): (0, b"\xff\n", b"")})

    row = inventory._repository_record(runner, repo, required=True)

    assert row["status"] == "git-head-probe-error"
    assert row["error"]["type"] == "UnicodeDecodeError"
    assert row["metadata"]["head"] is None


def test_nonzero_git_head_is_not_mislabeled_unborn(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    runner = git_runner(
        repo,
        {
            ("rev-parse", "HEAD"): (128, b"", b"fatal"),
            ("symbolic-ref", "--quiet", "HEAD"): (1, b"", b"fatal"),
        },
    )

    row = inventory._repository_record(runner, repo, required=True)

    assert row["status"] == "git-head-probe-error"
    assert row["error"]["type"] == "NonZeroExit"
    assert row["metadata"]["head_state"] == "probe-error"


def test_malformed_git_head_sha_is_a_required_probe_error(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    runner = git_runner(
        repo,
        {("rev-parse", "HEAD"): (0, b"NOT_A_SHA\n", b"")},
    )

    row = inventory._repository_record(runner, repo, required=True)

    assert row["status"] == "git-head-probe-error"
    assert row["error"]["type"] == "MalformedGitObjectId"


def test_git_dir_probe_failure_creates_a_ref_error_row(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    runner = git_runner(
        repo,
        {("rev-parse", "--git-dir"): (2, b"", b"broken git metadata")},
    )

    rows = inventory._git_ref_records(runner, (repo,), "main")

    assert len(rows) == 1
    assert rows[0]["status"] == "git-ref-probe-error"
    assert rows[0]["probe"] == "git rev-parse --git-dir"


def test_undecodable_git_diff_cannot_produce_a_clean_repository(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    runner = git_runner(
        repo,
        {
            (
                "diff",
                "--cached",
                "--binary",
                "--full-index",
                "--no-ext-diff",
                "--no-textconv",
                "HEAD",
                "--",
            ): (0, b"\xff", b""),
        },
    )

    row = inventory._repository_record(runner, repo, required=True)

    assert row["status"] == "git-diff-probe-error"
    assert row["error"]["type"] == "UnicodeDecodeError"


@pytest.mark.parametrize(
    "private_relative_path",
    (
        ".claude/projects/-fixture/session-private.jsonl",
        ".claude/archive/private.md",
        ".codex/logs/private.log",
    ),
)
def test_symlink_cannot_hash_private_history_archive_or_log(
    tmp_path: Path,
    private_relative_path: str,
) -> None:
    roots = fixture_roots(tmp_path)
    target = roots.home / private_relative_path
    target.parent.mkdir(parents=True)
    target.write_text("private fixture\n", encoding="utf-8")
    link = roots.repo_root / "private-link"
    link.symlink_to(target)

    item = inspect_path(
        link,
        allowed_roots=roots.allowed_roots(),
        excluded_roots=roots.excludes,
    )

    assert item.sha256 is None
    assert item.symlink["inside_allowed_roots"] is False
    assert item.symlink["scope_decision"] == "reject-outside-allowed-roots"


def test_tmp_symlink_scope_allows_named_dan_runtime_but_rejects_foreign_file(
    tmp_path: Path,
) -> None:
    roots = fixture_roots(tmp_path)
    dan_target = roots.tmp_root / "dan-voice/state.json"
    foreign_target = roots.tmp_root / "unrelated-private.txt"
    for target in (dan_target, foreign_target):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("fixture\n", encoding="utf-8")
    dan_link = roots.repo_root / "dan-runtime-link"
    foreign_link = roots.repo_root / "foreign-runtime-link"
    dan_link.symlink_to(dan_target)
    foreign_link.symlink_to(foreign_target)

    dan_item = inspect_path(dan_link, allowed_roots=roots.allowed_roots())
    foreign_item = inspect_path(foreign_link, allowed_roots=roots.allowed_roots())

    assert dan_item.sha256 is not None
    assert foreign_item.sha256 is None
    assert foreign_item.symlink["inside_allowed_roots"] is False


def test_symlink_hash_stops_at_limit_when_target_grows_during_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    target = allowed / "growing.txt"
    target.write_bytes(b"1234")
    link = allowed / "link.txt"
    link.symlink_to(target.name)
    real_read = os.read
    bytes_returned = 0
    grew = False

    def growing_read(descriptor: int, size: int) -> bytes:
        nonlocal bytes_returned, grew
        if not grew:
            with target.open("ab") as handle:
                handle.write(b"5678")
            grew = True
        chunk = real_read(descriptor, size)
        bytes_returned += len(chunk)
        return chunk

    monkeypatch.setattr(inventory.os, "read", growing_read)

    item = inspect_path(
        link,
        allowed_roots=(allowed,),
        max_symlink_target_bytes=4,
    )

    assert bytes_returned <= 5
    assert item.sha256 is None
    assert item.symlink["scope_decision"] == "target-too-large-during-read"


def test_manifest_rejects_malformed_git_and_sha256_digests() -> None:
    manifest = manifest_shell()
    manifest["selected_base"]["head"] = "NOT_A_SHA"
    manifest["surfaces"]["git_refs"] = [
        {
            "repository": "/fixture/repo",
            "ref": "refs/heads/main",
            "head": "NOT_A_SHA",
            "upstream": None,
            "chosen_base": "a" * 40,
            "unreachable_from_base": ["still-not-a-sha"],
            "decision": "retain-selected-integration-line",
        }
    ]
    manifest["surfaces"]["processes"] = [process_row(runtime_signature="short")]
    manifest["surfaces"]["voice_assets"] = [
        {
            "path": "/fixture/voice.json",
            "kind": "file",
            "sha256": "short",
            "status": "present",
            "required": False,
            "decision": "reconcile-license-hash-and-version-in-task6",
        }
    ]

    errors = validate_manifest(manifest)

    assert any("selected_base.head must be a Git SHA-1 or SHA-256" in error for error in errors)
    assert any("surfaces.git_refs[0].head must be a Git SHA-1 or SHA-256" in error for error in errors)
    assert any("unreachable_from_base[0] must be a Git SHA-1 or SHA-256" in error for error in errors)
    assert any("runtime_signature must be SHA-256" in error for error in errors)
    assert any("voice_assets[0].sha256 must be SHA-256" in error for error in errors)


def test_manifest_accepts_structural_sha256_git_object_id() -> None:
    manifest = manifest_shell()
    manifest["selected_base"]["head"] = "a" * 64

    assert validate_manifest(manifest) == []


def test_launchd_pid_and_exit_status_must_be_integers() -> None:
    manifest = manifest_shell()
    manifest["surfaces"]["launchd"] = [
        {
            "kind": "launchd",
            "pid": "123",
            "last_exit_status": "0",
            "label": "com.dan.dand",
            "status": "loaded",
            "decision": "replace-with-single-com-dan-dand-in-task11",
        },
        {
            "kind": "launchd",
            "pid": -1,
            "last_exit_status": 0,
            "label": "com.dan.jarvisd",
            "status": "loaded",
            "decision": "replace-with-single-com-dan-dand-in-task11",
        },
    ]
    manifest["surfaces"]["processes"] = [process_row(pid=0, ppid=-1)]

    errors = validate_manifest(manifest)

    assert any("launchd[0].pid must be an integer or null" in error for error in errors)
    assert any("last_exit_status must be an integer or null" in error for error in errors)
    assert any("launchd[1].pid must be positive when present" in error for error in errors)
    assert any("processes[0].pid must be positive" in error for error in errors)
    assert any("processes[0].ppid must be non-negative" in error for error in errors)


def test_manifest_rejects_controls_unknown_enums_and_non_normalized_paths() -> None:
    manifest = manifest_shell()
    manifest["surfaces"]["processes"] = [
        process_row(
            executable="python3\nprivate",
            status="invented-state",
        )
    ]
    manifest["surfaces"]["voice_assets"] = [
        {
            "path": "/fixture/assets/../private.json",
            "kind": "made-up-kind",
            "sha256": "c" * 64,
            "status": "present",
            "required": False,
            "decision": "reconcile-license-hash-and-version-in-task6",
        }
    ]

    errors = validate_manifest(manifest)

    assert any("contains control characters" in error for error in errors)
    assert any("processes[0].status has unknown enum value" in error for error in errors)
    assert any("voice_assets[0].kind has unknown enum value" in error for error in errors)
    assert any("voice_assets[0].path must be an absolute normalized path" in error for error in errors)


def test_manifest_blocks_high_confidence_secret_in_allowed_string_field() -> None:
    synthetic_secret = "gh" + "p_" + "A" * 36
    manifest = manifest_shell()
    manifest["surfaces"]["processes"] = [
        process_row(executable=synthetic_secret)
    ]

    errors = validate_manifest(manifest)

    assert any("contains a high-confidence secret" in error for error in errors)


def test_secret_validation_does_not_reject_arbitrary_safe_strings() -> None:
    manifest = manifest_shell()
    manifest["surfaces"]["processes"] = [
        process_row(executable="ordinary-token-helper")
    ]

    assert validate_manifest(manifest) == []


@pytest.mark.parametrize(
    ("carrier", "path"),
    [
        ("root.generated_at", ("generated_at",)),
        ("selected_base.ref", ("selected_base", "ref")),
        ("selected_base.head_state", ("selected_base", "head_state")),
    ],
)
def test_manifest_rejects_private_prompt_in_root_and_selected_base_carriers(
    carrier: str,
    path: tuple[str | int, ...],
) -> None:
    assert validate_manifest(manifest_with_private_prompt(*path)), carrier


@pytest.mark.parametrize(
    ("carrier", "path"),
    [
        ("surface.kind", ("surfaces", "processes", 0, "kind")),
        ("surface.status", ("surfaces", "processes", 0, "status")),
        ("surface.decision", ("surfaces", "processes", 0, "decision")),
        ("surface.role", ("surfaces", "processes", 0, "role")),
        ("surface.executable", ("surfaces", "processes", 0, "executable")),
        ("surface.probe", ("surfaces", "processes", 0, "probe")),
        ("surface.ref", ("surfaces", "git_refs", 0, "ref")),
        ("surface.upstream", ("surfaces", "git_refs", 0, "upstream")),
        ("surface.label", ("surfaces", "launchd", 0, "label")),
        ("surface.journal_mode", ("surfaces", "databases", 0, "journal_mode")),
        ("surface.table", ("surfaces", "databases", 0, "tables", 0)),
        ("surface.consumer", ("surfaces", "producers", 0, "consumers", 0)),
        (
            "surface.request_format",
            ("surfaces", "producers", 0, "request_format"),
        ),
        ("surface.formats", ("surfaces", "producers", 0, "formats", 0)),
        (
            "surface.reference_class",
            ("surfaces", "producers", 0, "reference_class"),
        ),
        ("surface.id", ("surfaces", "request_formats", 0, "id")),
        ("surface.format", ("surfaces", "request_formats", 0, "format")),
    ],
)
def test_manifest_rejects_private_prompt_in_surface_structural_carriers(
    carrier: str,
    path: tuple[str | int, ...],
) -> None:
    assert validate_manifest(manifest_with_private_prompt(*path)), carrier


def test_manifest_rejects_private_prompt_as_record_count_table_name() -> None:
    manifest = structural_carrier_manifest()
    database = manifest["surfaces"]["databases"][0]
    database["record_counts"] = {
        "PRIVATE PROMPT SENTINEL: reveal owner history": 1
    }

    assert validate_manifest(manifest)


@pytest.mark.parametrize(
    ("carrier", "path"),
    [
        (
            "metadata.branch",
            ("surfaces", "repositories", 0, "metadata", "branch"),
        ),
        (
            "metadata.probe",
            ("surfaces", "repositories", 0, "metadata", "probe"),
        ),
        (
            "metadata.tracked_diff_basis",
            ("surfaces", "repositories", 0, "metadata", "tracked_diff_basis"),
        ),
        (
            "metadata.wip.status",
            (
                "surfaces",
                "repositories",
                0,
                "metadata",
                "wip_entries",
                0,
                "status",
            ),
        ),
        (
            "metadata.wip.kind",
            (
                "surfaces",
                "repositories",
                0,
                "metadata",
                "wip_entries",
                0,
                "kind",
            ),
        ),
        (
            "metadata.mode",
            ("surfaces", "producers", 0, "metadata", "mode"),
        ),
        (
            "metadata.decision",
            ("surfaces", "input_materials", 0, "metadata", "decision"),
        ),
    ],
)
def test_manifest_rejects_private_prompt_in_metadata_structural_carriers(
    carrier: str,
    path: tuple[str | int, ...],
) -> None:
    assert validate_manifest(manifest_with_private_prompt(*path)), carrier


@pytest.mark.parametrize(
    ("carrier", "path"),
    [
        ("error.type", ("surfaces", "processes", 0, "error", "type")),
        (
            "error.operation",
            ("surfaces", "processes", 0, "error", "operation"),
        ),
        (
            "activity.kind",
            ("surfaces", "producers", 0, "activity_evidence", 0, "kind"),
        ),
        (
            "activity.source",
            ("surfaces", "producers", 0, "activity_evidence", 0, "source"),
        ),
    ],
)
def test_manifest_rejects_private_prompt_in_error_and_activity_carriers(
    carrier: str,
    path: tuple[str | int, ...],
) -> None:
    assert validate_manifest(manifest_with_private_prompt(*path)), carrier


@pytest.mark.parametrize(
    ("carrier", "path", "prompt_shaped_value"),
    [
        (
            "surface.decision.kebab",
            ("surfaces", "processes", 0, "decision"),
            "private-prompt-reveal-owner-history",
        ),
        (
            "metadata.decision.kebab",
            ("surfaces", "input_materials", 0, "metadata", "decision"),
            "private-prompt-reveal-owner-history",
        ),
        (
            "error.type.pascal",
            ("surfaces", "processes", 0, "error", "type"),
            "PrivatePromptSentinel",
        ),
        (
            "error.operation.kebab",
            ("surfaces", "processes", 0, "error", "operation"),
            "reveal-owner-history",
        ),
        (
            "wip.error.type.pascal",
            (
                "surfaces",
                "repositories",
                0,
                "metadata",
                "wip_entries",
                0,
                "error",
                "type",
            ),
            "PrivatePromptSentinel",
        ),
        (
            "wip.error.operation.kebab",
            (
                "surfaces",
                "repositories",
                0,
                "metadata",
                "wip_entries",
                0,
                "error",
                "operation",
            ),
            "reveal-owner-history",
        ),
    ],
)
def test_manifest_rejects_prompt_shaped_values_that_fit_former_slug_regexes(
    carrier: str,
    path: tuple[str | int, ...],
    prompt_shaped_value: str,
) -> None:
    manifest = copy.deepcopy(structural_carrier_manifest())
    target: Any = manifest
    for component in path[:-1]:
        target = target[component]
    target[path[-1]] = prompt_shaped_value

    assert validate_manifest(manifest), carrier


@pytest.mark.parametrize(
    ("carrier", "path"),
    [
        (
            "wip.path",
            ("surfaces", "repositories", 0, "metadata", "wip_entries", 0, "path"),
        ),
        (
            "wip.original_path",
            (
                "surfaces",
                "repositories",
                0,
                "metadata",
                "wip_entries",
                0,
                "original_path",
            ),
        ),
        (
            "wip.target",
            ("surfaces", "repositories", 0, "metadata", "wip_entries", 0, "target"),
        ),
        (
            "symlink.raw_target",
            (
                "surfaces",
                "repositories",
                0,
                "metadata",
                "wip_entries",
                0,
                "symlink",
                "raw_target",
            ),
        ),
        (
            "symlink.normalized_target",
            (
                "surfaces",
                "repositories",
                0,
                "metadata",
                "wip_entries",
                0,
                "symlink",
                "normalized_target",
            ),
        ),
        (
            "symlink.target_state",
            (
                "surfaces",
                "repositories",
                0,
                "metadata",
                "wip_entries",
                0,
                "symlink",
                "target_state",
            ),
        ),
        (
            "symlink.target_kind",
            (
                "surfaces",
                "repositories",
                0,
                "metadata",
                "wip_entries",
                0,
                "symlink",
                "target_kind",
            ),
        ),
        (
            "symlink.scope_decision",
            (
                "surfaces",
                "repositories",
                0,
                "metadata",
                "wip_entries",
                0,
                "symlink",
                "scope_decision",
            ),
        ),
    ],
)
def test_manifest_rejects_private_prompt_in_wip_and_symlink_carriers(
    carrier: str,
    path: tuple[str | int, ...],
) -> None:
    assert validate_manifest(manifest_with_private_prompt(*path)), carrier


def test_process_collector_redacts_secret_before_manifest_serialization(
    tmp_path: Path,
) -> None:
    roots = fixture_roots(tmp_path)
    synthetic_secret = "gh" + "p_" + "B" * 36

    def runner(args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        if args == ["ps", "-axo", "pid=,ppid=,command="]:
            return subprocess.CompletedProcess(
                args,
                0,
                f"321 1 /tmp/{synthetic_secret} --role jarvisd\n",
                "",
            )
        return quiet_runner(args)

    manifest = build_inventory(roots, runner=runner)
    serialized = json.dumps(manifest)
    rows = [
        row
        for row in manifest["surfaces"]["processes"]
        if row.get("kind") == "process"
    ]

    assert synthetic_secret not in serialized
    assert rows[0]["executable"] == "REDACTED"


def test_malformed_launchctl_row_is_probe_error_not_loaded_private_payload(
    tmp_path: Path,
) -> None:
    roots = fixture_roots(tmp_path)
    private_value = "PRIVATE_STATUS_TEXT"

    def runner(args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        if args == ["launchctl", "list"]:
            return subprocess.CompletedProcess(
                args,
                0,
                f"- {private_value} com.dan.dand\n",
                "",
            )
        return quiet_runner(args)

    manifest = build_inventory(roots, runner=runner)
    serialized = json.dumps(manifest)
    rows = manifest["surfaces"]["launchd"]

    assert not any(row.get("status") == "loaded" for row in rows)
    assert any(row.get("error", {}).get("type") == "MalformedLaunchdRecord" for row in rows)
    assert private_value not in serialized


def test_product_launchctl_rows_with_invalid_shape_are_content_free_probe_errors(
    tmp_path: Path,
) -> None:
    rows_by_case: dict[str, list[dict[str, object]]] = {}
    payloads: dict[str, object] = {
        "missing-column": "-\tcom.dan.dand\n",
        "extra-column": "-\t0\tcom.dan.dand\tPRIVATE_STATUS_TEXT\n",
        "private-label-suffix": "-\t0\tcom.dan.dand:PRIVATE_STATUS_TEXT\n",
        "undecodable": b"-\t0\tcom.dan.dand\xff\n",
    }

    for case, payload in payloads.items():
        case_root = tmp_path / case
        case_root.mkdir()
        roots = fixture_roots(case_root)

        def runner(
            args: list[str],
            *,
            launchctl_payload: object = payload,
            **_: object,
        ) -> subprocess.CompletedProcess[object]:
            if args == ["launchctl", "list"]:
                return subprocess.CompletedProcess(args, 0, launchctl_payload, b"")
            return quiet_runner(args)

        manifest = build_inventory(roots, runner=runner)
        rows = manifest["surfaces"]["launchd"]
        assert isinstance(rows, list)
        rows_by_case[case] = rows
        assert "PRIVATE_STATUS_TEXT" not in json.dumps(manifest)

    for case, rows in rows_by_case.items():
        assert not any(row.get("status") == "loaded" for row in rows), case
        assert any(
            row.get("status") == "probe-error"
            and row.get("probe") == "launchctl-list"
            and row.get("error", {}).get("type")
            in {"MalformedLaunchdRecord", "UnicodeDecodeError"}
            for row in rows
        ), case


def test_selected_base_exposes_undecodable_head_as_unresolved_error(
    tmp_path: Path,
) -> None:
    roots = fixture_roots(tmp_path)
    (roots.repo_root / ".git").mkdir()
    runner = git_runner(
        roots.repo_root,
        {("rev-parse", "HEAD"): (0, b"\xff\n", b"")},
    )

    manifest = build_inventory(roots, runner=runner)
    selected_base = manifest["selected_base"]

    assert selected_base["head"] is None
    assert selected_base["head_state"] == "probe-error"
    assert selected_base["error"]["type"] == "UnicodeDecodeError"
    assert any("selected_base has unresolved required error" in error for error in validate_manifest(manifest))


def test_canonical_manifest_parent_may_be_created_securely(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    destination = home / ".dan/migration/release1-source-manifest.json"

    write_manifest_atomic(
        manifest_shell(),
        destination,
        canonical_home=home,
    )

    assert destination.exists()
    assert destination.stat().st_mode & 0o777 == 0o600
    assert destination.parent.stat().st_mode & 0o777 == 0o700


def test_custom_manifest_parent_must_already_exist(tmp_path: Path) -> None:
    destination = tmp_path / "missing-parent/manifest.json"

    with pytest.raises(FileNotFoundError, match="custom manifest parent must already exist"):
        write_manifest_atomic(manifest_shell(), destination)

    assert not destination.parent.exists()


def test_canonical_writer_never_chmods_existing_user_directory(tmp_path: Path) -> None:
    home = tmp_path / "home"
    directory = home / ".dan/migration"
    directory.mkdir(parents=True, mode=0o755)
    directory.chmod(0o755)
    destination = directory / "release1-source-manifest.json"

    with pytest.raises(PermissionError, match="manifest directory mode must be 0700"):
        write_manifest_atomic(
            manifest_shell(),
            destination,
            canonical_home=home,
        )

    assert directory.stat().st_mode & 0o777 == 0o755
    assert not destination.exists()


def test_check_manifest_rejects_insecure_parent_mode(tmp_path: Path) -> None:
    directory = tmp_path / "shared"
    directory.mkdir(mode=0o755)
    directory.chmod(0o755)
    destination = directory / "manifest.json"
    destination.write_text(json.dumps(manifest_shell()), encoding="utf-8")
    destination.chmod(0o600)

    _, errors = check_manifest(destination)

    assert any("manifest directory mode must be 0700" in error for error in errors)
    assert directory.stat().st_mode & 0o777 == 0o755


def test_check_manifest_rejects_symlink_parent(tmp_path: Path) -> None:
    real_directory = tmp_path / "real"
    real_directory.mkdir(mode=0o700)
    linked_directory = tmp_path / "linked"
    linked_directory.symlink_to(real_directory, target_is_directory=True)
    real_manifest = real_directory / "manifest.json"
    real_manifest.write_text(json.dumps(manifest_shell()), encoding="utf-8")
    real_manifest.chmod(0o600)

    _, errors = check_manifest(linked_directory / "manifest.json")

    assert any("manifest directory must not be a symlink" in error for error in errors)
