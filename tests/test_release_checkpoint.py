"""Contracts for the immutable Release 1 checkpoint."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

import dan.release.checkpoint as checkpoint_module
import dan.release.evidence as evidence_module
from dan.migration.inventory import InventoryRoots, build_inventory, write_manifest_atomic
from dan.release.checkpoint import (
    CandidateRef,
    DirtyScopeBase,
    EvidenceRef,
    IncompleteWorktreeDelta,
    InvalidCheckpoint,
    InvalidReviewScope,
    OutOfScopeCommittedDelta,
    OutOfScopeWorktreeDelta,
    capture_release_checkpoint,
    checkpoint_evidence_envelope,
    read_review_scope_v1,
    read_task_scope_manifest,
    write_checkpoint_exclusive,
)
from dan.release.evidence import (
    active_evidence_roots_from_environment,
    canonical_envelope_sha256,
    read_evidence_envelope,
    validate_evidence_root,
)

TASK_IDS = (
    "0.1", "0.2", "0.3", "0.4",
    "1.1", "1.2", "1.3", "1.4", "1.5", "1.6", "1.7", "1.8", "1.9",
    "2.1", "2.2", "2.3", "2.4", "2.5", "2.6", "2.7", "2.8", "2.9",
    "3.1", "3.2", "3.3", "3.4", "3.5", "3.6",
    "4.1", "4.2", "4.3", "4.4", "4.5", "4.6", "4.7",
    "5.1", "5.2", "5.3", "5.4", "5.5", "5.6",
    "release1-final-integration",
)
SCOPE_PATHS = (
    "dan/release/__init__.py",
    "dan/release/checkpoint.py",
    "dan/release/evidence.py",
    "dan/release/producer_ids.py",
    "release/review-scope-v1.json",
    "scripts/dan-release-checkpoint",
    "tests/test_release_checkpoint.py",
    "tests/test_release_evidence.py",
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def _run(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _unavailable_runner(
    args: list[str], **kwargs: object
) -> subprocess.CompletedProcess[str]:
    if args and args[0] == "git":
        return subprocess.run(args, **kwargs)  # type: ignore[arg-type]
    if args == ["ps", "-axo", "pid=,ppid=,command="]:
        return subprocess.CompletedProcess(args, 0, "", "")
    if args == ["launchctl", "list"]:
        return subprocess.CompletedProcess(args, 0, "", "")
    return subprocess.CompletedProcess(args, 127, "", "unavailable in fixture")


class CheckpointFixture:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.repo = tmp_path / "repo"
        self.home = tmp_path / "home"
        self.runtime = tmp_path / "runtime"
        self.evidence = tmp_path / "evidence"
        self.scope_path = self.evidence / "task-0.1-scope.json"
        self.repo.mkdir()
        self.home.mkdir()
        self.runtime.mkdir()
        self.evidence.mkdir(mode=0o700)
        _run(self.repo, "init", "-q")
        _run(self.repo, "config", "user.name", "Fixture")
        _run(self.repo, "config", "user.email", "fixture@example.invalid")
        _run(self.repo, "checkout", "-q", "-b", "agent/dan-release1-integration")
        (self.repo / "README.md").write_text("fixture\n", encoding="utf-8")
        _run(self.repo, "add", "README.md")
        _run(self.repo, "commit", "-q", "-m", "base")
        self.base_head = _git(self.repo, "rev-parse", "HEAD")
        _run(self.repo, "tag", "-a", "dan-v1-foundation-candidate", "-m", "candidate")

        inventory_path = self.home / ".dan/migration/release1-source-manifest.json"
        inventory_path.parent.mkdir(parents=True, mode=0o700)
        manifest = build_inventory(
            InventoryRoots(
                home=self.home,
                repo_root=self.repo,
                tmp_root=self.runtime,
            ),
            runner=_unavailable_runner,
        )
        write_manifest_atomic(manifest, inventory_path, canonical_home=self.home)

        monkeypatch.setenv("HOME", str(self.home))
        monkeypatch.setenv("DAN_RUNTIME_DIR", str(self.runtime))
        monkeypatch.setenv("DAN_DB_PATH", str(self.home / ".dan/dan.sqlite3"))
        monkeypatch.setenv("DAN_RELEASE_EVIDENCE_ROOT", str(self.evidence))
        monkeypatch.delenv("DAN_CONFIG", raising=False)
        monkeypatch.delenv("VOICE_CONFIG_DIR", raising=False)
        monkeypatch.setattr(
            checkpoint_module,
            "EXPECTED_CANDIDATE_TARGET_SHA",
            self.base_head,
        )
        self._write_scope_manifest()

    def _write_scope_manifest(self) -> None:
        empty_sha = hashlib.sha256(b"").hexdigest()
        payload = {
            "schema_version": 1,
            "branch": "agent/dan-release1-integration",
            "base_head": self.base_head,
            "clean_status_sha256": empty_sha,
            "clean_index_diff_sha256": empty_sha,
            "clean_worktree_diff_sha256": empty_sha,
            "scope_paths": list(SCOPE_PATHS),
        }
        self.scope_path.write_text(
            json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        os.chmod(self.scope_path, 0o600)
        self.scope_sha256 = hashlib.sha256(self.scope_path.read_bytes()).hexdigest()

    def create_exact_scope_delta(self) -> None:
        source_registry = Path(__file__).parents[1] / "release/review-scope-v1.json"
        for relative in SCOPE_PATHS:
            path = self.repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            if relative == "release/review-scope-v1.json":
                path.write_bytes(source_registry.read_bytes())
            else:
                path.write_text(f"fixture: {relative}\n", encoding="utf-8")

    def capture(self, phase: str, *, expected_head: str | None = None):
        return capture_release_checkpoint(
            repo=self.repo,
            candidate_tag="dan-v1-foundation-candidate",
            phase=phase,
            expected_head=expected_head or self.base_head,
            scope_manifest=self.scope_path,
            scope_sha256=self.scope_sha256,
        )

    def commit_scope_delta(self) -> str:
        _run(self.repo, "add", *SCOPE_PATHS)
        _run(self.repo, "commit", "-q", "-m", "task 0.1")
        return _git(self.repo, "rev-parse", "HEAD")


@pytest.fixture
def checkpoint_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> CheckpointFixture:
    return CheckpointFixture(tmp_path, monkeypatch)


def test_review_scope_registry_has_the_exact_expanded_release1_task_set() -> None:
    repo = Path(__file__).parents[1]
    registry = read_review_scope_v1(repo / "release/review-scope-v1.json")
    assert registry.task_ids == TASK_IDS
    for task in registry.tasks[:-1]:
        assert task.review_mode == "task-diff"
        assert task.allowed_paths == tuple(sorted(set(task.allowed_paths)))
        assert task.allowed_paths
        assert all(not path.startswith(("/", "../")) for path in task.allowed_paths)
    final = registry.tasks[-1]
    assert final.review_mode == "checkpoint-to-final-head"
    assert final.allowed_paths == registry.union_paths(task_ids=registry.task_ids[:-1])


def test_scope_manifest_refuses_a_dirty_base(checkpoint_fixture: CheckpointFixture) -> None:
    payload = json.loads(checkpoint_fixture.scope_path.read_text(encoding="utf-8"))
    payload["clean_status_sha256"] = hashlib.sha256(b"dirty").hexdigest()
    checkpoint_fixture.scope_path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    digest = hashlib.sha256(checkpoint_fixture.scope_path.read_bytes()).hexdigest()
    with pytest.raises(DirtyScopeBase):
        read_task_scope_manifest(checkpoint_fixture.scope_path, expected_sha256=digest)


def test_precommit_checkpoint_accepts_exact_tooling_delta(
    checkpoint_fixture: CheckpointFixture,
) -> None:
    checkpoint_fixture.create_exact_scope_delta()
    report = checkpoint_fixture.capture("precommit-delta")
    assert report.head == report.base_head
    assert report.dirty_paths == report.scope_paths == SCOPE_PATHS
    assert report.observed_delta_paths == SCOPE_PATHS
    assert report.review_scope.path == "release/review-scope-v1.json"
    assert report.review_scope.sha256 == hashlib.sha256(
        (checkpoint_fixture.repo / report.review_scope.path).read_bytes()
    ).hexdigest()


def test_precommit_checkpoint_rejects_extra_or_missing_delta_path(
    checkpoint_fixture: CheckpointFixture,
) -> None:
    checkpoint_fixture.create_exact_scope_delta()
    (checkpoint_fixture.repo / "unexpected.txt").write_text("extra\n", encoding="utf-8")
    with pytest.raises(OutOfScopeWorktreeDelta):
        checkpoint_fixture.capture("precommit-delta")
    (checkpoint_fixture.repo / "unexpected.txt").unlink()
    (checkpoint_fixture.repo / "tests/test_release_checkpoint.py").unlink()
    with pytest.raises(IncompleteWorktreeDelta):
        checkpoint_fixture.capture("precommit-delta")


def test_final_clean_checkpoint_requires_only_scoped_committed_delta(
    checkpoint_fixture: CheckpointFixture,
) -> None:
    checkpoint_fixture.create_exact_scope_delta()
    final_head = checkpoint_fixture.commit_scope_delta()
    report = checkpoint_fixture.capture("final-clean", expected_head=final_head)
    assert report.dirty_paths == ()
    assert report.head == final_head != report.base_head
    assert report.observed_delta_paths == SCOPE_PATHS

    (checkpoint_fixture.repo / "unexpected.txt").write_text("extra\n", encoding="utf-8")
    _run(checkpoint_fixture.repo, "add", "unexpected.txt")
    _run(checkpoint_fixture.repo, "commit", "-q", "-m", "extra")
    extra_head = _git(checkpoint_fixture.repo, "rev-parse", "HEAD")
    with pytest.raises(OutOfScopeCommittedDelta):
        checkpoint_fixture.capture("final-clean", expected_head=extra_head)


def test_checkpoint_emits_shared_release_evidence_envelope(
    checkpoint_fixture: CheckpointFixture,
) -> None:
    checkpoint_fixture.create_exact_scope_delta()
    report = checkpoint_fixture.capture("precommit-delta")
    envelope = checkpoint_evidence_envelope(report)
    assert envelope.kind == "release_checkpoint"
    assert envelope.producer_id == "dan-release-checkpoint:v1"
    assert envelope.subject_sha == checkpoint_fixture.base_head
    assert envelope.status == "green"
    assert envelope.unknown_evidence == ()
    assert envelope.report_sha256 == canonical_envelope_sha256(envelope)


def test_checkpoint_refuses_existing_output(checkpoint_fixture: CheckpointFixture) -> None:
    checkpoint_fixture.create_exact_scope_delta()
    report = checkpoint_fixture.capture("precommit-delta")
    output = checkpoint_fixture.evidence / "checkpoint.json"
    output.write_text("historical", encoding="utf-8")
    with pytest.raises(FileExistsError):
        write_checkpoint_exclusive(output, report)
    assert output.read_text(encoding="utf-8") == "historical"


def test_checkpoint_write_round_trip_binds_candidate_and_inventory(
    checkpoint_fixture: CheckpointFixture,
) -> None:
    checkpoint_fixture.create_exact_scope_delta()
    report = checkpoint_fixture.capture("precommit-delta")
    assert report.branch == "agent/dan-release1-integration"
    assert report.head == _git(checkpoint_fixture.repo, "rev-parse", "HEAD")
    assert report.candidate.target_sha == _git(
        checkpoint_fixture.repo, "rev-parse", "dan-v1-foundation-candidate^{}"
    )
    assert report.inventory.sha256 == hashlib.sha256(
        Path(report.inventory.path).read_bytes()
    ).hexdigest()

    output = checkpoint_fixture.evidence / "checkpoint.json"
    write_checkpoint_exclusive(output, report)
    roots = active_evidence_roots_from_environment(repo=checkpoint_fixture.repo)
    validated = validate_evidence_root(checkpoint_fixture.evidence, active_roots=roots)
    envelope = read_evidence_envelope(
        output,
        evidence_root=validated,
        expected_kind="release_checkpoint",
        expected_producer_id="dan-release-checkpoint:v1",
    )
    assert envelope.result["phase"] == "precommit-delta"
    assert stat.S_IMODE(output.stat().st_mode) == 0o600


def _write_registry_with_path(repo: Path, path: str) -> None:
    registry_path = repo / "release/review-scope-v1.json"
    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    ordinary = payload["tasks"][1]["allowed_paths"]
    ordinary.append(path)
    payload["tasks"][1]["allowed_paths"] = sorted(set(ordinary))
    final = payload["tasks"][-1]["allowed_paths"]
    final.append(path)
    payload["tasks"][-1]["allowed_paths"] = sorted(set(final))
    registry_path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    "path",
    (
        "src/*.py",
        "src/file?.py",
        "src/file[0].py",
        ":(glob)src/**",
        ":!tests/safe.py",
        "src/bad\x00name.py",
        "src/bad\nname.py",
        "src/bad\x7fname.py",
        ".git/config",
        "src/.GIT/config",
    ),
)
def test_review_scope_rejects_nonliteral_or_git_internal_paths(
    checkpoint_fixture: CheckpointFixture,
    path: str,
) -> None:
    checkpoint_fixture.create_exact_scope_delta()
    _write_registry_with_path(checkpoint_fixture.repo, path)
    with pytest.raises(InvalidReviewScope):
        read_review_scope_v1(
            checkpoint_fixture.repo / "release/review-scope-v1.json"
        )


def test_review_scope_rejects_existing_symlink_prefix(
    checkpoint_fixture: CheckpointFixture,
) -> None:
    checkpoint_fixture.create_exact_scope_delta()
    outside = checkpoint_fixture.repo.parent / "outside"
    outside.mkdir()
    (checkpoint_fixture.repo / "alias").symlink_to(outside, target_is_directory=True)
    _write_registry_with_path(checkpoint_fixture.repo, "alias/escape.py")
    with pytest.raises(InvalidReviewScope, match="symlink"):
        read_review_scope_v1(
            checkpoint_fixture.repo / "release/review-scope-v1.json"
        )


@pytest.mark.parametrize("target_location", ("inside", "outside"))
def test_review_scope_rejects_existing_leaf_symlink(
    checkpoint_fixture: CheckpointFixture,
    target_location: str,
) -> None:
    checkpoint_fixture.create_exact_scope_delta()
    if target_location == "inside":
        target = checkpoint_fixture.repo / "README.md"
    else:
        target = checkpoint_fixture.repo.parent / "outside.py"
        target.write_text("outside\n", encoding="utf-8")
    (checkpoint_fixture.repo / "linked.py").symlink_to(target)
    _write_registry_with_path(checkpoint_fixture.repo, "linked.py")

    with pytest.raises(InvalidReviewScope, match="symlink"):
        read_review_scope_v1(
            checkpoint_fixture.repo / "release/review-scope-v1.json"
        )


def test_review_scope_rejects_existing_directory_leaf(
    checkpoint_fixture: CheckpointFixture,
) -> None:
    checkpoint_fixture.create_exact_scope_delta()
    (checkpoint_fixture.repo / "directory-leaf").mkdir()
    _write_registry_with_path(checkpoint_fixture.repo, "directory-leaf")

    with pytest.raises(InvalidReviewScope, match="regular file"):
        read_review_scope_v1(
            checkpoint_fixture.repo / "release/review-scope-v1.json"
        )


def test_review_scope_accepts_existing_regular_leaf(
    checkpoint_fixture: CheckpointFixture,
) -> None:
    checkpoint_fixture.create_exact_scope_delta()
    (checkpoint_fixture.repo / "ordinary.py").write_text("ordinary\n", encoding="utf-8")
    _write_registry_with_path(checkpoint_fixture.repo, "ordinary.py")

    registry = read_review_scope_v1(
        checkpoint_fixture.repo / "release/review-scope-v1.json"
    )

    assert "ordinary.py" in registry.tasks[1].allowed_paths


def test_review_scope_accepts_missing_future_leaf(
    checkpoint_fixture: CheckpointFixture,
) -> None:
    checkpoint_fixture.create_exact_scope_delta()
    _write_registry_with_path(checkpoint_fixture.repo, "future/missing.py")

    registry = read_review_scope_v1(
        checkpoint_fixture.repo / "release/review-scope-v1.json"
    )

    assert "future/missing.py" in registry.tasks[1].allowed_paths


@pytest.mark.parametrize(
    "variable",
    (
        "GIT_INDEX_FILE",
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_COMMON_DIR",
        "GIT_CONFIG_COUNT",
        "GIT_EXTERNAL_DIFF",
    ),
)
def test_checkpoint_rejects_repo_sensitive_git_environment(
    checkpoint_fixture: CheckpointFixture,
    monkeypatch: pytest.MonkeyPatch,
    variable: str,
) -> None:
    checkpoint_fixture.create_exact_scope_delta()
    monkeypatch.setenv(variable, str(checkpoint_fixture.repo / "attacker"))
    with pytest.raises(InvalidCheckpoint, match=variable):
        checkpoint_fixture.capture("precommit-delta")


def test_checkpoint_disables_external_diff_and_textconv(
    checkpoint_fixture: CheckpointFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkpoint_fixture.create_exact_scope_delta()
    calls: list[tuple[str, ...]] = []
    original = checkpoint_module._git_bytes

    def recording_git(repo: Path, *args: str) -> bytes:
        calls.append(args)
        return original(repo, *args)

    monkeypatch.setattr(checkpoint_module, "_git_bytes", recording_git)
    checkpoint_fixture.capture("precommit-delta")
    diff_calls = [args for args in calls if args and args[0] == "diff"]
    assert diff_calls
    assert all("--no-ext-diff" in args for args in diff_calls)
    assert all("--no-textconv" in args for args in diff_calls)


@pytest.mark.parametrize(
    "candidate",
    (
        "HEAD",
        "agent/dan-release1-integration",
        "refs/heads/agent/dan-release1-integration",
        "HEAD~1",
    ),
)
def test_checkpoint_rejects_non_candidate_tag_revisions(
    checkpoint_fixture: CheckpointFixture,
    candidate: str,
) -> None:
    checkpoint_fixture.create_exact_scope_delta()
    with pytest.raises(InvalidCheckpoint, match="candidate"):
        capture_release_checkpoint(
            repo=checkpoint_fixture.repo,
            candidate_tag=candidate,
            phase="precommit-delta",
            expected_head=checkpoint_fixture.base_head,
            scope_manifest=checkpoint_fixture.scope_path,
            scope_sha256=checkpoint_fixture.scope_sha256,
        )


def test_checkpoint_accepts_lightweight_commit_candidate_tag(
    checkpoint_fixture: CheckpointFixture,
) -> None:
    checkpoint_fixture.create_exact_scope_delta()
    _run(checkpoint_fixture.repo, "tag", "-d", "dan-v1-foundation-candidate")
    _run(
        checkpoint_fixture.repo,
        "tag",
        "dan-v1-foundation-candidate",
        checkpoint_fixture.base_head,
    )
    report = checkpoint_fixture.capture("precommit-delta")
    assert report.candidate.target_sha == checkpoint_fixture.base_head


def test_checkpoint_rejects_non_commit_candidate_tag(
    checkpoint_fixture: CheckpointFixture,
) -> None:
    checkpoint_fixture.create_exact_scope_delta()
    blob = subprocess.check_output(
        ["git", "-C", str(checkpoint_fixture.repo), "hash-object", "-w", "--stdin"],
        input=b"not a commit\n",
    ).decode("ascii").strip()
    _run(
        checkpoint_fixture.repo,
        "tag",
        "-f",
        "dan-v1-foundation-candidate",
        blob,
    )
    with pytest.raises(InvalidCheckpoint, match="commit"):
        checkpoint_fixture.capture("precommit-delta")


def test_checkpoint_rejects_moved_candidate_tag(
    checkpoint_fixture: CheckpointFixture,
) -> None:
    checkpoint_fixture.create_exact_scope_delta()
    moved = subprocess.check_output(
        [
            "git",
            "-C",
            str(checkpoint_fixture.repo),
            "commit-tree",
            f"{checkpoint_fixture.base_head}^{{tree}}",
        ],
        input=b"moved candidate\n",
    ).decode("ascii").strip()
    _run(
        checkpoint_fixture.repo,
        "tag",
        "-f",
        "dan-v1-foundation-candidate",
        moved,
    )
    with pytest.raises(InvalidCheckpoint, match="candidate"):
        checkpoint_fixture.capture("precommit-delta")


@pytest.mark.parametrize(
    "field",
    (
        "schema_version",
        "phase",
        "scope_source",
        "review_scope",
        "inventory",
        "candidate",
    ),
)
def test_checkpoint_write_rejects_each_forged_authority_field(
    checkpoint_fixture: CheckpointFixture,
    field: str,
) -> None:
    checkpoint_fixture.create_exact_scope_delta()
    final_head = checkpoint_fixture.commit_scope_delta()
    report = checkpoint_fixture.capture("final-clean", expected_head=final_head)
    unrelated = checkpoint_fixture.repo / "README.md"
    unrelated_sha = hashlib.sha256(unrelated.read_bytes()).hexdigest()
    candidate_record = json.dumps(
        {"name": "HEAD", "target_sha": final_head},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    forgeries = {
        "schema_version": 99,
        "phase": "forged",
        "scope_source": EvidenceRef(path=str(unrelated), sha256=unrelated_sha),
        "review_scope": EvidenceRef(path="README.md", sha256=unrelated_sha),
        "inventory": EvidenceRef(path=str(unrelated), sha256=unrelated_sha),
        "candidate": CandidateRef(
            name="HEAD",
            target_sha=final_head,
            record_sha256=hashlib.sha256(candidate_record).hexdigest(),
        ),
    }
    forged = replace(report, **{field: forgeries[field]})
    output = checkpoint_fixture.evidence / f"forged-{field}.json"
    with pytest.raises(InvalidCheckpoint):
        write_checkpoint_exclusive(output, forged)
    assert not output.exists()


def test_checkpoint_write_uses_recaptured_timestamp_not_forged_input(
    checkpoint_fixture: CheckpointFixture,
) -> None:
    checkpoint_fixture.create_exact_scope_delta()
    report = checkpoint_fixture.capture("precommit-delta")
    forged_timestamp = "2000-01-01T00:00:00+00:00"
    forged = replace(report, created_at_utc=forged_timestamp)
    output = checkpoint_fixture.evidence / "timestamp.json"

    write_checkpoint_exclusive(output, forged)

    roots = active_evidence_roots_from_environment(repo=checkpoint_fixture.repo)
    validated = validate_evidence_root(checkpoint_fixture.evidence, active_roots=roots)
    envelope = read_evidence_envelope(
        output,
        evidence_root=validated,
        expected_kind="release_checkpoint",
        expected_producer_id="dan-release-checkpoint:v1",
    )
    assert envelope.created_at_utc != forged_timestamp
    assert envelope.result["created_at_utc"] == envelope.created_at_utc


def test_missing_inventory_derives_unknown_instead_of_green(
    checkpoint_fixture: CheckpointFixture,
) -> None:
    checkpoint_fixture.create_exact_scope_delta()
    inventory = checkpoint_fixture.home / ".dan/migration/release1-source-manifest.json"
    inventory.unlink()
    report = checkpoint_fixture.capture("precommit-delta")
    envelope = checkpoint_evidence_envelope(report)
    assert envelope.status == "unknown"
    assert envelope.unknown_evidence == ("TASK1_INVENTORY_UNAVAILABLE",)


def test_checkpoint_write_rejects_real_root_rename_substitution(
    checkpoint_fixture: CheckpointFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkpoint_fixture.create_exact_scope_delta()
    final_head = checkpoint_fixture.commit_scope_delta()
    report = checkpoint_fixture.capture("final-clean", expected_head=final_head)
    output = checkpoint_fixture.evidence / "checkpoint.json"
    displaced = checkpoint_fixture.repo.with_name("validated-repo")
    original_identity = os.stat(checkpoint_fixture.repo)
    original_check_output = subprocess.check_output
    injection_fired = False
    replacement_identity: tuple[int, int] | None = None

    def swap_before_git_exec(args: list[str], **kwargs: object) -> bytes:
        nonlocal injection_fired, replacement_identity
        if not injection_fired and args[-2:] == ["rev-parse", "--show-toplevel"]:
            checkpoint_fixture.repo.rename(displaced)
            subprocess.run(
                [
                    "git",
                    "clone",
                    "-q",
                    "--no-local",
                    str(displaced),
                    str(checkpoint_fixture.repo),
                ],
                check=True,
                capture_output=True,
            )
            replacement = os.stat(checkpoint_fixture.repo)
            replacement_identity = (replacement.st_dev, replacement.st_ino)
            injection_fired = True
        return original_check_output(args, **kwargs)  # type: ignore[call-overload]

    monkeypatch.setattr(subprocess, "check_output", swap_before_git_exec)
    failure: InvalidCheckpoint | None = None
    try:
        write_checkpoint_exclusive(output, report)
    except InvalidCheckpoint as exc:
        failure = exc

    assert injection_fired
    assert replacement_identity != (original_identity.st_dev, original_identity.st_ino)
    assert _git(checkpoint_fixture.repo, "rev-parse", "HEAD") == final_head
    assert _git(checkpoint_fixture.repo, "status", "--porcelain=v1") == ""
    assert failure is not None, "repository substitution must invalidate the checkpoint"
    assert "repository identity changed" in str(failure)
    assert not output.exists()


def test_fingerprint_hashes_the_descriptor_bytes_not_a_swapped_path(
    checkpoint_fixture: CheckpointFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkpoint_fixture.create_exact_scope_delta()
    target = checkpoint_fixture.repo / "dan/release/checkpoint.py"
    replacement = checkpoint_fixture.repo / "replacement.py"
    replacement.write_bytes(b"attacker bytes\n")
    expected = hashlib.sha256(target.read_bytes()).hexdigest()
    target_identity = (target.stat().st_dev, target.stat().st_ino)
    original_open = os.open
    original_read = os.read
    target_descriptor: int | None = None
    open_injection_fired = False
    descriptor_read_fired = False

    def swapping_open(
        path: os.PathLike[str] | str,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal open_injection_fired, target_descriptor
        descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
        details = os.fstat(descriptor)
        if (
            not open_injection_fired
            and (details.st_dev, details.st_ino) == target_identity
        ):
            target_descriptor = descriptor
            os.replace(replacement, target)
            open_injection_fired = True
        return descriptor

    def recording_read(descriptor: int, size: int) -> bytes:
        nonlocal descriptor_read_fired
        if descriptor == target_descriptor:
            descriptor_read_fired = True
        return original_read(descriptor, size)

    monkeypatch.setattr(checkpoint_module.os, "open", swapping_open)
    monkeypatch.setattr(checkpoint_module.os, "read", recording_read)

    with checkpoint_module._RepositoryAnchor.open(
        checkpoint_fixture.repo
    ) as repository:
        fingerprint = checkpoint_module._fingerprint(
            repository,
            relative="dan/release/checkpoint.py",
        )

    assert open_injection_fired
    assert descriptor_read_fired
    assert fingerprint.sha256 == expected


def test_inventory_hash_and_validation_use_the_same_bytes(
    checkpoint_fixture: CheckpointFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inventory = checkpoint_fixture.home / ".dan/migration/release1-source-manifest.json"
    inventory_identity = (inventory.stat().st_dev, inventory.stat().st_ino)
    original_open = os.open
    original_read = os.read
    inventory_descriptor: int | None = None
    open_hook_fired = False
    read_injection_fired = False

    def recording_open(
        path: os.PathLike[str] | str,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal inventory_descriptor, open_hook_fired
        descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
        details = os.fstat(descriptor)
        if (details.st_dev, details.st_ino) == inventory_identity:
            inventory_descriptor = descriptor
            open_hook_fired = True
        return descriptor

    def injecting_read(descriptor: int, size: int) -> bytes:
        nonlocal read_injection_fired
        if descriptor == inventory_descriptor:
            if not read_injection_fired:
                read_injection_fired = True
                return b"{}\n"
            return b""
        return original_read(descriptor, size)

    monkeypatch.setattr(checkpoint_module.os, "open", recording_open)
    monkeypatch.setattr(checkpoint_module.os, "read", injecting_read)

    reference, unknown_evidence = checkpoint_module._inventory_ref()

    assert open_hook_fired
    assert read_injection_fired
    assert reference.sha256 == hashlib.sha256(b"{}\n").hexdigest()
    assert unknown_evidence == ("TASK1_INVENTORY_INVALID",)


def test_checkpoint_publication_rolls_back_repository_swap_at_final_link(
    checkpoint_fixture: CheckpointFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkpoint_fixture.create_exact_scope_delta()
    final_head = checkpoint_fixture.commit_scope_delta()
    report = checkpoint_fixture.capture("final-clean", expected_head=final_head)
    output = checkpoint_fixture.evidence / "publish-race.json"
    displaced = checkpoint_fixture.repo.with_name("publish-race-original")
    replacement = checkpoint_fixture.repo.with_name("publish-race-replacement")
    original_identity = os.stat(checkpoint_fixture.repo)
    original_link = os.link
    injection_fired = False
    replacement_identity: tuple[int, int] | None = None

    def swap_at_final_link(
        source: os.PathLike[str] | str,
        destination: os.PathLike[str] | str,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> None:
        nonlocal injection_fired, replacement_identity
        if destination == output.name and not injection_fired:
            original_link(
                source,
                destination,
                src_dir_fd=src_dir_fd,
                dst_dir_fd=dst_dir_fd,
                follow_symlinks=follow_symlinks,
            )
            checkpoint_fixture.repo.rename(displaced)
            subprocess.run(
                [
                    "git",
                    "clone",
                    "-q",
                    "--no-local",
                    str(displaced),
                    str(checkpoint_fixture.repo),
                ],
                check=True,
                capture_output=True,
            )
            replacement_details = os.stat(checkpoint_fixture.repo)
            replacement_identity = (
                replacement_details.st_dev,
                replacement_details.st_ino,
            )
            injection_fired = True
            return
        original_link(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )

    monkeypatch.setattr(evidence_module.os, "link", swap_at_final_link)
    failure: InvalidCheckpoint | None = None
    try:
        write_checkpoint_exclusive(output, report)
    except InvalidCheckpoint as exc:
        failure = exc
    finally:
        if injection_fired:
            checkpoint_fixture.repo.rename(replacement)
            displaced.rename(checkpoint_fixture.repo)

    assert injection_fired
    assert replacement_identity != (original_identity.st_dev, original_identity.st_ino)
    assert _git(replacement, "rev-parse", "HEAD") == final_head
    assert _git(replacement, "status", "--porcelain=v1") == ""
    assert failure is not None, "repository substitution must invalidate publication"
    assert "repository identity changed" in str(failure)
    assert not output.exists()
    assert list(checkpoint_fixture.evidence.glob(".dan-evidence-*")) == []

    write_checkpoint_exclusive(output, report)
    assert output.exists()


def test_checkpoint_closes_repository_anchor_inside_publication_completion(
    checkpoint_fixture: CheckpointFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkpoint_fixture.create_exact_scope_delta()
    final_head = checkpoint_fixture.commit_scope_delta()
    report = checkpoint_fixture.capture("final-clean", expected_head=final_head)
    output = checkpoint_fixture.evidence / "completion-close.json"
    evidence_details = checkpoint_fixture.evidence.stat()
    evidence_identity = (evidence_details.st_dev, evidence_details.st_ino)
    original_anchor_open = checkpoint_module._RepositoryAnchor.open
    original_close = os.close
    original_fstat = os.fstat
    anchor_descriptor: int | None = None
    events: list[str] = []

    def recording_anchor_open(
        cls: type[checkpoint_module._RepositoryAnchor],
        path: Path,
    ) -> checkpoint_module._RepositoryAnchor:
        nonlocal anchor_descriptor
        repository = original_anchor_open(path)
        anchor_descriptor = repository.descriptor
        return repository

    def recording_close(descriptor: int) -> None:
        if descriptor == anchor_descriptor:
            events.append("repository-close")
        else:
            details = original_fstat(descriptor)
            if (
                output.exists()
                and (details.st_dev, details.st_ino) == evidence_identity
            ):
                events.append("evidence-parent-close")
                assert anchor_descriptor is not None
                with pytest.raises(OSError):
                    original_fstat(anchor_descriptor)
        original_close(descriptor)

    monkeypatch.setattr(
        checkpoint_module._RepositoryAnchor,
        "open",
        classmethod(recording_anchor_open),
    )
    monkeypatch.setattr(os, "close", recording_close)

    write_checkpoint_exclusive(output, report)

    assert anchor_descriptor is not None
    assert events[0:2] == ["repository-close", "evidence-parent-close"]
    assert output.exists()


def test_repository_anchor_open_closes_exact_descriptor_when_fstat_fails(
    checkpoint_fixture: CheckpointFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_details = checkpoint_fixture.repo.stat()
    repo_identity = (repo_details.st_dev, repo_details.st_ino)
    original_fstat = os.fstat
    failed_descriptor: int | None = None

    def one_shot_repository_fstat(descriptor: int) -> os.stat_result:
        nonlocal failed_descriptor
        details = original_fstat(descriptor)
        if failed_descriptor is None and (details.st_dev, details.st_ino) == repo_identity:
            failed_descriptor = descriptor
            raise OSError("forced repository fstat failure")
        return details

    monkeypatch.setattr(checkpoint_module.os, "fstat", one_shot_repository_fstat)
    with pytest.raises(OSError, match="repository fstat failure"):
        checkpoint_module._RepositoryAnchor.open(checkpoint_fixture.repo)

    assert failed_descriptor is not None
    with pytest.raises(OSError):
        original_fstat(failed_descriptor)


def test_repository_anchor_exit_preserves_primary_when_close_also_fails(
    checkpoint_fixture: CheckpointFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = checkpoint_module._RepositoryAnchor.open(checkpoint_fixture.repo)
    descriptor = repository.descriptor
    original_close = os.close
    close_failure_fired = False

    def one_shot_repository_close(candidate: int) -> None:
        nonlocal close_failure_fired
        if candidate == descriptor and not close_failure_fired:
            close_failure_fired = True
            original_close(candidate)
            raise OSError("secondary repository close failure")
        original_close(candidate)

    monkeypatch.setattr(checkpoint_module.os, "close", one_shot_repository_close)
    with pytest.raises(InvalidCheckpoint, match="primary capture failure") as captured:
        with repository:
            raise InvalidCheckpoint("primary capture failure")

    assert close_failure_fired
    assert any(
        "secondary repository close failure" in note
        for note in getattr(captured.value, "__notes__", ())
    )
    with pytest.raises(OSError):
        os.fstat(descriptor)
