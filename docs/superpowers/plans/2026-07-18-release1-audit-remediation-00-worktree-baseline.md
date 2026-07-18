# DAN Release 1 Audit Remediation — Batch 0 Worktree and Baseline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Freeze a clean committed Release 1 HEAD, prove that Task 0.1 changes exactly its declared bootstrap scope before commit, re-issue the checkpoint from the clean final HEAD after commit, remove only safe caches from the legacy namespace, and build a baseline v2 that actually blocks audio and binds tests to the checkout and interpreter.

**Architecture:** Batch 0 deploys nothing. The read-only checkpoint and hygiene scanner establish the input evidence; controlled cleanup may affect only caches under the precisely resolved local `jarvis/`. The checkpoint and baseline emit the shared `ReleaseEvidenceEnvelope` consumed by later release gates; they do not invent private report formats. One importable `dan/release/producer_ids.py` owns every Release 1 producer ID and fixed registry mapping. The fail-closed audio guard is enforced at production execution boundaries and by the test plugin. The baseline records the SHAs of the checkout, interpreter, collection, and guard.

**Tech Stack:** Python 3.11+, git plumbing, importlib, pathlib, pytest plugin API, subprocess fakes, JSON, SHA-256.

## Global Constraints

- Fable's implementation is already committed at `5c931d7563ace2056c1ca215458ea8fe230a4b36`, with follow-up `1895aa4da02233782e124e6d1e9e8be23f505166`; it is committed history and not a worktree input.
- Task 0.1 may begin only after all seven remediation plans are committed, the active branch is `agent/dan-release1-integration`, and both index and worktree are clean across two consecutive status reads.
- Task 0.1 may change exactly `dan/release/__init__.py`, `dan/release/producer_ids.py`, `dan/release/evidence.py`, `dan/release/checkpoint.py`, `scripts/dan-release-checkpoint`, `release/review-scope-v1.json`, `tests/test_release_evidence.py`, and `tests/test_release_checkpoint.py`. The pre-edit scope manifest freezes this list and the clean base HEAD; the pre-commit checkpoint rejects any missing or extra delta path.
- The historical Task 1 manifest and candidate tag are read-only. The new checkpoint must be created under a new name using exclusive creation.
- Cleanup must not use globs or `rm -rf`; it must plan exact paths, verify their type and root, and only then remove `.pyc`, `__pycache__`, and `.DS_Store`.
- No test may invoke real CoreAudio, `afplay`, `say`, the Supertonic CLI, or Supertonic serve.
- Automated evidence, test HOME, pytest temporary files, runtime state, databases, and tool caches must live under a fresh `mktemp` directory inside an operator-supplied `DAN_RELEASE_EVIDENCE_ROOT`. That root must resolve outside the checkout, active `~/.dan`, active `~/.config`, and any `DAN_CONFIG`/`VOICE_CONFIG_DIR` override. Never use active HOME as an evidence root.
- Task 0.1 has two mandatory checkpoint phases against the same immutable scope manifest: `precommit-delta` while the exact eight tooling/registry paths are dirty on the frozen base HEAD, then `final-clean` after those paths are committed and the worktree is clean on the new final HEAD. Later gates consume only the `final-clean` checkpoint.

Run this bootstrap at the beginning of every Batch 0 executor session. Every RED, GREEN, cleanup, and baseline block below calls `dan_new_evidence` again, so no command reuses a prior test HOME or report directory:

```bash
DAN_ACTIVE_HOME="${HOME:?HOME must be set}"
DAN_ACTIVE_DAN_CONFIG="${DAN_CONFIG:-$DAN_ACTIVE_HOME/.dan/config.toml}"
DAN_ACTIVE_VOICE_CONFIG="${VOICE_CONFIG_DIR:-$DAN_ACTIVE_HOME/.config/voice}"
DAN_ACTIVE_DB="${DAN_DB_PATH:-$DAN_ACTIVE_HOME/.dan/dan.sqlite3}"
DAN_ACTIVE_RUNTIME="${DAN_RUNTIME_DIR:-$DAN_ACTIVE_HOME/.dan/runtime}"
DAN_REPO_ROOT="$(git rev-parse --show-toplevel)"
: "${DAN_RELEASE_EVIDENCE_ROOT:?set DAN_RELEASE_EVIDENCE_ROOT to a pre-created external directory}"

DAN_RELEASE_EVIDENCE_ROOT="$(
  .venv/bin/python - \
    "$DAN_RELEASE_EVIDENCE_ROOT" "$DAN_REPO_ROOT" "$DAN_ACTIVE_HOME" \
    "$DAN_ACTIVE_DAN_CONFIG" "$DAN_ACTIVE_VOICE_CONFIG" \
    "$DAN_ACTIVE_DB" "$DAN_ACTIVE_RUNTIME" <<'PY'
import sys
from pathlib import Path

raw_root = Path(sys.argv[1]).expanduser()
if not raw_root.is_absolute() or not raw_root.is_dir() or raw_root.is_symlink():
    raise SystemExit("DAN_RELEASE_EVIDENCE_ROOT must be an absolute, existing, non-symlink directory")
if any(component.is_symlink() for component in (raw_root, *raw_root.parents)):
    raise SystemExit("DAN_RELEASE_EVIDENCE_ROOT ancestry must not contain symlinks")
root = raw_root.resolve(strict=True)
repo = Path(sys.argv[2]).resolve(strict=True)
home = Path(sys.argv[3]).expanduser().resolve(strict=True)
for raw in (
    repo,
    home / ".dan",
    home / ".config",
    home / ".claude",
    Path(sys.argv[4]),
    Path(sys.argv[5]),
    Path(sys.argv[6]),
    Path(sys.argv[7]),
):
    forbidden = raw.expanduser().resolve(strict=False)
    if root == forbidden or root.is_relative_to(forbidden) or forbidden.is_relative_to(root):
        raise SystemExit(f"evidence root overlaps protected path: {forbidden}")
print(root)
PY
)" || exit 1
export DAN_RELEASE_EVIDENCE_ROOT

dan_new_evidence() {
  case "${1-}" in
    ""|*[!A-Za-z0-9._-]*) return 2 ;;
  esac
  umask 077
  DAN_TASK_EVIDENCE_ROOT="$(mktemp -d "${DAN_RELEASE_EVIDENCE_ROOT%/}/$1.XXXXXX")" || return 1
  DAN_TEST_HOME="$(mktemp -d "$DAN_TASK_EVIDENCE_ROOT/home.XXXXXX")" || return 1
  DAN_TEST_RUNTIME="$DAN_TASK_EVIDENCE_ROOT/runtime"
  mkdir -p "$DAN_TEST_RUNTIME" "$DAN_TEST_HOME/.cache" \
    "$DAN_TEST_HOME/.config" "$DAN_TEST_HOME/.local/share"
  export DAN_TASK_EVIDENCE_ROOT DAN_TEST_HOME DAN_TEST_RUNTIME
  printf 'evidence=%s\ntest_home=%s\n' "$DAN_TASK_EVIDENCE_ROOT" "$DAN_TEST_HOME"
}
```

---

## Task 0.1: Freeze the clean base HEAD and write an immutable release checkpoint

**Files:**

- Create: `dan/release/__init__.py`
- Create: `dan/release/producer_ids.py`
- Create: `dan/release/evidence.py`
- Create: `dan/release/checkpoint.py`
- Create: `scripts/dan-release-checkpoint`
- Create: `release/review-scope-v1.json`
- Create: `tests/test_release_evidence.py`
- Create: `tests/test_release_checkpoint.py`
- Read only: existing Task 1 inventory and `dan-v1-foundation-candidate`

- [ ] **Step 1: Capture the pre-edit evidence**

```bash
dan_new_evidence task-0.1-freeze
export DAN_FREEZE_EVIDENCE_ROOT="$DAN_TASK_EVIDENCE_ROOT"
git branch --show-current > "$DAN_FREEZE_EVIDENCE_ROOT/branch.txt"
git rev-parse HEAD > "$DAN_FREEZE_EVIDENCE_ROOT/head.txt"
git status --porcelain=v1 -z > "$DAN_FREEZE_EVIDENCE_ROOT/status-1.z"
git status --porcelain=v1 -z > "$DAN_FREEZE_EVIDENCE_ROOT/status-2.z"
cmp "$DAN_FREEZE_EVIDENCE_ROOT/status-1.z" "$DAN_FREEZE_EVIDENCE_ROOT/status-2.z"
test ! -s "$DAN_FREEZE_EVIDENCE_ROOT/status-2.z"
git rev-parse dan-v1-foundation-candidate^{} \
  > "$DAN_FREEZE_EVIDENCE_ROOT/candidate-target.txt"
.venv/bin/python - \
  "$DAN_REPO_ROOT" "$DAN_FREEZE_EVIDENCE_ROOT/branch.txt" \
  "$DAN_FREEZE_EVIDENCE_ROOT/head.txt" "$DAN_FREEZE_EVIDENCE_ROOT/status-2.z" \
  "$DAN_FREEZE_EVIDENCE_ROOT/task-0.1-scope.json" <<'PY'
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

repo = Path(sys.argv[1]).resolve(strict=True)
expected_branch = Path(sys.argv[2]).read_text(encoding="utf-8").strip()
expected_head = Path(sys.argv[3]).read_text(encoding="ascii").strip()
status_path = Path(sys.argv[4])
output = Path(sys.argv[5])
status_cmd = ["git", "-C", str(repo), "status", "--porcelain=v1", "-z"]
clean_status = status_path.read_bytes()
if clean_status or subprocess.check_output(status_cmd):
    raise SystemExit("Task 0.1 must start from a clean worktree and index")

def git_bytes(*args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(repo), *args])

branch = git_bytes("branch", "--show-current").decode("utf-8").strip()
if branch != expected_branch or branch != "agent/dan-release1-integration":
    raise SystemExit(f"unexpected branch: {branch}")
base_head = git_bytes("rev-parse", "HEAD").decode("ascii").strip()
if base_head != expected_head:
    raise SystemExit("HEAD moved between shell capture and scope construction")
index_diff = git_bytes("diff", "--cached", "--binary", "--no-ext-diff", "HEAD")
worktree_diff = git_bytes("diff", "--binary", "--no-ext-diff")
if index_diff or worktree_diff:
    raise SystemExit("clean status disagrees with git diff")

payload = {
    "schema_version": 1,
    "branch": branch,
    "base_head": base_head,
    "clean_status_sha256": hashlib.sha256(clean_status).hexdigest(),
    "clean_index_diff_sha256": hashlib.sha256(index_diff).hexdigest(),
    "clean_worktree_diff_sha256": hashlib.sha256(worktree_diff).hexdigest(),
    "scope_paths": [
        "dan/release/__init__.py",
        "dan/release/checkpoint.py",
        "dan/release/evidence.py",
        "dan/release/producer_ids.py",
        "release/review-scope-v1.json",
        "scripts/dan-release-checkpoint",
        "tests/test_release_checkpoint.py",
        "tests/test_release_evidence.py",
    ],
}
if git_bytes("rev-parse", "HEAD").decode("ascii").strip() != base_head:
    raise SystemExit("HEAD changed while scope manifest was built")
if subprocess.check_output(status_cmd):
    raise SystemExit("worktree changed while scope manifest was built")
fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())
directory_fd = os.open(output.parent, os.O_RDONLY)
try:
    os.fsync(directory_fd)
finally:
    os.close(directory_fd)
PY
shasum -a 256 "$DAN_FREEZE_EVIDENCE_ROOT/task-0.1-scope.json" \
  > "$DAN_FREEZE_EVIDENCE_ROOT/task-0.1-scope.sha256"
```

Expected: `branch.txt` contains `agent/dan-release1-integration`; `head.txt` freezes the clean committed implementation base HEAD; both NUL-delimited status files are byte-identical and empty; `candidate-target.txt` contains `1852d7f62d132b0e96543c4ec87b255bbab2381c`; and `task-0.1-scope.json` canonically binds that base HEAD, the empty status/diff hashes, and the exact eight Task 0.1 paths. If the branch is wrong, either status read is nonempty, HEAD moves, or the scope file already exists, stop. Preserve `DAN_FREEZE_EVIDENCE_ROOT` in the executor handoff; this immutable scope and its SHA-256 govern both Task 0.1 checkpoint phases.

- [ ] **Step 2: Write the RED checkpoint contract**

```python
def test_checkpoint_binds_head_status_candidate_and_inventory_sha(tmp_path: Path) -> None:
    fixture = committed_task_fixture(tmp_path)
    report = fixture.capture(phase="final-clean")
    assert report.branch == "agent/dan-release1-integration"
    assert report.head == git_head(report.repo)
    assert report.base_head == fixture.scope.base_head
    assert report.candidate.target_sha == git_tag_target(report.repo, report.candidate.name)
    assert report.inventory.sha256 == sha256_file(report.inventory.path)
    assert report.dirty_paths == ()
    assert report.observed_delta_paths == report.scope_paths


def test_checkpoint_refuses_existing_output(tmp_path: Path) -> None:
    output = tmp_path / "checkpoint.json"
    output.write_text("historical", encoding="utf-8")
    with pytest.raises(FileExistsError):
        write_checkpoint_exclusive(output, checkpoint_fixture())
    assert output.read_text(encoding="utf-8") == "historical"


def test_checkpoint_emits_shared_release_evidence_envelope(frozen_repo: Path) -> None:
    report = run_checkpoint(frozen_repo)
    assert report.kind == "release_checkpoint"
    assert report.producer_id == "dan-release-checkpoint:v1"
    assert report.subject_sha == git_head(frozen_repo)
    assert report.status == "green"
    assert report.unknown_evidence == ()
    assert report.report_sha256 == canonical_envelope_sha256(report)


def test_release_producer_ids_are_fixed_and_central() -> None:
    assert dict(RELEASE_PRODUCER_IDS) == {
        "release_checkpoint": "dan-release-checkpoint:v1",
        "baseline_v2": "dan-test-baseline:v2",
        "batch1_data_cutover": "dan-release-report:batch1_data_cutover:v1",
        "batch2_runtime_host": "dan-release-report:batch2_runtime_host:v1",
        "batch3_persona_config_voice": "dan-release-report:batch3_persona_config_voice:v1",
        "batch4_panel_test_release": "dan-release-report:batch4_panel_test_release:v1",
        "offline_clean_clone_build": "dan-release-build-gate:v1",
        "active_home_release_audit": "dan-release-audit:v2",
        "deployment_receipt": "dan-deployment-receipt:v1",
        "rollback_rehearsal": "dan-cutover-rehearsal:v1",
        "voice_acceptance_m5": "dan-voice-acceptance:v2",
        "agent_review_summary": "dan-review-evidence:v1",
    }
    assert tuple(BATCH_REPORT_PRODUCER_IDS) == (
        "batch1_data_cutover",
        "batch2_runtime_host",
        "batch3_persona_config_voice",
        "batch4_panel_test_release",
    )
    assert RELEASE_PRODUCER_IDS["release_checkpoint"] == RELEASE_CHECKPOINT_PRODUCER_ID
    assert RELEASE_PRODUCER_IDS["baseline_v2"] == TEST_BASELINE_PRODUCER_ID
    assert RELEASE_PRODUCER_IDS["deployment_receipt"] == DEPLOYMENT_RECEIPT_PRODUCER_ID
    assert RELEASE_PRODUCER_IDS["offline_clean_clone_build"] == RELEASE_BUILD_GATE_PRODUCER_ID
    assert RELEASE_PRODUCER_IDS["voice_acceptance_m5"] == VOICE_ACCEPTANCE_PRODUCER_ID
    assert RELEASE_PRODUCER_IDS["active_home_release_audit"] == RELEASE_AUDIT_PRODUCER_ID
    assert RELEASE_PRODUCER_IDS["rollback_rehearsal"] == ROLLBACK_REHEARSAL_PRODUCER_ID
    assert RELEASE_PRODUCER_IDS["agent_review_summary"] == REVIEW_EVIDENCE_PRODUCER_ID


def test_review_scope_registry_has_the_exact_expanded_release1_task_set(repo: Path) -> None:
    registry = read_review_scope_v1(repo / "release/review-scope-v1.json")
    assert registry.task_ids == (
        "0.1", "0.2", "0.3", "0.4",
        "1.1", "1.2", "1.3", "1.4", "1.5", "1.6", "1.7", "1.8", "1.9",
        "2.1", "2.2", "2.3", "2.4", "2.5", "2.6", "2.7", "2.8", "2.9",
        "3.1", "3.2", "3.3", "3.4", "3.5", "3.6",
        "4.1", "4.2", "4.3", "4.4", "4.5", "4.6", "4.7",
        "5.1", "5.2", "5.3", "5.4", "5.5", "5.6",
        "release1-final-integration",
    )
    for task in registry.tasks[:-1]:
        assert task.review_mode == "task-diff"
        assert task.allowed_paths == tuple(sorted(set(task.allowed_paths)))
        assert task.allowed_paths
        assert all(is_normalized_repo_file(path) for path in task.allowed_paths)
    final = registry.tasks[-1]
    assert final.review_mode == "checkpoint-to-final-head"
    assert final.allowed_paths == registry.union_paths(task_ids=registry.task_ids[:-1])


def test_checkpoint_hashes_the_fixed_repo_review_scope(checkpoint_fixture: CheckpointFixture) -> None:
    checkpoint_fixture.create_exact_scope_delta()
    report = checkpoint_fixture.capture(phase="precommit-delta")
    assert report.review_scope.path == "release/review-scope-v1.json"
    assert report.review_scope.sha256 == sha256_file(
        checkpoint_fixture.repo / "release/review-scope-v1.json"
    )
    assert "--review-scope" not in checkpoint_fixture.cli_option_names


def test_evidence_root_rejects_protected_or_symlinked_ancestry(
    evidence_fixture: EvidenceRootFixture,
) -> None:
    for root in evidence_fixture.protected_roots_and_symlink_aliases:
        with pytest.raises(UnsafeEvidenceRoot):
            validate_evidence_root(root, active_roots=evidence_fixture.active_roots)


def test_evidence_writer_is_exclusive_0600_fsynced_and_strictly_parseable(
    evidence_fixture: EvidenceRootFixture,
) -> None:
    output = evidence_fixture.root / "report.json"
    write_evidence_envelope_exclusive(
        output,
        evidence_fixture.envelope,
        evidence_root=evidence_fixture.validated_root,
    )
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert read_evidence_envelope(
        output,
        evidence_root=evidence_fixture.validated_root,
        expected_kind="fixture",
    ) == evidence_fixture.envelope
    with pytest.raises(FileExistsError):
        write_evidence_envelope_exclusive(
            output,
            evidence_fixture.envelope,
            evidence_root=evidence_fixture.validated_root,
        )


def test_scope_manifest_refuses_a_dirty_base(checkpoint_fixture: CheckpointFixture) -> None:
    checkpoint_fixture.create_untracked_path("already-dirty.txt")
    with pytest.raises(DirtyScopeBase):
        checkpoint_fixture.write_scope_manifest()


def test_precommit_checkpoint_accepts_exact_tooling_delta(
    checkpoint_fixture: CheckpointFixture,
) -> None:
    checkpoint_fixture.create_exact_scope_delta()
    report = checkpoint_fixture.capture(phase="precommit-delta")
    assert report.head == report.base_head
    assert report.dirty_paths == report.scope_paths
    assert report.observed_delta_paths == report.scope_paths


def test_precommit_checkpoint_rejects_extra_or_missing_delta_path(
    checkpoint_fixture: CheckpointFixture,
) -> None:
    checkpoint_fixture.create_exact_scope_delta()
    checkpoint_fixture.create_unassigned_path("unexpected.txt")
    with pytest.raises(OutOfScopeWorktreeDelta):
        checkpoint_fixture.capture(phase="precommit-delta")
    checkpoint_fixture.remove_scoped_path("tests/test_release_checkpoint.py")
    with pytest.raises(IncompleteWorktreeDelta):
        checkpoint_fixture.capture(phase="precommit-delta")


def test_final_clean_checkpoint_requires_only_scoped_committed_delta(
    checkpoint_fixture: CheckpointFixture,
) -> None:
    checkpoint_fixture.create_exact_scope_delta()
    checkpoint_fixture.commit_scope_delta()
    report = checkpoint_fixture.capture(phase="final-clean")
    assert report.dirty_paths == ()
    assert report.head != report.base_head
    checkpoint_fixture.commit_unassigned_path("unexpected.txt")
    with pytest.raises(OutOfScopeCommittedDelta):
        checkpoint_fixture.capture(phase="final-clean")
```

- [ ] **Step 3: Verify RED**

```bash
dan_new_evidence task-0.1-red
env -u DAN_CONFIG -u VOICE_CONFIG_DIR \
  HOME="$DAN_TEST_HOME" XDG_CACHE_HOME="$DAN_TEST_HOME/.cache" \
  XDG_CONFIG_HOME="$DAN_TEST_HOME/.config" XDG_DATA_HOME="$DAN_TEST_HOME/.local/share" \
  TMPDIR="$DAN_TEST_RUNTIME" DAN_RUNTIME_DIR="$DAN_TEST_RUNTIME" \
  DAN_DB_PATH="$DAN_TASK_EVIDENCE_ROOT/dan.sqlite3" \
  DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider \
  --basetemp "$DAN_TASK_EVIDENCE_ROOT/pytest-tmp" \
  --junitxml "$DAN_TASK_EVIDENCE_ROOT/pytest-red.xml" \
  tests/test_release_evidence.py tests/test_release_checkpoint.py
```

Expected: import failures for `dan.release.producer_ids`, `dan.release.evidence`, and `dan.release.checkpoint`; the failure is the missing Batch 0 schema/tooling, not an unrelated fixture or environment error.

- [ ] **Step 4: Implement the immutable model and writer**

```python
# dan/release/producer_ids.py — the only production source of producer-ID literals
from types import MappingProxyType
from typing import Final


RELEASE_CHECKPOINT_PRODUCER_ID: Final = "dan-release-checkpoint:v1"
TEST_BASELINE_PRODUCER_ID: Final = "dan-test-baseline:v2"
BATCH1_DATA_CUTOVER_REPORT_PRODUCER_ID: Final = "dan-release-report:batch1_data_cutover:v1"
BATCH2_RUNTIME_HOST_REPORT_PRODUCER_ID: Final = "dan-release-report:batch2_runtime_host:v1"
BATCH3_PERSONA_CONFIG_VOICE_REPORT_PRODUCER_ID: Final = (
    "dan-release-report:batch3_persona_config_voice:v1"
)
BATCH4_PANEL_TEST_RELEASE_REPORT_PRODUCER_ID: Final = (
    "dan-release-report:batch4_panel_test_release:v1"
)
RELEASE_BUILD_GATE_PRODUCER_ID: Final = "dan-release-build-gate:v1"
RELEASE_AUDIT_PRODUCER_ID: Final = "dan-release-audit:v2"
DEPLOYMENT_RECEIPT_PRODUCER_ID: Final = "dan-deployment-receipt:v1"
ROLLBACK_REHEARSAL_PRODUCER_ID: Final = "dan-cutover-rehearsal:v1"
VOICE_ACCEPTANCE_PRODUCER_ID: Final = "dan-voice-acceptance:v2"
REVIEW_EVIDENCE_PRODUCER_ID: Final = "dan-review-evidence:v1"

BATCH_REPORT_PRODUCER_IDS: Final[Mapping[str, str]] = MappingProxyType({
    "batch1_data_cutover": BATCH1_DATA_CUTOVER_REPORT_PRODUCER_ID,
    "batch2_runtime_host": BATCH2_RUNTIME_HOST_REPORT_PRODUCER_ID,
    "batch3_persona_config_voice": BATCH3_PERSONA_CONFIG_VOICE_REPORT_PRODUCER_ID,
    "batch4_panel_test_release": BATCH4_PANEL_TEST_RELEASE_REPORT_PRODUCER_ID,
})
CORE_EVIDENCE_PRODUCERS: Final[Mapping[str, str]] = MappingProxyType({
    "release_checkpoint": RELEASE_CHECKPOINT_PRODUCER_ID,
    "baseline_v2": TEST_BASELINE_PRODUCER_ID,
})
RELEASE_PRODUCER_IDS: Final[Mapping[str, str]] = MappingProxyType({
    **CORE_EVIDENCE_PRODUCERS,
    **BATCH_REPORT_PRODUCER_IDS,
    "offline_clean_clone_build": RELEASE_BUILD_GATE_PRODUCER_ID,
    "active_home_release_audit": RELEASE_AUDIT_PRODUCER_ID,
    "deployment_receipt": DEPLOYMENT_RECEIPT_PRODUCER_ID,
    "rollback_rehearsal": ROLLBACK_REHEARSAL_PRODUCER_ID,
    "voice_acceptance_m5": VOICE_ACCEPTANCE_PRODUCER_ID,
    "agent_review_summary": REVIEW_EVIDENCE_PRODUCER_ID,
})


# dan/release/evidence.py imports producer IDs; it never repeats their literals.
@dataclass(frozen=True)
class EvidenceInput:
    role: str
    sha256: str


@dataclass(frozen=True)
class ActiveEvidenceRoots:
    repo: Path
    home_dan: Path
    home_config: Path
    home_claude: Path
    dan_config: Path
    voice_config: Path
    runtime: Path
    database: Path


@dataclass(frozen=True)
class ValidatedEvidenceRoot:
    path: Path


@dataclass(frozen=True)
class ReleaseEvidenceEnvelope:
    schema_version: Literal[1]
    kind: str
    producer_id: str
    created_at_utc: str
    subject_sha: str
    artifact_sha256: str | None
    status: Literal["green", "red", "unknown"]
    finding_codes: tuple[str, ...]
    unknown_evidence: tuple[str, ...]
    input_evidence: tuple[EvidenceInput, ...]
    result: Mapping[str, JsonValue]
    report_sha256: str


def active_evidence_roots_from_environment(*, repo: Path) -> ActiveEvidenceRoots: ...
def validate_evidence_root(
    root: Path,
    *,
    active_roots: ActiveEvidenceRoots,
) -> ValidatedEvidenceRoot: ...
def canonical_envelope_sha256(envelope: ReleaseEvidenceEnvelope) -> str: ...
def write_evidence_envelope_exclusive(
    path: Path,
    envelope: ReleaseEvidenceEnvelope,
    *,
    evidence_root: ValidatedEvidenceRoot,
) -> None: ...
def read_evidence_envelope(
    path: Path,
    *,
    evidence_root: ValidatedEvidenceRoot,
    expected_kind: str,
    expected_producer_id: str | None = None,
) -> ReleaseEvidenceEnvelope: ...


@dataclass(frozen=True)
class TaskScopeManifest:
    schema_version: Literal[1]
    branch: str
    base_head: str
    clean_status_sha256: str
    clean_index_diff_sha256: str
    clean_worktree_diff_sha256: str
    scope_paths: tuple[str, ...]


@dataclass(frozen=True)
class PathFingerprint:
    path: str
    kind: Literal["regular", "symlink", "absent"]
    sha256: str | None


@dataclass(frozen=True)
class ReviewTaskScope:
    task_id: str
    review_mode: Literal["task-diff", "checkpoint-to-final-head"]
    allowed_paths: tuple[str, ...]


@dataclass(frozen=True)
class ReviewScopeV1:
    schema_version: Literal[1]
    tasks: tuple[ReviewTaskScope, ...]

    @property
    def task_ids(self) -> tuple[str, ...]: ...
    def union_paths(self, *, task_ids: tuple[str, ...]) -> tuple[str, ...]: ...


@dataclass(frozen=True)
class ReleaseCheckpoint:
    schema_version: int
    created_at_utc: str
    phase: Literal["precommit-delta", "final-clean"]
    repo: str
    branch: str
    base_head: str
    head: str
    dirty_paths: tuple[str, ...]
    scope_paths: tuple[str, ...]
    observed_delta_paths: tuple[str, ...]
    delta_fingerprints: tuple[PathFingerprint, ...]
    scope_source: EvidenceRef
    review_scope: EvidenceRef
    candidate: CandidateRef
    inventory: EvidenceRef


def write_checkpoint_exclusive(path: Path, checkpoint: ReleaseCheckpoint) -> None:
    envelope = checkpoint_evidence_envelope(checkpoint)
    active_roots = active_evidence_roots_from_environment(repo=Path(checkpoint.repo))
    root = validate_evidence_root(Path(os.environ["DAN_RELEASE_EVIDENCE_ROOT"]), active_roots=active_roots)
    write_evidence_envelope_exclusive(path, envelope, evidence_root=root)
```

`release/review-scope-v1.json` is the only repository review-scope authority. Author it as canonical UTF-8 JSON with the exact expanded, ordered 42 IDs asserted above: tasks 0.1–0.4, 1.1–1.9, 2.1–2.9, 3.1–3.6, 4.1–4.7, 5.1–5.6, then `release1-final-integration`. For each ordinary task, `allowed_paths` is the sorted unique literal set of every `Create`, `Modify`, or `Delete` file in that task's committed plan at the frozen base HEAD; read-only files are not diff scope. Paths must be normalized repository-relative files with no glob, directory wildcard, symlink alias, absolute path, `.` or `..`. The final entry's `allowed_paths` is the exact sorted union of all 41 ordinary scopes and its mode is `checkpoint-to-final-head`; all other modes are `task-diff`. The strict reader rejects missing/extra/reordered task IDs, missing/extra keys, duplicate paths, a wrong final union, unknown modes, or non-canonical bytes. Tests may repeat the required ID tuple, but no second production manifest or caller-supplied task set exists.

The final-clean checkpoint hashes the fixed path as input role `review-scope-v1`. Batch 5 may only read that repository file, require its SHA-256 to equal the final-clean checkpoint input, and produce reviews for every listed ID; its CLI has no task-manifest, required-set, scope-extension, or scope-override option. `release1-final-integration` is special: its review subject must equal the exact final HEAD, its baseline must equal the final-clean release-checkpoint subject SHA, and its reviewed diff hash must cover the full binary `checkpoint_sha..final_head` diff—not a caller-selected subset. Any registry, HEAD, or full-diff drift invalidates the review and all dependent gates.

`dan/release/evidence.py` is the one complete envelope source of truth for all later batches. `dan/release/producer_ids.py` is separately the sole production authority for every Release 1 producer-ID literal: `evidence.py`, checkpoint/baseline code, later producer modules, strict validators, and Batch 5's registry import its constants or immutable mappings. Scripts and owning modules must not redefine the strings; only the exact contract test above repeats literals to detect drift. `CORE_EVIDENCE_PRODUCERS`, `BATCH_REPORT_PRODUCER_IDS`, and `RELEASE_PRODUCER_IDS` all live in this central module, and Batch 5 adds recipes without rebuilding the mapping. `report_sha256` is the SHA-256 of UTF-8 canonical JSON (sorted keys, compact separators, no NaN, newline excluded) with that field omitted. The strict parser rejects unknown/missing keys, duplicate JSON keys, invalid UTF-8/types/enums/hashes/timestamps, mismatched kind/producer expectations, non-canonical content, and a stored hash that differs from the recomputed hash. `validate_evidence_root()` rejects relative roots, nonexistent/non-directory roots, symlinked ancestry, and either-direction overlap with the checkout, active `~/.dan`, `~/.config`, `~/.claude`, `DAN_CONFIG`, voice config, active runtime, and active database. The writer requires a validated root and an existing non-symlink parent beneath it, refuses output outside it, uses exclusive creation at mode `0600`, fsyncs the file and parent directory, and never overwrites. Batch 5 may add report-producer recipes and aggregation but must import this schema/parser/writer and the central producer registry instead of redefining them.

`capture_release_checkpoint()` must use the existing inventory builder, resolve the exact annotated/lightweight tag target, and accept `expected_head`, `scope_manifest`, the scope file's expected SHA-256, and `phase`. Read the external task-scope bytes once, verify their digest before strict parsing, require canonical JSON and the exact eight sorted `scope_paths`, and reject a scope whose recorded base status or diffs are not empty. Independently open only `$repo/release/review-scope-v1.json`, strictly validate the registry contract above, and hash its exact bytes; there is no CLI path override. In `precommit-delta`, current HEAD must equal `base_head` and the complete staged, unstaged, untracked, removed, and type-changed path set must equal `scope_paths`; fingerprint every scoped path and reject any missing or extra path. In `final-clean`, current HEAD must equal the caller's new `expected_head`, index and worktree must be empty, `base_head` must be an ancestor of that HEAD, and the complete committed diff `base_head..HEAD` must equal `scope_paths`; fingerprint those final tree entries. Re-check HEAD, branch, tag, external scope digest, fixed review-scope bytes/digest, phase-specific status/diff, and fingerprints immediately before exclusive creation.

The CLI imports `RELEASE_CHECKPOINT_PRODUCER_ID` and wraps `ReleaseCheckpoint` in `ReleaseEvidenceEnvelope(kind="release_checkpoint", producer_id=RELEASE_CHECKPOINT_PRODUCER_ID, subject_sha=phase_checked_head, artifact_sha256=None)`. Its fixed recipe derives `status`; no CLI option may set status, producer, subject SHA, finding codes, unknowns, or the review-scope path. Inputs include SHA-256 records for the immutable external task-scope manifest, fixed repository review-scope bytes, inventory bytes, and canonical candidate-ref record. A scope, registry, status, HEAD, tag, or delta mismatch aborts before output; other missing observations become fail-closed `unknown` evidence, never caller-declared GREEN. Store only hashes, paths, types, and status—not private contents. The pre-commit envelope proves the bootstrap delta, while release consumers accept only a `final-clean` result bound to the post-commit HEAD.

- [ ] **Step 5: Verify GREEN and review**

```bash
dan_new_evidence task-0.1-green
env -u DAN_CONFIG -u VOICE_CONFIG_DIR \
  HOME="$DAN_TEST_HOME" XDG_CACHE_HOME="$DAN_TEST_HOME/.cache" \
  XDG_CONFIG_HOME="$DAN_TEST_HOME/.config" XDG_DATA_HOME="$DAN_TEST_HOME/.local/share" \
  TMPDIR="$DAN_TEST_RUNTIME" DAN_RUNTIME_DIR="$DAN_TEST_RUNTIME" \
  DAN_DB_PATH="$DAN_TASK_EVIDENCE_ROOT/dan.sqlite3" \
  DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider \
  --basetemp "$DAN_TASK_EVIDENCE_ROOT/pytest-tmp" \
  --junitxml "$DAN_TASK_EVIDENCE_ROOT/pytest-green.xml" \
  tests/test_release_evidence.py tests/test_release_checkpoint.py
RUFF_CACHE_DIR="$DAN_TASK_EVIDENCE_ROOT/ruff-cache" \
  .venv/bin/ruff check dan/release/producer_ids.py dan/release/evidence.py dan/release/checkpoint.py \
  tests/test_release_evidence.py tests/test_release_checkpoint.py
git diff --check
```

Expected: all pass. Spec reviewer must prove the old manifest and candidate tag cannot be overwritten.

- [ ] **Step 6: Validate the exact dirty tooling delta before commit**

```bash
test "$(git rev-parse HEAD)" = "$(cat "$DAN_FREEZE_EVIDENCE_ROOT/head.txt")"
test "$(shasum -a 256 "$DAN_FREEZE_EVIDENCE_ROOT/task-0.1-scope.json" | cut -d ' ' -f 1)" = \
  "$(cut -d ' ' -f 1 "$DAN_FREEZE_EVIDENCE_ROOT/task-0.1-scope.sha256")"
dan_new_evidence task-0.1-precommit-checkpoint
env PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
  .venv/bin/python scripts/dan-release-checkpoint \
  --repo . \
  --candidate-tag dan-v1-foundation-candidate \
  --phase precommit-delta \
  --expected-head "$(cat "$DAN_FREEZE_EVIDENCE_ROOT/head.txt")" \
  --scope-manifest "$DAN_FREEZE_EVIDENCE_ROOT/task-0.1-scope.json" \
  --scope-sha256 "$(cut -d ' ' -f 1 "$DAN_FREEZE_EVIDENCE_ROOT/task-0.1-scope.sha256")" \
  --output "$DAN_TASK_EVIDENCE_ROOT/release1-task-0.1-precommit.json"
```

Expected: a new exclusive `release_checkpoint` envelope at the printed `$DAN_TASK_EVIDENCE_ROOT/release1-task-0.1-precommit.json`, phase `precommit-delta`, producer `dan-release-checkpoint:v1`, subject SHA equal to the still-frozen base HEAD, exactly eight dirty/scoped/observed delta paths, canonical `report_sha256`, complete input hashes including `review-scope-v1`, derived GREEN, and no unknown evidence. Any ninth path or missing scoped file fails without writing output. This command does not stage or commit; the authorized release orchestrator commits Task 0.1 separately.

- [ ] **Step 7: Re-run from the clean final HEAD after the Task 0.1 commit**

```bash
test -z "$(git status --porcelain=v1)"
DAN_TASK_0_1_FINAL_HEAD="$(git rev-parse HEAD)"
test "$DAN_TASK_0_1_FINAL_HEAD" != "$(cat "$DAN_FREEZE_EVIDENCE_ROOT/head.txt")"
git merge-base --is-ancestor \
  "$(cat "$DAN_FREEZE_EVIDENCE_ROOT/head.txt")" "$DAN_TASK_0_1_FINAL_HEAD"
dan_new_evidence task-0.1-final-clean-checkpoint
env PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
  .venv/bin/python scripts/dan-release-checkpoint \
  --repo . \
  --candidate-tag dan-v1-foundation-candidate \
  --phase final-clean \
  --expected-head "$DAN_TASK_0_1_FINAL_HEAD" \
  --scope-manifest "$DAN_FREEZE_EVIDENCE_ROOT/task-0.1-scope.json" \
  --scope-sha256 "$(cut -d ' ' -f 1 "$DAN_FREEZE_EVIDENCE_ROOT/task-0.1-scope.sha256")" \
  --output "$DAN_TASK_EVIDENCE_ROOT/release1-task-0.1-final-clean.json"
```

Expected: the final worktree and index are empty; the committed diff from the frozen base HEAD contains exactly the eight scoped paths; and the new exclusive envelope is phase `final-clean`, subject SHA `$DAN_TASK_0_1_FINAL_HEAD`, canonical, GREEN, and complete. Record this exact path in the release handoff. This final-clean envelope supersedes the pre-commit evidence for later release gates and mutates only the external evidence root.

## Task 0.2: Detect and remove only safe legacy checkout caches

**Files:**

- Create: `dan/release/checkout_hygiene.py`
- Create: `scripts/dan-checkout-hygiene`
- Create: `tests/test_checkout_hygiene.py`
- Modify: `tests/test_imports.py`

- [ ] **Step 1: Write RED tests for physical namespace and safe plan**

```python
def test_cleanup_plan_targets_only_safe_cache_entries(tmp_path: Path) -> None:
    legacy = tmp_path / "jarvis"
    cache = legacy / "sub" / "__pycache__"
    cache.mkdir(parents=True)
    (cache / "module.cpython-311.pyc").write_bytes(b"pyc")
    (legacy / "keep.py").write_text("keep = True\n", encoding="utf-8")
    plan = plan_safe_cache_removal(repo=tmp_path, legacy_root=legacy)
    assert all(item.kind in {"pyc", "pycache", "ds_store"} for item in plan.items)
    assert legacy / "keep.py" not in {item.path for item in plan.items}


def test_final_import_surface_rejects_physical_jarvis_namespace(tmp_path: Path) -> None:
    (tmp_path / "jarvis").mkdir()
    finding = scan_checkout_hygiene(tmp_path)
    assert finding.legacy_namespace_present is True
```

- [ ] **Step 2: Verify RED**

```bash
dan_new_evidence task-0.2-red
env -u DAN_CONFIG -u VOICE_CONFIG_DIR \
  HOME="$DAN_TEST_HOME" XDG_CACHE_HOME="$DAN_TEST_HOME/.cache" \
  XDG_CONFIG_HOME="$DAN_TEST_HOME/.config" XDG_DATA_HOME="$DAN_TEST_HOME/.local/share" \
  TMPDIR="$DAN_TEST_RUNTIME" DAN_RUNTIME_DIR="$DAN_TEST_RUNTIME" \
  DAN_DB_PATH="$DAN_TASK_EVIDENCE_ROOT/dan.sqlite3" \
  DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider \
  --basetemp "$DAN_TASK_EVIDENCE_ROOT/pytest-tmp" \
  --junitxml "$DAN_TASK_EVIDENCE_ROOT/pytest-red.xml" \
  tests/test_checkout_hygiene.py tests/test_imports.py
```

- [ ] **Step 3: Implement the exact-root scanner and exclusive report writer**

Implementation contract:

```python
SAFE_CACHE_NAMES = frozenset({"__pycache__", ".DS_Store"})


def plan_safe_cache_removal(*, repo: Path, legacy_root: Path) -> HygienePlan:
    repo = repo.resolve(strict=True)
    legacy_root = legacy_root.resolve(strict=True)
    if legacy_root.parent != repo or legacy_root.name != "jarvis":
        raise UnsafeCleanupTarget(str(legacy_root))
    # Enumerate entries, reject symlinks/non-cache files, return exact paths.
```

Apply mode must re-resolve every path immediately before deletion, refuse symlinks, remove files first and then empty `__pycache__` directories. It must refuse to remove the top-level `jarvis/` if any non-cache entry remains.

The CLI requires `--output PATH`, opens that JSON report with exclusive creation, flushes/fsyncs it, and records the resolved repo root plus every planned, skipped, and removed path. It must reject an output path inside the checkout, active `~/.dan`, or active config; Batch 0 always passes a path under `$DAN_TASK_EVIDENCE_ROOT`.

- [ ] **Step 4: Verify GREEN**

```bash
dan_new_evidence task-0.2-green
env -u DAN_CONFIG -u VOICE_CONFIG_DIR \
  HOME="$DAN_TEST_HOME" XDG_CACHE_HOME="$DAN_TEST_HOME/.cache" \
  XDG_CONFIG_HOME="$DAN_TEST_HOME/.config" XDG_DATA_HOME="$DAN_TEST_HOME/.local/share" \
  TMPDIR="$DAN_TEST_RUNTIME" DAN_RUNTIME_DIR="$DAN_TEST_RUNTIME" \
  DAN_DB_PATH="$DAN_TASK_EVIDENCE_ROOT/dan.sqlite3" \
  DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider \
  --basetemp "$DAN_TASK_EVIDENCE_ROOT/pytest-tmp" \
  --junitxml "$DAN_TASK_EVIDENCE_ROOT/pytest-green.xml" \
  tests/test_checkout_hygiene.py tests/test_imports.py
RUFF_CACHE_DIR="$DAN_TASK_EVIDENCE_ROOT/ruff-cache" \
  .venv/bin/ruff check dan/release/checkout_hygiene.py \
  tests/test_checkout_hygiene.py tests/test_imports.py
git diff --check
```

Expected RED: import failure for `dan.release.checkout_hygiene`. Expected GREEN: both files pass and the report-writer tests prove exclusive creation plus protected-root rejection.

- [ ] **Step 5: Run controlled local cleanup and re-check imports**

```bash
dan_new_evidence task-0.2-controlled-cleanup
env -u DAN_CONFIG -u VOICE_CONFIG_DIR \
  HOME="$DAN_TEST_HOME" XDG_CACHE_HOME="$DAN_TEST_HOME/.cache" \
  XDG_CONFIG_HOME="$DAN_TEST_HOME/.config" XDG_DATA_HOME="$DAN_TEST_HOME/.local/share" \
  TMPDIR="$DAN_TEST_RUNTIME" DAN_RUNTIME_DIR="$DAN_TEST_RUNTIME" \
  DAN_DB_PATH="$DAN_TASK_EVIDENCE_ROOT/dan.sqlite3" \
  DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
  .venv/bin/python scripts/dan-checkout-hygiene --repo . --legacy-root ./jarvis \
  --output "$DAN_TASK_EVIDENCE_ROOT/checkout-hygiene-plan.json"
env -u DAN_CONFIG -u VOICE_CONFIG_DIR \
  HOME="$DAN_TEST_HOME" XDG_CACHE_HOME="$DAN_TEST_HOME/.cache" \
  XDG_CONFIG_HOME="$DAN_TEST_HOME/.config" XDG_DATA_HOME="$DAN_TEST_HOME/.local/share" \
  TMPDIR="$DAN_TEST_RUNTIME" DAN_RUNTIME_DIR="$DAN_TEST_RUNTIME" \
  DAN_DB_PATH="$DAN_TASK_EVIDENCE_ROOT/dan.sqlite3" \
  DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
  .venv/bin/python scripts/dan-checkout-hygiene --repo . --legacy-root ./jarvis \
  --apply-safe-cache --output "$DAN_TASK_EVIDENCE_ROOT/checkout-hygiene-apply.json"
env -u DAN_CONFIG -u VOICE_CONFIG_DIR \
  HOME="$DAN_TEST_HOME" XDG_CACHE_HOME="$DAN_TEST_HOME/.cache" \
  XDG_CONFIG_HOME="$DAN_TEST_HOME/.config" XDG_DATA_HOME="$DAN_TEST_HOME/.local/share" \
  TMPDIR="$DAN_TEST_RUNTIME" DAN_RUNTIME_DIR="$DAN_TEST_RUNTIME" \
  DAN_DB_PATH="$DAN_TASK_EVIDENCE_ROOT/dan.sqlite3" \
  DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider \
  --basetemp "$DAN_TASK_EVIDENCE_ROOT/pytest-tmp" \
  --junitxml "$DAN_TASK_EVIDENCE_ROOT/imports-after-cleanup.xml" tests/test_imports.py
```

Expected: apply report lists every removed path; `find_spec("jarvis") is None`. If any source file or symlink exists, stop and report it instead of deleting.

## Task 0.3: Add a fail-closed audio execution boundary

**Files:**

- Modify: `dan/audio/__init__.py`
- Create: `dan/audio/execution.py`
- Create: `tests/audio_guard_plugin.py`
- Create: `tests/test_audio_execution_guard.py`
- Modify: `dan/voice/player.py`
- Modify: `dan/voice/tts.py`
- Modify: `dan/voice/recorder.py`
- Modify: `dan/migration/test_safety.py`
- Modify: `tests/test_test_safety.py`
- Modify: `tests/conftest.py`

- [ ] **Step 1: Write RED tests at the real execution edges**

```python
def test_disable_audio_blocks_coreaudio_and_supertonic(
    monkeypatch: pytest.MonkeyPatch,
    wav_chunk: SynthesizedChunk,
    render_snapshot: RenderSnapshot,
) -> None:
    monkeypatch.setenv("DAN_DISABLE_AUDIO", "1")
    backend = RecordingAudioBackend()
    with pytest.raises(AudioExecutionDisabled):
        CoreAudioPlayer(backend=backend).play(
            wav_chunk,
            should_play=lambda: True,
            on_started=lambda: None,
        )
    assert backend.start_calls == 0
    engine = supertonic_fixture()
    with pytest.raises(AudioExecutionDisabled):
        engine.synthesize("never", render_snapshot)
    assert engine.spawn_count == 0


def test_disable_audio_blocks_default_backend_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DAN_DISABLE_AUDIO", "1")
    backend_factory = ForbiddenBackendFactory()
    monkeypatch.setattr(player_module, "_AVFoundationBackend", backend_factory)
    with pytest.raises(AudioExecutionDisabled):
        CoreAudioPlayer()
    assert backend_factory.calls == 0


def test_disable_mic_blocks_sox_before_file_or_process_creation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DAN_DISABLE_MIC", "1")
    recorder = sox_recorder_fixture()
    with pytest.raises(MicrophoneExecutionDisabled):
        recorder.start()
    assert recorder.workdir_entries() == ()
    assert recorder.spawn_count == 0


def test_non_audio_subprocess_still_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DAN_DISABLE_AUDIO", "1")
    completed = subprocess.run([sys.executable, "-c", "print('ok')"], check=True)
    assert completed.returncode == 0
```

Add a fixture test whose test body imports a helper containing `afplay`; classification must be `live/manual` even though the string is not in the test body.

- [ ] **Step 2: Verify RED**

```bash
dan_new_evidence task-0.3-red
env -u DAN_CONFIG -u VOICE_CONFIG_DIR \
  HOME="$DAN_TEST_HOME" XDG_CACHE_HOME="$DAN_TEST_HOME/.cache" \
  XDG_CONFIG_HOME="$DAN_TEST_HOME/.config" XDG_DATA_HOME="$DAN_TEST_HOME/.local/share" \
  TMPDIR="$DAN_TEST_RUNTIME" DAN_RUNTIME_DIR="$DAN_TEST_RUNTIME" \
  DAN_DB_PATH="$DAN_TASK_EVIDENCE_ROOT/dan.sqlite3" \
  DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider \
  --basetemp "$DAN_TASK_EVIDENCE_ROOT/pytest-tmp" \
  --junitxml "$DAN_TASK_EVIDENCE_ROOT/pytest-red.xml" \
  tests/test_audio_execution_guard.py tests/test_test_safety.py
```

Expected: missing `dan.audio.execution` and imported-helper classification failure.

- [ ] **Step 3: Implement one guard, no product test mode**

```python
class AudioExecutionDisabled(RuntimeError):
    pass


class MicrophoneExecutionDisabled(RuntimeError):
    pass


def assert_audio_execution_allowed(*, operation: str) -> None:
    if os.environ.get("DAN_DISABLE_AUDIO") == "1":
        raise AudioExecutionDisabled(f"audio execution disabled: {operation}")


def assert_microphone_execution_allowed(*, operation: str) -> None:
    if os.environ.get("DAN_DISABLE_MIC") == "1":
        raise MicrophoneExecutionDisabled(f"microphone execution disabled: {operation}")
```

Call the audio guard immediately before native player initialization/playback and before any Supertonic serve/CLI synthesis subprocess. Call the microphone guard before the recorder creates a WAV or spawns `sox`. The pytest plugin additionally denies known audio executables on `subprocess.Popen` and records that it loaded; it must not introduce a product-facing `mock` or `test` provider.

- [ ] **Step 4: Verify GREEN and static-classifier coverage**

```bash
dan_new_evidence task-0.3-green
env -u DAN_CONFIG -u VOICE_CONFIG_DIR \
  HOME="$DAN_TEST_HOME" XDG_CACHE_HOME="$DAN_TEST_HOME/.cache" \
  XDG_CONFIG_HOME="$DAN_TEST_HOME/.config" XDG_DATA_HOME="$DAN_TEST_HOME/.local/share" \
  TMPDIR="$DAN_TEST_RUNTIME" DAN_RUNTIME_DIR="$DAN_TEST_RUNTIME" \
  DAN_DB_PATH="$DAN_TASK_EVIDENCE_ROOT/dan.sqlite3" \
  DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
  .venv/bin/python -m pytest -q -p tests.audio_guard_plugin -p no:cacheprovider \
  --basetemp "$DAN_TASK_EVIDENCE_ROOT/pytest-tmp" \
  --junitxml "$DAN_TASK_EVIDENCE_ROOT/pytest-green.xml" \
  tests/test_audio_execution_guard.py tests/test_test_safety.py tests/test_audio_player.py \
  tests/test_voice_tts_supertonic.py tests/test_voice_recorder.py
RUFF_CACHE_DIR="$DAN_TASK_EVIDENCE_ROOT/ruff-cache" \
  .venv/bin/ruff check dan/audio dan/voice/player.py dan/voice/tts.py \
  dan/voice/recorder.py dan/migration/test_safety.py tests/audio_guard_plugin.py \
  tests/test_audio_execution_guard.py
git diff --check
```

Expected: guard-loaded marker is present and no real audio edge executes.

## Task 0.4: Bind baseline v2 to checkout, interpreter and collected node IDs

**Files:**

- Modify: `scripts/dan-test-baseline`
- Read: `dan/release/evidence.py`
- Modify: `dan/migration/test_safety.py`
- Modify: `tests/test_test_safety.py`

- [ ] **Step 1: Write RED report-v2 tests**

```python
def test_report_v2_binds_guard_checkout_and_interpreter(tmp_path: Path) -> None:
    report = run_baseline_fixture(tmp_path)
    assert report.kind == "baseline_v2"
    assert report.producer_id == "dan-test-baseline:v2"
    assert report.subject_sha == fixture_head(tmp_path)
    assert report.result["schema_version"] == 2
    assert report.result["interpreter"]["realpath"] == str(Path(sys.executable).resolve())
    assert report.result["audio_guard"]["loaded"] is True
    assert report.result["collection"]["nodeids_sha256"] == sha256_lines(
        report.result["collection"]["nodeids"]
    )
    assert report.status == "green"
    assert report.unknown_evidence == ()
    assert report.report_sha256 == canonical_envelope_sha256(report)


def test_collection_and_execution_use_same_controlled_command(tmp_path: Path) -> None:
    report = run_baseline_fixture(tmp_path)
    assert report.result["collection"]["command_sha256"] == report.result["execution"]["command_sha256"]
```

- [ ] **Step 2: Verify RED**

```bash
dan_new_evidence task-0.4-red
env -u DAN_CONFIG -u VOICE_CONFIG_DIR \
  HOME="$DAN_TEST_HOME" XDG_CACHE_HOME="$DAN_TEST_HOME/.cache" \
  XDG_CONFIG_HOME="$DAN_TEST_HOME/.config" XDG_DATA_HOME="$DAN_TEST_HOME/.local/share" \
  TMPDIR="$DAN_TEST_RUNTIME" DAN_RUNTIME_DIR="$DAN_TEST_RUNTIME" \
  DAN_DB_PATH="$DAN_TASK_EVIDENCE_ROOT/dan.sqlite3" \
  DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
  .venv/bin/python -m pytest -q -p tests.audio_guard_plugin -p no:cacheprovider \
  --basetemp "$DAN_TASK_EVIDENCE_ROOT/pytest-tmp" \
  --junitxml "$DAN_TASK_EVIDENCE_ROOT/pytest-red.xml" tests/test_test_safety.py
```

- [ ] **Step 3: Implement baseline report v2**

The command builder must use `Path(sys.executable).resolve() -m pytest`, explicitly load `-p tests.audio_guard_plugin`, prepend a temporary directory of fake audio executables to `PATH`, use the same base argv for collect and execute, and reject interpreters/shebangs resolving into legacy repo roots. `scripts/dan-test-baseline` requires `--pytest-plugin tests.audio_guard_plugin`; that exact string is its only accepted value and is also enforced in the built argv, so the option cannot select a weaker plugin. It imports `TEST_BASELINE_PRODUCER_ID`, requires absolute `HOME` and `DAN_TEST_REPORT_HOME` paths under the validated external evidence root, puts pytest `--basetemp`, fake executables, runtime state, database, and every intermediate report below `DAN_TEST_REPORT_HOME`, and rejects any overlap with the checkout, active `~/.dan`, active `~/.config`, or active config overrides. The final output is `$DAN_TEST_REPORT_HOME/baseline-v2.json`, a shared `ReleaseEvidenceEnvelope(kind="baseline_v2", producer_id=TEST_BASELINE_PRODUCER_ID)`. It snapshots HEAD before and after, uses the final unchanged HEAD as `subject_sha`, records SHA-256 inputs for node IDs, controlled argv, interpreter identity, guard module, and checkout status, and derives GREEN only when the exact node set passes with the guard loaded. Any missing observation populates `unknown_evidence` and prevents GREEN; the CLI exposes no self-attestation override.

- [ ] **Step 4: Verify focused GREEN**

```bash
dan_new_evidence task-0.4-green
mkdir -p "$DAN_TASK_EVIDENCE_ROOT/test-report"
env -u DAN_CONFIG -u VOICE_CONFIG_DIR \
  HOME="$DAN_TEST_HOME" XDG_CACHE_HOME="$DAN_TEST_HOME/.cache" \
  XDG_CONFIG_HOME="$DAN_TEST_HOME/.config" XDG_DATA_HOME="$DAN_TEST_HOME/.local/share" \
  TMPDIR="$DAN_TEST_RUNTIME" DAN_RUNTIME_DIR="$DAN_TEST_RUNTIME" \
  DAN_DB_PATH="$DAN_TASK_EVIDENCE_ROOT/dan.sqlite3" \
  DAN_TEST_REPORT_HOME="$DAN_TASK_EVIDENCE_ROOT/test-report" \
  DAN_RELEASE_EVIDENCE_ROOT="$DAN_RELEASE_EVIDENCE_ROOT" \
  DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
  .venv/bin/python -m pytest -q -p tests.audio_guard_plugin -p no:cacheprovider \
  --basetemp "$DAN_TASK_EVIDENCE_ROOT/pytest-tmp" \
  --junitxml "$DAN_TASK_EVIDENCE_ROOT/pytest-green.xml" tests/test_test_safety.py
RUFF_CACHE_DIR="$DAN_TASK_EVIDENCE_ROOT/ruff-cache" \
  .venv/bin/ruff check scripts/dan-test-baseline dan/migration/test_safety.py \
  tests/test_test_safety.py
git diff --check
```

Expected RED: schema-v2 assertions fail. Expected GREEN: all focused tests pass and prove protected-root rejection as well as equal collect/execute command hashes.

- [ ] **Step 5: Run the real baseline v2**

```bash
dan_new_evidence task-0.4-real-baseline
mkdir -p "$DAN_TASK_EVIDENCE_ROOT/baseline-report"
env -u DAN_CONFIG -u VOICE_CONFIG_DIR \
  HOME="$DAN_TEST_HOME" XDG_CACHE_HOME="$DAN_TEST_HOME/.cache" \
  XDG_CONFIG_HOME="$DAN_TEST_HOME/.config" XDG_DATA_HOME="$DAN_TEST_HOME/.local/share" \
  TMPDIR="$DAN_TEST_RUNTIME" DAN_RUNTIME_DIR="$DAN_TEST_RUNTIME" \
  DAN_DB_PATH="$DAN_TASK_EVIDENCE_ROOT/dan.sqlite3" \
  DAN_TEST_REPORT_HOME="$DAN_TASK_EVIDENCE_ROOT/baseline-report" \
  DAN_RELEASE_EVIDENCE_ROOT="$DAN_RELEASE_EVIDENCE_ROOT" \
  DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
  .venv/bin/python scripts/dan-test-baseline \
  --pytest-plugin tests.audio_guard_plugin
```

Expected: the printed `$DAN_TASK_EVIDENCE_ROOT/baseline-report/baseline-v2.json` is envelope schema 1 with producer `dan-test-baseline:v2`, result schema 2, final unchanged subject SHA, exact collected/passed/failed node sets, loaded guard proof, canonical/input hashes, derived GREEN, no unknown evidence, and matching checkout/interpreter hashes. Record that exact file in the handoff. Any failed node blocks Batch 1.

- [ ] **Step 6: Batch 0 review gate**

```bash
dan_new_evidence task-0.review
RUFF_CACHE_DIR="$DAN_TASK_EVIDENCE_ROOT/ruff-cache" \
  .venv/bin/ruff check dan/release dan/audio dan/migration/test_safety.py tests
git diff --check > "$DAN_TASK_EVIDENCE_ROOT/git-diff-check.txt"
git status --porcelain=v1 -z > "$DAN_TASK_EVIDENCE_ROOT/final-status.z"
```

Reviewers must verify: no path outside the declared task scopes entered the diff; no historical evidence was overwritten; cleanup was exact-root and recoverable from its report; audio was not started; baseline is reproducible from a clean checkout.
