"""Read-only source-of-truth inventory for the DAN Release 1 migration.

The inventory deliberately records metadata, hashes, reference relationships,
and database counts.  It never serializes file contents or SQLite row values.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import sqlite3
import stat
import subprocess
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol


SCHEMA_VERSION = 1
MANIFEST_FILE_MODE = 0o600
CANONICAL_MANIFEST_RELATIVE_PATH = Path(
    ".dan/migration/release1-source-manifest.json"
)
SURFACE_NAMES = (
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

_LAUNCHD_LABEL_PREFIXES = ("com.ozzy.", "com.dan.")
_LAUNCHD_EXACT_LABELS = {"ai.openclaw.gateway"}
_PRODUCER_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"dan-voice/req", "legacy-dan-voice-json"),
    (b"dan-voice", "legacy-dan-voice-runtime"),
    (b"voice_broker.py", "legacy-broker-process"),
    (b"feeder.sh", "legacy-playlist-feeder"),
    (b"DAN_BROKER_ENGINE", "legacy-engine-environment"),
    (b"playlist.txt", "legacy-playlist-lines"),
    (b"/voice/speak", "jarvis-http-voice-intent"),
    (b"say_gpt.sh", "legacy-gpt-say-wrapper"),
    (b"dan speak", "dan-cli-speech-intent"),
)
_TEXT_SUFFIXES = {
    "",
    ".bash",
    ".cfg",
    ".conf",
    ".fish",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".plist",
    ".py",
    ".rules",
    ".sh",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
    ".zsh",
}
_SKIP_DIRECTORY_NAMES = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".superpowers",
    ".svn",
    ".venv",
    "__pycache__",
    "node_modules",
}
_MAX_DISCOVERY_FILE_BYTES = 4 * 1024 * 1024
_MAX_SYMLINK_TARGET_BYTES = 64 * 1024 * 1024
_DECISION_PLACEHOLDER = re.compile(r"\b(?:pending|tbd|todo)\b", re.IGNORECASE)
_SAFE_EXECUTABLE = re.compile(r"[^A-Za-z0-9._+-]")
_PROCESS_ROLES: tuple[tuple[str, str], ...] = (
    ("voice_broker", "legacy-broker"),
    ("feeder", "legacy-feeder"),
    ("jarvisd", "jarvis-daemon"),
    ("menubar-controller", "legacy-panel"),
    ("supertonic", "supertonic-engine"),
    ("openclaw", "openclaw-host"),
    ("standup", "standup-host"),
    ("higiena", "higiena-host"),
    ("dan-voice", "legacy-voice-runtime"),
    ("dand", "dan-daemon"),
    ("jarvis", "jarvis-runtime"),
)


class Runner(Protocol):
    """Subset of ``subprocess.run`` used by the collector."""

    def __call__(self, args: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]: ...


@dataclass(frozen=True)
class RunResult:
    returncode: int
    stdout: str
    stdout_bytes: bytes
    decode_error: bool = False
    error_type: str | None = None


@dataclass(frozen=True)
class ProcessObservation:
    """Private process evidence; ``command`` is never serialized."""

    pid: int
    ppid: int
    role: str
    executable: str
    command: str


@dataclass(frozen=True)
class ScannedReferenceFile:
    path: Path
    payload: bytes


@dataclass(frozen=True)
class InventoryItem:
    path: str
    kind: Literal["file", "directory", "symlink", "process", "launchd", "database"]
    target: str | None
    sha256: str | None
    status: str
    consumers: tuple[str, ...] = ()
    request_format: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)
    required: bool = False
    error: Mapping[str, object] | None = None
    symlink: Mapping[str, object] | None = None
    reference_class: str | None = None
    activity_evidence: tuple[Mapping[str, str], ...] = ()

    def to_mapping(self) -> dict[str, object]:
        result = asdict(self)
        result["metadata"] = dict(sorted(self.metadata.items()))
        result["activity_evidence"] = [dict(row) for row in self.activity_evidence]
        if self.error is None:
            result.pop("error")
        if self.symlink is None:
            result.pop("symlink")
        if self.reference_class is None:
            result.pop("reference_class")
            result.pop("activity_evidence")
        return result


@dataclass(frozen=True)
class InventoryRoots:
    """Filesystem anchors for a production or disposable inventory."""

    home: Path
    repo_root: Path
    tmp_root: Path = Path("/tmp")
    excludes: tuple[Path, ...] = ()
    enforce_production_requirements: bool = False

    @classmethod
    def production(
        cls,
        repo_root: Path,
        *,
        home: Path | None = None,
        tmp_root: Path = Path("/tmp"),
        excludes: Iterable[Path] = (),
    ) -> InventoryRoots:
        actual_home = (home or Path.home()).expanduser()
        required_archive_exclusion = actual_home / ".claude/archive"
        return cls(
            home=actual_home,
            repo_root=repo_root,
            tmp_root=tmp_root,
            excludes=_unique_paths((*excludes, required_archive_exclusion)),
            enforce_production_requirements=True,
        )

    def repository_paths(self) -> tuple[Path, ...]:
        dev = self.home / "Documents/dev"
        return _unique_paths(
            (
                self.repo_root,
                dev / "jarvis",
                dev / "dan",
                dev / "DANv2",
                dev / "menubar-controller",
            )
        )

    def claude_project_memory_roots(self) -> tuple[Path, ...]:
        projects = self.home / ".claude/projects"
        return _unique_paths(
            projects / ("-" + str(path.absolute()).lstrip("/").replace("/", "-")) / "memory"
            for path in self.repository_paths()
        )

    def reference_memory_roots(self) -> tuple[Path, ...]:
        return _unique_paths(
            self.claude_project_memory_roots()
            + (
                self.home / ".openclaw/workspace/memory",
                self.home / ".codex/memories/MEMORY.md",
            )
        )

    def active_skill_roots(
        self,
        error_sink: list[Mapping[str, object]] | None = None,
    ) -> tuple[Path, ...]:
        repository_skill_roots = tuple(
            candidate
            for repository in self.repository_paths()
            for candidate in (
                repository / ".agents/skills",
                repository / ".claude/skills",
                repository / "skills",
            )
        )
        return _unique_paths(
            (
                self.home / ".agents/skills",
                self.home / ".claude/skills",
                self.home / ".codex/skills",
                self.home / ".codex/memories/skills",
                self.home / ".openclaw/workspace/skills",
                self.home / ".openclaw/plugin-skills",
            )
            + repository_skill_roots
            + self.plugin_skill_roots(error_sink)
        )

    def plugin_skill_roots(
        self,
        error_sink: list[Mapping[str, object]] | None = None,
    ) -> tuple[Path, ...]:
        discovered: list[Path] = []
        for cache in (
            self.home / ".claude/plugins/cache",
            self.home / ".codex/plugins/cache",
        ):
            for path in _walk_paths(
                cache,
                self.excludes,
                error_sink=error_sink,
                required=False,
            ):
                if path.name == "SKILL.md":
                    discovered.append(path.parent)
        return _unique_paths(discovered)

    def active_scan_roots(self) -> tuple[Path, ...]:
        """Name every active production root covered by the Task 1 inventory."""

        return _unique_paths(
            self.repository_paths()
            + (
                self.home / ".agents",
                self.home / ".claude",
                self.home / ".codex",
                self.home / ".openclaw",
                self.home / "AGENTS.md",
                self.home / ".claude/CLAUDE.md",
                self.home / "Library/LaunchAgents",
            )
            + self.reference_memory_roots()
        )

    def producer_scan_roots(
        self,
        error_sink: list[Mapping[str, object]] | None = None,
    ) -> tuple[Path, ...]:
        """Executable, config, and injected-instruction subsets of active roots."""

        return _unique_paths(
            self.repository_paths()
            + self.active_skill_roots(error_sink)
            + (
                self.home / ".claude/hooks",
                self.home / ".claude/bin",
                self.home / ".claude/agents",
                self.home / ".claude/settings.json",
                self.home / ".claude/settings.local.json",
                self.home / ".claude/statusline-command.sh",
                self.home / ".codex/rules",
                self.home / ".codex/AGENTS.md",
                self.home / ".codex/config.toml",
                self.home / ".codex/memories/MEMORY.md",
                self.home / ".openclaw/openclaw.json",
                self.home / ".openclaw/plugin-skills",
                self.home / ".openclaw/service-env",
                self.home / ".openclaw/workspace/skills",
                self.home / ".openclaw/workspace/AGENTS.md",
                self.home / ".openclaw/workspace/DREAMS.md",
                self.home / ".openclaw/workspace/HEARTBEAT.md",
                self.home / ".openclaw/workspace/IDENTITY.md",
                self.home / ".openclaw/workspace/MEMORY.md",
                self.home / ".openclaw/workspace/SOUL.md",
                self.home / ".openclaw/workspace/TOOLS.md",
                self.home / ".openclaw/workspace/USER.md",
                self.home / "AGENTS.md",
                self.home / ".claude/CLAUDE.md",
            )
            + self.reference_memory_roots()
        )

    def allowed_roots(self) -> tuple[Path, ...]:
        """Explicit roots whose regular symlink targets may be hashed."""

        return _unique_paths(
            self.repository_paths()
            + (
                self.home / ".dan",
                self.home / ".jarvis",
                self.home / ".config/voice",
                self.home / ".cache/supertonic3/custom_styles",
                self.home / ".agents",
                self.home / ".claude",
                self.home / ".codex",
                self.home / ".openclaw",
                self.home / "Library/LaunchAgents",
                self.home / "Documents/summary.md",
                self.home / "Documents/opinia-planu.md",
                self.home / "Desktop/djdan-visualizer.html",
                self.home / "AGENTS.md",
                self.tmp_root,
            )
        )

    def is_required(self, path: Path) -> bool:
        absolute = path.expanduser().absolute()
        if absolute == self.repo_root.expanduser().absolute():
            return True
        if not self.enforce_production_requirements:
            return False
        required = {
            candidate.absolute()
            for candidate in (
                self.home / "Documents/dev/jarvis",
                self.home / "Documents/dev/dan",
                self.home / "Documents/dev/DANv2",
                self.home / "Documents/dev/menubar-controller",
                self.home / "Documents/dev/dan/config/persona/DAN.md",
                self.home / ".jarvis/jarvis.db",
                self.home / ".dan/memory.db",
                self.home / "AGENTS.md",
                self.home / ".claude/CLAUDE.md",
            )
        }
        return absolute in required


@dataclass(frozen=True)
class InventoryReport:
    schema_version: int
    generated_at: str
    selected_base: Mapping[str, object]
    roots: Mapping[str, object]
    surfaces: Mapping[str, list[Mapping[str, object]]]

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "selected_base": dict(self.selected_base),
            "roots": dict(self.roots),
            "surfaces": {name: list(self.surfaces[name]) for name in SURFACE_NAMES},
        }


def _unique_paths(paths: Iterable[Path]) -> tuple[Path, ...]:
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        expanded = path.expanduser()
        key = str(expanded.absolute())
        if key in seen:
            continue
        seen.add(key)
        result.append(expanded)
    return tuple(result)


def _error_payload(error_type: str, operation: str) -> dict[str, object]:
    return {"type": error_type, "operation": operation, "resolved": False}


def _path_error_record(
    path: Path,
    *,
    operation: str,
    error_type: str,
    required: bool,
) -> dict[str, object]:
    return {
        "kind": "path_error",
        "path": str(path.expanduser().absolute()),
        "status": "path-error",
        "required": required,
        "error": _error_payload(error_type, operation),
    }


def _stat_identity(value: os.stat_result) -> tuple[int, int, int, int]:
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)


def _hash_regular_path(
    path: Path,
    *,
    expected: os.stat_result,
) -> tuple[str | None, Mapping[str, object] | None]:
    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        return None, _error_payload(type(exc).__name__, "open")
    error: Mapping[str, object] | None = None
    digest_value: str | None = None
    try:
        try:
            opened = os.fstat(descriptor)
        except OSError as exc:
            error = _error_payload(type(exc).__name__, "fstat")
            opened = None
        if opened is not None and (
            not stat.S_ISREG(opened.st_mode)
            or _stat_identity(opened) != _stat_identity(expected)
        ):
            error = _error_payload("FileChangedDuringScan", "open")
        if opened is not None and error is None:
            digest = hashlib.sha256()
            try:
                while True:
                    chunk = os.read(descriptor, 1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
            except OSError as exc:
                error = _error_payload(type(exc).__name__, "hash")
            if error is None:
                try:
                    finished = os.fstat(descriptor)
                except OSError as exc:
                    error = _error_payload(type(exc).__name__, "hash-verify")
                else:
                    if _stat_identity(finished) != _stat_identity(opened):
                        error = _error_payload("FileChangedDuringScan", "hash")
                    else:
                        digest_value = digest.hexdigest()
    finally:
        try:
            os.close(descriptor)
        except OSError as exc:
            if error is None:
                error = _error_payload(type(exc).__name__, "close")
                digest_value = None
    if error is not None:
        return None, error
    try:
        current = os.lstat(path)
    except OSError as exc:
        return None, _error_payload(type(exc).__name__, "verify")
    if _stat_identity(current) != _stat_identity(expected):
        return None, _error_payload("FileChangedDuringScan", "verify")
    return digest_value, None


def sha256_file(path: Path) -> str:
    expected = os.lstat(path)
    if not stat.S_ISREG(expected.st_mode):
        raise OSError("sha256 source is not a regular file")
    digest, error = _hash_regular_path(path, expected=expected)
    if error is not None or digest is None:
        raise OSError(str(error.get("type", "hash-error") if error else "hash-error"))
    return digest


def _normalized_link_target(path: Path, raw_target: str) -> Path:
    candidate = Path(raw_target)
    if not candidate.is_absolute():
        candidate = path.parent / candidate
    return candidate.resolve(strict=False)


def _inside_allowed_roots(
    path: Path,
    allowed_roots: Iterable[Path],
    excluded_roots: Iterable[Path],
) -> bool:
    normalized = path.resolve(strict=False)
    if any(_is_under(normalized, excluded.resolve(strict=False)) for excluded in excluded_roots):
        return False
    return any(_is_under(normalized, root.resolve(strict=False)) for root in allowed_roots)


def _target_kind(value: os.stat_result) -> str:
    if stat.S_ISREG(value.st_mode):
        return "file"
    if stat.S_ISDIR(value.st_mode):
        return "directory"
    return "other"


def inspect_path(
    path: Path,
    *,
    consumers: Iterable[str] = (),
    request_format: str | None = None,
    expected_kind: Literal["file", "directory", "database"] = "file",
    status: str | None = None,
    metadata: Mapping[str, object] | None = None,
    required: bool = False,
    allowed_roots: Iterable[Path] | None = None,
    excluded_roots: Iterable[Path] = (),
    max_symlink_target_bytes: int = _MAX_SYMLINK_TARGET_BYTES,
    reference_class: str | None = None,
    activity_evidence: Iterable[Mapping[str, str]] = (),
) -> InventoryItem:
    """Describe a path without serializing its contents."""

    expanded = path.expanduser()
    item_metadata = dict(metadata or {})
    try:
        path_stat = os.lstat(expanded)
    except FileNotFoundError:
        if required:
            return InventoryItem(
                path=str(expanded.absolute()),
                kind=expected_kind,
                target=None,
                sha256=None,
                status="path-error",
                consumers=tuple(sorted(set(consumers))),
                request_format=request_format,
                metadata=item_metadata,
                required=True,
                error=_error_payload("MissingPath", "lstat"),
                reference_class=reference_class,
                activity_evidence=tuple(activity_evidence),
            )
        return InventoryItem(
            path=str(expanded.absolute()),
            kind=expected_kind,
            target=None,
            sha256=None,
            status=status or "missing",
            consumers=tuple(sorted(set(consumers))),
            request_format=request_format,
            metadata=item_metadata,
            required=False,
            reference_class=reference_class,
            activity_evidence=tuple(activity_evidence),
        )
    except OSError as exc:
        return InventoryItem(
            path=str(expanded.absolute()),
            kind=expected_kind,
            target=None,
            sha256=None,
            status="path-error",
            consumers=tuple(sorted(set(consumers))),
            request_format=request_format,
            metadata=item_metadata,
            required=required,
            error=_error_payload(type(exc).__name__, "lstat"),
            reference_class=reference_class,
            activity_evidence=tuple(activity_evidence),
        )

    if stat.S_ISLNK(path_stat.st_mode):
        try:
            raw_target_value = os.readlink(expanded)
            raw_target = os.fsdecode(raw_target_value)
            target = _normalized_link_target(expanded, raw_target)
        except (OSError, RuntimeError) as exc:
            return InventoryItem(
                path=str(expanded.absolute()),
                kind="symlink",
                target=None,
                sha256=None,
                status="path-error",
                consumers=tuple(sorted(set(consumers))),
                request_format=request_format,
                metadata=item_metadata,
                required=required,
                error=_error_payload(type(exc).__name__, "readlink"),
                reference_class=reference_class,
                activity_evidence=tuple(activity_evidence),
            )
        roots_for_link = tuple(allowed_roots or (expanded.parent,))
        try:
            inside_scope = _inside_allowed_roots(target, roots_for_link, excluded_roots)
        except (OSError, RuntimeError) as exc:
            link_metadata = {
                "raw_target": raw_target,
                "normalized_target": str(target),
                "target_state": "error",
                "target_kind": "unknown",
                "target_is_absolute": Path(raw_target).is_absolute(),
                "inside_allowed_roots": False,
                "scope_decision": "scope-normalization-error",
                "target_size_bytes": None,
            }
            return InventoryItem(
                path=str(expanded.absolute()),
                kind="symlink",
                target=str(target),
                sha256=None,
                status="path-error",
                consumers=tuple(sorted(set(consumers))),
                request_format=request_format,
                metadata=item_metadata,
                required=required,
                error=_error_payload(type(exc).__name__, "symlink-scope"),
                symlink=link_metadata,
                reference_class=reference_class,
                activity_evidence=tuple(activity_evidence),
            )
        target_state = "existing"
        kind = "unknown"
        target_size: int | None = None
        try:
            target_stat = os.lstat(target)
            kind = _target_kind(target_stat)
            if kind == "file":
                target_size = target_stat.st_size
        except FileNotFoundError:
            target_state = "broken"
            target_stat = None
        except OSError as exc:
            link_metadata = {
                "raw_target": raw_target,
                "normalized_target": str(target),
                "target_state": "error",
                "target_kind": "unknown",
                "target_is_absolute": Path(raw_target).is_absolute(),
                "inside_allowed_roots": inside_scope,
                "scope_decision": "target-read-error",
                "target_size_bytes": None,
            }
            return InventoryItem(
                path=str(expanded.absolute()),
                kind="symlink",
                target=str(target),
                sha256=None,
                status="path-error",
                consumers=tuple(sorted(set(consumers))),
                request_format=request_format,
                metadata=item_metadata,
                required=required,
                error=_error_payload(type(exc).__name__, "symlink-target-lstat"),
                symlink=link_metadata,
                reference_class=reference_class,
                activity_evidence=tuple(activity_evidence),
            )

        scope_decision = "hash-allowed-regular-target"
        target_hash: str | None = None
        link_status = status or "present"
        error: Mapping[str, object] | None = None
        if target_state == "broken":
            scope_decision = "broken-target"
            link_status = status or "broken"
        elif not inside_scope:
            scope_decision = "reject-outside-allowed-roots"
        elif kind != "file":
            scope_decision = "allowed-nonregular-target"
        elif target_size is not None and target_size > max_symlink_target_bytes:
            scope_decision = "target-too-large"
        elif target_stat is not None:
            target_hash, error = _hash_regular_path(target, expected=target_stat)
            if error is not None:
                link_status = "path-error"
                scope_decision = "target-read-error"

        if target_hash is not None:
            try:
                link_after = os.lstat(expanded)
                raw_after = os.fsdecode(os.readlink(expanded))
            except OSError as exc:
                error = _error_payload(type(exc).__name__, "symlink-verify")
                link_status = "path-error"
                scope_decision = "target-changed-during-scan"
                target_state = "changed"
                target_hash = None
            else:
                if (
                    (link_after.st_dev, link_after.st_ino) != (path_stat.st_dev, path_stat.st_ino)
                    or raw_after != raw_target
                ):
                    error = _error_payload("SymlinkChangedDuringScan", "symlink-verify")
                    link_status = "path-error"
                    scope_decision = "target-changed-during-scan"
                    target_state = "changed"
                    target_hash = None

        link_metadata = {
            "raw_target": raw_target,
            "normalized_target": str(target),
            "target_state": target_state,
            "target_kind": kind,
            "target_is_absolute": Path(raw_target).is_absolute(),
            "inside_allowed_roots": inside_scope,
            "scope_decision": scope_decision,
            "target_size_bytes": target_size,
        }
        return InventoryItem(
            path=str(expanded.absolute()),
            kind="symlink",
            target=str(target),
            sha256=target_hash,
            status=link_status,
            consumers=tuple(sorted(set(consumers))),
            request_format=request_format,
            metadata=item_metadata,
            required=required,
            error=error,
            symlink=link_metadata,
            reference_class=reference_class,
            activity_evidence=tuple(activity_evidence),
        )
    if stat.S_ISDIR(path_stat.st_mode):
        return InventoryItem(
            path=str(expanded.absolute()),
            kind="directory",
            target=None,
            sha256=None,
            status=status or "present",
            consumers=tuple(sorted(set(consumers))),
            request_format=request_format,
            metadata=item_metadata,
            required=required,
            reference_class=reference_class,
            activity_evidence=tuple(activity_evidence),
        )
    if stat.S_ISREG(path_stat.st_mode):
        kind: Literal["file", "database"] = "database" if expected_kind == "database" else "file"
        item_metadata.setdefault("size_bytes", path_stat.st_size)
        item_metadata.setdefault("mode", oct(path_stat.st_mode & 0o777))
        digest, error = _hash_regular_path(expanded, expected=path_stat)
        return InventoryItem(
            path=str(expanded.absolute()),
            kind=kind,
            target=None,
            sha256=digest,
            status="path-error" if error is not None else (status or "present"),
            consumers=tuple(sorted(set(consumers))),
            request_format=request_format,
            metadata=item_metadata,
            required=required,
            error=error,
            reference_class=reference_class,
            activity_evidence=tuple(activity_evidence),
        )
    return InventoryItem(
        path=str(expanded.absolute()),
        kind=expected_kind,
        target=None,
        sha256=None,
        status="path-error",
        consumers=tuple(sorted(set(consumers))),
        request_format=request_format,
        metadata=item_metadata,
        required=required,
        error=_error_payload("UnsupportedPathType", "lstat"),
        reference_class=reference_class,
        activity_evidence=tuple(activity_evidence),
    )


def _is_under(path: Path, parent: Path) -> bool:
    try:
        path.absolute().relative_to(parent.absolute())
    except ValueError:
        return False
    return True


def _is_skipped_directory_name(name: str) -> bool:
    return (
        name in _SKIP_DIRECTORY_NAMES
        or name == "venv"
        or name.endswith("-venv")
        or name.endswith("_venv")
    )


def _is_excluded(path: Path, excludes: Iterable[Path]) -> bool:
    if any(_is_skipped_directory_name(part) for part in path.parts):
        return True
    return any(_is_under(path, excluded.expanduser()) for excluded in excludes)


def _walk_paths(
    root: Path,
    excludes: Iterable[Path],
    *,
    error_sink: list[Mapping[str, object]] | None = None,
    required: bool = False,
) -> Iterable[Path]:
    sink = error_sink if error_sink is not None else []

    def record_walk_error(exc: OSError, fallback: Path = root) -> None:
        failure_path = Path(exc.filename) if exc.filename else fallback
        sink.append(
            _path_error_record(
                failure_path,
                operation="walk",
                error_type=type(exc).__name__,
                required=required,
            )
        )

    try:
        root_stat = os.lstat(root)
    except FileNotFoundError:
        if required:
            sink.append(
                _path_error_record(
                    root,
                    operation="walk",
                    error_type="MissingPath",
                    required=True,
                )
            )
        return
    except OSError as exc:
        record_walk_error(exc)
        return
    if stat.S_ISREG(root_stat.st_mode) or stat.S_ISLNK(root_stat.st_mode):
        if not _is_excluded(root, excludes):
            yield root
        return
    try:
        walker = os.walk(root, followlinks=False, onerror=record_walk_error)
        for directory, dirnames, filenames in walker:
            directory_path = Path(directory)
            dirnames[:] = sorted(
                name
                for name in dirnames
                if not _is_excluded(directory_path / name, excludes)
            )
            for name in sorted(filenames):
                path = directory_path / name
                if not _is_excluded(path, excludes):
                    yield path
            for name in sorted(dirnames):
                path = directory_path / name
                try:
                    path_stat = os.lstat(path)
                except OSError as exc:
                    record_walk_error(exc, path)
                    continue
                if stat.S_ISLNK(path_stat.st_mode) and not _is_excluded(path, excludes):
                    yield path
    except OSError as exc:
        record_walk_error(exc)


def _run(
    runner: Runner,
    args: Sequence[str],
    *,
    cwd: Path | None = None,
) -> RunResult:
    try:
        completed = runner(
            list(args),
            cwd=str(cwd) if cwd else None,
            text=False,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        return RunResult(
            returncode=127,
            stdout="",
            stdout_bytes=b"",
            error_type=type(exc).__name__,
        )

    def decode(value: object) -> tuple[str, bytes, bool]:
        if isinstance(value, bytes):
            try:
                return value.decode("utf-8"), value, False
            except UnicodeDecodeError:
                return value.decode("utf-8", errors="ignore"), value, True
        if isinstance(value, str):
            return value, value.encode("utf-8", errors="surrogateescape"), False
        return "", b"", value not in (None, "", b"")

    stdout, stdout_bytes, stdout_decode_error = decode(completed.stdout)
    _, _, stderr_decode_error = decode(completed.stderr)
    return RunResult(
        returncode=int(completed.returncode),
        stdout=stdout,
        stdout_bytes=stdout_bytes,
        decode_error=stdout_decode_error or stderr_decode_error,
    )


def _git_output(runner: Runner, repo: Path, args: Sequence[str]) -> tuple[int, str]:
    result = _run(runner, ("git", "--no-optional-locks", "-C", str(repo), *args))
    return result.returncode, result.stdout.strip()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="surrogateescape")).hexdigest()


def _git_diff_sha256(
    runner: Runner,
    repo: Path,
    args: Sequence[str],
) -> tuple[str | None, str]:
    result = _run(runner, ("git", "--no-optional-locks", "-C", str(repo), *args))
    if result.returncode != 0:
        return None, ""
    return hashlib.sha256(result.stdout_bytes).hexdigest(), result.stdout


def _parse_porcelain_v1_z(output: str, repo: Path) -> list[Mapping[str, object]]:
    chunks = output.split("\0")
    records: list[Mapping[str, object]] = []
    index = 0
    while index < len(chunks):
        chunk = chunks[index]
        index += 1
        if len(chunk) < 4:
            continue
        status = chunk[:2]
        relative_path = chunk[3:]
        original_path: str | None = None
        if "R" in status or "C" in status:
            if index < len(chunks) and chunks[index]:
                original_path = chunks[index]
                index += 1

        item = inspect_path(repo / relative_path)
        record: dict[str, object] = {
            "status": status,
            "path": relative_path,
            "kind": item.kind,
            "sha256": item.sha256,
        }
        if original_path is not None:
            record["original_path"] = original_path
        if item.target is not None:
            record["target"] = item.target
        records.append(record)
    return sorted(records, key=lambda row: (str(row["path"]), str(row["status"])))


def _untracked_tree_sha256(entries: Iterable[Mapping[str, object]]) -> str:
    digest = hashlib.sha256()
    for entry in entries:
        if entry.get("status") != "??":
            continue
        for key in ("path", "kind", "sha256", "target"):
            digest.update(str(entry.get(key, "")).encode("utf-8", errors="surrogateescape"))
            digest.update(b"\0")
    return digest.hexdigest()


def _repository_exclusion_pathspecs(
    repository: Path,
    excludes: Iterable[Path],
) -> tuple[str, ...]:
    pathspecs = {
        f":(exclude,glob)**/{name}/**" for name in _SKIP_DIRECTORY_NAMES
    }
    pathspecs.update(
        {
            ":(exclude,glob)**/venv/**",
            ":(exclude,glob)**/*-venv/**",
            ":(exclude,glob)**/*_venv/**",
        }
    )
    for excluded in excludes:
        try:
            relative = excluded.expanduser().absolute().relative_to(repository.absolute())
        except ValueError:
            continue
        if relative.parts:
            pathspecs.add(f":(exclude,glob){relative.as_posix()}")
            pathspecs.add(f":(exclude,glob){relative.as_posix()}/**")
    return tuple(sorted(pathspecs))


def _repository_record(
    runner: Runner,
    path: Path,
    excludes: Iterable[Path] = (),
    *,
    required: bool = False,
    allowed_roots: Iterable[Path] | None = None,
) -> Mapping[str, object]:
    item = inspect_path(
        path,
        expected_kind="directory",
        required=required,
        allowed_roots=allowed_roots,
        excluded_roots=excludes,
    )
    result = item.to_mapping()
    if item.status != "present":
        return result
    code, top = _git_output(runner, path, ("rev-parse", "--show-toplevel"))
    if code != 0:
        result["status"] = "present-not-git"
        return result
    head_code, head = _git_output(runner, path, ("rev-parse", "HEAD"))
    _, branch = _git_output(runner, path, ("branch", "--show-current"))
    pathspecs = _repository_exclusion_pathspecs(path, excludes)
    status_result = _run(
        runner,
        (
            "git",
            "--no-optional-locks",
            "-C",
            str(path),
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            "--",
            *pathspecs,
        ),
    )
    if status_result.returncode != 0 or status_result.decode_error:
        result["status"] = "git-status-probe-error"
        result["metadata"] = {
            "branch": branch or None,
            "head": head if head_code == 0 and head else None,
            "toplevel": top,
            "probe": "git status --porcelain=v1 -z",
            "returncode": status_result.returncode,
        }
        result["required"] = required
        result["error"] = _error_payload(
            (
                "UnicodeDecodeError"
                if status_result.decode_error
                else status_result.error_type or "NonZeroExit"
            ),
            "git-status",
        )
        return result
    porcelain = status_result.stdout
    wip_entries = _parse_porcelain_v1_z(porcelain, path)
    staged_sha, staged_patch = _git_diff_sha256(
        runner,
        path,
        (
            "diff",
            "--cached",
            "--binary",
            "--full-index",
            "--no-ext-diff",
            "--no-textconv",
            "HEAD",
            "--",
            *pathspecs,
        ),
    )
    if staged_sha is None and head_code != 0:
        staged_sha, staged_patch = _git_diff_sha256(
            runner,
            path,
            (
                "diff",
                "--cached",
                "--binary",
                "--full-index",
                "--no-ext-diff",
                "--no-textconv",
                "--",
                *pathspecs,
            ),
        )
    unstaged_sha, unstaged_patch = _git_diff_sha256(
        runner,
        path,
        (
            "diff",
            "--binary",
            "--full-index",
            "--no-ext-diff",
            "--no-textconv",
            "--",
            *pathspecs,
        ),
    )
    tracked_sha, _ = _git_diff_sha256(
        runner,
        path,
        (
            "diff",
            "--binary",
            "--full-index",
            "--no-ext-diff",
            "--no-textconv",
            "HEAD",
            "--",
            *pathspecs,
        ),
    )
    tracked_basis = "HEAD"
    if tracked_sha is None and head_code != 0:
        tracked_sha = _sha256_text(f"staged\0{staged_patch}\0unstaged\0{unstaged_patch}")
        tracked_basis = "unborn-staged-and-unstaged"

    diff_probe_error = any(
        digest is None for digest in (staged_sha, unstaged_sha, tracked_sha)
    )
    result["status"] = (
        "git-diff-probe-error"
        if diff_probe_error
        else ("dirty" if wip_entries else "clean")
    )
    result["metadata"] = {
        "branch": branch or None,
        "head": head if head_code == 0 and head else None,
        "toplevel": top,
        "dirty_entry_count": len(wip_entries),
        "wip_entries": wip_entries,
        "tracked_diff_sha256": tracked_sha,
        "tracked_diff_basis": tracked_basis,
        "staged_diff_sha256": staged_sha,
        "unstaged_diff_sha256": unstaged_sha,
        "untracked_tree_sha256": _untracked_tree_sha256(wip_entries),
    }
    if diff_probe_error:
        result["required"] = required
        result["error"] = _error_payload("NonZeroExit", "git-diff")
    return result


def _git_ref_records(
    runner: Runner,
    repositories: Iterable[Path],
    base_ref: str,
) -> list[Mapping[str, object]]:
    records: list[Mapping[str, object]] = []
    for repo in repositories:
        code, _ = _git_output(runner, repo, ("rev-parse", "--git-dir"))
        if code != 0:
            continue
        refs_result = _run(
            runner,
            (
                "git",
                "--no-optional-locks",
                "-C",
                str(repo),
                "for-each-ref",
                "--format=%(refname)%00%(objectname)%00%(upstream:short)",
                "refs/heads",
                "refs/remotes",
                "refs/rescue",
                "refs/spike",
            ),
        )
        if refs_result.returncode != 0 or refs_result.decode_error:
            records.append(
                {
                    "kind": "probe_error",
                    "repository": str(repo.absolute()),
                    "status": "git-ref-probe-error",
                    "probe": "git for-each-ref",
                    "returncode": refs_result.returncode,
                    "required": True,
                    "error": _error_payload(
                        (
                            "UnicodeDecodeError"
                            if refs_result.decode_error
                            else refs_result.error_type or "NonZeroExit"
                        ),
                        "git-for-each-ref",
                    ),
                }
            )
            continue
        refs = refs_result.stdout
        base_code, base_sha = _git_output(runner, repo, ("rev-parse", "--verify", base_ref))
        if base_code != 0:
            _, base_sha = _git_output(runner, repo, ("rev-parse", "HEAD"))
        for line_index, line in enumerate(refs.splitlines(), start=1):
            parts = line.split("\0")
            if len(parts) < 2:
                records.append(
                    {
                        "kind": "probe_error",
                        "repository": str(repo.absolute()),
                        "status": "git-ref-probe-error",
                        "probe": "git for-each-ref",
                        "line_index": line_index,
                        "required": True,
                        "error": _error_payload(
                            "MalformedGitRefRecord",
                            "git-for-each-ref",
                        ),
                    }
                )
                continue
            ref_name, head = parts[:2]
            upstream = parts[2] if len(parts) > 2 else ""
            unreachable: list[str] = []
            if base_sha:
                unique_code, unique_output = _git_output(
                    runner,
                    repo,
                    ("rev-list", ref_name, "--not", base_sha),
                )
                if unique_code == 0 and unique_output:
                    unreachable = unique_output.splitlines()
            record: dict[str, object] = {
                "repository": str(repo.absolute()),
                "ref": ref_name,
                "head": head,
                "upstream": upstream or None,
                "chosen_base": base_sha or None,
                "unreachable_from_base": unreachable,
            }
            if base_sha and unique_code != 0:
                record["status"] = "git-ref-ancestry-probe-error"
                record["returncode"] = unique_code
                record["required"] = True
                record["error"] = _error_payload("NonZeroExit", "git-rev-list")
            records.append(record)
    return sorted(
        records,
        key=lambda row: (str(row["repository"]), str(row.get("ref", ""))),
    )


def _probe_error_record(
    probe: str,
    *,
    error_type: str,
    required: bool,
    returncode: int | None = None,
    line_index: int | None = None,
) -> dict[str, object]:
    record: dict[str, object] = {
        "kind": "probe_error",
        "status": "probe-error",
        "probe": probe,
        "required": required,
        "error": _error_payload(error_type, probe),
    }
    if returncode is not None:
        record["returncode"] = returncode
    if line_index is not None:
        record["line_index"] = line_index
    return record


def _process_role(command: str) -> str | None:
    lowered = command.lower()
    for token, role in _PROCESS_ROLES:
        if token in lowered:
            return role
    return None


def _process_executable(command: str) -> str:
    try:
        argv = shlex.split(command, posix=True)
    except ValueError:
        argv = command.split()
    if not argv:
        return "unknown"
    executable = Path(argv[0]).name or "unknown"
    sanitized = _SAFE_EXECUTABLE.sub("_", executable)
    return sanitized[:128] or "unknown"


def _process_records(
    runner: Runner,
) -> tuple[list[Mapping[str, object]], list[ProcessObservation]]:
    result = _run(runner, ("ps", "-axo", "pid=,ppid=,command="))
    if result.returncode != 0:
        return (
            [
                _probe_error_record(
                    "ps",
                    error_type=result.error_type or "NonZeroExit",
                    required=True,
                    returncode=result.returncode,
                )
            ],
            [],
        )
    records: list[Mapping[str, object]] = []
    observations: list[ProcessObservation] = []
    if result.decode_error:
        records.append(
            _probe_error_record(
                "ps",
                error_type="UnicodeDecodeError",
                required=True,
                returncode=result.returncode,
            )
        )
    for line_index, line in enumerate(result.stdout.splitlines(), start=1):
        fields = line.strip().split(maxsplit=2)
        if len(fields) != 3:
            records.append(
                _probe_error_record(
                    "ps",
                    error_type="MalformedProcessRecord",
                    required=True,
                    line_index=line_index,
                )
            )
            continue
        pid, ppid, command = fields
        try:
            pid_value = int(pid)
            ppid_value = int(ppid)
        except ValueError:
            records.append(
                _probe_error_record(
                    "ps",
                    error_type="MalformedProcessRecord",
                    required=True,
                    line_index=line_index,
                )
            )
            continue
        if pid_value == os.getpid():
            continue
        role = _process_role(command)
        if role is None:
            continue
        executable = _process_executable(command)
        runtime_signature = _sha256_text(f"{role}\0{executable}")
        records.append(
            {
                "kind": "process",
                "pid": pid_value,
                "ppid": ppid_value,
                "role": role,
                "executable": executable,
                "runtime_signature": runtime_signature,
                "status": "running",
            }
        )
        observations.append(
            ProcessObservation(
                pid=pid_value,
                ppid=ppid_value,
                role=role,
                executable=executable,
                command=command,
            )
        )
    return records, observations


def _launchd_records(runner: Runner, roots: InventoryRoots) -> list[Mapping[str, object]]:
    records: list[Mapping[str, object]] = []
    launch_agents = roots.home / "Library/LaunchAgents"
    discovery_errors: list[Mapping[str, object]] = []
    launch_agent_paths = _directory_named_paths(
        launch_agents,
        predicate=lambda name: name.endswith(".plist")
        and _is_product_launchd_label(Path(name).stem),
        error_sink=discovery_errors,
        required=roots.is_required(launch_agents),
    )
    for path in launch_agent_paths:
        records.append(
            inspect_path(
                path,
                required=roots.is_required(path),
                allowed_roots=roots.allowed_roots(),
                excluded_roots=roots.excludes,
            ).to_mapping()
        )
    records.extend(discovery_errors)
    result = _run(runner, ("launchctl", "list"))
    if result.returncode == 0:
        if result.decode_error:
            records.append(
                _probe_error_record(
                    "launchctl-list",
                    error_type="UnicodeDecodeError",
                    required=True,
                    returncode=result.returncode,
                )
            )
        for line_index, line in enumerate(result.stdout.splitlines(), start=1):
            fields = line.split(maxsplit=2)
            label = fields[2] if len(fields) > 2 else line.strip()
            if not _is_product_launchd_label(label):
                continue
            if len(fields) != 3:
                records.append(
                    _probe_error_record(
                        "launchctl-list",
                        error_type="MalformedLaunchdRecord",
                        required=True,
                        line_index=line_index,
                    )
                )
                continue
            records.append(
                {
                    "kind": "launchd",
                    "pid": None if not fields or fields[0] == "-" else fields[0],
                    "last_exit_status": fields[1] if len(fields) > 1 else None,
                    "label": label,
                    "status": "loaded",
                }
            )
    else:
        records.append(
            _probe_error_record(
                "launchctl-list",
                error_type=result.error_type or "NonZeroExit",
                required=True,
                returncode=result.returncode,
            )
        )
    return records


def _is_product_launchd_label(label: str) -> bool:
    lowered = label.lower()
    return lowered in _LAUNCHD_EXACT_LABELS or lowered.startswith(_LAUNCHD_LABEL_PREFIXES)


def inspect_database(
    path: Path,
    *,
    runner: Runner = subprocess.run,
    required: bool = False,
) -> Mapping[str, object]:
    """Inspect SQLite structure without file metadata, handles, or row values."""

    del runner  # Open-handle evidence is intentionally outside the private manifest.
    absolute = path.expanduser().absolute()
    try:
        path_stat = os.lstat(absolute)
    except FileNotFoundError:
        record: dict[str, object] = {
            "path": str(absolute),
            "kind": "database",
            "status": "path-error" if required else "missing",
            "required": required,
        }
        if required:
            record["error"] = _error_payload("MissingPath", "lstat")
        return record
    except OSError as exc:
        return {
            "path": str(absolute),
            "kind": "database",
            "status": "path-error",
            "required": required,
            "error": _error_payload(type(exc).__name__, "lstat"),
        }
    if not stat.S_ISREG(path_stat.st_mode):
        return {
            "path": str(absolute),
            "kind": "database",
            "status": "path-error",
            "required": required,
            "error": _error_payload("NotRegularFile", "sqlite-open"),
        }

    record = {
        "path": str(absolute),
        "kind": "database",
        "status": "present",
        "required": required,
    }
    uri = f"file:{absolute}?mode=ro"
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=1.0)
        connection.execute("PRAGMA query_only=ON")
        user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        schema_version = int(connection.execute("PRAGMA schema_version").fetchone()[0])
        journal_mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0])
        table_names = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        counts: dict[str, int] = {}
        for table in table_names:
            escaped = table.replace('"', '""')
            try:
                counts[table] = int(
                    connection.execute(f'SELECT COUNT(*) FROM "{escaped}"').fetchone()[0]
                )
            except sqlite3.Error as exc:
                record["status"] = "sqlite-probe-error"
                record["error"] = _error_payload(type(exc).__name__, "sqlite-count")
                break
        record.update(
            {
                "user_version": user_version,
                "schema_version": schema_version,
                "journal_mode": journal_mode,
                "tables": table_names,
                "record_counts": counts,
            }
        )
    except sqlite3.Error as exc:
        record["status"] = "sqlite-probe-error"
        record["error"] = _error_payload(type(exc).__name__, "sqlite-probe")
    finally:
        if connection is not None:
            connection.close()
    return record


def _database_records(roots: InventoryRoots, runner: Runner) -> list[Mapping[str, object]]:
    candidates = {
        roots.home / ".dan/memory.db",
        roots.home / ".dan/dan.db",
        roots.home / ".jarvis/jarvis.db",
    }
    discovery_errors: list[Mapping[str, object]] = []
    for directory in (roots.home / ".dan", roots.home / ".jarvis"):
        candidates.update(
            _directory_named_paths(
                directory,
                predicate=lambda name: name.endswith(".db"),
                error_sink=discovery_errors,
                required=roots.is_required(directory),
            )
        )
    records = [
        inspect_database(path, runner=runner, required=roots.is_required(path))
        for path in sorted(candidates, key=str)
    ]
    records.extend(discovery_errors)
    return records


def _records_for_roots(
    roots_to_scan: Iterable[Path],
    roots: InventoryRoots,
) -> list[Mapping[str, object]]:
    records: list[Mapping[str, object]] = []
    errors: list[Mapping[str, object]] = []
    seen: set[str] = set()
    for root in roots_to_scan:
        root_key = str(root.absolute())
        if root_key not in seen:
            records.append(
                inspect_path(
                    root,
                    expected_kind="directory",
                    required=roots.is_required(root),
                    allowed_roots=roots.allowed_roots(),
                    excluded_roots=roots.excludes,
                ).to_mapping()
            )
            seen.add(root_key)
        for path in _walk_paths(
            root,
            roots.excludes,
            error_sink=errors,
            required=roots.is_required(root),
        ):
            key = str(path.absolute())
            if key in seen:
                continue
            seen.add(key)
            records.append(
                inspect_path(
                    path,
                    required=roots.is_required(path),
                    allowed_roots=roots.allowed_roots(),
                    excluded_roots=roots.excludes,
                ).to_mapping()
            )
    records.extend(errors)
    return records


def _directory_named_paths(
    directory: Path,
    *,
    predicate: Any,
    error_sink: list[Mapping[str, object]],
    required: bool,
) -> tuple[Path, ...]:
    try:
        with os.scandir(directory) as entries:
            return tuple(
                sorted(
                    (directory / entry.name for entry in entries if predicate(entry.name)),
                    key=str,
                )
            )
    except FileNotFoundError:
        if required:
            error_sink.append(
                _path_error_record(
                    directory,
                    operation="scandir",
                    error_type="MissingPath",
                    required=True,
                )
            )
        return ()
    except OSError as exc:
        error_sink.append(
            _path_error_record(
                directory,
                operation="scandir",
                error_type=type(exc).__name__,
                required=required,
            )
        )
        return ()


def _config_source_paths(
    roots: InventoryRoots,
    error_sink: list[Mapping[str, object]] | None = None,
) -> tuple[Path, ...]:
    donor = roots.home / "Documents/dev/dan"
    sink = error_sink if error_sink is not None else []
    claude_agents_root = roots.home / ".claude/agents"
    claude_agents = _directory_named_paths(
        claude_agents_root,
        predicate=lambda name: name.endswith(".md"),
        error_sink=sink,
        required=roots.is_required(claude_agents_root),
    )
    repository_configs = tuple(
        candidate
        for repository in roots.repository_paths()
        for candidate in (
            repository / "AGENTS.md",
            repository / "CLAUDE.md",
            repository / ".claude/settings.json",
            repository / ".claude/settings.local.json",
        )
    )
    return _unique_paths(
        (
            donor / "config/persona/DAN.md",
            donor / "state/overrides.json",
            roots.home / ".config/voice/personas.toml",
            roots.home / ".config/voice/pronunciations.toml",
            roots.home / ".config/voice/gains.json",
            roots.home / ".jarvis/jarvis.toml",
            roots.home / ".dan/config.toml",
            roots.home / ".dan/owner.toml",
            roots.home / ".dan/secrets.env",
            roots.home / ".agents/.skill-lock.json",
            roots.home / "AGENTS.md",
            roots.home / ".claude/CLAUDE.md",
            roots.home / ".claude/plugins/installed_plugins.json",
            roots.home / ".claude/settings.json",
            roots.home / ".claude/settings.local.json",
            roots.home / ".codex/.codex-global-state.json",
            roots.home / ".codex/AGENTS.md",
            roots.home / ".codex/auth.json",
            roots.home / ".codex/config.toml",
            roots.home / ".codex/memories/MEMORY.md",
            roots.home / ".codex/rules/default.rules",
            roots.home / ".openclaw/exec-approvals.json",
            roots.home / ".openclaw/identity/device-auth.json",
            roots.home / ".openclaw/openclaw.json",
            roots.home / ".openclaw/workspace/AGENTS.md",
            roots.home / ".openclaw/workspace/DREAMS.md",
            roots.home / ".openclaw/workspace/HEARTBEAT.md",
            roots.home / ".openclaw/workspace/IDENTITY.md",
            roots.home / ".openclaw/workspace/MEMORY.md",
            roots.home / ".openclaw/workspace/SOUL.md",
            roots.home / ".openclaw/workspace/TOOLS.md",
            roots.home / ".openclaw/workspace/USER.md",
        )
        + repository_configs
        + claude_agents
        + roots.reference_memory_roots()
    )


def _voice_asset_roots(roots: InventoryRoots) -> tuple[Path, ...]:
    donor = roots.home / "Documents/dev/dan"
    return _unique_paths(
        (
            roots.home / ".config/voice",
            roots.home / ".cache/supertonic3/custom_styles",
            donor / "tools/jarvis/chatterbox",
            donor / "config/voice",
            donor / "_sesja-glosy-2026-07-11",
        )
    )


def _hook_roots(roots: InventoryRoots) -> tuple[Path, ...]:
    return _unique_paths((roots.home / ".claude/hooks", roots.home / ".claude/bin"))


def _read_signatures(path: Path) -> tuple[tuple[str, ...], bytes, Mapping[str, object] | None]:
    try:
        path_stat = os.lstat(path)
    except OSError as exc:
        return (), b"", _error_payload(type(exc).__name__, "read-signatures-lstat")
    if not stat.S_ISREG(path_stat.st_mode):
        return (), b"", _error_payload("NotRegularFile", "read-signatures-lstat")
    if path_stat.st_size > _MAX_DISCOVERY_FILE_BYTES:
        return (), b"", None

    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        return (), b"", _error_payload(type(exc).__name__, "read-signatures-open")
    error: Mapping[str, object] | None = None
    payload = b""
    try:
        try:
            opened = os.fstat(descriptor)
        except OSError as exc:
            error = _error_payload(type(exc).__name__, "read-signatures-fstat")
            opened = None
        if opened is not None and (
            not stat.S_ISREG(opened.st_mode)
            or _stat_identity(opened) != _stat_identity(path_stat)
        ):
            error = _error_payload("FileChangedDuringScan", "read-signatures-open")
        if opened is not None and error is None:
            chunks: list[bytes] = []
            total = 0
            try:
                while True:
                    read_size = min(
                        1024 * 1024,
                        _MAX_DISCOVERY_FILE_BYTES + 1 - total,
                    )
                    chunk = os.read(descriptor, read_size)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    total += len(chunk)
                    if total > _MAX_DISCOVERY_FILE_BYTES:
                        error = _error_payload(
                            "FileChangedDuringScan",
                            "read-signatures-size",
                        )
                        break
            except OSError as exc:
                error = _error_payload(type(exc).__name__, "read-signatures-read")
            if error is None:
                try:
                    finished = os.fstat(descriptor)
                except OSError as exc:
                    error = _error_payload(
                        type(exc).__name__,
                        "read-signatures-verify",
                    )
                else:
                    if _stat_identity(finished) != _stat_identity(opened):
                        error = _error_payload(
                            "FileChangedDuringScan",
                            "read-signatures-verify",
                        )
                    else:
                        payload = b"".join(chunks)
    finally:
        try:
            os.close(descriptor)
        except OSError as exc:
            if error is None:
                error = _error_payload(type(exc).__name__, "read-signatures-close")
                payload = b""
    if error is not None:
        return (), b"", error
    try:
        current = os.lstat(path)
    except OSError as exc:
        return (), b"", _error_payload(type(exc).__name__, "read-signatures-verify")
    if _stat_identity(current) != _stat_identity(path_stat):
        return (), b"", _error_payload(
            "FileChangedDuringScan",
            "read-signatures-verify",
        )
    return (
        tuple(name for token, name in _PRODUCER_SIGNATURES if token in payload),
        payload,
        None,
    )


def _is_executable_file(path: Path) -> bool:
    try:
        return bool(os.lstat(path).st_mode & 0o111)
    except OSError:
        return False


def _is_backup_reference(path: Path) -> bool:
    lowered = "/".join(part.lower() for part in path.parts)
    return ".bak-" in path.name.lower() or "quarantine" in lowered or "/archive/" in lowered


def _is_memory_reference(path: Path, roots: InventoryRoots) -> bool:
    if path.name == "MEMORY.md" or "/.codex/memories/" in str(path):
        return True
    return any(_is_under(path, root) for root in roots.reference_memory_roots())


def _is_active_instruction(path: Path) -> bool:
    if "/plugins/cache/" in str(path):
        return False
    return path.name in {"AGENTS.md", "CLAUDE.md", "SKILL.md"} or path.suffix == ".rules"


def _line_invokes_candidate(line: str, candidate: Path) -> bool:
    if str(candidate) not in line and candidate.name not in line:
        return False
    lowered = line.lower()
    return any(
        marker in lowered
        for marker in (
            "run:",
            "exec ",
            "source ",
            "bash ",
            "zsh ",
            " sh ",
            "python ",
            "python3 ",
            "command:",
            "$(",
        )
    )


def _payload_invokes_candidate(payload: bytes, candidate: Path) -> bool:
    text = payload.decode("utf-8", errors="ignore")
    return any(_line_invokes_candidate(line, candidate) for line in text.splitlines())


def _process_invokes_candidate(observation: ProcessObservation, candidate: Path) -> bool:
    if str(candidate) in observation.command:
        return True
    try:
        argv = shlex.split(observation.command, posix=True)
    except ValueError:
        argv = observation.command.split()
    return candidate.name in {Path(argument).name for argument in argv}


def _call_evidence_kind(source: Path) -> str | None:
    source_string = str(source)
    if _is_backup_reference(source):
        return None
    if source.name == "SKILL.md":
        return "active-skill-call"
    if source.name in {"AGENTS.md", "CLAUDE.md"} or source.suffix == ".rules":
        return "active-instruction-call"
    if source.suffix == ".plist":
        return "launchd-config"
    if source.name in {"settings.json", "settings.local.json"}:
        return "hook-config"
    if "/.claude/hooks/" in source_string or "/.claude/bin/" in source_string:
        return "runtime-call"
    if _is_executable_file(source):
        return "runtime-call"
    return None


def _runtime_activity_evidence(
    candidate: Path,
    scanned_files: Iterable[ScannedReferenceFile],
    process_observations: Iterable[ProcessObservation],
) -> tuple[Mapping[str, str], ...]:
    evidence: list[Mapping[str, str]] = []
    for process in process_observations:
        if _process_invokes_candidate(process, candidate):
            evidence.append(
                {
                    "kind": "process",
                    "source": f"process:{process.pid}:{process.role}",
                }
            )
    for source in scanned_files:
        if source.path == candidate or not _payload_invokes_candidate(source.payload, candidate):
            continue
        evidence_kind = _call_evidence_kind(source.path)
        if evidence_kind is not None:
            evidence.append({"kind": evidence_kind, "source": str(source.path.absolute())})
    unique = {
        (str(row["kind"]), str(row["source"])): row
        for row in evidence
    }
    return tuple(unique[key] for key in sorted(unique))


def _classify_reference(
    path: Path,
    roots: InventoryRoots,
    scanned_files: Iterable[ScannedReferenceFile],
    process_observations: Iterable[ProcessObservation],
) -> tuple[str, tuple[Mapping[str, str], ...]]:
    runtime_evidence = _runtime_activity_evidence(path, scanned_files, process_observations)
    if runtime_evidence:
        return "active-runtime-producer", runtime_evidence
    if _is_memory_reference(path, roots):
        return "historical-memory-reference", ()
    if _is_backup_reference(path):
        return "inactive-backup-archive-candidate", ()
    if _is_active_instruction(path):
        evidence_kind = "active-skill" if path.name == "SKILL.md" else "active-instruction"
        return (
            "active-consumer-instruction",
            ({"kind": evidence_kind, "source": str(path.absolute())},),
        )
    return "unproven-runtime-reference", ()


def _is_producer_candidate(path: Path) -> bool:
    """Limit producer discovery to executable/config/injected instruction surfaces."""

    if tuple(path.parts[-3:]) == ("jarvis", "migration", "inventory.py"):
        return False
    lowered_parts = {part.lower() for part in path.parts}
    if "tests" in lowered_parts or "docs" in lowered_parts:
        return False
    path_string = str(path)
    if "/workspace/memory/" in path_string or (
        "/.claude/projects/" in path_string and "/memory/" in path_string
    ):
        return True
    if path.name in {"AGENTS.md", "CLAUDE.md", "SKILL.md", "MEMORY.md"}:
        return True
    if _is_executable_file(path) or ".bak-" in path.name.lower():
        return True
    if path.suffix.lower() in {
        "",
        ".bash",
        ".conf",
        ".fish",
        ".js",
        ".json",
        ".plist",
        ".py",
        ".rules",
        ".sh",
        ".toml",
        ".yaml",
        ".yml",
        ".zsh",
    }:
        return True
    return path.suffix.lower() == ".txt" and (
        "playlist" in path.name.lower() or "live" in lowered_parts
    )


def _producer_records(
    roots: InventoryRoots,
    process_observations: Iterable[ProcessObservation],
) -> tuple[
    list[Mapping[str, object]],
    list[Mapping[str, object]],
    list[ScannedReferenceFile],
]:
    producers: list[Mapping[str, object]] = []
    request_formats: list[Mapping[str, object]] = []
    scanned_files: list[ScannedReferenceFile] = []
    signature_candidates: list[tuple[Path, tuple[str, ...], bool]] = []
    errors: list[Mapping[str, object]] = []
    seen: set[str] = set()
    for root in roots.producer_scan_roots(errors):
        scan_required = roots.is_required(root)
        for path in _walk_paths(
            root,
            roots.excludes,
            error_sink=errors,
            required=scan_required,
        ):
            try:
                path_stat = os.lstat(path)
            except OSError as exc:
                errors.append(
                    _path_error_record(
                        path,
                        operation="lstat",
                        error_type=type(exc).__name__,
                        required=scan_required,
                    )
                )
                continue
            if not stat.S_ISREG(path_stat.st_mode) or not _is_producer_candidate(path):
                continue
            key = str(path.absolute())
            if key in seen:
                continue
            seen.add(key)
            signatures, payload, read_error = _read_signatures(path)
            if read_error is not None:
                errors.append(
                    {
                        "kind": "path_error",
                        "path": key,
                        "status": "path-error",
                        "required": scan_required,
                        "error": read_error,
                    }
                )
                continue
            scanned_files.append(ScannedReferenceFile(path=path, payload=payload))
            if not signatures:
                continue
            signature_candidates.append((path, signatures, scan_required))

    process_snapshot = tuple(process_observations)
    scanned_snapshot = tuple(scanned_files)
    for path, signatures, required in signature_candidates:
        reference_class, activity_evidence = _classify_reference(
            path,
            roots,
            scanned_snapshot,
            process_snapshot,
        )
        item = inspect_path(
            path,
            request_format=",".join(signatures),
            required=required,
            allowed_roots=roots.allowed_roots(),
            excluded_roots=roots.excludes,
            reference_class=reference_class,
            activity_evidence=activity_evidence,
        )
        record = item.to_mapping()
        record["formats"] = list(signatures)
        producers.append(record)
        producer_key = str(path.absolute())
        for format_name in signatures:
            request_formats.append(
                {
                    "id": f"{format_name}:{producer_key}",
                    "format": format_name,
                    "producer_path": producer_key,
                    "status": "discovered",
                    "reference_class": reference_class,
                    "activity_evidence": list(activity_evidence),
                }
            )
    producers.extend(errors)
    request_formats.extend(
        (
            {
                "id": "legacy-dan-voice-json-runtime",
                "format": "legacy-dan-voice-json",
                "producer_path": str((roots.tmp_root / "dan-voice/req").absolute()),
                "status": "runtime-contract",
                "reference_class": "unproven-runtime-reference",
                "activity_evidence": [],
            },
            {
                "id": "legacy-claude-hook-switch",
                "format": "legacy-hook-off-file",
                "producer_path": str((roots.tmp_root / "claude-loud-thinking/OFF").absolute()),
                "status": "runtime-contract",
                "reference_class": "unproven-runtime-reference",
                "activity_evidence": [],
            },
        )
    )
    return producers, request_formats, scanned_files


def _find_input_materials(
    roots: InventoryRoots,
    scanned_files: Iterable[ScannedReferenceFile],
    process_observations: Iterable[ProcessObservation],
    existing_records: Iterable[Mapping[str, object]] = (),
) -> list[Mapping[str, object]]:
    donor = roots.home / "Documents/dev/dan"
    explicit = [
        roots.home / "Documents/summary.md",
        roots.home / "Documents/opinia-planu.md",
        roots.home / "Desktop/djdan-visualizer.html",
        donor / "docs/RADIO-DAN-KONSOLIDACJA-PLAN.md",
        donor / "_sesja-glosy-2026-07-11",
        donor / "_quarantine-continuity-fix-2026-07-08",
        donor / "_quarantine-wcinki-2026-07-11",
        roots.home / ".claude/skills/_quarantine-gadanie-2026-07-14",
    ]
    scan_errors: list[Mapping[str, object]] = []
    explicit.extend(
        _find_voice_lab_materials(
            donor,
            roots.excludes,
            error_sink=scan_errors,
            required=roots.is_required(donor),
        )
    )
    candidates = _unique_paths(explicit)
    searchable_files = tuple(scanned_files)
    live_processes = tuple(process_observations)
    existing_by_path = {
        str(record.get("path")): record for record in existing_records if record.get("path")
    }
    records: dict[str, Mapping[str, object]] = {}
    historical_names = {path.name for path in candidates if "quarantine" in path.name.lower()}
    for path in candidates:
        consumers, activity_evidence = _find_consumers(
            path,
            searchable_files,
            live_processes,
            roots,
        )
        if path.name in historical_names:
            decision = "active-source" if consumers else "archive/do-not-copy"
        else:
            decision = "input-material"
        root_item = inspect_path(
            path,
            consumers=consumers,
            expected_kind="directory" if path.suffix == "" else "file",
            status=decision,
            metadata={"decision": decision, "source_root": str(path.absolute())},
            required=roots.is_required(path),
            allowed_roots=roots.allowed_roots(),
            excluded_roots=roots.excludes,
            activity_evidence=activity_evidence,
            reference_class=(
                "active-runtime-producer"
                if activity_evidence
                else (
                    "inactive-backup-archive-candidate"
                    if path.name in historical_names
                    else "historical-memory-reference"
                )
            ),
        )
        root_record = root_item.to_mapping()
        records[str(path.absolute())] = root_record
        if root_item.kind != "directory" or root_item.status in {"missing", "path-error"}:
            continue
        for child in _walk_paths(
            path,
            roots.excludes,
            error_sink=scan_errors,
            required=roots.is_required(path),
        ):
            key = str(child.absolute())
            if key in existing_by_path:
                child_record = dict(existing_by_path[key])
                child_record["metadata"] = {
                    **dict(child_record.get("metadata", {})),
                    "decision": decision,
                    "source_root": str(path.absolute()),
                }
                child_record["activity_evidence"] = list(activity_evidence)
            else:
                child_record = inspect_path(
                    child,
                    status=decision,
                    metadata={"decision": decision, "source_root": str(path.absolute())},
                    required=roots.is_required(child),
                    allowed_roots=roots.allowed_roots(),
                    excluded_roots=roots.excludes,
                    activity_evidence=activity_evidence,
                    reference_class=(
                        "active-runtime-producer"
                        if activity_evidence
                        else (
                            "inactive-backup-archive-candidate"
                            if path.name in historical_names
                            else "historical-memory-reference"
                        )
                    ),
                ).to_mapping()
            records[key] = child_record
    for error in scan_errors:
        key = f"error:{error.get('path', '')}:{len(records)}"
        records[key] = error
    return [records[key] for key in sorted(records)]


def _find_voice_lab_materials(
    donor: Path,
    excludes: Iterable[Path],
    *,
    error_sink: list[Mapping[str, object]] | None = None,
    required: bool = False,
) -> tuple[Path, ...]:
    matches: list[Path] = []
    sink = error_sink if error_sink is not None else []
    try:
        donor_stat = os.lstat(donor)
    except FileNotFoundError:
        return ()
    except OSError as exc:
        sink.append(
            _path_error_record(
                donor,
                operation="lstat",
                error_type=type(exc).__name__,
                required=required,
            )
        )
        return ()
    if not stat.S_ISDIR(donor_stat.st_mode):
        return ()

    def onerror(exc: OSError) -> None:
        failure_path = Path(exc.filename) if exc.filename else donor
        sink.append(
            _path_error_record(
                failure_path,
                operation="walk",
                error_type=type(exc).__name__,
                required=required,
            )
        )

    for directory, dirnames, filenames in os.walk(
        donor,
        followlinks=False,
        onerror=onerror,
    ):
        directory_path = Path(directory)
        dirnames[:] = sorted(
            name
            for name in dirnames
            if not _is_excluded(directory_path / name, excludes)
        )
        for name in (*dirnames, *sorted(filenames)):
            path = directory_path / name
            if _is_excluded(path, excludes):
                continue
            normalized = name.lower().replace("_", "-")
            if "voice" in normalized and "lab" in normalized:
                matches.append(path)
    return _unique_paths(matches)


def _find_consumers(
    candidate: Path,
    files: Iterable[ScannedReferenceFile],
    process_records: Iterable[ProcessObservation] = (),
    roots: InventoryRoots | None = None,
) -> tuple[tuple[str, ...], tuple[Mapping[str, str], ...]]:
    needles = {candidate.name.encode(), str(candidate).encode()}
    consumers: list[str] = []
    evidence: list[Mapping[str, str]] = []
    for scanned in files:
        path = scanned.path
        if path == candidate or _is_under(path, candidate):
            continue
        if roots is not None and _is_memory_reference(path, roots):
            continue
        if not any(needle and needle in scanned.payload for needle in needles):
            continue
        if not _payload_invokes_candidate(scanned.payload, candidate):
            continue
        evidence_kind = _call_evidence_kind(path)
        if evidence_kind is None:
            continue
        source = str(path.absolute())
        consumers.append(source)
        evidence.append({"kind": evidence_kind, "source": source})
    for process in process_records:
        if _process_invokes_candidate(process, candidate):
            consumers.append(f"process:{process.pid}")
            evidence.append(
                {
                    "kind": "process",
                    "source": f"process:{process.pid}:{process.role}",
                }
            )
    unique_evidence = {
        (str(row["kind"]), str(row["source"])): row
        for row in evidence
    }
    return (
        tuple(sorted(set(consumers))),
        tuple(unique_evidence[key] for key in sorted(unique_evidence)),
    )


def _runtime_path_records(roots: InventoryRoots) -> list[Mapping[str, object]]:
    candidates: list[Path] = [
        roots.home / ".dan",
        roots.home / ".jarvis",
        roots.tmp_root / "claude-loud-thinking",
    ]
    discovery_errors: list[Mapping[str, object]] = []
    candidates.extend(
        _directory_named_paths(
            roots.tmp_root,
            predicate=lambda name: name.startswith("dan-"),
            error_sink=discovery_errors,
            required=roots.is_required(roots.tmp_root),
        )
    )
    records = _records_for_roots(_unique_paths(candidates), roots)
    records.extend(discovery_errors)
    return records


def _decision_for(surface: str, record: Mapping[str, object], roots: InventoryRoots) -> str:
    status = str(record.get("status", ""))
    path = str(record.get("path", record.get("producer_path", "")))
    lowered = " ".join(
        (
            path,
            str(record.get("role", "")),
            str(record.get("executable", "")),
            str(record.get("label", "")),
            str(record.get("format", "")),
            str(record.get("request_format", "")),
            str(record.get("reference_class", "")),
        )
    ).lower()
    if status == "missing":
        if surface == "databases" and path.endswith("/.dan/dan.db"):
            return "create-and-verify-through-versioned-migration"
        if surface == "config_sources" and path.endswith("/.dan/config.toml"):
            return "create-installation-config-in-task5"
        if surface == "config_sources" and path.endswith("/.dan/owner.toml"):
            return "create-private-owner-config-in-task5"
        if surface == "config_sources" and path.endswith("/.dan/secrets.env"):
            return "create-private-secrets-config-mode-0600-in-task5"
        return "record-missing-source"
    if "error" in status or "probe-unavailable" in status:
        return "record-probe-failure-and-recheck-at-review-gate"

    if surface == "repositories":
        if path == str(roots.repo_root.absolute()):
            return "use-as-release1-integration-worktree"
        if path == str((roots.home / "Documents/dev/jarvis").absolute()):
            return "use-as-accepted-runtime-source"
        return "retain-read-only-donor-through-observation-gate"
    if surface == "git_refs":
        return "retain-ref-unchanged-and-apply-ref-decision-ledger"
    if surface == "processes":
        if "openclaw" in lowered or "higiena" in lowered:
            return "retain-external-host-and-audit-adapter-in-task11"
        return "observe-only-in-task1-stop-only-during-journaled-cutover"
    if surface == "launchd":
        if "openclaw" in lowered or "higiena" in lowered:
            return "retain-external-host-launch-agent"
        return "replace-or-disable-during-task11-and-cutover"
    if surface == "databases":
        if "/.dan/dan.db" in path:
            return "create-and-verify-through-versioned-migration"
        if "/.dan/memory.db" in path:
            return "backup-and-import-with-lineage-in-task3"
        if "/.jarvis/" in path:
            return "backup-and-evolve-as-dan-db-in-task3"
        return "retain-private-and-classify-before-data-migration"
    if surface == "voice_assets":
        return "reconcile-license-hash-and-version-in-task6"
    if surface == "config_sources":
        if path.endswith(("owner.toml", "secrets.env", "auth.json", "device-auth.json")):
            return "retain-private-never-commit"
        if path.endswith("/.codex/.codex-global-state.json"):
            return "retain-private-never-commit-and-audit-host-config-in-task11"
        if path.endswith("config/persona/DAN.md"):
            return "migrate-as-single-persona-canon-in-task5"
        if path.endswith("state/overrides.json"):
            return "reconcile-every-key-and-retire-runtime-owner-in-task5"
        if "/.config/voice/" in path:
            return "reconcile-and-version-in-task6"
        if path.endswith("/.jarvis/jarvis.toml"):
            return "import-approved-installation-values-in-task5"
        if path.endswith(("AGENTS.md", "CLAUDE.md", "default.rules")):
            return "rewrite-managed-reference-during-task11-cutover"
        if any(
            marker in path
            for marker in (
                "/.claude/agents/",
                "/.codex/memories/",
                "/.openclaw/workspace/",
            )
        ):
            return "audit-active-instruction-and-migrate-or-disable-in-task11"
        if path.endswith("/.claude/plugins/installed_plugins.json"):
            return "retain-host-plugin-registry-and-audit-adapters-in-task11"
        return "classify-in-config-registry-before-write"
    if surface == "skills":
        if "/.openclaw/workspace/skills/radio-dan/" in lowered:
            return "replace-live-openclaw-skill-with-thin-dan-adapter-in-task11"
        if "/.openclaw/workspace/skills/danv2-enhanced/" in lowered:
            return "retain-disabled-openclaw-skill-and-retire-after-task11-audit"
        if "quarantine" in lowered:
            return "retain-historical-do-not-copy-unless-live-consumer-proves-active"
        if "/plugins/cache/" in lowered:
            return "classify-installed-plugin-version-and-migrate-or-disable-in-task11"
        return "migrate-to-thin-dan-adapter-or-disable-in-task11"
    if surface == "hooks":
        return "migrate-to-fail-open-dan-adapter-or-disable-in-task11"
    if surface == "symlinks":
        tmp_prefix = f"{roots.tmp_root.absolute()}/dan-"
        if path.startswith(tmp_prefix):
            return "observe-ephemeral-link-and-retire-with-runtime-in-task12"
        if "chatterbox" in lowered or "custom_styles" in lowered:
            return "classify-license-and-version-or-fetch-in-task6"
        return "replace-with-managed-dan-link-or-disable-in-task11"
    if surface == "producers":
        reference_class = str(record.get("reference_class", ""))
        if reference_class == "historical-memory-reference":
            return "retain-as-historical-reference-not-runtime-evidence"
        if reference_class == "inactive-backup-archive-candidate":
            return "archive-do-not-copy-without-named-runtime-evidence"
        if reference_class == "active-consumer-instruction":
            return "migrate-active-instruction-to-thin-dan-contract-in-task11"
        if reference_class == "unproven-runtime-reference":
            return "retain-as-unproven-reference-and-recheck-before-cutover"
        if "dan-cli-speech-intent" in lowered:
            return "retain-as-target-machine-contract"
        if "jarvis-http-voice-intent" in lowered:
            return "replace-with-voice-service-contract-in-task8"
        return "migrate-to-dan-speak-or-disable-in-task11"
    if surface == "request_formats":
        reference_class = str(record.get("reference_class", ""))
        if reference_class == "historical-memory-reference":
            return "retain-as-historical-reference-not-runtime-evidence"
        if reference_class == "inactive-backup-archive-candidate":
            return "archive-do-not-copy-without-named-runtime-evidence"
        if "dan-cli-speech-intent" in lowered:
            return "retain-as-target-machine-contract"
        if "jarvis-http-voice-intent" in lowered:
            return "replace-with-voice-service-contract-in-task8"
        if "legacy" in lowered:
            return "migrate-explicitly-or-disable-before-cutover"
        return "retain-as-inventory-evidence"
    if surface == "runtime_paths":
        if "/.dan" in path:
            return "preserve-private-state-and-migrate-with-backup"
        if "/.jarvis" in path:
            return "backup-and-retire-only-after-verified-cutover"
        return "backup-contract-and-retire-in-task12-cutover"
    if surface == "input_materials":
        metadata = record.get("metadata", {})
        if isinstance(metadata, Mapping) and metadata.get("decision"):
            return str(metadata["decision"])
        return "retain-as-read-only-migration-evidence"
    return "retain-as-task1-evidence"


def _attach_surface_decisions(
    surfaces: Mapping[str, list[Mapping[str, object]]],
    roots: InventoryRoots,
) -> dict[str, list[Mapping[str, object]]]:
    decided: dict[str, list[Mapping[str, object]]] = {}
    for surface, records in surfaces.items():
        decided[surface] = []
        for record in records:
            row = dict(record)
            row["decision"] = _decision_for(surface, row, roots)
            decided[surface].append(row)
        decided[surface].sort(
            key=lambda row: json.dumps(
                row,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    return decided


class InventoryBuilder:
    def __init__(self, roots: InventoryRoots, runner: Runner = subprocess.run) -> None:
        self._roots = roots
        self._runner = runner

    def collect(self) -> InventoryReport:
        repository_paths = self._roots.repository_paths()
        repositories = [
            _repository_record(
                self._runner,
                path,
                self._roots.excludes,
                required=self._roots.is_required(path),
                allowed_roots=self._roots.allowed_roots(),
            )
            for path in repository_paths
        ]
        _, branch = _git_output(self._runner, self._roots.repo_root, ("branch", "--show-current"))
        _, head = _git_output(self._runner, self._roots.repo_root, ("rev-parse", "HEAD"))
        selected_base_ref = branch or "HEAD"
        processes, process_observations = _process_records(self._runner)
        producers, request_formats, scanned_files = _producer_records(
            self._roots,
            process_observations,
        )

        voice_assets = _records_for_roots(_voice_asset_roots(self._roots), self._roots)
        config_discovery_errors: list[Mapping[str, object]] = []
        config_sources = [
            inspect_path(
                path,
                expected_kind=(
                    "directory"
                    if path in self._roots.reference_memory_roots() and path.suffix == ""
                    else "file"
                ),
                required=self._roots.is_required(path),
                allowed_roots=self._roots.allowed_roots(),
                excluded_roots=self._roots.excludes,
                reference_class=(
                    "historical-memory-reference"
                    if _is_memory_reference(path, self._roots)
                    else None
                ),
            ).to_mapping()
            for path in _config_source_paths(self._roots, config_discovery_errors)
        ]
        config_sources.extend(config_discovery_errors)
        skill_discovery_errors: list[Mapping[str, object]] = []
        skills = _records_for_roots(
            self._roots.active_skill_roots(skill_discovery_errors),
            self._roots,
        )
        skills.extend(skill_discovery_errors)
        hooks = _records_for_roots(_hook_roots(self._roots), self._roots)
        runtime_paths = _runtime_path_records(self._roots)
        input_materials = _find_input_materials(
            self._roots,
            scanned_files,
            process_observations,
            voice_assets,
        )

        symlink_records: dict[str, Mapping[str, object]] = {}
        evidence_surfaces = (
            voice_assets,
            config_sources,
            skills,
            hooks,
            runtime_paths,
            input_materials,
        )
        for surface in evidence_surfaces:
            for item in surface:
                if item.get("kind") == "symlink":
                    symlink_records[str(item["path"])] = item

        undecided_surfaces: dict[str, list[Mapping[str, object]]] = {
            "repositories": repositories,
            "git_refs": _git_ref_records(
                self._runner,
                repository_paths,
                selected_base_ref,
            ),
            "processes": processes,
            "launchd": _launchd_records(self._runner, self._roots),
            "databases": _database_records(self._roots, self._runner),
            "voice_assets": voice_assets,
            "config_sources": config_sources,
            "skills": skills,
            "hooks": hooks,
            "symlinks": [symlink_records[key] for key in sorted(symlink_records)],
            "producers": producers,
            "request_formats": request_formats,
            "runtime_paths": runtime_paths,
            "input_materials": input_materials,
        }
        surfaces = _attach_surface_decisions(undecided_surfaces, self._roots)
        return InventoryReport(
            schema_version=SCHEMA_VERSION,
            generated_at=datetime.now(UTC).isoformat(),
            selected_base={
                "repository": str(self._roots.repo_root.absolute()),
                "ref": selected_base_ref,
                "head": head or None,
            },
            roots={
                "home": str(self._roots.home.absolute()),
                "repo_root": str(self._roots.repo_root.absolute()),
                "tmp_root": str(self._roots.tmp_root.absolute()),
                "excluded": [str(path.absolute()) for path in self._roots.excludes],
                "production": [
                    str(path.absolute()) for path in self._roots.active_scan_roots()
                ],
            },
            surfaces=surfaces,
        )


def build_inventory(
    roots: InventoryRoots,
    *,
    runner: Runner = subprocess.run,
) -> dict[str, object]:
    return InventoryBuilder(roots=roots, runner=runner).collect().to_mapping()


def write_manifest_atomic(manifest: Mapping[str, object], destination: Path) -> None:
    """Write mode 0600 through a sibling temporary file and ``os.replace``."""

    destination = destination.expanduser().absolute()
    directory = destination.parent
    if any(path.is_symlink() for path in (directory, *directory.parents)):
        raise ValueError("manifest directory must not be a symlink")
    if directory.exists():
        if not directory.is_dir():
            raise NotADirectoryError(f"manifest parent is not a directory: {directory}")
        directory_mode = directory.stat().st_mode & 0o777
        if directory_mode != 0o700:
            raise PermissionError(
                f"manifest directory mode must be 0700, got {directory_mode:04o}: {directory}"
            )
    else:
        directory.mkdir(parents=True, mode=0o700)
        os.chmod(directory, 0o700)
    file_descriptor, temporary_name = tempfile.mkstemp(
        dir=directory,
        prefix=f".{destination.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(file_descriptor, MANIFEST_FILE_MODE)
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        os.chmod(destination, MANIFEST_FILE_MODE)
        directory_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _prepare_canonical_manifest_directory(home: Path, destination: Path) -> None:
    canonical = (home.expanduser() / CANONICAL_MANIFEST_RELATIVE_PATH).absolute()
    if destination != canonical:
        return
    directory = destination.parent
    if any(path.is_symlink() for path in (directory, *directory.parents)):
        raise ValueError("manifest directory must not be a symlink")
    if directory.exists() and not directory.is_dir():
        raise NotADirectoryError(f"manifest parent is not a directory: {directory}")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)


def _contains_key(value: object, forbidden: str) -> bool:
    if isinstance(value, Mapping):
        if forbidden in value:
            return True
        return any(
            key != "record_counts" and _contains_key(child, forbidden)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_contains_key(child, forbidden) for child in value)
    return False


_ROOT_FIELDS = {"schema_version", "generated_at", "selected_base", "roots", "surfaces"}
_SELECTED_BASE_FIELDS = {"repository", "ref", "head"}
_ROOTS_FIELDS = {"home", "repo_root", "tmp_root", "excluded", "production"}
_PATH_FIELDS = {
    "path",
    "kind",
    "target",
    "sha256",
    "status",
    "consumers",
    "request_format",
    "metadata",
    "required",
    "error",
    "symlink",
    "reference_class",
    "activity_evidence",
    "decision",
}
_SURFACE_FIELDS: Mapping[str, set[str]] = {
    "repositories": _PATH_FIELDS,
    "git_refs": {
        "kind",
        "repository",
        "ref",
        "head",
        "upstream",
        "chosen_base",
        "unreachable_from_base",
        "status",
        "probe",
        "returncode",
        "line_index",
        "required",
        "error",
        "decision",
    },
    "processes": {
        "kind",
        "pid",
        "ppid",
        "role",
        "executable",
        "runtime_signature",
        "status",
        "probe",
        "returncode",
        "line_index",
        "required",
        "error",
        "decision",
    },
    "launchd": _PATH_FIELDS
    | {"label", "pid", "last_exit_status", "probe", "returncode", "line_index"},
    "databases": {
        "path",
        "kind",
        "status",
        "required",
        "user_version",
        "schema_version",
        "journal_mode",
        "tables",
        "record_counts",
        "error",
        "decision",
    },
    "voice_assets": _PATH_FIELDS,
    "config_sources": _PATH_FIELDS,
    "skills": _PATH_FIELDS,
    "hooks": _PATH_FIELDS,
    "symlinks": _PATH_FIELDS,
    "producers": _PATH_FIELDS | {"formats", "probe", "returncode", "line_index"},
    "request_formats": {
        "id",
        "format",
        "producer_path",
        "status",
        "reference_class",
        "activity_evidence",
        "decision",
    },
    "runtime_paths": _PATH_FIELDS,
    "input_materials": _PATH_FIELDS,
}
_METADATA_FIELDS: Mapping[str, set[str]] = {
    "repositories": {
        "branch",
        "head",
        "toplevel",
        "probe",
        "returncode",
        "dirty_entry_count",
        "wip_entries",
        "tracked_diff_sha256",
        "tracked_diff_basis",
        "staged_diff_sha256",
        "unstaged_diff_sha256",
        "untracked_tree_sha256",
    },
    "voice_assets": {"size_bytes", "mode"},
    "config_sources": {"size_bytes", "mode"},
    "skills": {"size_bytes", "mode"},
    "hooks": {"size_bytes", "mode"},
    "symlinks": {"size_bytes", "mode", "decision", "source_root"},
    "producers": {"size_bytes", "mode"},
    "runtime_paths": {"size_bytes", "mode"},
    "input_materials": {"size_bytes", "mode", "decision", "source_root"},
    "launchd": {"size_bytes", "mode"},
}
_WIP_FIELDS = {"status", "path", "kind", "sha256", "original_path", "target", "error", "symlink"}
_ERROR_FIELDS = {"type", "operation", "resolved"}
_SYMLINK_FIELDS = {
    "raw_target",
    "normalized_target",
    "target_state",
    "target_kind",
    "target_is_absolute",
    "inside_allowed_roots",
    "scope_decision",
    "target_size_bytes",
}
_ACTIVITY_FIELDS = {"kind", "source"}
_FORBIDDEN_FIELDS = {
    "args",
    "arguments",
    "argv",
    "cmdline",
    "command",
    "command_line",
    "contents",
    "data",
    "open_handles",
    "payload",
    "raw_argv",
    "raw_command",
    "records",
    "row_values",
    "rows",
    "stderr",
    "stdout",
    "text",
}
_REFERENCE_CLASSES = {
    "active-runtime-producer",
    "active-consumer-instruction",
    "historical-memory-reference",
    "inactive-backup-archive-candidate",
    "unproven-runtime-reference",
}
_PATH_SURFACES = {
    "repositories",
    "voice_assets",
    "config_sources",
    "skills",
    "hooks",
    "symlinks",
    "producers",
    "runtime_paths",
    "input_materials",
}
_SURFACE_STRING_FIELDS = {
    "chosen_base",
    "decision",
    "executable",
    "format",
    "head",
    "id",
    "journal_mode",
    "kind",
    "label",
    "path",
    "probe",
    "producer_path",
    "ref",
    "reference_class",
    "repository",
    "request_format",
    "role",
    "runtime_signature",
    "sha256",
    "status",
    "target",
    "upstream",
}
_NULLABLE_SURFACE_STRING_FIELDS = {
    "chosen_base",
    "head",
    "label",
    "request_format",
    "sha256",
    "target",
    "upstream",
}
_SURFACE_INTEGER_FIELDS = {
    "line_index",
    "ppid",
    "returncode",
    "schema_version",
    "user_version",
}
_METADATA_INTEGER_FIELDS = {"dirty_entry_count", "returncode", "size_bytes"}
_METADATA_STRING_FIELDS = {
    "branch",
    "decision",
    "head",
    "mode",
    "probe",
    "source_root",
    "staged_diff_sha256",
    "toplevel",
    "tracked_diff_basis",
    "tracked_diff_sha256",
    "unstaged_diff_sha256",
    "untracked_tree_sha256",
}
_NULLABLE_METADATA_STRING_FIELDS = {
    "branch",
    "head",
    "staged_diff_sha256",
    "tracked_diff_sha256",
    "unstaged_diff_sha256",
}


def _required_surface_fields(
    surface: str,
    row: Mapping[str, object],
) -> set[str]:
    if surface in _PATH_SURFACES or (surface == "launchd" and "path" in row):
        return {"path", "kind", "status", "required", "decision"}
    if surface == "databases":
        required = {"path", "kind", "status", "required", "decision"}
        if row.get("status") == "present":
            required.update(
                {
                    "user_version",
                    "schema_version",
                    "journal_mode",
                    "tables",
                    "record_counts",
                }
            )
        return required
    if row.get("kind") == "probe_error":
        required = {"kind", "status", "probe", "required", "error", "decision"}
        if surface == "git_refs":
            required.add("repository")
        return required
    if surface == "git_refs":
        return {
            "repository",
            "ref",
            "head",
            "upstream",
            "chosen_base",
            "unreachable_from_base",
            "decision",
        }
    if surface == "processes":
        return {
            "kind",
            "pid",
            "ppid",
            "role",
            "executable",
            "runtime_signature",
            "status",
            "decision",
        }
    if surface == "launchd":
        return {
            "kind",
            "pid",
            "last_exit_status",
            "label",
            "status",
            "decision",
        }
    if surface == "request_formats":
        return {
            "id",
            "format",
            "producer_path",
            "status",
            "reference_class",
            "activity_evidence",
            "decision",
        }
    return {"decision"}


def _report_unknown_fields(
    value: Mapping[str, object],
    allowed: set[str],
    location: str,
    errors: list[str],
) -> None:
    for key in sorted(set(value) - allowed):
        errors.append(f"unknown field {location}.{key}")


def _validate_error(
    value: object,
    location: str,
    errors: list[str],
) -> None:
    if not isinstance(value, Mapping):
        errors.append(f"{location} must be an object")
        return
    _report_unknown_fields(value, _ERROR_FIELDS, location, errors)
    missing = sorted(_ERROR_FIELDS - set(value))
    if missing:
        errors.append(f"{location} missing fields: {', '.join(missing)}")
    if "type" in value and not isinstance(value["type"], str):
        errors.append(f"{location}.type must be a string")
    if "operation" in value and not isinstance(value["operation"], str):
        errors.append(f"{location}.operation must be a string")
    if "resolved" in value and not isinstance(value["resolved"], bool):
        errors.append(f"{location}.resolved must be a boolean")


def _validate_activity_evidence(
    value: object,
    location: str,
    errors: list[str],
) -> None:
    if not isinstance(value, (list, tuple)):
        errors.append(f"{location} must be a list")
        return
    for index, row in enumerate(value):
        row_location = f"{location}[{index}]"
        if not isinstance(row, Mapping):
            errors.append(f"{row_location} must be an object")
            continue
        _report_unknown_fields(row, _ACTIVITY_FIELDS, row_location, errors)
        if set(row) != _ACTIVITY_FIELDS:
            errors.append(f"{row_location} must contain kind and source")
        elif not all(isinstance(row[key], str) and row[key] for key in _ACTIVITY_FIELDS):
            errors.append(f"{row_location} kind and source must be non-empty strings")


def _validate_symlink(value: object, location: str, errors: list[str]) -> None:
    if not isinstance(value, Mapping):
        errors.append(f"{location} must be an object")
        return
    _report_unknown_fields(value, _SYMLINK_FIELDS, location, errors)
    missing = sorted(_SYMLINK_FIELDS - set(value))
    if missing:
        errors.append(f"{location} missing fields: {', '.join(missing)}")
    for key in (
        "raw_target",
        "normalized_target",
        "target_state",
        "target_kind",
        "scope_decision",
    ):
        if key in value and not isinstance(value[key], str):
            errors.append(f"{location}.{key} must be a string")
    for key in ("target_is_absolute", "inside_allowed_roots"):
        if key in value and not isinstance(value[key], bool):
            errors.append(f"{location}.{key} must be a boolean")
    if "target_size_bytes" in value and value["target_size_bytes"] is not None:
        if (
            isinstance(value["target_size_bytes"], bool)
            or not isinstance(value["target_size_bytes"], int)
            or value["target_size_bytes"] < 0
        ):
            errors.append(f"{location}.target_size_bytes must be a non-negative integer or null")


def _validate_metadata(
    surface: str,
    value: object,
    location: str,
    errors: list[str],
) -> None:
    if not isinstance(value, Mapping):
        errors.append(f"{location} must be an object")
        return
    allowed = _METADATA_FIELDS.get(surface, set())
    _report_unknown_fields(value, allowed, location, errors)
    for key in _METADATA_INTEGER_FIELDS & set(value):
        if isinstance(value[key], bool) or not isinstance(value[key], int):
            errors.append(f"{location}.{key} must be an integer")
    for key in _METADATA_STRING_FIELDS & set(value):
        item = value[key]
        if item is None and key in _NULLABLE_METADATA_STRING_FIELDS:
            continue
        if not isinstance(item, str):
            errors.append(f"{location}.{key} must be a string")
    if "wip_entries" in value:
        entries = value["wip_entries"]
        if not isinstance(entries, list):
            errors.append(f"{location}.wip_entries must be a list")
        else:
            for index, entry in enumerate(entries):
                entry_location = f"{location}.wip_entries[{index}]"
                if not isinstance(entry, Mapping):
                    errors.append(f"{entry_location} must be an object")
                    continue
                _report_unknown_fields(entry, _WIP_FIELDS, entry_location, errors)
                for key in _WIP_FIELDS - {"error", "symlink"}:
                    if key not in entry:
                        continue
                    item = entry[key]
                    if item is not None and not isinstance(item, str):
                        errors.append(f"{entry_location}.{key} must be a string or null")
                if "error" in entry:
                    _validate_error(entry["error"], f"{entry_location}.error", errors)
                if "symlink" in entry:
                    _validate_symlink(entry["symlink"], f"{entry_location}.symlink", errors)


def _validate_surface_row(
    surface: str,
    row: Mapping[str, object],
    location: str,
    errors: list[str],
) -> None:
    _report_unknown_fields(row, _SURFACE_FIELDS[surface], location, errors)
    missing = sorted(_required_surface_fields(surface, row) - set(row))
    if missing:
        errors.append(f"{location} missing fields: {', '.join(missing)}")
    status_value = row.get("status")
    if isinstance(status_value, str) and "error" in status_value:
        for field_name in ("required", "error"):
            if field_name not in row:
                errors.append(f"{location} error row missing field: {field_name}")
    for key in _SURFACE_STRING_FIELDS & set(row):
        item = row[key]
        if item is None and key in _NULLABLE_SURFACE_STRING_FIELDS:
            continue
        if not isinstance(item, str):
            errors.append(f"{location}.{key} must be a string")
    for key in _SURFACE_INTEGER_FIELDS & set(row):
        item = row[key]
        if isinstance(item, bool) or not isinstance(item, int):
            errors.append(f"{location}.{key} must be an integer")
    if "required" in row and not isinstance(row["required"], bool):
        errors.append(f"{location}.required must be a boolean")
    if "pid" in row:
        pid = row["pid"]
        if surface == "launchd":
            if pid is not None and (
                isinstance(pid, bool) or not isinstance(pid, (int, str))
            ):
                errors.append(f"{location}.pid must be an integer, string, or null")
        elif isinstance(pid, bool) or not isinstance(pid, int):
            errors.append(f"{location}.pid must be an integer")
    if "last_exit_status" in row and row["last_exit_status"] is not None:
        if isinstance(row["last_exit_status"], bool) or not isinstance(
            row["last_exit_status"], (int, str)
        ):
            errors.append(f"{location}.last_exit_status must be an integer, string, or null")
    for key in ("unreachable_from_base", "formats"):
        if key in row and (
            not isinstance(row[key], list)
            or not all(isinstance(item, str) for item in row[key])
        ):
            errors.append(f"{location}.{key} must be a list of strings")
    decision = row.get("decision")
    if not isinstance(decision, str) or not decision.strip():
        errors.append(f"{location} has no decision")
    elif _DECISION_PLACEHOLDER.search(decision):
        errors.append(f"{location}.decision contains a placeholder")
    if "metadata" in row:
        _validate_metadata(surface, row["metadata"], f"{location}.metadata", errors)
    if "error" in row:
        _validate_error(row["error"], f"{location}.error", errors)
        error_value = row["error"]
        if (
            row.get("required") is True
            and isinstance(error_value, Mapping)
            and error_value.get("resolved") is not True
        ):
            errors.append(f"{location} has unresolved required error")
    if "symlink" in row:
        _validate_symlink(row["symlink"], f"{location}.symlink", errors)
    if "activity_evidence" in row:
        _validate_activity_evidence(
            row["activity_evidence"],
            f"{location}.activity_evidence",
            errors,
        )
    if "reference_class" in row and row["reference_class"] not in _REFERENCE_CLASSES:
        errors.append(f"{location}.reference_class is unknown")
    if "consumers" in row:
        consumers = row["consumers"]
        if not isinstance(consumers, (list, tuple)) or not all(
            isinstance(item, str) for item in consumers
        ):
            errors.append(f"{location}.consumers must be a list of strings")
    if "formats" in row:
        formats = row["formats"]
        if not isinstance(formats, list) or not all(isinstance(item, str) for item in formats):
            errors.append(f"{location}.formats must be a list of strings")
    if surface == "databases":
        if "tables" in row and (
            not isinstance(row["tables"], list)
            or not all(isinstance(item, str) for item in row["tables"])
        ):
            errors.append(f"{location}.tables must be a list of strings")
        if "record_counts" in row:
            counts = row["record_counts"]
            if not isinstance(counts, Mapping) or not all(
                isinstance(key, str)
                and not isinstance(value, bool)
                and isinstance(value, int)
                and value >= 0
                for key, value in counts.items()
            ):
                errors.append(f"{location}.record_counts must map table names to counts")


def validate_manifest(manifest: Mapping[str, object]) -> list[str]:
    errors: list[str] = []
    if not isinstance(manifest, Mapping):
        return ["manifest root must be an object"]
    _report_unknown_fields(manifest, _ROOT_FIELDS, "root", errors)
    missing_root = sorted(_ROOT_FIELDS - set(manifest))
    if missing_root:
        errors.append(f"root missing fields: {', '.join(missing_root)}")
    schema_version = manifest.get("schema_version")
    if isinstance(schema_version, bool) or schema_version != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    if "generated_at" in manifest and not isinstance(manifest["generated_at"], str):
        errors.append("generated_at must be a string")
    selected_base = manifest.get("selected_base")
    if not isinstance(selected_base, Mapping):
        errors.append("selected_base must be an object")
    else:
        _report_unknown_fields(selected_base, _SELECTED_BASE_FIELDS, "selected_base", errors)
        missing = sorted(_SELECTED_BASE_FIELDS - set(selected_base))
        if missing:
            errors.append(f"selected_base missing fields: {', '.join(missing)}")
        for key in ("repository", "ref"):
            if key in selected_base and not isinstance(selected_base[key], str):
                errors.append(f"selected_base.{key} must be a string")
        if "head" in selected_base and selected_base["head"] is not None:
            if not isinstance(selected_base["head"], str):
                errors.append("selected_base.head must be a string or null")
    roots = manifest.get("roots")
    if not isinstance(roots, Mapping):
        errors.append("roots must be an object")
    else:
        _report_unknown_fields(roots, _ROOTS_FIELDS, "roots", errors)
        missing = sorted(_ROOTS_FIELDS - set(roots))
        if missing:
            errors.append(f"roots missing fields: {', '.join(missing)}")
        for key in ("home", "repo_root", "tmp_root"):
            if key in roots and not isinstance(roots[key], str):
                errors.append(f"roots.{key} must be a string")
        for key in ("excluded", "production"):
            if key in roots and (
                not isinstance(roots[key], list)
                or not all(isinstance(item, str) for item in roots[key])
            ):
                errors.append(f"roots.{key} must be a list of strings")
    surfaces = manifest.get("surfaces")
    if not isinstance(surfaces, Mapping):
        errors.append("surfaces must be an object")
    else:
        missing = sorted(set(SURFACE_NAMES) - set(surfaces))
        extra = sorted(set(surfaces) - set(SURFACE_NAMES))
        if missing:
            errors.append(f"missing surfaces: {', '.join(missing)}")
        if extra:
            errors.append(f"unknown surfaces: {', '.join(extra)}")
        for name in SURFACE_NAMES:
            if name in surfaces and not isinstance(surfaces[name], list):
                errors.append(f"surface {name} must be a list")
            elif name in surfaces:
                for index, row in enumerate(surfaces[name]):
                    location = f"surfaces.{name}[{index}]"
                    if not isinstance(row, Mapping):
                        errors.append(f"{location} must be an object")
                    else:
                        _validate_surface_row(name, row, location, errors)
    for forbidden in sorted(_FORBIDDEN_FIELDS):
        if _contains_key(manifest, forbidden):
            errors.append(f"manifest must not contain field {forbidden}")
    return errors


def check_manifest(path: Path) -> tuple[Mapping[str, object], list[str]]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {}, [f"cannot read manifest: {type(exc).__name__}"]
    errors = validate_manifest(manifest)
    try:
        mode = path.stat().st_mode & 0o777
    except OSError:
        mode = 0
    if mode != MANIFEST_FILE_MODE:
        errors.append(f"manifest mode must be 0600, got {mode:04o}")
    manifest_path = str(path.expanduser().absolute())
    surfaces = manifest.get("surfaces", {})
    if isinstance(surfaces, Mapping) and any(
        isinstance(row, Mapping) and str(row.get("path", "")) == manifest_path
        for rows in surfaces.values()
        if isinstance(rows, list)
        for row in rows
    ):
        errors.append("manifest must not inventory its own destination")
    return manifest, errors


def _manifest_sha256(path: Path) -> str:
    return sha256_file(path)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build or verify the DAN Release 1 source manifest"
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--output", type=Path, help="write a fresh private manifest")
    action.add_argument("--check", type=Path, help="validate an existing private manifest")
    parser.add_argument("--exclude", action="append", type=Path, default=[])
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--tmp-root", type=Path, default=Path("/tmp"))
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.check:
        manifest, errors = check_manifest(args.check.expanduser())
        if errors:
            for error in errors:
                print(f"ERROR: {error}")
            return 1
        surfaces = manifest["surfaces"]
        counts = ", ".join(f"{name}={len(surfaces[name])}" for name in SURFACE_NAMES)
        print(
            f"manifest ok: {args.check.expanduser()} "
            f"sha256={_manifest_sha256(args.check.expanduser())} {counts}"
        )
        return 0

    output = args.output.expanduser().absolute()
    repo_root = args.repo_root.expanduser().absolute()
    roots = InventoryRoots.production(
        repo_root,
        home=args.home.expanduser(),
        tmp_root=args.tmp_root.expanduser(),
        excludes=(
            *(path.expanduser() for path in args.exclude),
            output,
        ),
    )
    _prepare_canonical_manifest_directory(roots.home, output)
    manifest = build_inventory(roots)
    errors = validate_manifest(manifest)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    write_manifest_atomic(manifest, output)
    print(f"manifest written: {output} sha256={_manifest_sha256(output)}")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through the script wrapper
    raise SystemExit(main())
