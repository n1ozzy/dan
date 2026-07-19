"""Immutable checkpoint capture for the Release 1 remediation series."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Literal, cast

from dan.migration.inventory import CANONICAL_MANIFEST_RELATIVE_PATH, validate_manifest
from dan.release.evidence import (
    EvidenceInput,
    JsonValue,
    ReleaseEvidenceEnvelope,
    active_evidence_roots_from_environment,
    canonical_envelope_sha256,
    validate_evidence_root,
    write_evidence_envelope_exclusive,
)
from dan.release.producer_ids import RELEASE_CHECKPOINT_PRODUCER_ID

EXPECTED_BRANCH = "agent/dan-release1-integration"
EXPECTED_CANDIDATE_TAG = "dan-v1-foundation-candidate"
EXPECTED_CANDIDATE_REF = f"refs/tags/{EXPECTED_CANDIDATE_TAG}"
EXPECTED_CANDIDATE_TARGET_SHA = "1852d7f62d132b0e96543c4ec87b255bbab2381c"
REVIEW_SCOPE_PATH = "release/review-scope-v1.json"
TASK_0_1_SCOPE_PATHS = (
    "dan/release/__init__.py",
    "dan/release/checkpoint.py",
    "dan/release/evidence.py",
    "dan/release/producer_ids.py",
    REVIEW_SCOPE_PATH,
    "scripts/dan-release-checkpoint",
    "tests/test_release_checkpoint.py",
    "tests/test_release_evidence.py",
)
RELEASE1_TASK_IDS = (
    "0.1",
    "0.2",
    "0.3",
    "0.4",
    "1.1",
    "1.2",
    "1.3",
    "1.4",
    "1.5",
    "1.6",
    "1.7",
    "1.8",
    "1.9",
    "2.1",
    "2.2",
    "2.3",
    "2.4",
    "2.5",
    "2.6",
    "2.7",
    "2.8",
    "2.9",
    "3.1",
    "3.2",
    "3.3",
    "3.4",
    "3.5",
    "3.6",
    "4.1",
    "4.2",
    "4.3",
    "4.4",
    "4.5",
    "4.6",
    "4.7",
    "5.1",
    "5.2",
    "5.3",
    "5.4",
    "5.5",
    "5.6",
    "release1-final-integration",
)
_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
_HEX_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_GIT_SHA = re.compile(r"[0-9a-f]{40,64}\Z")
_REPO_SENSITIVE_GIT_ENV = {
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_ATTR_NOSYSTEM",
    "GIT_CEILING_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_CONFIG",
    "GIT_CONFIG_COUNT",
    "GIT_CONFIG_GLOBAL",
    "GIT_CONFIG_NOSYSTEM",
    "GIT_CONFIG_PARAMETERS",
    "GIT_CONFIG_SYSTEM",
    "GIT_DIFF_OPTS",
    "GIT_DIR",
    "GIT_DISCOVERY_ACROSS_FILESYSTEM",
    "GIT_EXTERNAL_DIFF",
    "GIT_EXEC_PATH",
    "GIT_GLOB_PATHSPECS",
    "GIT_ICASE_PATHSPECS",
    "GIT_INDEX_FILE",
    "GIT_LITERAL_PATHSPECS",
    "GIT_NAMESPACE",
    "GIT_NOGLOB_PATHSPECS",
    "GIT_NO_REPLACE_OBJECTS",
    "GIT_OBJECT_DIRECTORY",
    "GIT_REPLACE_REF_BASE",
    "GIT_SHALLOW_FILE",
    "GIT_WORK_TREE",
}


class InvalidCheckpoint(ValueError):
    """Checkpoint input is malformed or changed while it is being captured."""


class InvalidTaskScope(InvalidCheckpoint):
    """The immutable Task 0.1 scope file does not satisfy its schema."""


class DirtyScopeBase(InvalidTaskScope):
    """The scope claims a base that was not captured cleanly."""


class InvalidReviewScope(InvalidCheckpoint):
    """The repository review-scope registry is not the fixed v1 contract."""


class OutOfScopeWorktreeDelta(InvalidCheckpoint):
    """The pre-commit worktree contains an unassigned path."""


class IncompleteWorktreeDelta(InvalidCheckpoint):
    """The pre-commit worktree is missing a required Task 0.1 path."""


class OutOfScopeCommittedDelta(InvalidCheckpoint):
    """The base-to-final committed diff contains an unassigned path."""


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
    def task_ids(self) -> tuple[str, ...]:
        return tuple(task.task_id for task in self.tasks)

    def union_paths(self, *, task_ids: tuple[str, ...]) -> tuple[str, ...]:
        selected = set(task_ids)
        unknown = selected.difference(self.task_ids)
        if unknown:
            raise KeyError(f"unknown review task IDs: {sorted(unknown)}")
        return tuple(
            sorted(
                {
                    path
                    for task in self.tasks
                    if task.task_id in selected
                    for path in task.allowed_paths
                }
            )
        )


@dataclass(frozen=True)
class EvidenceRef:
    path: str
    sha256: str


@dataclass(frozen=True)
class CandidateRef:
    name: str
    target_sha: str
    record_sha256: str


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
    unknown_evidence: tuple[str, ...]


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_json_bytes(payload: Mapping[str, object]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _reject_duplicate_keys(pairs: Sequence[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise InvalidCheckpoint(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _parse_canonical_object(
    encoded: bytes,
    *,
    error_type: type[InvalidCheckpoint],
) -> Mapping[str, object]:
    try:
        text = encoded.decode("utf-8", errors="strict")
        parsed = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, InvalidCheckpoint) as exc:
        raise error_type("input is not strict UTF-8 JSON") from exc
    if not isinstance(parsed, Mapping):
        raise error_type("JSON root must be an object")
    try:
        canonical = _canonical_json_bytes(parsed) + b"\n"
    except (TypeError, ValueError) as exc:
        raise error_type("input contains non-canonical JSON values") from exc
    if encoded != canonical:
        raise error_type("input bytes are not canonical JSON")
    return parsed


def _validate_sha256(value: object, *, name: str, error: type[InvalidCheckpoint]) -> str:
    if not isinstance(value, str) or not _HEX_SHA256.fullmatch(value):
        raise error(f"{name} must be a lowercase SHA-256")
    return value


def _directory_open_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )


def _file_open_flags() -> int:
    return os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)


def _open_absolute_directory_nofollow(path: Path) -> int:
    if not path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts[1:]):
        raise InvalidCheckpoint("path must be absolute and normalized")
    descriptor = os.open("/", _directory_open_flags())
    try:
        for component in path.parts[1:]:
            child = os.open(component, _directory_open_flags(), dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except OSError as exc:
        os.close(descriptor)
        raise InvalidCheckpoint(f"path ancestry is missing or symlinked: {path}") from exc


class _RepositoryAnchor:
    def __init__(self, path: Path, descriptor: int, details: os.stat_result) -> None:
        self.path = path
        self.descriptor: int | None = descriptor
        self.device = details.st_dev
        self.inode = details.st_ino

    @classmethod
    def open(cls, path: Path) -> _RepositoryAnchor:
        descriptor = _open_absolute_directory_nofollow(path)
        try:
            details = os.fstat(descriptor)
        except BaseException as error:
            try:
                os.close(descriptor)
            except OSError as close_error:
                error.add_note(f"repository descriptor close failed: {close_error}")
            raise
        return cls(path, descriptor, details)

    def __enter__(self) -> _RepositoryAnchor:
        return self

    def __exit__(
        self,
        exc_type: object,
        exc_value: object,
        traceback: object,
    ) -> bool:
        try:
            self.close_once()
        except BaseException as close_error:
            if isinstance(exc_value, BaseException):
                exc_value.add_note(f"repository descriptor close failed: {close_error}")
                return False
            raise
        return False

    def _require_descriptor(self) -> int:
        descriptor = self.descriptor
        if descriptor is None:
            raise InvalidCheckpoint("anchored repository descriptor became invalid")
        return descriptor

    def close_once(self) -> None:
        descriptor = self.descriptor
        if descriptor is None:
            return
        self.descriptor = None
        os.close(descriptor)

    @property
    def identity(self) -> tuple[int, int]:
        return self.device, self.inode

    def duplicate_descriptor(self) -> int:
        descriptor = self._require_descriptor()
        details = os.fstat(descriptor)
        if (details.st_dev, details.st_ino) != self.identity:
            raise InvalidCheckpoint("anchored repository identity changed")
        return os.dup(descriptor)

    def verify_identity(self) -> None:
        descriptor = self._require_descriptor()
        try:
            anchored = os.fstat(descriptor)
        except OSError as exc:
            raise InvalidCheckpoint("anchored repository descriptor became invalid") from exc
        if (anchored.st_dev, anchored.st_ino) != self.identity:
            raise InvalidCheckpoint("anchored repository identity changed")
        try:
            current_descriptor = _open_absolute_directory_nofollow(self.path)
        except (InvalidCheckpoint, OSError) as exc:
            raise InvalidCheckpoint("repository identity changed at supplied path") from exc
        try:
            current = os.fstat(current_descriptor)
        finally:
            os.close(current_descriptor)
        if (current.st_dev, current.st_ino) != self.identity:
            raise InvalidCheckpoint("repository identity changed at supplied path")

    def enter_git_process(self) -> None:
        os.fchdir(self._require_descriptor())


class _RepositoryPublicationGuard:
    def __init__(self, repository: _RepositoryAnchor) -> None:
        self.repository = repository

    def before_link(self) -> None:
        self.repository.verify_identity()

    def before_commit(self) -> None:
        primary_error: BaseException | None = None
        primary_traceback = None
        try:
            self.repository.verify_identity()
        except BaseException as error:
            primary_error = error
            primary_traceback = error.__traceback__
        try:
            self.repository.close_once()
        except BaseException as close_error:
            if primary_error is None:
                raise
            primary_error.add_note(
                f"repository descriptor close failed: {close_error}"
            )
        if primary_error is not None:
            raise primary_error.with_traceback(primary_traceback)


def _read_repository_file_nofollow(
    repository: _RepositoryAnchor,
    relative: str,
) -> tuple[bytes, os.stat_result, os.stat_result]:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise InvalidCheckpoint("repository file path must be normalized and relative")
    descriptor = repository.duplicate_descriptor()
    try:
        for component in pure.parts[:-1]:
            try:
                child = os.open(component, _directory_open_flags(), dir_fd=descriptor)
            except OSError as exc:
                raise InvalidCheckpoint(
                    f"repository file ancestry is missing or symlinked: {relative}"
                ) from exc
            os.close(descriptor)
            descriptor = child
        parent_details = os.fstat(descriptor)
        try:
            file_descriptor = os.open(
                pure.parts[-1],
                _file_open_flags(),
                dir_fd=descriptor,
            )
        except OSError as exc:
            raise InvalidCheckpoint(
                f"cannot open repository file without symlinks: {relative}"
            ) from exc
        try:
            before = os.fstat(file_descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise InvalidCheckpoint(f"repository path is not a regular file: {relative}")
            chunks: list[bytes] = []
            while True:
                chunk = os.read(file_descriptor, 1024 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
            after = os.fstat(file_descriptor)
            if (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
            ) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
            ):
                raise InvalidCheckpoint(
                    f"repository file changed while it was read: {relative}"
                )
            return b"".join(chunks), after, parent_details
        finally:
            os.close(file_descriptor)
    finally:
        os.close(descriptor)


def _read_regular_file_nofollow(path: Path) -> tuple[bytes, os.stat_result, os.stat_result]:
    parent_descriptor = _open_absolute_directory_nofollow(path.parent)
    try:
        parent_details = os.fstat(parent_descriptor)
        try:
            descriptor = os.open(path.name, _file_open_flags(), dir_fd=parent_descriptor)
        except OSError as exc:
            raise InvalidCheckpoint(f"cannot open regular file without symlinks: {path}") from exc
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise InvalidCheckpoint(f"path is not a regular file: {path}")
            chunks: list[bytes] = []
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
            after = os.fstat(descriptor)
            if (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
            ) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
            ):
                raise InvalidCheckpoint(f"file changed while it was read: {path}")
            return b"".join(chunks), after, parent_details
        finally:
            os.close(descriptor)
    finally:
        os.close(parent_descriptor)


def _reject_symlink_prefix(
    repo: Path | _RepositoryAnchor,
    path: PurePosixPath,
) -> None:
    descriptor = (
        repo.duplicate_descriptor()
        if isinstance(repo, _RepositoryAnchor)
        else _open_absolute_directory_nofollow(repo)
    )
    try:
        for index, component in enumerate(path.parts):
            is_leaf = index == len(path.parts) - 1
            try:
                details = os.stat(component, dir_fd=descriptor, follow_symlinks=False)
            except FileNotFoundError:
                return
            if stat.S_ISLNK(details.st_mode):
                raise InvalidReviewScope(
                    f"allowed path has an existing symlink component: {path.as_posix()}"
                )
            if is_leaf:
                if not stat.S_ISREG(details.st_mode):
                    raise InvalidReviewScope(
                        f"allowed path leaf is not a regular file: {path.as_posix()}"
                    )
                return
            if not stat.S_ISDIR(details.st_mode):
                raise InvalidReviewScope(
                    f"allowed path prefix is not a directory: {path.as_posix()}"
                )
            try:
                child = os.open(component, _directory_open_flags(), dir_fd=descriptor)
            except OSError as exc:
                raise InvalidReviewScope(
                    f"cannot anchor allowed path prefix: {path.as_posix()}"
                ) from exc
            os.close(descriptor)
            descriptor = child
    finally:
        os.close(descriptor)


def _validate_repo_file(
    path: object,
    *,
    repo: Path | _RepositoryAnchor | None = None,
) -> str:
    if not isinstance(path, str) or not path or "\\" in path or path.endswith("/"):
        raise InvalidReviewScope("allowed path is not a normalized repository file")
    if path.startswith(":") or any(character in "*?[]" for character in path):
        raise InvalidReviewScope("allowed path must be a literal repository file")
    if any(unicodedata.category(character).startswith("C") for character in path):
        raise InvalidReviewScope("allowed path must not contain control characters")
    pure = PurePosixPath(path)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise InvalidReviewScope("allowed path is not a normalized repository file")
    if pure.as_posix() != path:
        raise InvalidReviewScope("allowed path is not normalized")
    if any(part.casefold() == ".git" for part in pure.parts):
        raise InvalidReviewScope("allowed path must not address Git internals")
    if repo is not None:
        _reject_symlink_prefix(repo, pure)
    return path


def read_task_scope_manifest(
    path: Path,
    *,
    expected_sha256: str,
) -> TaskScopeManifest:
    """Read the immutable external scope exactly once and verify its digest."""

    _validate_sha256(expected_sha256, name="expected scope SHA", error=InvalidTaskScope)
    try:
        encoded, _, _ = _read_regular_file_nofollow(path.expanduser().absolute())
    except InvalidCheckpoint as exc:
        raise InvalidTaskScope("cannot safely read task scope manifest") from exc
    if _sha256_bytes(encoded) != expected_sha256:
        raise InvalidTaskScope("task scope SHA-256 does not match")
    payload = _parse_canonical_object(encoded, error_type=InvalidTaskScope)
    expected_keys = {
        "schema_version",
        "branch",
        "base_head",
        "clean_status_sha256",
        "clean_index_diff_sha256",
        "clean_worktree_diff_sha256",
        "scope_paths",
    }
    if set(payload) != expected_keys:
        raise InvalidTaskScope("task scope keys do not match schema")
    if payload["schema_version"] != 1 or isinstance(payload["schema_version"], bool):
        raise InvalidTaskScope("task scope schema_version must be 1")
    branch = payload["branch"]
    base_head = payload["base_head"]
    if branch != EXPECTED_BRANCH:
        raise InvalidTaskScope("task scope branch is not the integration branch")
    if not isinstance(base_head, str) or not _GIT_SHA.fullmatch(base_head):
        raise InvalidTaskScope("task scope base_head is invalid")
    status_sha = _validate_sha256(
        payload["clean_status_sha256"], name="clean status hash", error=InvalidTaskScope
    )
    index_sha = _validate_sha256(
        payload["clean_index_diff_sha256"],
        name="clean index diff hash",
        error=InvalidTaskScope,
    )
    worktree_sha = _validate_sha256(
        payload["clean_worktree_diff_sha256"],
        name="clean worktree diff hash",
        error=InvalidTaskScope,
    )
    if (status_sha, index_sha, worktree_sha) != (
        _EMPTY_SHA256,
        _EMPTY_SHA256,
        _EMPTY_SHA256,
    ):
        raise DirtyScopeBase("task scope base status and diffs must be empty")
    paths = payload["scope_paths"]
    if not isinstance(paths, list) or tuple(paths) != TASK_0_1_SCOPE_PATHS:
        raise InvalidTaskScope("task scope paths are not the exact Task 0.1 set")
    return TaskScopeManifest(
        schema_version=1,
        branch=branch,
        base_head=base_head,
        clean_status_sha256=status_sha,
        clean_index_diff_sha256=index_sha,
        clean_worktree_diff_sha256=worktree_sha,
        scope_paths=tuple(cast(list[str], paths)),
    )


def _review_scope_from_bytes(
    encoded: bytes,
    *,
    repo: Path | _RepositoryAnchor | None = None,
) -> ReviewScopeV1:
    payload = _parse_canonical_object(encoded, error_type=InvalidReviewScope)
    if set(payload) != {"schema_version", "tasks"}:
        raise InvalidReviewScope("review scope keys do not match schema")
    if payload["schema_version"] != 1 or isinstance(payload["schema_version"], bool):
        raise InvalidReviewScope("review scope schema_version must be 1")
    raw_tasks = payload["tasks"]
    if not isinstance(raw_tasks, list):
        raise InvalidReviewScope("review scope tasks must be an array")
    tasks: list[ReviewTaskScope] = []
    for raw in raw_tasks:
        if not isinstance(raw, Mapping) or set(raw) != {
            "task_id",
            "review_mode",
            "allowed_paths",
        }:
            raise InvalidReviewScope("review task keys do not match schema")
        task_id = raw["task_id"]
        mode = raw["review_mode"]
        paths = raw["allowed_paths"]
        if not isinstance(task_id, str):
            raise InvalidReviewScope("task_id must be a string")
        if mode not in {"task-diff", "checkpoint-to-final-head"}:
            raise InvalidReviewScope("unknown review mode")
        if not isinstance(paths, list):
            raise InvalidReviewScope("allowed_paths must be an array")
        normalized = tuple(_validate_repo_file(path, repo=repo) for path in paths)
        if not normalized or normalized != tuple(sorted(set(normalized))):
            raise InvalidReviewScope("allowed_paths must be non-empty, sorted and unique")
        tasks.append(
            ReviewTaskScope(
                task_id=task_id,
                review_mode=cast(
                    Literal["task-diff", "checkpoint-to-final-head"], mode
                ),
                allowed_paths=normalized,
            )
        )
    result = ReviewScopeV1(schema_version=1, tasks=tuple(tasks))
    if result.task_ids != RELEASE1_TASK_IDS:
        raise InvalidReviewScope("review task IDs are missing, extra or reordered")
    if any(task.review_mode != "task-diff" for task in result.tasks[:-1]):
        raise InvalidReviewScope("ordinary review tasks must use task-diff")
    final = result.tasks[-1]
    if final.review_mode != "checkpoint-to-final-head":
        raise InvalidReviewScope("final integration review mode is invalid")
    expected_union = result.union_paths(task_ids=result.task_ids[:-1])
    if final.allowed_paths != expected_union:
        raise InvalidReviewScope("final integration scope is not the exact task union")
    return result


def read_review_scope_v1(path: Path) -> ReviewScopeV1:
    """Strictly read the sole repository review-scope authority."""

    absolute = path.expanduser().absolute()
    try:
        encoded, _, _ = _read_regular_file_nofollow(absolute)
    except InvalidCheckpoint as exc:
        raise InvalidReviewScope("cannot safely read review-scope authority") from exc
    repo = absolute.parent.parent if absolute.name == "review-scope-v1.json" else None
    return _review_scope_from_bytes(encoded, repo=repo)


def _git_bytes(repo: Path | _RepositoryAnchor, *args: str) -> bytes:
    if isinstance(repo, Path):
        resolved = repo.expanduser().absolute()
        with _RepositoryAnchor.open(resolved) as repository:
            return _git_bytes(repository, *args)

    inherited = sorted(
        name
        for name in os.environ
        if name in _REPO_SENSITIVE_GIT_ENV
        or name.startswith("GIT_CONFIG_KEY_")
        or name.startswith("GIT_CONFIG_VALUE_")
    )
    if inherited:
        raise InvalidCheckpoint(
            "unsafe Git environment variables are not allowed: " + ", ".join(inherited)
        )
    environment = {
        name: value for name, value in os.environ.items() if not name.startswith("GIT_")
    }
    environment.update(
        {
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
        }
    )
    command = [
        "git",
        "--no-optional-locks",
        "--no-replace-objects",
        "--literal-pathspecs",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "core.excludesFile=/dev/null",
        "-c",
        "protocol.ext.allow=never",
        *args,
    ]
    repo.verify_identity()
    repository_descriptor = repo._require_descriptor()
    try:
        try:
            return subprocess.check_output(
                command,
                env=environment,
                stderr=subprocess.PIPE,
                pass_fds=(repository_descriptor,),
                preexec_fn=repo.enter_git_process,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            if isinstance(exc, OSError):
                raise InvalidCheckpoint(
                    f"git {' '.join(args)} could not execute"
                ) from exc
            message = exc.stderr.decode("utf-8", errors="replace").strip()
            raise InvalidCheckpoint(f"git {' '.join(args)} failed: {message}") from exc
    finally:
        repo.verify_identity()


def _git_text(repo: Path | _RepositoryAnchor, *args: str) -> str:
    try:
        return _git_bytes(repo, *args).decode("utf-8", errors="strict").strip()
    except UnicodeDecodeError as exc:
        raise InvalidCheckpoint(f"git {' '.join(args)} returned non-UTF-8 text") from exc


def _nul_paths(payload: bytes) -> set[str]:
    values = payload.split(b"\0")
    if values and values[-1] == b"":
        values.pop()
    try:
        return {value.decode("utf-8", errors="strict") for value in values}
    except UnicodeDecodeError as exc:
        raise InvalidCheckpoint("git returned a non-UTF-8 path") from exc


def _dirty_paths(repo: Path | _RepositoryAnchor) -> tuple[str, ...]:
    tracked = _nul_paths(
        _git_bytes(
            repo,
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--name-only",
            "-z",
            "--no-renames",
            "HEAD",
            "--",
        )
    )
    untracked = _nul_paths(
        _git_bytes(repo, "ls-files", "--others", "--exclude-standard", "-z", "--")
    )
    return tuple(sorted(tracked | untracked))


def _committed_paths(
    repo: Path | _RepositoryAnchor,
    *,
    base_head: str,
    head: str,
) -> tuple[str, ...]:
    return tuple(
        sorted(
            _nul_paths(
                _git_bytes(
                    repo,
                    "diff",
                    "--no-ext-diff",
                    "--no-textconv",
                    "--name-only",
                    "-z",
                    "--no-renames",
                    f"{base_head}..{head}",
                    "--",
                )
            )
        )
    )


def _fingerprint(repo: _RepositoryAnchor, *, relative: str) -> PathFingerprint:
    parts = PurePosixPath(relative).parts
    descriptor = repo.duplicate_descriptor()
    try:
        for component in parts[:-1]:
            try:
                child = os.open(component, _directory_open_flags(), dir_fd=descriptor)
            except FileNotFoundError:
                return PathFingerprint(path=relative, kind="absent", sha256=None)
            except OSError as exc:
                raise InvalidCheckpoint(
                    f"scoped path ancestry is symlinked or invalid: {relative}"
                ) from exc
            os.close(descriptor)
            descriptor = child
        try:
            before = os.stat(parts[-1], dir_fd=descriptor, follow_symlinks=False)
        except FileNotFoundError:
            return PathFingerprint(path=relative, kind="absent", sha256=None)
        if stat.S_ISREG(before.st_mode):
            try:
                file_descriptor = os.open(
                    parts[-1],
                    _file_open_flags(),
                    dir_fd=descriptor,
                )
            except OSError as exc:
                raise InvalidCheckpoint(f"cannot open scoped file: {relative}") from exc
            try:
                opened = os.fstat(file_descriptor)
                if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                    raise InvalidCheckpoint(
                        f"scoped path changed before it was opened: {relative}"
                    )
                chunks: list[bytes] = []
                while True:
                    chunk = os.read(file_descriptor, 1024 * 1024)
                    if not chunk:
                        break
                    chunks.append(chunk)
                after = os.fstat(file_descriptor)
                if (
                    opened.st_dev,
                    opened.st_ino,
                    opened.st_size,
                    opened.st_mtime_ns,
                ) != (
                    after.st_dev,
                    after.st_ino,
                    after.st_size,
                    after.st_mtime_ns,
                ):
                    raise InvalidCheckpoint(
                        f"scoped file changed while it was read: {relative}"
                    )
                return PathFingerprint(
                    path=relative,
                    kind="regular",
                    sha256=_sha256_bytes(b"".join(chunks)),
                )
            finally:
                os.close(file_descriptor)
        if stat.S_ISLNK(before.st_mode):
            try:
                target = os.readlink(parts[-1], dir_fd=descriptor)
                after = os.stat(parts[-1], dir_fd=descriptor, follow_symlinks=False)
            except OSError as exc:
                raise InvalidCheckpoint(f"cannot read scoped symlink: {relative}") from exc
            if (after.st_dev, after.st_ino, after.st_mtime_ns) != (
                before.st_dev,
                before.st_ino,
                before.st_mtime_ns,
            ):
                raise InvalidCheckpoint(f"scoped symlink changed while read: {relative}")
            return PathFingerprint(
                path=relative,
                kind="symlink",
                sha256=_sha256_bytes(target.encode("utf-8")),
            )
        raise InvalidCheckpoint(f"scoped path has unsupported type: {relative}")
    finally:
        os.close(descriptor)


def _fingerprints(
    repo: _RepositoryAnchor,
    paths: tuple[str, ...],
) -> tuple[PathFingerprint, ...]:
    return tuple(_fingerprint(repo, relative=relative) for relative in paths)


def _candidate_ref(repo: Path | _RepositoryAnchor, name: str) -> CandidateRef:
    if name not in {EXPECTED_CANDIDATE_TAG, EXPECTED_CANDIDATE_REF}:
        raise InvalidCheckpoint(
            f"candidate must be the fixed tag {EXPECTED_CANDIDATE_REF}"
        )
    try:
        target = _git_text(
            repo,
            "rev-parse",
            "--verify",
            "--end-of-options",
            f"{EXPECTED_CANDIDATE_REF}^{{commit}}",
        )
    except InvalidCheckpoint as exc:
        raise InvalidCheckpoint("candidate tag must resolve to a commit") from exc
    if not _GIT_SHA.fullmatch(target):
        raise InvalidCheckpoint("candidate tag did not resolve to a commit SHA")
    if target != EXPECTED_CANDIDATE_TARGET_SHA:
        raise InvalidCheckpoint("candidate tag target moved from the frozen commit")
    record = _canonical_json_bytes(
        {"name": EXPECTED_CANDIDATE_REF, "target_sha": target}
    )
    return CandidateRef(
        name=EXPECTED_CANDIDATE_REF,
        target_sha=target,
        record_sha256=_sha256_bytes(record),
    )


def _inventory_ref() -> tuple[EvidenceRef, tuple[str, ...]]:
    home = Path(os.environ.get("HOME", str(Path.home()))).expanduser()
    path = (home / CANONICAL_MANIFEST_RELATIVE_PATH).absolute()
    try:
        encoded, file_details, parent_details = _read_regular_file_nofollow(path)
    except (InvalidCheckpoint, OSError):
        unavailable = _canonical_json_bytes(
            {"path": str(path), "status": "unavailable"}
        )
        return (
            EvidenceRef(path=str(path), sha256=_sha256_bytes(unavailable)),
            ("TASK1_INVENTORY_UNAVAILABLE",),
        )
    errors: list[str] = []
    if stat.S_IMODE(parent_details.st_mode) != 0o700:
        errors.append("manifest directory mode must be 0700")
    if stat.S_IMODE(file_details.st_mode) != 0o600:
        errors.append("manifest mode must be 0600")
    try:
        text = encoded.decode("utf-8", errors="strict")
        manifest = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, InvalidCheckpoint) as exc:
        errors.append(f"cannot parse manifest bytes: {type(exc).__name__}")
        manifest = {}
    if not isinstance(manifest, Mapping):
        errors.append("manifest root must be an object")
        manifest = {}
    errors.extend(validate_manifest(manifest))
    surfaces = manifest.get("surfaces", {})
    if isinstance(surfaces, Mapping) and any(
        isinstance(row, Mapping) and str(row.get("path", "")) == str(path)
        for rows in surfaces.values()
        if isinstance(rows, list)
        for row in rows
    ):
        errors.append("manifest must not inventory its own destination")
    reference = EvidenceRef(path=str(path), sha256=_sha256_bytes(encoded))
    if errors:
        return reference, ("TASK1_INVENTORY_INVALID",)
    return reference, ()


def _require_exact_precommit_delta(
    dirty: tuple[str, ...], scope: tuple[str, ...]
) -> None:
    extra = sorted(set(dirty).difference(scope))
    if extra:
        raise OutOfScopeWorktreeDelta(f"out-of-scope worktree paths: {extra}")
    missing = sorted(set(scope).difference(dirty))
    if missing:
        raise IncompleteWorktreeDelta(f"missing Task 0.1 worktree paths: {missing}")


def _require_exact_committed_delta(
    observed: tuple[str, ...], scope: tuple[str, ...]
) -> None:
    if observed != scope:
        extra = sorted(set(observed).difference(scope))
        missing = sorted(set(scope).difference(observed))
        raise OutOfScopeCommittedDelta(
            f"committed delta mismatch: extra={extra}, missing={missing}"
        )


def _capture_release_checkpoint(
    *,
    repository: _RepositoryAnchor,
    candidate_tag: str,
    phase: Literal["precommit-delta", "final-clean"] | str,
    expected_head: str,
    scope_manifest: Path,
    scope_sha256: str,
) -> ReleaseCheckpoint:
    if phase not in {"precommit-delta", "final-clean"}:
        raise InvalidCheckpoint("unknown checkpoint phase")
    resolved_repo = repository.path
    scope_path = scope_manifest.expanduser().absolute()
    scope = read_task_scope_manifest(scope_path, expected_sha256=scope_sha256)
    review_bytes, _, _ = _read_repository_file_nofollow(
        repository,
        REVIEW_SCOPE_PATH,
    )
    _review_scope_from_bytes(review_bytes, repo=repository)
    review_sha = _sha256_bytes(review_bytes)

    git_top = Path(
        _git_text(repository, "rev-parse", "--show-toplevel")
    ).absolute()
    repository.verify_identity()
    if git_top != resolved_repo:
        raise InvalidCheckpoint("Git top-level does not match the supplied repository")
    branch = _git_text(repository, "branch", "--show-current")
    head = _git_text(repository, "rev-parse", "HEAD")
    if branch != scope.branch or branch != EXPECTED_BRANCH:
        raise InvalidCheckpoint(f"unexpected branch: {branch}")
    if head != expected_head:
        raise InvalidCheckpoint("HEAD does not match --expected-head")
    dirty = _dirty_paths(repository)
    if phase == "precommit-delta":
        if head != scope.base_head:
            raise InvalidCheckpoint("precommit HEAD must equal frozen base_head")
        _require_exact_precommit_delta(dirty, scope.scope_paths)
        observed = dirty
    else:
        if dirty:
            raise InvalidCheckpoint(f"final-clean checkout is dirty: {list(dirty)}")
        try:
            _git_bytes(
                repository,
                "merge-base",
                "--is-ancestor",
                scope.base_head,
                head,
            )
        except InvalidCheckpoint as exc:
            raise InvalidCheckpoint("frozen base is not an ancestor of final HEAD") from exc
        observed = _committed_paths(
            repository, base_head=scope.base_head, head=head
        )
        _require_exact_committed_delta(observed, scope.scope_paths)

    fingerprints = _fingerprints(repository, scope.scope_paths)
    candidate = _candidate_ref(repository, candidate_tag)
    inventory, unknown_evidence = _inventory_ref()

    scope_recheck, _, _ = _read_regular_file_nofollow(scope_path)
    if _sha256_bytes(scope_recheck) != scope_sha256:
        raise InvalidCheckpoint("task scope changed during checkpoint capture")
    review_recheck, _, _ = _read_repository_file_nofollow(
        repository,
        REVIEW_SCOPE_PATH,
    )
    if review_recheck != review_bytes:
        raise InvalidCheckpoint("review scope changed during checkpoint capture")
    if _git_text(repository, "branch", "--show-current") != branch:
        raise InvalidCheckpoint("branch changed during checkpoint capture")
    if _git_text(repository, "rev-parse", "HEAD") != head:
        raise InvalidCheckpoint("HEAD changed during checkpoint capture")
    if _candidate_ref(repository, candidate_tag) != candidate:
        raise InvalidCheckpoint("candidate tag changed during checkpoint capture")
    if _dirty_paths(repository) != dirty:
        raise InvalidCheckpoint("worktree changed during checkpoint capture")
    if phase == "precommit-delta":
        _require_exact_precommit_delta(dirty, scope.scope_paths)
    else:
        committed_recheck = _committed_paths(
            repository,
            base_head=scope.base_head,
            head=head,
        )
        if committed_recheck != observed:
            raise InvalidCheckpoint("committed delta changed during checkpoint capture")
    if _fingerprints(repository, scope.scope_paths) != fingerprints:
        raise InvalidCheckpoint("scoped path changed during checkpoint capture")
    inventory_recheck, unknown_recheck = _inventory_ref()
    if (inventory_recheck, unknown_recheck) != (inventory, unknown_evidence):
        raise InvalidCheckpoint("Task 1 inventory changed during checkpoint capture")

    return ReleaseCheckpoint(
        schema_version=1,
        created_at_utc=datetime.now(UTC).isoformat(),
        phase=cast(Literal["precommit-delta", "final-clean"], phase),
        repo=str(resolved_repo),
        branch=branch,
        base_head=scope.base_head,
        head=head,
        dirty_paths=dirty,
        scope_paths=scope.scope_paths,
        observed_delta_paths=observed,
        delta_fingerprints=fingerprints,
        scope_source=EvidenceRef(path=str(scope_path), sha256=scope_sha256),
        review_scope=EvidenceRef(path=REVIEW_SCOPE_PATH, sha256=review_sha),
        candidate=candidate,
        inventory=inventory,
        unknown_evidence=unknown_evidence,
    )


def capture_release_checkpoint(
    *,
    repo: Path,
    candidate_tag: str,
    phase: Literal["precommit-delta", "final-clean"] | str,
    expected_head: str,
    scope_manifest: Path,
    scope_sha256: str,
) -> ReleaseCheckpoint:
    """Capture a fail-closed checkpoint without writing repository or host state."""

    resolved_repo = repo.expanduser().absolute()
    with _RepositoryAnchor.open(resolved_repo) as repository:
        checkpoint = _capture_release_checkpoint(
            repository=repository,
            candidate_tag=candidate_tag,
            phase=phase,
            expected_head=expected_head,
            scope_manifest=scope_manifest,
            scope_sha256=scope_sha256,
        )
        repository.verify_identity()
        return checkpoint


def _fingerprint_mapping(item: PathFingerprint) -> dict[str, JsonValue]:
    return {"path": item.path, "kind": item.kind, "sha256": item.sha256}


def _checkpoint_result(checkpoint: ReleaseCheckpoint) -> dict[str, JsonValue]:
    return {
        "schema_version": checkpoint.schema_version,
        "created_at_utc": checkpoint.created_at_utc,
        "phase": checkpoint.phase,
        "repo": checkpoint.repo,
        "branch": checkpoint.branch,
        "base_head": checkpoint.base_head,
        "head": checkpoint.head,
        "dirty_paths": list(checkpoint.dirty_paths),
        "scope_paths": list(checkpoint.scope_paths),
        "observed_delta_paths": list(checkpoint.observed_delta_paths),
        "delta_fingerprints": [
            _fingerprint_mapping(item) for item in checkpoint.delta_fingerprints
        ],
        "scope_source": {
            "path": checkpoint.scope_source.path,
            "sha256": checkpoint.scope_source.sha256,
        },
        "review_scope": {
            "path": checkpoint.review_scope.path,
            "sha256": checkpoint.review_scope.sha256,
        },
        "candidate": {
            "name": checkpoint.candidate.name,
            "target_sha": checkpoint.candidate.target_sha,
            "record_sha256": checkpoint.candidate.record_sha256,
        },
        "inventory": {
            "path": checkpoint.inventory.path,
            "sha256": checkpoint.inventory.sha256,
        },
        "unknown_evidence": list(checkpoint.unknown_evidence),
    }


def checkpoint_evidence_envelope(
    checkpoint: ReleaseCheckpoint,
) -> ReleaseEvidenceEnvelope:
    """Wrap a verified checkpoint in the shared evidence envelope."""

    envelope = ReleaseEvidenceEnvelope(
        schema_version=1,
        kind="release_checkpoint",
        producer_id=RELEASE_CHECKPOINT_PRODUCER_ID,
        created_at_utc=checkpoint.created_at_utc,
        subject_sha=checkpoint.head,
        artifact_sha256=None,
        status="unknown" if checkpoint.unknown_evidence else "green",
        finding_codes=(),
        unknown_evidence=checkpoint.unknown_evidence,
        input_evidence=(
            EvidenceInput(
                role="task-scope-manifest", sha256=checkpoint.scope_source.sha256
            ),
            EvidenceInput(
                role="review-scope-v1", sha256=checkpoint.review_scope.sha256
            ),
            EvidenceInput(role="task1-inventory", sha256=checkpoint.inventory.sha256),
            EvidenceInput(
                role="candidate-ref", sha256=checkpoint.candidate.record_sha256
            ),
        ),
        result=_checkpoint_result(checkpoint),
        report_sha256="",
    )
    return replace(
        envelope,
        report_sha256=canonical_envelope_sha256(envelope),
    )


def _revalidate_checkpoint(
    checkpoint: ReleaseCheckpoint,
    *,
    repository: _RepositoryAnchor,
) -> ReleaseCheckpoint:
    if type(checkpoint) is not ReleaseCheckpoint:
        raise InvalidCheckpoint("checkpoint must use the exact ReleaseCheckpoint type")
    if (
        type(checkpoint.scope_source) is not EvidenceRef
        or type(checkpoint.review_scope) is not EvidenceRef
        or type(checkpoint.inventory) is not EvidenceRef
        or type(checkpoint.candidate) is not CandidateRef
        or type(checkpoint.delta_fingerprints) is not tuple
        or any(type(item) is not PathFingerprint for item in checkpoint.delta_fingerprints)
    ):
        raise InvalidCheckpoint("checkpoint contains non-canonical authority objects")
    if checkpoint.schema_version != 1 or isinstance(checkpoint.schema_version, bool):
        raise InvalidCheckpoint("checkpoint schema_version must be 1")
    if checkpoint.phase not in {"precommit-delta", "final-clean"}:
        raise InvalidCheckpoint("unknown checkpoint phase")
    if checkpoint.branch != EXPECTED_BRANCH:
        raise InvalidCheckpoint("checkpoint branch is not the integration branch")
    if checkpoint.scope_paths != TASK_0_1_SCOPE_PATHS:
        raise InvalidCheckpoint("checkpoint scope is not the exact Task 0.1 set")
    if checkpoint.review_scope.path != REVIEW_SCOPE_PATH:
        raise InvalidCheckpoint("checkpoint review scope path is not fixed")
    if checkpoint.candidate.name != EXPECTED_CANDIDATE_REF:
        raise InvalidCheckpoint("checkpoint candidate identity is not fixed")
    if checkpoint.repo != str(repository.path):
        raise InvalidCheckpoint("checkpoint repository path is not canonical")

    recaptured = _capture_release_checkpoint(
        repository=repository,
        candidate_tag=EXPECTED_CANDIDATE_REF,
        phase=checkpoint.phase,
        expected_head=checkpoint.head,
        scope_manifest=Path(checkpoint.scope_source.path),
        scope_sha256=checkpoint.scope_source.sha256,
    )
    submitted = replace(checkpoint, created_at_utc=recaptured.created_at_utc)
    if recaptured != submitted:
        raise InvalidCheckpoint(
            "checkpoint fields do not match an authoritative final recapture"
        )
    repository.verify_identity()
    return recaptured


def write_checkpoint_exclusive(path: Path, checkpoint: ReleaseCheckpoint) -> None:
    """Revalidate mutable observations, then exclusively write the checkpoint."""

    if type(checkpoint) is not ReleaseCheckpoint or not isinstance(checkpoint.repo, str):
        raise InvalidCheckpoint("checkpoint must use the exact ReleaseCheckpoint type")
    repo = Path(checkpoint.repo).expanduser().absolute()
    with _RepositoryAnchor.open(repo) as repository:
        trusted = _revalidate_checkpoint(checkpoint, repository=repository)
        envelope = checkpoint_evidence_envelope(trusted)
        active_roots = active_evidence_roots_from_environment(repo=repo)
        root_value = os.environ.get("DAN_RELEASE_EVIDENCE_ROOT")
        if not root_value:
            raise InvalidCheckpoint("DAN_RELEASE_EVIDENCE_ROOT must be set")
        root = validate_evidence_root(Path(root_value), active_roots=active_roots)
        write_evidence_envelope_exclusive(
            path,
            envelope,
            evidence_root=root,
            publication_guard=_RepositoryPublicationGuard(repository),
        )
