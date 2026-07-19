"""Contracts for literal, fail-closed legacy checkout cache cleanup."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import stat
import subprocess
import sys
from pathlib import Path

import pytest

import dan.release.checkout_hygiene as checkout_hygiene
from dan.release.checkout_hygiene import (
    SAFE_CACHE_NAMES,
    HygieneReport,
    UnsafeCleanupPlan,
    UnsafeCleanupTarget,
    UnsafeReportOutput,
    apply_safe_cache_removal,
    build_hygiene_report,
    plan_safe_cache_removal,
    run_checkout_hygiene,
    scan_checkout_hygiene,
    write_hygiene_report_exclusive,
)

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "dan-checkout-hygiene"
REAL_LEGACY_EMPTY_DIRECTORIES = (
    "api",
    "audio",
    "brain",
    "daemon",
    "diagnostics",
    "events",
    "macos",
    "mcp",
    "memory",
    "panel",
    "runtime",
    "security",
    "store",
    "tools",
    "turns",
    "voice",
    "workers",
)


def _make_repo(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    legacy = repo / "jarvis"
    legacy.mkdir(parents=True)
    return repo, legacy


def _configure_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, repo: Path
) -> Path:
    home = tmp_path / "home"
    evidence = tmp_path / "evidence"
    home.mkdir(exist_ok=True)
    evidence.mkdir(mode=0o700, exist_ok=True)
    evidence.chmod(0o700)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("DAN_RELEASE_EVIDENCE_ROOT", str(evidence))
    monkeypatch.setenv("DAN_RUNTIME_DIR", str(home / ".dan" / "runtime"))
    monkeypatch.setenv("DAN_DB_PATH", str(home / ".dan" / "dan.sqlite3"))
    monkeypatch.delenv("DAN_CONFIG", raising=False)
    monkeypatch.delenv("VOICE_CONFIG_DIR", raising=False)
    assert not evidence.is_relative_to(repo)
    return evidence


def _subprocess_environment(tmp_path: Path, evidence: Path) -> dict[str, str]:
    home = tmp_path / "cli-home"
    home.mkdir(exist_ok=True)
    return {
        **os.environ,
        "HOME": str(home),
        "DAN_RELEASE_EVIDENCE_ROOT": str(evidence),
        "DAN_RUNTIME_DIR": str(home / ".dan" / "runtime"),
        "DAN_DB_PATH": str(home / ".dan" / "dan.sqlite3"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
    }


def _snapshot(root: Path) -> tuple[tuple[str, int, int, bytes | None], ...]:
    rows: list[tuple[str, int, int, bytes | None]] = []
    for path in sorted((root, *root.rglob("*")), key=lambda item: str(item)):
        details = path.lstat()
        content = path.read_bytes() if stat.S_ISREG(details.st_mode) else None
        rows.append((str(path.relative_to(root.parent)), details.st_mode, details.st_ino, content))
    return tuple(rows)


def test_scan_distinguishes_absent_from_physical_empty_legacy_namespace(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    absent = scan_checkout_hygiene(repo)
    (repo / "jarvis").mkdir()
    present = scan_checkout_hygiene(repo)

    assert absent.legacy_namespace_present is False
    assert present.legacy_namespace_present is True
    assert present.legacy_root == repo.resolve() / "jarvis"


def test_cleanup_plan_targets_only_pyc_pycache_and_ds_store(tmp_path: Path) -> None:
    repo, legacy = _make_repo(tmp_path)
    cache = legacy / "sub" / "__pycache__"
    cache.mkdir(parents=True)
    (cache / "module.cpython-311.pyc").write_bytes(b"pyc")
    (legacy / ".DS_Store").write_bytes(b"finder")
    (legacy / "keep.py").write_text("keep = True\n", encoding="utf-8")

    plan = plan_safe_cache_removal(repo=repo, legacy_root=legacy)

    assert SAFE_CACHE_NAMES == frozenset({"__pycache__", ".DS_Store"})
    assert {item.kind for item in plan.items} == {"pyc", "pycache", "ds_store"}
    assert legacy / "keep.py" not in {item.path for item in plan.items}
    assert "jarvis/keep.py" in {item.relative_path for item in plan.skipped}
    assert "jarvis/sub" in {item.relative_path for item in plan.skipped}
    assert plan.eligible is False


def test_cleanup_plan_is_canonically_sorted_and_exact(tmp_path: Path) -> None:
    repo, legacy = _make_repo(tmp_path)
    (legacy / "z.pyc").write_bytes(b"z")
    (legacy / ".DS_Store").write_bytes(b"finder")
    cache = legacy / "__pycache__"
    cache.mkdir()
    (cache / "a.pyc").write_bytes(b"a")

    plan = plan_safe_cache_removal(repo=repo, legacy_root=legacy)

    relative_paths = [item.relative_path for item in plan.items]
    assert relative_paths == sorted(relative_paths)
    assert relative_paths == [
        "jarvis/.DS_Store",
        "jarvis/__pycache__",
        "jarvis/__pycache__/a.pyc",
        "jarvis/z.pyc",
    ]
    assert all(not path.startswith("/") and ".." not in Path(path).parts for path in relative_paths)


def test_cleanup_plan_rejects_non_exact_legacy_root(tmp_path: Path) -> None:
    repo, legacy = _make_repo(tmp_path)
    sibling = tmp_path / "sibling" / "jarvis"
    sibling.mkdir(parents=True)
    nested = repo / "nested" / "jarvis"
    nested.mkdir(parents=True)
    differently_named = repo / "legacy"
    differently_named.mkdir()
    alias = repo / "jarvis-alias"
    alias.symlink_to(legacy, target_is_directory=True)
    normalized_alias = repo / "nested" / ".." / "jarvis"

    for candidate in (
        sibling,
        nested,
        differently_named,
        legacy / "..",
        alias,
        normalized_alias,
    ):
        with pytest.raises(UnsafeCleanupTarget):
            plan_safe_cache_removal(repo=repo, legacy_root=candidate)


def test_cleanup_plan_rejects_symlinked_repo_and_exact_jarvis_symlink(
    tmp_path: Path,
) -> None:
    repo, legacy = _make_repo(tmp_path)
    repo_alias = tmp_path / "repo-alias"
    repo_alias.symlink_to(repo, target_is_directory=True)
    with pytest.raises(UnsafeCleanupTarget):
        plan_safe_cache_removal(repo=repo_alias, legacy_root=repo_alias / "jarvis")

    actual = tmp_path / "actual-jarvis"
    legacy.rename(actual)
    legacy.symlink_to(actual, target_is_directory=True)
    assert scan_checkout_hygiene(repo).legacy_namespace_present is True
    with pytest.raises(UnsafeCleanupTarget):
        plan_safe_cache_removal(repo=repo, legacy_root=legacy)


def test_cleanup_plan_rejects_repo_substitution_inside_first_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, legacy = _make_repo(tmp_path)
    (legacy / ".DS_Store").write_bytes(b"original")
    moved = tmp_path / "repo-original"
    real_open = checkout_hygiene.os.open
    injection_fired = False

    def racing_open(
        path: object, flags: int, mode: int = 0o777, **kwargs: object
    ) -> int:
        nonlocal injection_fired
        if path == repo.name and kwargs.get("dir_fd") is not None and not injection_fired:
            repo.rename(moved)
            (repo / "jarvis").mkdir(parents=True)
            (repo / "jarvis" / ".DS_Store").write_bytes(b"substitute")
            injection_fired = True
        return real_open(path, flags, mode, **kwargs)

    monkeypatch.setattr(checkout_hygiene.os, "open", racing_open)

    with pytest.raises(UnsafeCleanupTarget, match="identity"):
        plan_safe_cache_removal(repo=repo, legacy_root=repo / "jarvis")

    assert injection_fired is True
    assert (moved / "jarvis" / ".DS_Store").read_bytes() == b"original"
    assert (repo / "jarvis" / ".DS_Store").read_bytes() == b"substitute"


def test_cleanup_plan_rejects_jarvis_substitution_inside_repo_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, legacy = _make_repo(tmp_path)
    (legacy / ".DS_Store").write_bytes(b"original")
    moved = repo / "jarvis-original"
    real_open = checkout_hygiene.os.open
    injection_fired = False

    def racing_open(
        path: object, flags: int, mode: int = 0o777, **kwargs: object
    ) -> int:
        nonlocal injection_fired
        if path == repo.name and kwargs.get("dir_fd") is not None and not injection_fired:
            legacy.rename(moved)
            legacy.mkdir()
            (legacy / ".DS_Store").write_bytes(b"substitute")
            injection_fired = True
        return real_open(path, flags, mode, **kwargs)

    monkeypatch.setattr(checkout_hygiene.os, "open", racing_open)

    with pytest.raises(UnsafeCleanupTarget, match="identity"):
        plan_safe_cache_removal(repo=repo, legacy_root=legacy)

    assert injection_fired is True
    assert (moved / ".DS_Store").read_bytes() == b"original"
    assert (legacy / ".DS_Store").read_bytes() == b"substitute"


def test_cleanup_plan_rejects_ancestor_substitution_during_component_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    anchor = tmp_path / "unique-anchor"
    repo, legacy = _make_repo(anchor)
    (legacy / ".DS_Store").write_bytes(b"original")
    moved = tmp_path / "anchor-original"
    real_open = checkout_hygiene.os.open
    injection_fired = False

    def racing_open(
        path: object, flags: int, mode: int = 0o777, **kwargs: object
    ) -> int:
        nonlocal injection_fired
        if path == anchor.name and kwargs.get("dir_fd") is not None and not injection_fired:
            anchor.rename(moved)
            (anchor / "repo" / "jarvis").mkdir(parents=True)
            (anchor / "repo" / "jarvis" / ".DS_Store").write_bytes(b"substitute")
            injection_fired = True
        return real_open(path, flags, mode, **kwargs)

    monkeypatch.setattr(checkout_hygiene.os, "open", racing_open)

    with pytest.raises(UnsafeCleanupTarget, match="identity"):
        plan_safe_cache_removal(repo=repo, legacy_root=legacy)

    assert injection_fired is True
    assert (moved / "repo" / "jarvis" / ".DS_Store").read_bytes() == b"original"
    assert (anchor / "repo" / "jarvis" / ".DS_Store").read_bytes() == b"substitute"


def test_component_anchor_fstat_primary_survives_close_failure_without_fd_leak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, legacy = _make_repo(tmp_path)
    (legacy / ".DS_Store").write_bytes(b"finder")
    real_open = checkout_hygiene.os.open
    real_fstat = checkout_hygiene.os.fstat
    real_close = checkout_hygiene.os.close
    live_descriptors: set[int] = set()
    target_descriptor: int | None = None
    fstat_failed = False
    close_failed = False

    def recording_open(
        path: object, flags: int, mode: int = 0o777, **kwargs: object
    ) -> int:
        nonlocal target_descriptor
        descriptor = real_open(path, flags, mode, **kwargs)
        live_descriptors.add(descriptor)
        if path == repo.name and kwargs.get("dir_fd") is not None:
            target_descriptor = descriptor
        return descriptor

    def failing_fstat(descriptor: int) -> os.stat_result:
        nonlocal fstat_failed
        if descriptor == target_descriptor and not fstat_failed:
            fstat_failed = True
            raise OSError("primary component fstat failure")
        return real_fstat(descriptor)

    def failing_close(descriptor: int) -> None:
        nonlocal close_failed
        real_close(descriptor)
        live_descriptors.discard(descriptor)
        if fstat_failed and not close_failed:
            close_failed = True
            raise OSError("secondary component close failure")

    monkeypatch.setattr(checkout_hygiene.os, "open", recording_open)
    monkeypatch.setattr(checkout_hygiene.os, "fstat", failing_fstat)
    monkeypatch.setattr(checkout_hygiene.os, "close", failing_close)

    with pytest.raises(UnsafeCleanupTarget) as captured:
        plan_safe_cache_removal(repo=repo, legacy_root=legacy)

    assert isinstance(captured.value.__cause__, OSError)
    assert "primary component fstat failure" in str(captured.value.__cause__)
    assert any(
        "secondary descriptor close failure" in note
        for note in captured.value.__cause__.__notes__
    )
    assert fstat_failed is True
    assert close_failed is True
    assert live_descriptors == set()


def test_scan_counts_a_dangling_jarvis_symlink_as_physical_namespace(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "jarvis").symlink_to(tmp_path / "missing", target_is_directory=True)

    assert scan_checkout_hygiene(repo).legacy_namespace_present is True


def test_cleanup_plan_flags_symlink_special_file_and_noncache_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, legacy = _make_repo(tmp_path)
    ordinary = legacy / "ordinary"
    ordinary.mkdir()
    (legacy / "link").symlink_to(ordinary, target_is_directory=True)
    fifo = legacy / "pipe"
    os.mkfifo(fifo)
    sock = socket.socket(socket.AF_UNIX)
    try:
        monkeypatch.chdir(legacy)
        sock.bind("socket")
        plan = plan_safe_cache_removal(repo=repo, legacy_root=legacy)
    finally:
        sock.close()

    reasons = {entry.relative_path: entry.reason for entry in plan.skipped}
    assert reasons == {
        "jarvis/link": "symlink",
        "jarvis/ordinary": "non-cache-directory",
        "jarvis/pipe": "special-file",
        "jarvis/socket": "special-file",
    }
    assert plan.eligible is False


@pytest.mark.parametrize(
    ("case", "relative", "reason"),
    (
        ("ds-store-directory", "jarvis/.DS_Store", "non-cache-directory"),
        ("ds-store-symlink", "jarvis/.DS_Store", "symlink"),
        ("pycache-file", "jarvis/__pycache__", "non-cache-file"),
        ("pycache-symlink", "jarvis/__pycache__", "symlink"),
        ("pyc-directory", "jarvis/module.pyc", "non-cache-directory"),
        ("pyc-symlink", "jarvis/module.pyc", "symlink"),
        ("non-pyc-in-pycache", "jarvis/__pycache__/note.txt", "non-cache-file"),
    ),
)
def test_safe_looking_names_with_wrong_types_are_blockers(
    tmp_path: Path, case: str, relative: str, reason: str
) -> None:
    repo, legacy = _make_repo(tmp_path)
    sentinel = repo / "sentinel"
    sentinel.write_bytes(b"outside")
    if case == "ds-store-directory":
        (legacy / ".DS_Store").mkdir()
    elif case == "ds-store-symlink":
        (legacy / ".DS_Store").symlink_to(sentinel)
    elif case == "pycache-file":
        (legacy / "__pycache__").write_bytes(b"not a directory")
    elif case == "pycache-symlink":
        (legacy / "__pycache__").symlink_to(sentinel)
    elif case == "pyc-directory":
        (legacy / "module.pyc").mkdir()
    elif case == "pyc-symlink":
        (legacy / "module.pyc").symlink_to(sentinel)
    else:
        cache = legacy / "__pycache__"
        cache.mkdir()
        (cache / "note.txt").write_bytes(b"not bytecode")

    plan = plan_safe_cache_removal(repo=repo, legacy_root=legacy)

    assert (relative, reason) in {
        (entry.relative_path, entry.reason) for entry in plan.skipped
    }
    assert relative not in {item.relative_path for item in plan.items}
    assert plan.eligible is False


def test_cleanup_plan_treats_arbitrary_empty_directories_as_blockers(
    tmp_path: Path,
) -> None:
    repo, legacy = _make_repo(tmp_path)
    (legacy / "api").mkdir()

    plan = plan_safe_cache_removal(repo=repo, legacy_root=legacy)

    assert plan.items == ()
    assert [(entry.relative_path, entry.reason) for entry in plan.skipped] == [
        ("jarvis/api", "non-cache-directory")
    ]
    assert plan.eligible is False


def test_real_root_mirror_is_an_exact_blocked_zero_removal_plan(
    tmp_path: Path,
) -> None:
    repo, legacy = _make_repo(tmp_path)
    safe = legacy / ".DS_Store"
    safe.write_bytes(b"finder")
    for name in REAL_LEGACY_EMPTY_DIRECTORIES:
        (legacy / name).mkdir()

    plan = plan_safe_cache_removal(repo=repo, legacy_root=legacy)

    assert [(item.relative_path, item.kind) for item in plan.items] == [
        ("jarvis/.DS_Store", "ds_store")
    ]
    assert [(item.relative_path, item.reason) for item in plan.skipped] == [
        (f"jarvis/{name}", "non-cache-directory")
        for name in REAL_LEGACY_EMPTY_DIRECTORIES
    ]
    assert plan.eligible is False
    with pytest.raises(UnsafeCleanupPlan):
        apply_safe_cache_removal(plan)
    assert safe.read_bytes() == b"finder"
    assert all((legacy / name).is_dir() for name in REAL_LEGACY_EMPTY_DIRECTORIES)


def test_apply_is_all_or_nothing_when_source_or_symlink_blocker_exists(
    tmp_path: Path,
) -> None:
    repo, legacy = _make_repo(tmp_path)
    safe = legacy / ".DS_Store"
    source = legacy / "keep.py"
    safe.write_bytes(b"finder")
    source.write_text("keep = True\n", encoding="utf-8")
    (legacy / "alias").symlink_to(source)
    plan = plan_safe_cache_removal(repo=repo, legacy_root=legacy)

    with pytest.raises(UnsafeCleanupPlan):
        apply_safe_cache_removal(plan)

    assert safe.read_bytes() == b"finder"
    assert source.read_text(encoding="utf-8") == "keep = True\n"
    assert (legacy / "alias").is_symlink()


@pytest.mark.parametrize("late_blocker", ["source", "symlink", "directory"])
def test_apply_is_zero_removal_when_safe_plan_gains_late_blocker(
    tmp_path: Path, late_blocker: str
) -> None:
    repo, legacy = _make_repo(tmp_path)
    first = legacy / "a.pyc"
    second = legacy / "z.pyc"
    first.write_bytes(b"a")
    second.write_bytes(b"z")
    plan = plan_safe_cache_removal(repo=repo, legacy_root=legacy)
    if late_blocker == "source":
        (legacy / "keep.py").write_bytes(b"source")
    elif late_blocker == "symlink":
        (legacy / "alias").symlink_to(first)
    else:
        (legacy / "api").mkdir()

    with pytest.raises(UnsafeCleanupPlan):
        apply_safe_cache_removal(plan)

    assert first.read_bytes() == b"a"
    assert second.read_bytes() == b"z"


def test_apply_detects_later_target_drift_before_deleting_earlier_target(
    tmp_path: Path,
) -> None:
    repo, legacy = _make_repo(tmp_path)
    first = legacy / "a.pyc"
    later = legacy / "z.pyc"
    first.write_bytes(b"a")
    later.write_bytes(b"z")
    plan = plan_safe_cache_removal(repo=repo, legacy_root=legacy)
    later.unlink()
    later.write_bytes(b"replacement")

    with pytest.raises(UnsafeCleanupPlan):
        apply_safe_cache_removal(plan)

    assert first.read_bytes() == b"a"
    assert later.read_bytes() == b"replacement"


def test_apply_rejects_real_root_rename_substitution_without_touching_substitute(
    tmp_path: Path,
) -> None:
    repo, legacy = _make_repo(tmp_path)
    original_cache = legacy / ".DS_Store"
    original_cache.write_bytes(b"original")
    plan = plan_safe_cache_removal(repo=repo, legacy_root=legacy)
    moved = repo / "jarvis-original"
    legacy.rename(moved)
    legacy.mkdir()
    substitute = legacy / ".DS_Store"
    substitute.write_bytes(b"substitute")

    with pytest.raises(UnsafeCleanupTarget, match="identity"):
        apply_safe_cache_removal(plan)

    assert (moved / ".DS_Store").read_bytes() == b"original"
    assert substitute.read_bytes() == b"substitute"


def test_apply_restores_whole_root_when_blocker_arrives_inside_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, legacy = _make_repo(tmp_path)
    first = legacy / "a.pyc"
    second = legacy / "z.pyc"
    first.write_bytes(b"a")
    second.write_bytes(b"z")
    plan = plan_safe_cache_removal(repo=repo, legacy_root=legacy)
    root_identity = (legacy.lstat().st_dev, legacy.lstat().st_ino)
    real_stage = checkout_hygiene._rename_exclusive
    stage_calls = 0
    physical_deletes: list[str] = []

    def racing_stage(
        source_descriptor: int,
        source_name: str,
        destination_descriptor: int,
        destination_name: str,
    ) -> None:
        nonlocal stage_calls
        stage_calls += 1
        if stage_calls == 1:
            (legacy / "keep.py").write_bytes(b"late blocker")
        real_stage(
            source_descriptor,
            source_name,
            destination_descriptor,
            destination_name,
        )

    monkeypatch.setattr(checkout_hygiene, "_rename_exclusive", racing_stage)
    monkeypatch.setattr(
        checkout_hygiene.os,
        "unlink",
        lambda *args, **kwargs: physical_deletes.append("unlink"),
    )
    monkeypatch.setattr(
        checkout_hygiene.os,
        "rmdir",
        lambda *args, **kwargs: physical_deletes.append("rmdir"),
    )

    with pytest.raises(UnsafeCleanupPlan, match="stable cleanup plan"):
        apply_safe_cache_removal(plan)

    assert stage_calls == 2
    assert physical_deletes == []
    assert first.read_bytes() == b"a"
    assert second.read_bytes() == b"z"
    assert (legacy / "keep.py").read_bytes() == b"late blocker"
    assert (legacy.lstat().st_dev, legacy.lstat().st_ino) == root_identity


def test_apply_quarantines_then_restores_last_moment_root_substitute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, legacy = _make_repo(tmp_path)
    (legacy / ".DS_Store").write_bytes(b"original")
    plan = plan_safe_cache_removal(repo=repo, legacy_root=legacy)
    moved = repo / "jarvis-original"
    real_stage = checkout_hygiene._rename_exclusive
    stage_calls = 0

    def racing_stage(
        source_descriptor: int,
        source_name: str,
        destination_descriptor: int,
        destination_name: str,
    ) -> None:
        nonlocal stage_calls
        stage_calls += 1
        if stage_calls == 1:
            legacy.rename(moved)
            legacy.mkdir()
            (legacy / ".DS_Store").write_bytes(b"substitute")
        real_stage(
            source_descriptor,
            source_name,
            destination_descriptor,
            destination_name,
        )

    monkeypatch.setattr(checkout_hygiene, "_rename_exclusive", racing_stage)

    with pytest.raises(UnsafeCleanupPlan, match="stable cleanup plan"):
        apply_safe_cache_removal(plan)

    assert stage_calls == 2
    assert (moved / ".DS_Store").read_bytes() == b"original"
    assert (legacy / ".DS_Store").read_bytes() == b"substitute"


def test_apply_exclusive_stage_preserves_destination_competitor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, legacy = _make_repo(tmp_path)
    (legacy / ".DS_Store").write_bytes(b"original")
    plan = plan_safe_cache_removal(repo=repo, legacy_root=legacy)
    real_stage = checkout_hygiene._rename_exclusive
    competitor: Path | None = None

    def racing_stage(
        source_descriptor: int,
        source_name: str,
        destination_descriptor: int,
        destination_name: str,
    ) -> None:
        nonlocal competitor
        competitor = repo.parent / destination_name
        competitor.mkdir()
        (competitor / "marker").write_bytes(b"competitor")
        real_stage(
            source_descriptor,
            source_name,
            destination_descriptor,
            destination_name,
        )

    monkeypatch.setattr(checkout_hygiene, "_rename_exclusive", racing_stage)

    with pytest.raises(UnsafeCleanupPlan, match="exclusively quarantine"):
        apply_safe_cache_removal(plan)

    assert (legacy / ".DS_Store").read_bytes() == b"original"
    assert competitor is not None
    assert (competitor / "marker").read_bytes() == b"competitor"


def test_apply_refuses_when_exclusive_stage_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, legacy = _make_repo(tmp_path)
    (legacy / ".DS_Store").write_bytes(b"original")
    plan = plan_safe_cache_removal(repo=repo, legacy_root=legacy)
    before = _snapshot(legacy)

    def unavailable(*args: object, **kwargs: object) -> None:
        raise OSError("exclusive rename unavailable")

    monkeypatch.setattr(checkout_hygiene, "_rename_exclusive", unavailable)

    with pytest.raises(UnsafeCleanupPlan, match="exclusively quarantine"):
        apply_safe_cache_removal(plan)

    assert _snapshot(legacy) == before


def test_apply_rechecks_root_at_leaf_deletion_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, legacy = _make_repo(tmp_path)
    cache = legacy / ".DS_Store"
    cache.write_bytes(b"original")
    plan = plan_safe_cache_removal(repo=repo, legacy_root=legacy)
    moved = repo / "jarvis-original"
    real_stat = checkout_hygiene.os.stat
    target_stats = 0
    injection_fired = False

    def racing_stat(path: object, *args: object, **kwargs: object) -> os.stat_result:
        nonlocal target_stats, injection_fired
        result = real_stat(path, *args, **kwargs)
        if path == ".DS_Store" and kwargs.get("dir_fd") is not None:
            target_stats += 1
            if target_stats == 2:
                legacy.rename(moved)
                legacy.mkdir()
                (legacy / ".DS_Store").write_bytes(b"substitute")
                injection_fired = True
        return result

    monkeypatch.setattr(checkout_hygiene.os, "stat", racing_stat)

    with pytest.raises((UnsafeCleanupPlan, UnsafeCleanupTarget)):
        apply_safe_cache_removal(plan)

    assert injection_fired is True
    assert (moved / ".DS_Store").read_bytes() == b"original"
    assert (legacy / ".DS_Store").read_bytes() == b"substitute"


def test_apply_rechecks_nested_parent_at_leaf_deletion_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, legacy = _make_repo(tmp_path)
    cache_dir = legacy / "__pycache__"
    cache_dir.mkdir()
    cache = cache_dir / "module.pyc"
    cache.write_bytes(b"original")
    plan = plan_safe_cache_removal(repo=repo, legacy_root=legacy)
    moved = legacy / "cache-original"
    real_stat = checkout_hygiene.os.stat
    target_stats = 0
    injection_fired = False

    def racing_stat(path: object, *args: object, **kwargs: object) -> os.stat_result:
        nonlocal target_stats, injection_fired
        result = real_stat(path, *args, **kwargs)
        if path == "module.pyc" and kwargs.get("dir_fd") is not None:
            target_stats += 1
            if target_stats == 2:
                cache_dir.rename(moved)
                cache_dir.mkdir()
                (cache_dir / "module.pyc").write_bytes(b"substitute")
                injection_fired = True
        return result

    monkeypatch.setattr(checkout_hygiene.os, "stat", racing_stat)

    with pytest.raises(UnsafeCleanupPlan):
        apply_safe_cache_removal(plan)

    assert injection_fired is True
    assert (moved / "module.pyc").read_bytes() == b"original"
    assert (cache_dir / "module.pyc").read_bytes() == b"substitute"


def test_apply_rechecks_leaf_type_at_unlink_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, legacy = _make_repo(tmp_path)
    cache = legacy / "module.pyc"
    cache.write_bytes(b"original")
    sentinel = repo / "outside"
    sentinel.write_bytes(b"outside")
    plan = plan_safe_cache_removal(repo=repo, legacy_root=legacy)
    real_stat = checkout_hygiene.os.stat
    target_stats = 0
    injection_fired = False

    def racing_stat(path: object, *args: object, **kwargs: object) -> os.stat_result:
        nonlocal target_stats, injection_fired
        result = real_stat(path, *args, **kwargs)
        if path == "module.pyc" and kwargs.get("dir_fd") is not None:
            target_stats += 1
            if target_stats == 2:
                cache.unlink()
                cache.symlink_to(sentinel)
                injection_fired = True
        return result

    monkeypatch.setattr(checkout_hygiene.os, "stat", racing_stat)

    with pytest.raises(UnsafeCleanupPlan):
        apply_safe_cache_removal(plan)

    assert injection_fired is True
    assert cache.is_symlink()
    assert sentinel.read_bytes() == b"outside"


def test_apply_has_no_leaf_unlink_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, legacy = _make_repo(tmp_path)
    cache = legacy / ".DS_Store"
    cache.write_bytes(b"original")
    sentinel = repo / "outside"
    sentinel.write_bytes(b"outside")
    plan = plan_safe_cache_removal(repo=repo, legacy_root=legacy)
    real_stat = checkout_hygiene.os.stat
    target_stats = 0
    injection_fired = False

    def racing_stat(path: object, *args: object, **kwargs: object) -> os.stat_result:
        nonlocal target_stats, injection_fired
        result = real_stat(path, *args, **kwargs)
        if path == ".DS_Store" and kwargs.get("dir_fd") is not None:
            target_stats += 1
            if target_stats == 4:
                cache.unlink()
                cache.symlink_to(sentinel)
                injection_fired = True
        return result

    monkeypatch.setattr(checkout_hygiene.os, "stat", racing_stat)

    result = apply_safe_cache_removal(plan)

    assert injection_fired is False
    assert result.quarantine_path.is_dir()
    assert sentinel.read_bytes() == b"outside"


def test_apply_has_no_cache_directory_rmdir_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, legacy = _make_repo(tmp_path)
    cache_dir = legacy / "__pycache__"
    cache_dir.mkdir()
    plan = plan_safe_cache_removal(repo=repo, legacy_root=legacy)
    moved = legacy / "cache-original"
    real_stat = checkout_hygiene.os.stat
    target_stats = 0
    injection_fired = False

    def racing_stat(path: object, *args: object, **kwargs: object) -> os.stat_result:
        nonlocal target_stats, injection_fired
        result = real_stat(path, *args, **kwargs)
        if path == "__pycache__" and kwargs.get("dir_fd") is not None:
            target_stats += 1
            if target_stats == 4:
                cache_dir.rename(moved)
                cache_dir.mkdir()
                injection_fired = True
        return result

    monkeypatch.setattr(checkout_hygiene.os, "stat", racing_stat)

    result = apply_safe_cache_removal(plan)

    assert injection_fired is False
    assert result.quarantine_path.is_dir()


def test_apply_has_no_top_level_root_rmdir_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, legacy = _make_repo(tmp_path)
    plan = plan_safe_cache_removal(repo=repo, legacy_root=legacy)
    moved = repo / "jarvis-original"
    real_stat = checkout_hygiene.os.stat
    root_stats = 0
    injection_fired = False

    def racing_stat(path: object, *args: object, **kwargs: object) -> os.stat_result:
        nonlocal root_stats, injection_fired
        result = real_stat(path, *args, **kwargs)
        if path == "jarvis" and kwargs.get("dir_fd") is not None:
            root_stats += 1
            if root_stats == 3:
                legacy.rename(moved)
                legacy.mkdir()
                injection_fired = True
        return result

    monkeypatch.setattr(checkout_hygiene.os, "stat", racing_stat)

    result = apply_safe_cache_removal(plan)

    assert injection_fired is False
    assert result.quarantine_path.is_dir()


def test_apply_never_calls_unlink_for_quarantined_leaf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, legacy = _make_repo(tmp_path)
    cache = legacy / ".DS_Store"
    cache.write_bytes(b"original")
    sentinel = repo / "outside"
    sentinel.write_bytes(b"outside")
    plan = plan_safe_cache_removal(repo=repo, legacy_root=legacy)
    real_unlink = checkout_hygiene.os.unlink
    injection_fired = False

    def racing_unlink(path: object, *args: object, **kwargs: object) -> None:
        nonlocal injection_fired
        if not injection_fired:
            if os.path.lexists(cache):
                real_unlink(cache)
            cache.symlink_to(sentinel)
            injection_fired = True
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(checkout_hygiene.os, "unlink", racing_unlink)

    result = apply_safe_cache_removal(plan)

    assert injection_fired is False
    assert result.quarantine_path.is_dir()
    assert sentinel.read_bytes() == b"outside"


def test_apply_whole_root_stage_avoids_unlink_callback_blocker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, legacy = _make_repo(tmp_path)
    first = legacy / "a.pyc"
    second = legacy / "z.pyc"
    first.write_bytes(b"a")
    second.write_bytes(b"z")
    plan = plan_safe_cache_removal(repo=repo, legacy_root=legacy)
    real_unlink = checkout_hygiene.os.unlink
    injection_fired = False

    def blocker_unlink(path: object, *args: object, **kwargs: object) -> None:
        nonlocal injection_fired
        if not injection_fired:
            (legacy / "keep.py").write_bytes(b"late blocker")
            injection_fired = True
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(checkout_hygiene.os, "unlink", blocker_unlink)

    result = apply_safe_cache_removal(plan)

    assert injection_fired is False
    assert (result.quarantine_path / "a.pyc").read_bytes() == b"a"
    assert (result.quarantine_path / "z.pyc").read_bytes() == b"z"


def test_apply_whole_root_stage_avoids_sequential_unlink_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, legacy = _make_repo(tmp_path)
    first = legacy / "a.pyc"
    second = legacy / "z.pyc"
    first.write_bytes(b"a")
    second.write_bytes(b"z")
    plan = plan_safe_cache_removal(repo=repo, legacy_root=legacy)
    real_unlink = checkout_hygiene.os.unlink
    unlink_calls = 0
    injection_fired = False

    def failing_unlink(path: object, *args: object, **kwargs: object) -> None:
        nonlocal unlink_calls, injection_fired
        unlink_calls += 1
        if unlink_calls == 2:
            injection_fired = True
            raise OSError("injected second unlink failure")
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(checkout_hygiene.os, "unlink", failing_unlink)

    result = apply_safe_cache_removal(plan)

    assert injection_fired is False
    assert (result.quarantine_path / "a.pyc").read_bytes() == b"a"
    assert (result.quarantine_path / "z.pyc").read_bytes() == b"z"


def test_apply_never_calls_rmdir_for_quarantined_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, legacy = _make_repo(tmp_path)
    cache_dir = legacy / "__pycache__"
    cache_dir.mkdir()
    moved = legacy / "cache-original"
    plan = plan_safe_cache_removal(repo=repo, legacy_root=legacy)
    real_rmdir = checkout_hygiene.os.rmdir
    injection_fired = False

    def racing_rmdir(path: object, *args: object, **kwargs: object) -> None:
        nonlocal injection_fired
        if not injection_fired:
            if cache_dir.is_dir():
                cache_dir.rename(moved)
            cache_dir.mkdir()
            injection_fired = True
        real_rmdir(path, *args, **kwargs)

    monkeypatch.setattr(checkout_hygiene.os, "rmdir", racing_rmdir)

    result = apply_safe_cache_removal(plan)

    assert injection_fired is False
    assert (result.quarantine_path / "__pycache__").is_dir()


def test_apply_never_calls_rmdir_for_quarantined_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, legacy = _make_repo(tmp_path)
    moved = repo / "jarvis-original"
    plan = plan_safe_cache_removal(repo=repo, legacy_root=legacy)
    real_rmdir = checkout_hygiene.os.rmdir
    injection_fired = False

    def racing_rmdir(path: object, *args: object, **kwargs: object) -> None:
        nonlocal injection_fired
        if not injection_fired:
            if legacy.is_dir():
                legacy.rename(moved)
            legacy.mkdir()
            injection_fired = True
        real_rmdir(path, *args, **kwargs)

    monkeypatch.setattr(checkout_hygiene.os, "rmdir", racing_rmdir)

    result = apply_safe_cache_removal(plan)

    assert injection_fired is False
    assert result.quarantine_path.is_dir()


def test_apply_whole_root_stage_avoids_sequential_rmdir_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, legacy = _make_repo(tmp_path)
    outer = legacy / "__pycache__"
    inner = outer / "__pycache__"
    inner.mkdir(parents=True)
    plan = plan_safe_cache_removal(repo=repo, legacy_root=legacy)
    real_rmdir = checkout_hygiene.os.rmdir
    rmdir_calls = 0
    injection_fired = False

    def failing_rmdir(path: object, *args: object, **kwargs: object) -> None:
        nonlocal rmdir_calls, injection_fired
        rmdir_calls += 1
        if rmdir_calls == 2:
            injection_fired = True
            raise OSError("injected second rmdir failure")
        real_rmdir(path, *args, **kwargs)

    monkeypatch.setattr(checkout_hygiene.os, "rmdir", failing_rmdir)

    result = apply_safe_cache_removal(plan)

    assert injection_fired is False
    assert (result.quarantine_path / "__pycache__" / "__pycache__").is_dir()


@pytest.mark.parametrize("replacement", ["inode", "symlink"])
def test_apply_rejects_leaf_inode_or_type_swap(tmp_path: Path, replacement: str) -> None:
    repo, legacy = _make_repo(tmp_path)
    cache = legacy / "module.pyc"
    cache.write_bytes(b"planned")
    plan = plan_safe_cache_removal(repo=repo, legacy_root=legacy)
    cache.unlink()
    if replacement == "inode":
        cache.write_bytes(b"replacement")
    else:
        sentinel = repo / "outside"
        sentinel.write_bytes(b"outside")
        cache.symlink_to(sentinel)

    with pytest.raises(UnsafeCleanupPlan):
        apply_safe_cache_removal(plan)

    assert cache.exists() or cache.is_symlink()
    if replacement == "symlink":
        assert (repo / "outside").read_bytes() == b"outside"


def test_apply_preserves_quarantine_bytes_without_unlink_or_rmdir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, legacy = _make_repo(tmp_path)
    outer = legacy / "__pycache__"
    inner = outer / "__pycache__"
    inner.mkdir(parents=True)
    (outer / "outer.pyc").write_bytes(b"outer")
    (inner / "inner.pyc").write_bytes(b"inner")
    plan = plan_safe_cache_removal(repo=repo, legacy_root=legacy)
    operations: list[tuple[str, str]] = []
    real_unlink = os.unlink
    real_rmdir = os.rmdir

    def recording_unlink(path: str, *args: object, **kwargs: object) -> None:
        operations.append(("unlink", os.fspath(path)))
        real_unlink(path, *args, **kwargs)

    def recording_rmdir(path: str, *args: object, **kwargs: object) -> None:
        operations.append(("rmdir", os.fspath(path)))
        real_rmdir(path, *args, **kwargs)

    monkeypatch.setattr(os, "unlink", recording_unlink)
    monkeypatch.setattr(os, "rmdir", recording_rmdir)

    result = apply_safe_cache_removal(plan)

    assert operations == []
    assert (result.quarantine_path / "__pycache__" / "outer.pyc").read_bytes() == b"outer"
    assert (
        result.quarantine_path / "__pycache__" / "__pycache__" / "inner.pyc"
    ).read_bytes() == b"inner"
    assert result.removed[-1] == "jarvis"
    assert not legacy.exists()


def test_apply_removes_top_level_jarvis_only_when_exact_root_is_empty(
    tmp_path: Path,
) -> None:
    safe_repo, safe_legacy = _make_repo(tmp_path / "safe")
    (safe_legacy / ".DS_Store").write_bytes(b"finder")
    safe_plan = plan_safe_cache_removal(repo=safe_repo, legacy_root=safe_legacy)

    result = apply_safe_cache_removal(safe_plan)

    assert result.removed == ("jarvis/.DS_Store", "jarvis")
    assert not safe_legacy.exists()

    blocked_repo, blocked_legacy = _make_repo(tmp_path / "blocked")
    (blocked_legacy / ".DS_Store").write_bytes(b"finder")
    (blocked_legacy / "api").mkdir()
    blocked_plan = plan_safe_cache_removal(repo=blocked_repo, legacy_root=blocked_legacy)
    with pytest.raises(UnsafeCleanupPlan):
        apply_safe_cache_removal(blocked_plan)
    assert (blocked_legacy / ".DS_Store").exists()
    assert (blocked_legacy / "api").is_dir()


def test_plan_mode_never_mutates_the_tree(tmp_path: Path) -> None:
    repo, legacy = _make_repo(tmp_path)
    (legacy / ".DS_Store").write_bytes(b"finder")
    (legacy / "keep.py").write_text("keep = True\n", encoding="utf-8")
    before = _snapshot(legacy)

    plan_safe_cache_removal(repo=repo, legacy_root=legacy)

    assert _snapshot(legacy) == before


def test_report_writer_refuses_existing_output_without_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, legacy = _make_repo(tmp_path)
    (legacy / ".DS_Store").write_bytes(b"finder")
    evidence = _configure_evidence(tmp_path, monkeypatch, repo=repo)
    output = evidence / "report.json"
    output.write_bytes(b"history")
    report = build_hygiene_report(
        plan_safe_cache_removal(repo=repo, legacy_root=legacy), mode="plan"
    )

    with pytest.raises(FileExistsError):
        write_hygiene_report_exclusive(output, report, repo=repo)

    assert output.read_bytes() == b"history"
    assert (legacy / ".DS_Store").exists()


def test_run_reserves_output_with_real_exclusive_open_before_deletion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, legacy = _make_repo(tmp_path)
    safe = legacy / ".DS_Store"
    safe.write_bytes(b"finder")
    evidence = _configure_evidence(tmp_path, monkeypatch, repo=repo)
    output = evidence / "report.json"
    real_open = checkout_hygiene.os.open
    injection_fired = False

    def racing_open(
        path: object, flags: int, mode: int = 0o777, **kwargs: object
    ) -> int:
        nonlocal injection_fired
        if (
            path == "report.json"
            and flags & os.O_EXCL
            and kwargs.get("dir_fd") is not None
            and not injection_fired
        ):
            competitor = real_open(path, flags, 0o600, **kwargs)
            try:
                os.write(competitor, b"history")
                os.fsync(competitor)
            finally:
                os.close(competitor)
            injection_fired = True
        return real_open(path, flags, mode, **kwargs)

    monkeypatch.setattr(checkout_hygiene.os, "open", racing_open)

    with pytest.raises(FileExistsError):
        run_checkout_hygiene(
            repo=repo,
            legacy_root=legacy,
            output=output,
            apply_safe_cache=True,
        )

    assert injection_fired is True
    assert output.read_bytes() == b"history"
    assert safe.read_bytes() == b"finder"


def test_run_cleans_failed_post_create_reservation_before_deletion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, legacy = _make_repo(tmp_path)
    safe = legacy / ".DS_Store"
    safe.write_bytes(b"finder")
    evidence = _configure_evidence(tmp_path, monkeypatch, repo=repo)
    output = evidence / "report.json"
    injection_fired = False

    def failing_fchmod(descriptor: int, mode: int) -> None:
        nonlocal injection_fired
        injection_fired = True
        raise OSError(f"injected fchmod failure for {mode:o}")

    monkeypatch.setattr(checkout_hygiene.os, "fchmod", failing_fchmod)

    with pytest.raises(UnsafeReportOutput):
        run_checkout_hygiene(
            repo=repo,
            legacy_root=legacy,
            output=output,
            apply_safe_cache=True,
        )

    assert injection_fired is True
    assert safe.read_bytes() == b"finder"
    assert not os.path.lexists(output)


@pytest.mark.parametrize("failed_fsync", ["file", "parent"])
def test_run_fsync_failure_happens_before_first_deletion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed_fsync: str
) -> None:
    repo, legacy = _make_repo(tmp_path)
    safe = legacy / ".DS_Store"
    safe.write_bytes(b"finder")
    evidence = _configure_evidence(tmp_path, monkeypatch, repo=repo)
    output = evidence / "report.json"
    real_fsync = checkout_hygiene.os.fsync
    injection_fired = False

    def failing_fsync(descriptor: int) -> None:
        nonlocal injection_fired
        kind = "parent" if stat.S_ISDIR(os.fstat(descriptor).st_mode) else "file"
        if kind == failed_fsync and not injection_fired:
            injection_fired = True
            raise OSError(f"injected {kind} fsync failure")
        real_fsync(descriptor)

    monkeypatch.setattr(checkout_hygiene.os, "fsync", failing_fsync)

    with pytest.raises(OSError, match="injected"):
        run_checkout_hygiene(
            repo=repo,
            legacy_root=legacy,
            output=output,
            apply_safe_cache=True,
        )

    assert injection_fired is True
    assert safe.read_bytes() == b"finder"
    assert not output.exists()


@pytest.mark.parametrize(
    "failure_point", ["write", "file-fsync", "parent-fsync", "file-close"]
)
def test_run_preserves_durable_prepared_report_when_final_publication_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    repo, legacy = _make_repo(tmp_path)
    (legacy / ".DS_Store").write_bytes(b"finder")
    evidence = _configure_evidence(tmp_path, monkeypatch, repo=repo)
    output = evidence / "report.json"
    real_open = checkout_hygiene.os.open
    real_write = checkout_hygiene.os.write
    real_fsync = checkout_hygiene.os.fsync
    real_close = checkout_hygiene.os.close
    report_descriptor: int | None = None
    report_parent_descriptor: int | None = None
    injection_fired = False

    def recording_open(
        path: object, flags: int, mode: int = 0o777, **kwargs: object
    ) -> int:
        nonlocal report_descriptor, report_parent_descriptor
        descriptor = real_open(path, flags, mode, **kwargs)
        if path == "report.json" and flags & os.O_EXCL:
            report_descriptor = descriptor
            parent = kwargs.get("dir_fd")
            assert isinstance(parent, int)
            report_parent_descriptor = parent
        return descriptor

    def failing_write(descriptor: int, payload: bytes) -> int:
        nonlocal injection_fired
        if failure_point == "write" and descriptor == report_descriptor:
            injection_fired = True
            raise OSError("injected final report write failure")
        return real_write(descriptor, payload)

    def failing_fsync(descriptor: int) -> None:
        nonlocal injection_fired
        if failure_point == "parent-fsync" and descriptor == report_parent_descriptor:
            injection_fired = True
            raise OSError("injected final parent fsync failure")
        if failure_point == "file-fsync" and descriptor == report_descriptor:
            injection_fired = True
            raise OSError("injected final file fsync failure")
        real_fsync(descriptor)

    def failing_close(descriptor: int) -> None:
        nonlocal injection_fired
        real_close(descriptor)
        if (
            failure_point == "file-close"
            and descriptor == report_descriptor
            and not injection_fired
        ):
            injection_fired = True
            raise OSError("injected final report close failure")

    monkeypatch.setattr(checkout_hygiene.os, "open", recording_open)
    monkeypatch.setattr(checkout_hygiene.os, "write", failing_write)
    monkeypatch.setattr(checkout_hygiene.os, "fsync", failing_fsync)
    monkeypatch.setattr(checkout_hygiene.os, "close", failing_close)

    with pytest.raises(OSError, match="injected final"):
        run_checkout_hygiene(
            repo=repo,
            legacy_root=legacy,
            output=output,
            apply_safe_cache=True,
        )

    assert injection_fired is True
    assert not legacy.exists()
    payload = json.loads(
        output.with_name(f"{output.name}.intent").read_text(encoding="utf-8")
    )
    assert payload["status"] == "prepared"
    assert payload["removed"] == []


def test_run_rejects_reserved_output_replacement_after_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, legacy = _make_repo(tmp_path)
    (legacy / ".DS_Store").write_bytes(b"finder")
    evidence = _configure_evidence(tmp_path, monkeypatch, repo=repo)
    output = evidence / "report.json"
    displaced = evidence / "prepared-displaced.json"
    real_apply = checkout_hygiene.apply_safe_cache_removal
    injection_fired = False

    def racing_apply(plan: object, **kwargs: object) -> object:
        nonlocal injection_fired
        result = real_apply(plan, **kwargs)  # type: ignore[arg-type]
        output.rename(displaced)
        output.write_bytes(b"competitor")
        injection_fired = True
        return result

    monkeypatch.setattr(checkout_hygiene, "apply_safe_cache_removal", racing_apply)

    with pytest.raises(UnsafeReportOutput):
        run_checkout_hygiene(
            repo=repo,
            legacy_root=legacy,
            output=output,
            apply_safe_cache=True,
        )

    assert injection_fired is True
    assert output.read_bytes() == b"competitor"
    assert json.loads(
        output.with_name(f"{output.name}.intent").read_text(encoding="utf-8")
    )["status"] == "prepared"


def test_run_rejects_reserved_output_parent_replacement_after_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, legacy = _make_repo(tmp_path)
    (legacy / ".DS_Store").write_bytes(b"finder")
    evidence = _configure_evidence(tmp_path, monkeypatch, repo=repo)
    reports = evidence / "reports"
    reports.mkdir()
    moved = evidence / "reports-original"
    output = reports / "report.json"
    real_apply = checkout_hygiene.apply_safe_cache_removal
    injection_fired = False

    def racing_apply(plan: object, **kwargs: object) -> object:
        nonlocal injection_fired
        result = real_apply(plan, **kwargs)  # type: ignore[arg-type]
        reports.rename(moved)
        reports.mkdir()
        (reports / "report.json").write_bytes(b"competitor")
        injection_fired = True
        return result

    monkeypatch.setattr(checkout_hygiene, "apply_safe_cache_removal", racing_apply)

    with pytest.raises(UnsafeReportOutput):
        run_checkout_hygiene(
            repo=repo,
            legacy_root=legacy,
            output=output,
            apply_safe_cache=True,
        )

    assert injection_fired is True
    assert output.read_bytes() == b"competitor"
    assert json.loads((moved / "report.json.intent").read_text(encoding="utf-8"))[
        "status"
    ] == "prepared"


@pytest.mark.parametrize("replacement", ["leaf", "parent"])
def test_run_rejects_output_replacement_inside_final_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement: str,
) -> None:
    repo, legacy = _make_repo(tmp_path)
    (legacy / ".DS_Store").write_bytes(b"finder")
    evidence = _configure_evidence(tmp_path, monkeypatch, repo=repo)
    reports = evidence / "reports"
    reports.mkdir()
    moved_parent = evidence / "reports-original"
    output = reports / "report.json"
    displaced = reports / "completion-displaced.json"
    real_open = checkout_hygiene.os.open
    real_write = checkout_hygiene.os.write
    completion_descriptor: int | None = None
    injection_fired = False

    def recording_open(
        path: object, flags: int, mode: int = 0o777, **kwargs: object
    ) -> int:
        nonlocal completion_descriptor
        descriptor = real_open(path, flags, mode, **kwargs)
        if path == "report.json" and flags & os.O_EXCL:
            completion_descriptor = descriptor
        return descriptor

    def racing_write(descriptor: int, payload: bytes) -> int:
        nonlocal injection_fired
        if descriptor == completion_descriptor and not injection_fired:
            if replacement == "leaf":
                output.rename(displaced)
                output.write_bytes(b"competitor")
            else:
                reports.rename(moved_parent)
                reports.mkdir()
                output.write_bytes(b"competitor")
            injection_fired = True
        return real_write(descriptor, payload)

    monkeypatch.setattr(checkout_hygiene.os, "open", recording_open)
    monkeypatch.setattr(checkout_hygiene.os, "write", racing_write)

    with pytest.raises(UnsafeReportOutput):
        run_checkout_hygiene(
            repo=repo,
            legacy_root=legacy,
            output=output,
            apply_safe_cache=True,
        )

    assert injection_fired is True
    assert output.read_bytes() == b"competitor"
    intent = (
        reports / "report.json.intent"
        if replacement == "leaf"
        else moved_parent / "report.json.intent"
    )
    assert json.loads(intent.read_text(encoding="utf-8"))["status"] == "prepared"


def test_run_keeps_immutable_intent_when_staged_root_is_restored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, legacy = _make_repo(tmp_path)
    cache = legacy / ".DS_Store"
    cache.write_bytes(b"finder")
    evidence = _configure_evidence(tmp_path, monkeypatch, repo=repo)
    output = evidence / "report.json"
    real_stage = checkout_hygiene._rename_exclusive
    stage_calls = 0

    def racing_stage(
        source_descriptor: int,
        source_name: str,
        destination_descriptor: int,
        destination_name: str,
    ) -> None:
        nonlocal stage_calls
        if source_name == "jarvis":
            stage_calls += 1
            if stage_calls == 1:
                (legacy / "keep.py").write_bytes(b"late blocker")
        real_stage(
            source_descriptor,
            source_name,
            destination_descriptor,
            destination_name,
        )

    monkeypatch.setattr(checkout_hygiene, "_rename_exclusive", racing_stage)

    with pytest.raises(UnsafeCleanupPlan, match="stable cleanup plan"):
        run_checkout_hygiene(
            repo=repo,
            legacy_root=legacy,
            output=output,
            apply_safe_cache=True,
        )

    intent = output.with_name(f"{output.name}.intent")
    payload = json.loads(intent.read_text(encoding="utf-8"))
    assert payload["status"] == "prepared"
    assert payload["completion_path"] == str(output)
    assert cache.read_bytes() == b"finder"
    assert (legacy / "keep.py").read_bytes() == b"late blocker"
    assert not os.path.lexists(output)


def test_report_first_fstat_and_secondary_close_failure_is_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, legacy = _make_repo(tmp_path)
    (legacy / ".DS_Store").write_bytes(b"finder")
    evidence = _configure_evidence(tmp_path, monkeypatch, repo=repo)
    output = evidence / "report.json"
    report = build_hygiene_report(
        plan_safe_cache_removal(repo=repo, legacy_root=legacy), mode="plan"
    )
    real_open = checkout_hygiene.os.open
    real_fstat = checkout_hygiene.os.fstat
    real_close = checkout_hygiene.os.close
    report_descriptor: int | None = None
    fstat_failed = False
    close_failed = False

    def recording_open(
        path: object, flags: int, mode: int = 0o777, **kwargs: object
    ) -> int:
        nonlocal report_descriptor
        descriptor = real_open(path, flags, mode, **kwargs)
        if path == "report.json" and flags & os.O_EXCL:
            report_descriptor = descriptor
        return descriptor

    def failing_fstat(descriptor: int) -> os.stat_result:
        nonlocal fstat_failed
        if descriptor == report_descriptor and not fstat_failed:
            fstat_failed = True
            raise OSError("primary first report fstat failure")
        return real_fstat(descriptor)

    def failing_close(descriptor: int) -> None:
        nonlocal close_failed
        real_close(descriptor)
        if descriptor == report_descriptor and not close_failed:
            close_failed = True
            raise OSError("secondary report close failure")

    monkeypatch.setattr(checkout_hygiene.os, "open", recording_open)
    monkeypatch.setattr(checkout_hygiene.os, "fstat", failing_fstat)
    monkeypatch.setattr(checkout_hygiene.os, "close", failing_close)

    with pytest.raises(UnsafeReportOutput) as captured:
        write_hygiene_report_exclusive(output, report, repo=repo)

    assert isinstance(captured.value.__cause__, OSError)
    assert "primary first report fstat failure" in str(captured.value.__cause__)
    assert fstat_failed is True
    assert close_failed is True
    assert report_descriptor is not None
    with pytest.raises(OSError):
        real_fstat(report_descriptor)
    assert not os.path.lexists(output)

    write_hygiene_report_exclusive(output, report, repo=repo)
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "ready"


def test_failed_report_cleanup_preserves_competitor_injected_inside_unlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, legacy = _make_repo(tmp_path)
    (legacy / ".DS_Store").write_bytes(b"finder")
    evidence = _configure_evidence(tmp_path, monkeypatch, repo=repo)
    output = evidence / "report.json"
    displaced = evidence / "owned-displaced.json"
    real_unlink = checkout_hygiene.os.unlink
    injection_fired = False

    def failing_fchmod(descriptor: int, mode: int) -> None:
        raise OSError(f"primary injected fchmod failure for {mode:o}")

    def racing_unlink(path: object, *args: object, **kwargs: object) -> None:
        nonlocal injection_fired
        if not injection_fired:
            if output.exists():
                output.rename(displaced)
            output.write_bytes(b"competitor")
            injection_fired = True
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(checkout_hygiene.os, "fchmod", failing_fchmod)
    monkeypatch.setattr(checkout_hygiene.os, "unlink", racing_unlink)

    with pytest.raises(UnsafeReportOutput) as captured:
        run_checkout_hygiene(
            repo=repo,
            legacy_root=legacy,
            output=output,
            apply_safe_cache=True,
        )

    assert isinstance(captured.value.__cause__, OSError)
    assert "primary injected fchmod failure" in str(captured.value.__cause__)
    assert injection_fired is True
    assert output.read_bytes() == b"competitor"
    assert (legacy / ".DS_Store").read_bytes() == b"finder"


@pytest.mark.parametrize("protected", ["checkout", "home-dan", "config", "symlink-parent"])
def test_report_writer_rejects_checkout_home_dan_config_and_symlink_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, protected: str
) -> None:
    repo, legacy = _make_repo(tmp_path)
    (legacy / ".DS_Store").write_bytes(b"finder")
    evidence = _configure_evidence(tmp_path, monkeypatch, repo=repo)
    home = Path(os.environ["HOME"])
    if protected == "checkout":
        output = repo / "report.json"
    elif protected == "home-dan":
        output = home / ".dan" / "report.json"
    elif protected == "config":
        config = tmp_path / "active-config"
        config.mkdir()
        monkeypatch.setenv("DAN_CONFIG", str(config / "config.toml"))
        output = config / "report.json"
    else:
        real_parent = evidence / "real"
        real_parent.mkdir()
        (evidence / "linked").symlink_to(real_parent, target_is_directory=True)
        output = evidence / "linked" / "report.json"

    report = build_hygiene_report(
        plan_safe_cache_removal(repo=repo, legacy_root=legacy), mode="plan"
    )
    with pytest.raises(UnsafeReportOutput):
        write_hygiene_report_exclusive(output, report, repo=repo)

    assert (legacy / ".DS_Store").exists()


@pytest.mark.parametrize(
    "protected_root",
    [
        "checkout",
        "home-dan",
        "home-config",
        "home-claude",
        "active-config",
        "voice-config",
        "runtime",
        "database-parent",
        "symlink-root",
    ],
)
def test_run_rejects_protected_evidence_root_before_deletion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    protected_root: str,
) -> None:
    repo, legacy = _make_repo(tmp_path)
    safe = legacy / ".DS_Store"
    safe.write_bytes(b"finder")
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("DAN_RUNTIME_DIR", str(home / ".dan" / "runtime"))
    monkeypatch.setenv("DAN_DB_PATH", str(home / ".dan" / "dan.sqlite3"))
    monkeypatch.delenv("DAN_CONFIG", raising=False)
    monkeypatch.delenv("VOICE_CONFIG_DIR", raising=False)
    if protected_root == "checkout":
        repo.chmod(0o700)
        evidence = repo
    elif protected_root == "home-dan":
        evidence = home / ".dan"
        evidence.mkdir(mode=0o700)
    elif protected_root == "home-config":
        evidence = home / ".config"
        evidence.mkdir(mode=0o700)
    elif protected_root == "home-claude":
        evidence = home / ".claude"
        evidence.mkdir(mode=0o700)
    elif protected_root == "active-config":
        evidence = tmp_path / "active-config"
        evidence.mkdir(mode=0o700)
        monkeypatch.setenv("DAN_CONFIG", str(evidence / "config.toml"))
    elif protected_root == "voice-config":
        evidence = tmp_path / "voice-config"
        evidence.mkdir(mode=0o700)
        monkeypatch.setenv("VOICE_CONFIG_DIR", str(evidence))
    elif protected_root == "runtime":
        evidence = tmp_path / "runtime"
        evidence.mkdir(mode=0o700)
        monkeypatch.setenv("DAN_RUNTIME_DIR", str(evidence))
    elif protected_root == "database-parent":
        evidence = tmp_path / "database-parent"
        evidence.mkdir(mode=0o700)
        monkeypatch.setenv("DAN_DB_PATH", str(evidence / "dan.sqlite3"))
    else:
        real_evidence = tmp_path / "real-evidence"
        real_evidence.mkdir(mode=0o700)
        evidence = tmp_path / "evidence-link"
        evidence.symlink_to(real_evidence, target_is_directory=True)
    monkeypatch.setenv("DAN_RELEASE_EVIDENCE_ROOT", str(evidence))

    with pytest.raises(UnsafeReportOutput):
        run_checkout_hygiene(
            repo=repo,
            legacy_root=legacy,
            output=evidence / "report.json",
            apply_safe_cache=True,
        )

    assert safe.read_bytes() == b"finder"
    assert not os.path.lexists(evidence / "report.json")


def test_report_writer_flushes_and_fsyncs_file_and_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, legacy = _make_repo(tmp_path)
    (legacy / ".DS_Store").write_bytes(b"finder")
    evidence = _configure_evidence(tmp_path, monkeypatch, repo=repo)
    report = build_hygiene_report(
        plan_safe_cache_removal(repo=repo, legacy_root=legacy), mode="plan"
    )
    fsynced_types: list[str] = []
    real_fsync = os.fsync

    def recording_fsync(descriptor: int) -> None:
        mode = os.fstat(descriptor).st_mode
        fsynced_types.append("dir" if stat.S_ISDIR(mode) else "file")
        real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", recording_fsync)

    write_hygiene_report_exclusive(evidence / "report.json", report, repo=repo)

    assert "file" in fsynced_types
    assert "dir" in fsynced_types


def test_report_contains_resolved_repo_and_exact_planned_skipped_removed_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, legacy = _make_repo(tmp_path)
    (legacy / ".DS_Store").write_bytes(b"finder")
    (legacy / "keep.py").write_text("keep = True\n", encoding="utf-8")
    evidence = _configure_evidence(tmp_path, monkeypatch, repo=repo)
    output = evidence / "plan.json"

    report = run_checkout_hygiene(
        repo=repo,
        legacy_root=legacy,
        output=output,
        apply_safe_cache=False,
    )
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert isinstance(report, HygieneReport)
    assert payload == {
        "completion_path": None,
        "intent_sha256": None,
        "legacy_namespace_present": True,
        "legacy_root": str(legacy.resolve()),
        "mode": "plan",
        "planned": [{"kind": "ds_store", "path": "jarvis/.DS_Store"}],
        "quarantine_path": None,
        "removed": [],
        "repo": str(repo.resolve()),
        "schema_version": 2,
        "skipped": [{"path": "jarvis/keep.py", "reason": "non-cache-file"}],
        "status": "blocked",
        "transaction_id": None,
    }
    assert output.read_bytes().endswith(b"\n")
    assert output.read_bytes() == (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode()


def test_apply_validates_output_before_first_deletion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, legacy = _make_repo(tmp_path)
    safe = legacy / ".DS_Store"
    safe.write_bytes(b"finder")
    evidence = _configure_evidence(tmp_path, monkeypatch, repo=repo)
    output = evidence / "existing.json"
    output.write_bytes(b"history")

    with pytest.raises(FileExistsError):
        run_checkout_hygiene(
            repo=repo,
            legacy_root=legacy,
            output=output,
            apply_safe_cache=True,
        )

    assert safe.read_bytes() == b"finder"
    assert output.read_bytes() == b"history"


def test_cli_requires_output_and_defaults_to_plan_only(tmp_path: Path) -> None:
    repo, legacy = _make_repo(tmp_path)
    safe = legacy / ".DS_Store"
    safe.write_bytes(b"finder")
    evidence = tmp_path / "evidence"
    evidence.mkdir(mode=0o700)
    environment = _subprocess_environment(tmp_path, evidence)

    missing = subprocess.run(
        [sys.executable, str(SCRIPT), "--repo", str(repo), "--legacy-root", str(legacy)],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    output = evidence / "plan.json"
    planned = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--repo",
            str(repo),
            "--legacy-root",
            str(legacy),
            "--output",
            str(output),
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert missing.returncode == 2
    assert planned.returncode == 0, planned.stderr
    assert safe.read_bytes() == b"finder"
    assert json.loads(output.read_text(encoding="utf-8"))["mode"] == "plan"


def test_cli_apply_flag_is_the_only_mutating_mode(tmp_path: Path) -> None:
    repo, legacy = _make_repo(tmp_path)
    safe = legacy / ".DS_Store"
    safe.write_bytes(b"finder")
    evidence = tmp_path / "evidence"
    evidence.mkdir(mode=0o700)
    environment = _subprocess_environment(tmp_path, evidence)
    plan_output = evidence / "plan.json"
    apply_output = evidence / "apply.json"

    planned = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--repo",
            str(repo),
            "--legacy-root",
            str(legacy),
            "--output",
            str(plan_output),
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert planned.returncode == 0, planned.stderr
    assert safe.exists()

    applied = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--repo",
            str(repo),
            "--legacy-root",
            str(legacy),
            "--apply-safe-cache",
            "--output",
            str(apply_output),
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert applied.returncode == 0, applied.stderr
    assert not legacy.exists()
    payload = json.loads(apply_output.read_text(encoding="utf-8"))
    assert payload["mode"] == "apply-safe-cache"
    assert payload["status"] == "applied"
    assert payload["removed"] == ["jarvis/.DS_Store", "jarvis"]
    intent = apply_output.with_name(f"{apply_output.name}.intent")
    intent_payload = json.loads(intent.read_text(encoding="utf-8"))
    assert intent_payload["status"] == "prepared"
    assert intent_payload["transaction_id"] == payload["transaction_id"]
    assert payload["intent_sha256"] == hashlib.sha256(intent.read_bytes()).hexdigest()
    assert Path(payload["quarantine_path"]).is_dir()


def test_cli_apply_stops_without_mutation_when_plan_has_blockers(tmp_path: Path) -> None:
    repo, legacy = _make_repo(tmp_path)
    safe = legacy / ".DS_Store"
    safe.write_bytes(b"finder")
    (legacy / "api").mkdir()
    evidence = tmp_path / "evidence"
    evidence.mkdir(mode=0o700)
    environment = _subprocess_environment(tmp_path, evidence)
    output = evidence / "blocked.json"

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--repo",
            str(repo),
            "--legacy-root",
            str(legacy),
            "--apply-safe-cache",
            "--output",
            str(output),
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 2
    assert safe.read_bytes() == b"finder"
    assert (legacy / "api").is_dir()
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "blocked"


def test_cli_real_root_mirror_reports_exact_stop_without_removal(tmp_path: Path) -> None:
    repo, legacy = _make_repo(tmp_path)
    safe = legacy / ".DS_Store"
    safe.write_bytes(b"finder")
    for name in REAL_LEGACY_EMPTY_DIRECTORIES:
        (legacy / name).mkdir()
    evidence = tmp_path / "evidence"
    evidence.mkdir(mode=0o700)
    output = evidence / "blocked.json"

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--repo",
            str(repo),
            "--legacy-root",
            str(legacy),
            "--apply-safe-cache",
            "--output",
            str(output),
        ],
        env=_subprocess_environment(tmp_path, evidence),
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 2
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["status"] == "blocked"
    assert payload["removed"] == []
    assert payload["planned"] == [
        {"kind": "ds_store", "path": "jarvis/.DS_Store"}
    ]
    assert payload["skipped"] == [
        {"path": f"jarvis/{name}", "reason": "non-cache-directory"}
        for name in REAL_LEGACY_EMPTY_DIRECTORIES
    ]
    assert safe.read_bytes() == b"finder"
    assert all((legacy / name).is_dir() for name in REAL_LEGACY_EMPTY_DIRECTORIES)
