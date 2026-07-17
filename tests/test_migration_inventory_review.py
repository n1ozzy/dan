"""Negative review tests for the DAN Release 1 source inventory."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

import pytest

import jarvis.migration.inventory as inventory
from jarvis.migration.inventory import (
    InventoryRoots,
    build_inventory,
    check_manifest,
    inspect_database,
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
    return subprocess.CompletedProcess(args, 1, "", "unavailable in fixture")


def manifest_shell() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "generated_at": "2026-07-17T00:00:00+00:00",
        "selected_base": {
            "repository": "/fixture/repo",
            "ref": "fixture",
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


def voice_asset_row(
    *,
    decision: str = "reconcile-license-hash-and-version-in-task6",
) -> dict[str, object]:
    return {
        "path": "/fixture/voice.json",
        "kind": "file",
        "sha256": "c" * 64,
        "status": "present",
        "required": False,
        "metadata": {"size_bytes": 12, "mode": "0o600"},
        "decision": decision,
    }


def producer_rows(manifest: dict[str, object]) -> dict[str, dict[str, object]]:
    surfaces = manifest["surfaces"]
    assert isinstance(surfaces, dict)
    rows = surfaces["producers"]
    assert isinstance(rows, list)
    return {str(row["path"]): row for row in rows if "path" in row}


def test_current_claude_and_openclaw_memory_roots_are_classified_as_history(
    tmp_path: Path,
) -> None:
    roots = fixture_roots(tmp_path)
    claude_memory = roots.claude_project_memory_roots()[0] / "MEMORY.md"
    openclaw_memory = roots.home / ".openclaw/workspace/memory/2026-07-17.md"
    for path in (claude_memory, openclaw_memory):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("historical voice_broker.py reference\n", encoding="utf-8")

    manifest = build_inventory(roots, runner=quiet_runner)
    rows = producer_rows(manifest)

    assert rows[str(claude_memory)]["reference_class"] == "historical-memory-reference"
    assert rows[str(openclaw_memory)]["reference_class"] == "historical-memory-reference"
    assert rows[str(claude_memory)]["activity_evidence"] == []
    assert rows[str(openclaw_memory)]["activity_evidence"] == []


def test_session_history_is_not_scanned_as_active_reference(tmp_path: Path) -> None:
    roots = fixture_roots(tmp_path)
    project = roots.claude_project_memory_roots()[0].parent
    memory = project / "memory/MEMORY.md"
    session = project / "session-SECRET_SENTINEL.jsonl"
    memory.parent.mkdir(parents=True)
    memory.write_text("voice_broker.py\n", encoding="utf-8")
    session.write_text("voice_broker.py SECRET_SENTINEL\n", encoding="utf-8")

    manifest = build_inventory(roots, runner=quiet_runner)
    rows = producer_rows(manifest)

    assert str(memory) in rows
    assert str(session) not in rows
    assert "SECRET_SENTINEL" not in json.dumps(manifest)


def test_executable_without_suffix_requires_runtime_evidence_to_be_active(
    tmp_path: Path,
) -> None:
    roots = fixture_roots(tmp_path)
    executable = roots.repo_root / "bin/voice_broker"
    executable.parent.mkdir()
    executable.write_text("#!/bin/sh\n# voice_broker.py\n", encoding="utf-8")
    executable.chmod(0o755)

    def runner(args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        if args == ["ps", "-axo", "pid=,ppid=,command="]:
            command = f"4242 1 {executable} --token SECRET_SENTINEL\n"
            return subprocess.CompletedProcess(args, 0, command, "")
        return quiet_runner(args)

    manifest = build_inventory(roots, runner=runner)
    row = producer_rows(manifest)[str(executable)]
    serialized = json.dumps(manifest)

    assert row["reference_class"] == "active-runtime-producer"
    assert row["activity_evidence"] == [
        {"kind": "process", "source": "process:4242:legacy-broker"}
    ]
    assert "SECRET_SENTINEL" not in serialized
    assert "--token" not in serialized


def test_executable_backup_suffix_is_detected_but_not_resurrected(
    tmp_path: Path,
) -> None:
    roots = fixture_roots(tmp_path)
    backup = roots.repo_root / "bin/voice-worker.bak-20260717"
    backup.parent.mkdir()
    backup.write_text("#!/bin/sh\n# dan-voice/req\n", encoding="utf-8")
    backup.chmod(0o755)

    manifest = build_inventory(roots, runner=quiet_runner)
    row = producer_rows(manifest)[str(backup)]

    assert row["reference_class"] == "inactive-backup-archive-candidate"
    assert row["activity_evidence"] == []


def test_active_instruction_is_a_consumer_not_a_runtime_producer(tmp_path: Path) -> None:
    roots = fixture_roots(tmp_path)
    instruction = roots.repo_root / "AGENTS.md"
    instruction.write_text("Use the legacy dan-voice/req contract.\n", encoding="utf-8")

    manifest = build_inventory(roots, runner=quiet_runner)
    row = producer_rows(manifest)[str(instruction)]

    assert row["reference_class"] == "active-consumer-instruction"
    assert row["activity_evidence"] == [
        {"kind": "active-instruction", "source": str(instruction)}
    ]


def test_plain_text_quarantine_mention_does_not_make_it_active(tmp_path: Path) -> None:
    roots = fixture_roots(tmp_path)
    quarantine = roots.home / "Documents/dev/dan/_quarantine-continuity-fix-2026-07-08"
    quarantine.mkdir(parents=True)
    instruction = roots.repo_root / "AGENTS.md"
    instruction.write_text(
        "Historical note: _quarantine-continuity-fix-2026-07-08 was retired.\n",
        encoding="utf-8",
    )

    manifest = build_inventory(roots, runner=quiet_runner)
    materials = {
        row["path"]: row for row in manifest["surfaces"]["input_materials"]
    }

    assert materials[str(quarantine)]["decision"] == "archive/do-not-copy"
    assert materials[str(quarantine)]["activity_evidence"] == []


def test_active_skill_call_is_named_quarantine_activity_evidence(tmp_path: Path) -> None:
    roots = fixture_roots(tmp_path)
    quarantine = roots.home / "Documents/dev/dan/_quarantine-continuity-fix-2026-07-08"
    tool = quarantine / "resume.sh"
    tool.parent.mkdir(parents=True)
    tool.write_text("#!/bin/sh\n", encoding="utf-8")
    skill = roots.home / ".agents/skills/live/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(f"Run: zsh {tool}\n", encoding="utf-8")

    manifest = build_inventory(roots, runner=quiet_runner)
    materials = {
        row["path"]: row for row in manifest["surfaces"]["input_materials"]
    }

    assert materials[str(quarantine)]["decision"] == "active-source"
    assert materials[str(quarantine)]["activity_evidence"] == [
        {"kind": "active-skill-call", "source": str(skill)}
    ]


def test_walk_permission_error_is_recorded_on_required_surface(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    roots = fixture_roots(tmp_path)
    original_walk = inventory.os.walk

    def fake_walk(
        root: str | os.PathLike[str],
        *,
        followlinks: bool = False,
        onerror: Any = None,
    ) -> Any:
        if Path(root) == roots.repo_root:
            assert onerror is not None
            onerror(PermissionError(13, "denied", str(roots.repo_root)))
            return iter(())
        return original_walk(root, followlinks=followlinks, onerror=onerror)

    monkeypatch.setattr(inventory.os, "walk", fake_walk)

    manifest = build_inventory(roots, runner=quiet_runner)
    errors = [
        row
        for row in manifest["surfaces"]["producers"]
        if row.get("status") == "path-error"
    ]

    assert errors == [
        {
            "kind": "path_error",
            "path": str(roots.repo_root),
            "status": "path-error",
            "required": True,
            "error": {
                "type": "PermissionError",
                "operation": "walk",
                "resolved": False,
            },
            "decision": "record-probe-failure-and-recheck-at-review-gate",
        }
    ]
    assert any("unresolved required error" in error for error in validate_manifest(manifest))


def test_lstat_permission_error_is_not_reported_as_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "private.txt"
    original_lstat = inventory.os.lstat

    def denied(
        candidate: str | os.PathLike[str],
        *args: object,
        **kwargs: object,
    ) -> os.stat_result:
        if Path(candidate) == path:
            raise PermissionError(13, "denied", str(path))
        return original_lstat(candidate, *args, **kwargs)

    monkeypatch.setattr(inventory.os, "lstat", denied)

    item = inspect_path(path, required=True)

    assert item.status == "path-error"
    assert item.error == {
        "type": "PermissionError",
        "operation": "lstat",
        "resolved": False,
    }


def test_file_disappearing_between_lstat_and_open_is_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "vanishing.txt"
    path.write_text("fixture\n", encoding="utf-8")
    original_open = inventory.os.open

    def vanished(candidate: str | os.PathLike[str], flags: int, *args: object) -> int:
        if Path(candidate) == path:
            raise FileNotFoundError(2, "gone", str(path))
        return original_open(candidate, flags, *args)

    monkeypatch.setattr(inventory.os, "open", vanished)

    item = inspect_path(path, required=True)

    assert item.status == "path-error"
    assert item.sha256 is None
    assert item.error == {
        "type": "FileNotFoundError",
        "operation": "open",
        "resolved": False,
    }


def test_hash_read_error_is_recorded_without_crashing_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "unreadable.txt"
    path.write_text("fixture\n", encoding="utf-8")
    original_read = inventory.os.read

    def denied(descriptor: int, size: int) -> bytes:
        del descriptor, size
        raise OSError(5, "read failed")

    monkeypatch.setattr(inventory.os, "read", denied)
    try:
        item = inspect_path(path, required=True)
    finally:
        monkeypatch.setattr(inventory.os, "read", original_read)

    assert item.status == "path-error"
    assert item.sha256 is None
    assert item.error == {
        "type": "OSError",
        "operation": "hash",
        "resolved": False,
    }


def test_signature_read_error_becomes_safe_producer_path_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    roots = fixture_roots(tmp_path)
    candidate = roots.repo_root / "voice-worker"
    candidate.write_text("voice_broker.py\n", encoding="utf-8")
    candidate.chmod(0o755)
    original_open = inventory.os.open

    def denied(path: os.PathLike[str] | str, flags: int, *args: object, **kwargs: object) -> int:
        if Path(path) == candidate:
            raise PermissionError(13, "SECRET_SENTINEL", str(path))
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(inventory.os, "open", denied)

    manifest = build_inventory(roots, runner=quiet_runner)
    rows = manifest["surfaces"]["producers"]
    error = next(row for row in rows if row.get("path") == str(candidate))

    assert error["status"] == "path-error"
    assert error["error"]["type"] == "PermissionError"
    assert error["error"]["operation"] == "read-signatures-open"
    assert "SECRET_SENTINEL" not in json.dumps(manifest)


def test_malformed_ps_rows_are_recorded_and_valid_rows_remain_private(tmp_path: Path) -> None:
    roots = fixture_roots(tmp_path)

    def runner(args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        if args == ["ps", "-axo", "pid=,ppid=,command="]:
            output = (
                "garbage\n"
                "abc 1 python voice_broker.py\n"
                "123 nope python feeder.sh\n"
                "4242 1 python voice_broker.py --prompt SECRET_SENTINEL\n"
            )
            return subprocess.CompletedProcess(args, 0, output, "")
        return quiet_runner(args)

    manifest = build_inventory(roots, runner=runner)
    rows = manifest["surfaces"]["processes"]
    process = next(row for row in rows if row.get("kind") == "process")
    errors = [row for row in rows if row.get("kind") == "probe_error"]

    assert process["kind"] == "process"
    assert process["pid"] == 4242
    assert process["ppid"] == 1
    assert process["role"] == "legacy-broker"
    assert process["executable"] == "python"
    assert len(process["runtime_signature"]) == 64
    assert process["status"] == "running"
    assert process["decision"] == (
        "observe-only-in-task1-stop-only-during-journaled-cutover"
    )
    assert len(errors) == 3
    assert {row["error"]["type"] for row in errors} == {"MalformedProcessRecord"}
    assert "SECRET_SENTINEL" not in json.dumps(manifest)


def test_undecodable_subprocess_bytes_are_recorded_without_raw_output(tmp_path: Path) -> None:
    roots = fixture_roots(tmp_path)

    def runner(
        args: list[str], **_: object
    ) -> subprocess.CompletedProcess[bytes] | subprocess.CompletedProcess[str]:
        if args == ["ps", "-axo", "pid=,ppid=,command="]:
            return subprocess.CompletedProcess(
                args,
                0,
                b"4242 1 python voice_broker.py \xff\n",
                b"",
            )
        return quiet_runner(args)

    manifest = build_inventory(roots, runner=runner)  # type: ignore[arg-type]
    errors = [
        row
        for row in manifest["surfaces"]["processes"]
        if row.get("kind") == "probe_error"
    ]

    assert any(row["error"]["type"] == "UnicodeDecodeError" for row in errors)
    assert "\\ufffd" not in json.dumps(manifest)


def test_nonzero_probe_exit_is_required_unresolved_error(tmp_path: Path) -> None:
    roots = fixture_roots(tmp_path)

    def runner(args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        if args == ["ps", "-axo", "pid=,ppid=,command="]:
            return subprocess.CompletedProcess(args, 23, "", "SECRET_SENTINEL")
        return quiet_runner(args)

    manifest = build_inventory(roots, runner=runner)
    row = manifest["surfaces"]["processes"][0]

    assert row["kind"] == "probe_error"
    assert row["required"] is True
    assert row["returncode"] == 23
    assert row["error"] == {
        "type": "NonZeroExit",
        "operation": "ps",
        "resolved": False,
    }
    assert "SECRET_SENTINEL" not in json.dumps(manifest)
    assert any("unresolved required error" in error for error in validate_manifest(manifest))


def test_missing_required_and_optional_paths_are_distinct(tmp_path: Path) -> None:
    required = inspect_path(tmp_path / "required", required=True)
    optional = inspect_path(tmp_path / "optional", required=False)

    assert required.status == "path-error"
    assert required.required is True
    assert required.error == {
        "type": "MissingPath",
        "operation": "lstat",
        "resolved": False,
    }
    assert optional.status == "missing"
    assert optional.required is False
    assert optional.error is None


def test_relative_symlink_records_normalized_scope_and_hash(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    target = allowed / "target.sh"
    target.write_text("#!/bin/sh\n", encoding="utf-8")
    link = allowed / "link.sh"
    link.symlink_to("target.sh")

    item = inspect_path(link, allowed_roots=(allowed,))

    assert item.symlink == {
        "raw_target": "target.sh",
        "normalized_target": str(target.resolve()),
        "target_state": "existing",
        "target_kind": "file",
        "target_is_absolute": False,
        "inside_allowed_roots": True,
        "scope_decision": "hash-allowed-regular-target",
        "target_size_bytes": target.stat().st_size,
    }
    assert item.sha256 == hashlib.sha256(target.read_bytes()).hexdigest()


def test_relative_parent_escape_symlink_is_not_opened_or_hashed(tmp_path: Path) -> None:
    allowed = tmp_path / "scope/nested"
    allowed.mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("SECRET_SENTINEL\n", encoding="utf-8")
    link = allowed / "escape"
    link.symlink_to("../../outside.txt")

    item = inspect_path(link, allowed_roots=(allowed,))

    assert item.symlink["raw_target"] == "../../outside.txt"
    assert item.symlink["normalized_target"] == str(outside.resolve())
    assert item.symlink["inside_allowed_roots"] is False
    assert item.symlink["scope_decision"] == "reject-outside-allowed-roots"
    assert item.sha256 is None


def test_absolute_outside_root_symlink_is_not_opened_or_hashed(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("SECRET_SENTINEL\n", encoding="utf-8")
    link = allowed / "escape"
    link.symlink_to(outside)

    item = inspect_path(link, allowed_roots=(allowed,))

    assert item.symlink["target_is_absolute"] is True
    assert item.symlink["inside_allowed_roots"] is False
    assert item.symlink["scope_decision"] == "reject-outside-allowed-roots"
    assert item.sha256 is None


def test_broken_symlink_is_explicit_and_unhashed(tmp_path: Path) -> None:
    link = tmp_path / "broken"
    link.symlink_to("missing-target")

    item = inspect_path(link, allowed_roots=(tmp_path,))

    assert item.status == "broken"
    assert item.symlink["target_state"] == "broken"
    assert item.symlink["scope_decision"] == "broken-target"
    assert item.sha256 is None


def test_symlink_target_size_limit_precedes_hashing(tmp_path: Path) -> None:
    target = tmp_path / "large.bin"
    target.write_bytes(b"12345")
    link = tmp_path / "large-link"
    link.symlink_to(target)

    item = inspect_path(
        link,
        allowed_roots=(tmp_path,),
        max_symlink_target_bytes=4,
    )

    assert item.symlink["target_size_bytes"] == 5
    assert item.symlink["scope_decision"] == "target-too-large"
    assert item.sha256 is None


def test_symlink_target_change_during_scan_discards_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("first\n", encoding="utf-8")
    second.write_text("second\n", encoding="utf-8")
    link = tmp_path / "moving"
    link.symlink_to(first)
    targets = iter((str(first), str(second)))

    monkeypatch.setattr(inventory.os, "readlink", lambda _: next(targets))

    item = inspect_path(link, allowed_roots=(tmp_path,))

    assert item.status == "path-error"
    assert item.symlink["target_state"] == "changed"
    assert item.symlink["scope_decision"] == "target-changed-during-scan"
    assert item.sha256 is None


def test_symlink_target_permission_error_is_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "private.txt"
    target.write_text("fixture\n", encoding="utf-8")
    link = tmp_path / "private-link"
    link.symlink_to(target)
    original_open = inventory.os.open

    def denied(candidate: str | os.PathLike[str], flags: int, *args: object) -> int:
        if Path(candidate) == target:
            raise PermissionError(13, "SECRET_SENTINEL", str(target))
        return original_open(candidate, flags, *args)

    monkeypatch.setattr(inventory.os, "open", denied)

    item = inspect_path(link, allowed_roots=(tmp_path,))

    assert item.status == "path-error"
    assert item.symlink["scope_decision"] == "target-read-error"
    assert item.error["type"] == "PermissionError"
    assert item.sha256 is None


def test_process_manifest_never_serializes_raw_command_or_secret(tmp_path: Path) -> None:
    roots = fixture_roots(tmp_path)

    def runner(args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        if args == ["ps", "-axo", "pid=,ppid=,command="]:
            command = "4242 1 python voice_broker.py --prompt SECRET_SENTINEL\n"
            return subprocess.CompletedProcess(args, 0, command, "")
        return quiet_runner(args)

    manifest = build_inventory(roots, runner=runner)
    serialized = json.dumps(manifest)
    row = next(
        row for row in manifest["surfaces"]["processes"] if row.get("kind") == "process"
    )

    assert set(row) == {
        "kind",
        "pid",
        "ppid",
        "role",
        "executable",
        "runtime_signature",
        "status",
        "decision",
    }
    assert "SECRET_SENTINEL" not in serialized
    assert "command" not in row
    assert "argv" not in row


def test_file_secret_is_hashed_but_never_serialized(tmp_path: Path) -> None:
    roots = fixture_roots(tmp_path)
    producer = roots.repo_root / "voice-worker"
    producer.write_text("voice_broker.py SECRET_SENTINEL\n", encoding="utf-8")
    producer.chmod(0o755)

    manifest = build_inventory(roots, runner=quiet_runner)

    assert str(producer) in producer_rows(manifest)
    assert "SECRET_SENTINEL" not in json.dumps(manifest)


@pytest.mark.parametrize(
    "forbidden",
    ["argv", "command_line", "records", "contents", "payload", "text"],
)
def test_check_rejects_private_or_raw_process_fields(
    tmp_path: Path, forbidden: str
) -> None:
    manifest = manifest_shell()
    manifest["surfaces"]["processes"] = [
        process_row(**{forbidden: "SECRET_SENTINEL"})
    ]
    path = tmp_path / "manifest.json"
    write_manifest_atomic(manifest, path)

    _, errors = check_manifest(path)

    assert any(forbidden in error for error in errors)


@pytest.mark.parametrize("placeholder", ["", "pending", "TBD", "TODO later"])
def test_check_rejects_empty_or_placeholder_decision(
    tmp_path: Path, placeholder: str
) -> None:
    manifest = manifest_shell()
    manifest["surfaces"]["voice_assets"] = [voice_asset_row(decision=placeholder)]
    path = tmp_path / "manifest.json"
    write_manifest_atomic(manifest, path)

    _, errors = check_manifest(path)

    assert any("decision" in error for error in errors)


def test_schema_rejects_unknown_root_and_nested_fields() -> None:
    manifest = manifest_shell()
    manifest["surprise"] = {"payload": "SECRET_SENTINEL"}
    manifest["generated_at"] = {"secret": "SECRET_SENTINEL"}
    manifest["selected_base"]["unknown"] = True
    manifest["selected_base"]["repository"] = {"secret": "SECRET_SENTINEL"}
    manifest["roots"]["unknown"] = []
    manifest["roots"]["production"] = [{"secret": "SECRET_SENTINEL"}]
    row = voice_asset_row()
    row["path"] = {"secret": "SECRET_SENTINEL"}
    row["metadata"]["size_bytes"] = {"secret": "SECRET_SENTINEL"}
    row["metadata"]["unknown"] = "SECRET_SENTINEL"
    manifest["surfaces"]["voice_assets"] = [row]

    errors = validate_manifest(manifest)
    incomplete = manifest_shell()
    incomplete["surfaces"]["voice_assets"] = [{"decision": "retain-source"}]
    incomplete_errors = validate_manifest(incomplete)

    assert any("root.surprise" in error for error in errors)
    assert any("selected_base.unknown" in error for error in errors)
    assert any("roots.unknown" in error for error in errors)
    assert any("metadata.unknown" in error for error in errors)
    assert any("generated_at must be a string" in error for error in errors)
    assert any("selected_base.repository must be a string" in error for error in errors)
    assert any("roots.production must be a list of strings" in error for error in errors)
    assert any("surfaces.voice_assets[0].path must be a string" in error for error in errors)
    assert any("metadata.size_bytes must be an integer" in error for error in errors)
    assert any(
        "surfaces.voice_assets[0] missing fields" in error
        for error in incomplete_errors
    )


def test_unresolved_required_error_is_rejected_but_optional_error_is_allowed() -> None:
    required_manifest = manifest_shell()
    optional_manifest = manifest_shell()
    error_row = {
        "kind": "probe_error",
        "status": "probe-error",
        "probe": "ps",
        "required": True,
        "returncode": 1,
        "error": {"type": "NonZeroExit", "operation": "ps", "resolved": False},
        "decision": "record-probe-failure-and-recheck-at-review-gate",
    }
    required_manifest["surfaces"]["processes"] = [error_row]
    optional_manifest["surfaces"]["processes"] = [{**error_row, "required": False}]

    assert any(
        "unresolved required error" in error
        for error in validate_manifest(required_manifest)
    )
    assert not any(
        "unresolved required error" in error
        for error in validate_manifest(optional_manifest)
    )


def test_generated_manifest_passes_strict_schema(tmp_path: Path) -> None:
    manifest = build_inventory(fixture_roots(tmp_path), runner=quiet_runner)

    assert validate_manifest(manifest) == []


def test_sqlite_surface_contains_only_approved_metadata(tmp_path: Path) -> None:
    database = tmp_path / "private.db"
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA user_version=7")
    connection.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, body TEXT)")
    connection.execute("INSERT INTO memories(body) VALUES ('SECRET_SENTINEL')")
    connection.commit()
    expected_schema_version = int(connection.execute("PRAGMA schema_version").fetchone()[0])
    connection.close()

    record = inspect_database(database, runner=quiet_runner)
    serialized = json.dumps(record)

    assert set(record) == {
        "path",
        "kind",
        "status",
        "required",
        "user_version",
        "schema_version",
        "journal_mode",
        "tables",
        "record_counts",
    }
    assert record["user_version"] == 7
    assert record["schema_version"] == expected_schema_version
    assert record["tables"] == ["memories"]
    assert record["record_counts"] == {"memories": 1}
    assert "SECRET_SENTINEL" not in serialized
    assert "sha256" not in record
    assert "mode" not in record
    assert "size_bytes" not in record
    assert "open_handles" not in serialized


def test_sqlite_inventory_never_runs_open_handle_probe(tmp_path: Path) -> None:
    database = tmp_path / "private.db"
    sqlite3.connect(database).close()

    def no_subprocess(args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError(f"unexpected subprocess: {args[0]}")

    record = inspect_database(database, runner=no_subprocess)

    assert record["status"] == "present"


def test_ref_ledger_accounts_for_every_frozen_repository_ref() -> None:
    ledger = (
        Path(__file__).resolve().parents[1] / "docs/migration/REF-DECISIONS.md"
    ).read_text(encoding="utf-8")
    expected = {
        "refs/heads/claude/amazing-hawking-c80907": "06ba7421a1287f1b4fda50d24ac3631aa0296f5d",
        "refs/heads/claude/fix-brain-wiring": "5d92e987e5e550f077a6383b2c2259089d65b67c",
        "refs/heads/feat/dan-foundation-release1": "18417950a4653e5d666df745c62023778cfeb153",
        "refs/heads/main": "8a5a0f0d502f3a55afc64d7c4ebb4d135346b503",
        "refs/heads/rescue/audt-gpt5.5pro": "cdf19558fb957486ae61c1b695a03f8d388c17bb",
        "refs/heads/rescue/audt-gpt5.5pro-limit-cdn": "0b5ea9d11eb97b829cdd84950e6477579e1bbc00",
        "refs/heads/spike/jarvis-local-runtime-check": "18417950a4653e5d666df745c62023778cfeb153",
        "refs/remotes/origin/HEAD": "8a5a0f0d502f3a55afc64d7c4ebb4d135346b503",
        "refs/remotes/origin/feat/live-audio-resilience": (
            "cd92f98d163e66f7a6f4a882e1c3836335c4289d"
        ),
        "refs/remotes/origin/main": "8a5a0f0d502f3a55afc64d7c4ebb4d135346b503",
        "refs/remotes/origin/rescue/audit-8a5a0f0": "cdf19558fb957486ae61c1b695a03f8d388c17bb",
        "refs/remotes/origin/rescue/audt-gpt5.5pro": "cdf19558fb957486ae61c1b695a03f8d388c17bb",
        "refs/remotes/origin/spike/jarvis-local-runtime-check": (
            "b18143d4a192c0e0e1414f1418c8c464d5be7d48"
        ),
        "refs/remotes/origin/spike/jarvis-local-runtime-gpt-fixing": (
            "7333b13fd525a326fe47ef7f0c74cbae09a12cb8"
        ),
        "dan:refs/heads/agent/voice-lab": "0d20dcc6573930b1a3a6d3dd30de450658dcc9e0",
        "dan:refs/heads/feat/shared-voice-source": "f5eafed47dd365b83c7d69894157bd045a663d9a",
        "dan:refs/heads/main": "75045401ed127efa9a64018424a1c97101dffb36",
        "dan:refs/remotes/origin/HEAD": "75045401ed127efa9a64018424a1c97101dffb36",
        "dan:refs/remotes/origin/agent/voice-lab": "0d20dcc6573930b1a3a6d3dd30de450658dcc9e0",
        "dan:refs/remotes/origin/feat/shared-voice-source": (
            "34cbe7c746ec96d3344e44fd3fdc7075eb626e65"
        ),
        "dan:refs/remotes/origin/main": "75045401ed127efa9a64018424a1c97101dffb36",
        "dan:refs/remotes/origin/worktree-panel-web-ui": "79b27423523843140524b5f5cc1fbe7d65811e60",
        "DANv2:refs/heads/main": "5524d2ba00b7cfcca7af0d2672528fb75a509d5a",
        "DANv2:refs/remotes/origin/main": "5524d2ba00b7cfcca7af0d2672528fb75a509d5a",
    }

    missing = {
        ref: sha
        for ref, sha in expected.items()
        if not any(
            f"`{ref}`" in line and f"`{sha}`" in line
            for line in ledger.splitlines()
        )
    }

    assert missing == {}
