"""Adversarial FIX FIRST regressions for the Task 1 inventory."""

from __future__ import annotations

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
