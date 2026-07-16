"""Read-only source-of-truth inventory for the DAN Release 1 migration.

The inventory deliberately records metadata, hashes, reference relationships,
and database counts.  It never serializes file contents or SQLite row values.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
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

_PROCESS_TOKENS = (
    "jarvis",
    "dand",
    "dan-voice",
    "voice_broker",
    "feeder",
    "supertonic",
    "openclaw",
    "standup",
    "higiena",
    "menubar-controller",
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


class Runner(Protocol):
    """Subset of ``subprocess.run`` used by the collector."""

    def __call__(self, args: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]: ...


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

    def to_mapping(self) -> dict[str, object]:
        result = asdict(self)
        result["metadata"] = dict(sorted(self.metadata.items()))
        return result


@dataclass(frozen=True)
class InventoryRoots:
    """Filesystem anchors for a production or disposable inventory."""

    home: Path
    repo_root: Path
    tmp_root: Path = Path("/tmp")
    excludes: tuple[Path, ...] = ()

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

    def active_skill_roots(self) -> tuple[Path, ...]:
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
            + self.plugin_skill_roots()
        )

    def plugin_skill_roots(self) -> tuple[Path, ...]:
        discovered: list[Path] = []
        for cache in (
            self.home / ".claude/plugins/cache",
            self.home / ".codex/plugins/cache",
        ):
            for path in _walk_paths(cache, self.excludes):
                if path.name == "SKILL.md" and path.is_file():
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
        )

    def producer_scan_roots(self) -> tuple[Path, ...]:
        """Executable, config, and injected-instruction subsets of active roots."""

        return _unique_paths(
            self.repository_paths()
            + self.active_skill_roots()
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
                self.home / ".openclaw/workspace",
                self.home / "AGENTS.md",
                self.home / ".claude/CLAUDE.md",
            )
        )


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


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolved_link_target(path: Path) -> Path:
    raw_target = Path(os.readlink(path))
    if raw_target.is_absolute():
        return raw_target
    return (path.parent / raw_target).absolute()


def inspect_path(
    path: Path,
    *,
    consumers: Iterable[str] = (),
    request_format: str | None = None,
    expected_kind: Literal["file", "directory", "database"] = "file",
    status: str | None = None,
    metadata: Mapping[str, object] | None = None,
) -> InventoryItem:
    """Describe a path without serializing its contents."""

    expanded = path.expanduser()
    item_metadata = dict(metadata or {})
    if expanded.is_symlink():
        target = _resolved_link_target(expanded)
        target_hash = sha256_file(target) if target.is_file() else None
        return InventoryItem(
            path=str(expanded.absolute()),
            kind="symlink",
            target=str(target),
            sha256=target_hash,
            status=status or ("present" if target.exists() else "broken"),
            consumers=tuple(sorted(set(consumers))),
            request_format=request_format,
            metadata=item_metadata,
        )
    if expanded.is_dir():
        return InventoryItem(
            path=str(expanded.absolute()),
            kind="directory",
            target=None,
            sha256=None,
            status=status or "present",
            consumers=tuple(sorted(set(consumers))),
            request_format=request_format,
            metadata=item_metadata,
        )
    if expanded.is_file():
        kind: Literal["file", "database"] = "database" if expected_kind == "database" else "file"
        item_metadata.setdefault("size_bytes", expanded.stat().st_size)
        item_metadata.setdefault("mode", oct(expanded.stat().st_mode & 0o777))
        return InventoryItem(
            path=str(expanded.absolute()),
            kind=kind,
            target=None,
            sha256=sha256_file(expanded),
            status=status or "present",
            consumers=tuple(sorted(set(consumers))),
            request_format=request_format,
            metadata=item_metadata,
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


def _walk_paths(root: Path, excludes: Iterable[Path]) -> Iterable[Path]:
    if not root.exists() and not root.is_symlink():
        return
    if root.is_file() or root.is_symlink():
        if not _is_excluded(root, excludes):
            yield root
        return
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
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
            if path.is_symlink() and not _is_excluded(path, excludes):
                yield path


def _run(
    runner: Runner,
    args: Sequence[str],
    *,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return runner(
            list(args),
            cwd=str(cwd) if cwd else None,
            text=True,
            capture_output=True,
            check=False,
        )
    except (FileNotFoundError, OSError) as exc:
        return subprocess.CompletedProcess(list(args), 127, "", type(exc).__name__)


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
    return _sha256_text(result.stdout), result.stdout


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
) -> Mapping[str, object]:
    item = inspect_path(path, expected_kind="directory")
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
    if status_result.returncode != 0:
        result["status"] = "git-status-probe-error"
        result["metadata"] = {
            "branch": branch or None,
            "head": head if head_code == 0 and head else None,
            "toplevel": top,
            "probe": "git status --porcelain=v1 -z",
            "returncode": status_result.returncode,
        }
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
        code, refs = _git_output(
            runner,
            repo,
            (
                "for-each-ref",
                "--format=%(refname)%00%(objectname)%00%(upstream:short)",
                "refs/heads",
                "refs/remotes",
                "refs/rescue",
                "refs/spike",
            ),
        )
        if code != 0:
            records.append(
                {
                    "repository": str(repo.absolute()),
                    "status": "git-ref-probe-error",
                    "probe": "git for-each-ref",
                    "returncode": code,
                }
            )
            continue
        base_code, base_sha = _git_output(runner, repo, ("rev-parse", "--verify", base_ref))
        if base_code != 0:
            _, base_sha = _git_output(runner, repo, ("rev-parse", "HEAD"))
        for line in refs.splitlines():
            parts = line.split("\0")
            if len(parts) < 2:
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
            records.append(record)
    return sorted(
        records,
        key=lambda row: (str(row["repository"]), str(row.get("ref", ""))),
    )


def _process_records(runner: Runner) -> list[Mapping[str, object]]:
    result = _run(runner, ("ps", "-axo", "pid=,ppid=,command="))
    if result.returncode != 0:
        return [{"status": "probe-error", "probe": "ps", "returncode": result.returncode}]
    records: list[Mapping[str, object]] = []
    for line in result.stdout.splitlines():
        fields = line.strip().split(maxsplit=2)
        if len(fields) != 3:
            continue
        pid, ppid, command = fields
        pid_value = int(pid)
        if pid_value == os.getpid():
            continue
        lowered = command.lower()
        if not any(token in lowered for token in _PROCESS_TOKENS):
            continue
        records.append(
            {
                "kind": "process",
                "pid": pid_value,
                "ppid": int(ppid),
                "command": command,
                "status": "running",
            }
        )
    return records


def _launchd_records(runner: Runner, roots: InventoryRoots) -> list[Mapping[str, object]]:
    records: list[Mapping[str, object]] = []
    launch_agents = roots.home / "Library/LaunchAgents"
    if launch_agents.is_dir():
        for path in sorted(launch_agents.glob("*.plist")):
            if _is_product_launchd_label(path.stem):
                records.append(inspect_path(path).to_mapping())
    result = _run(runner, ("launchctl", "list"))
    if result.returncode == 0:
        for line in result.stdout.splitlines():
            fields = line.split(maxsplit=2)
            label = fields[2] if len(fields) > 2 else line.strip()
            if not _is_product_launchd_label(label):
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
            {"kind": "launchd", "status": "probe-unavailable", "returncode": result.returncode}
        )
    return records


def _is_product_launchd_label(label: str) -> bool:
    lowered = label.lower()
    return lowered in _LAUNCHD_EXACT_LABELS or lowered.startswith(_LAUNCHD_LABEL_PREFIXES)


def inspect_database(path: Path, *, runner: Runner = subprocess.run) -> Mapping[str, object]:
    item = inspect_path(path, expected_kind="database")
    record = item.to_mapping()
    if item.status != "present":
        return record
    uri = f"file:{path.absolute()}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=1.0)
        connection.execute("PRAGMA query_only=ON")
        user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        journal_mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0])
        table_names = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        counts: dict[str, int | str] = {}
        for table in table_names:
            escaped = table.replace('"', '""')
            try:
                counts[table] = int(
                    connection.execute(f'SELECT COUNT(*) FROM "{escaped}"').fetchone()[0]
                )
            except sqlite3.Error:
                counts[table] = "count-error"
        connection.close()
        record["metadata"] = {
            **dict(record.get("metadata", {})),
            "user_version": user_version,
            "journal_mode": journal_mode,
            "tables": table_names,
            "row_counts": counts,
        }
    except sqlite3.Error as exc:
        record["status"] = "sqlite-probe-error"
        record["metadata"] = {
            **dict(record.get("metadata", {})),
            "error_type": type(exc).__name__,
        }
    handles = _run(runner, ("lsof", "-Fpc", "--", str(path), f"{path}-wal", f"{path}-shm"))
    record["metadata"] = {
        **dict(record.get("metadata", {})),
        "open_handles": _parse_lsof_handles(handles.stdout) if handles.returncode in (0, 1) else [],
    }
    return record


def _parse_lsof_handles(output: str) -> list[Mapping[str, object]]:
    handles: list[Mapping[str, object]] = []
    current: dict[str, object] = {}
    for line in output.splitlines():
        if line.startswith("p"):
            if current:
                handles.append(current)
            current = {"pid": int(line[1:])} if line[1:].isdigit() else {"pid": line[1:]}
        elif line.startswith("c"):
            current["command"] = line[1:]
    if current:
        handles.append(current)
    return handles


def _database_records(roots: InventoryRoots, runner: Runner) -> list[Mapping[str, object]]:
    candidates = {
        roots.home / ".dan/memory.db",
        roots.home / ".dan/dan.db",
        roots.home / ".jarvis/jarvis.db",
    }
    for directory in (roots.home / ".dan", roots.home / ".jarvis"):
        if directory.is_dir():
            candidates.update(directory.glob("*.db"))
    return [inspect_database(path, runner=runner) for path in sorted(candidates, key=str)]


def _records_for_roots(
    roots_to_scan: Iterable[Path],
    excludes: Iterable[Path],
) -> list[Mapping[str, object]]:
    records: list[Mapping[str, object]] = []
    seen: set[str] = set()
    for root in roots_to_scan:
        root_key = str(root.absolute())
        if root_key not in seen:
            records.append(inspect_path(root, expected_kind="directory").to_mapping())
            seen.add(root_key)
        for path in _walk_paths(root, excludes):
            key = str(path.absolute())
            if key in seen:
                continue
            seen.add(key)
            records.append(inspect_path(path).to_mapping())
    return records


def _config_source_paths(roots: InventoryRoots) -> tuple[Path, ...]:
    donor = roots.home / "Documents/dev/dan"
    claude_agents = tuple(sorted((roots.home / ".claude/agents").glob("*.md")))
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


def _read_signatures(path: Path) -> tuple[str, ...]:
    try:
        if (
            path.suffix.lower() not in _TEXT_SUFFIXES
            or path.stat().st_size > _MAX_DISCOVERY_FILE_BYTES
        ):
            return ()
        payload = path.read_bytes()
    except OSError:
        return ()
    return tuple(name for token, name in _PRODUCER_SIGNATURES if token in payload)


def _is_producer_candidate(path: Path) -> bool:
    """Limit producer discovery to executable/config/injected instruction surfaces."""

    if tuple(path.parts[-3:]) == ("jarvis", "migration", "inventory.py"):
        return False
    lowered_parts = {part.lower() for part in path.parts}
    if "tests" in lowered_parts or "docs" in lowered_parts:
        return False
    if path.name in {"AGENTS.md", "CLAUDE.md", "SKILL.md", "MEMORY.md"}:
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
) -> tuple[list[Mapping[str, object]], list[Mapping[str, object]], list[Path]]:
    producers: list[Mapping[str, object]] = []
    request_formats: list[Mapping[str, object]] = []
    scanned_files: list[Path] = []
    seen: set[str] = set()
    for root in roots.producer_scan_roots():
        for path in _walk_paths(root, roots.excludes):
            if not path.is_file() or path.is_symlink() or not _is_producer_candidate(path):
                continue
            key = str(path.absolute())
            if key in seen:
                continue
            seen.add(key)
            scanned_files.append(path)
            signatures = _read_signatures(path)
            if not signatures:
                continue
            item = inspect_path(path, request_format=",".join(signatures))
            record = item.to_mapping()
            record["formats"] = list(signatures)
            producers.append(record)
            for format_name in signatures:
                request_formats.append(
                    {
                        "id": f"{format_name}:{key}",
                        "format": format_name,
                        "producer_path": key,
                        "status": "discovered",
                    }
                )
    request_formats.extend(
        (
            {
                "id": "legacy-dan-voice-json-runtime",
                "format": "legacy-dan-voice-json",
                "producer_path": str((roots.tmp_root / "dan-voice/req").absolute()),
                "status": "runtime-contract",
            },
            {
                "id": "legacy-claude-hook-switch",
                "format": "legacy-hook-off-file",
                "producer_path": str((roots.tmp_root / "claude-loud-thinking/OFF").absolute()),
                "status": "runtime-contract",
            },
        )
    )
    return producers, request_formats, scanned_files


def _find_input_materials(
    roots: InventoryRoots,
    scanned_files: Iterable[Path],
    process_records: Iterable[Mapping[str, object]],
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
    explicit.extend(_find_voice_lab_materials(donor, roots.excludes))
    candidates = _unique_paths(explicit)
    searchable_files = tuple(scanned_files)
    live_processes = tuple(process_records)
    existing_by_path = {
        str(record.get("path")): record for record in existing_records if record.get("path")
    }
    records: dict[str, Mapping[str, object]] = {}
    historical_names = {path.name for path in candidates if "quarantine" in path.name.lower()}
    for path in candidates:
        consumers = _find_consumers(path, searchable_files, live_processes)
        if path.name in historical_names:
            decision = "active-source" if consumers else "archive/do-not-copy"
        else:
            decision = "input-material"
        root_record = inspect_path(
            path,
            consumers=consumers,
            expected_kind="directory" if path.suffix == "" else "file",
            status=decision if path.exists() or path.is_symlink() else "missing",
            metadata={"decision": decision, "source_root": str(path.absolute())},
        ).to_mapping()
        records[str(path.absolute())] = root_record
        if not path.is_dir() or path.is_symlink():
            continue
        for child in _walk_paths(path, roots.excludes):
            key = str(child.absolute())
            if key in existing_by_path:
                child_record = dict(existing_by_path[key])
                child_record["metadata"] = {
                    **dict(child_record.get("metadata", {})),
                    "decision": decision,
                    "source_root": str(path.absolute()),
                }
            else:
                child_record = inspect_path(
                    child,
                    status=decision,
                    metadata={"decision": decision, "source_root": str(path.absolute())},
                ).to_mapping()
            records[key] = child_record
    return [records[key] for key in sorted(records)]


def _find_voice_lab_materials(donor: Path, excludes: Iterable[Path]) -> tuple[Path, ...]:
    matches: list[Path] = []
    if not donor.is_dir():
        return ()
    for directory, dirnames, filenames in os.walk(donor, followlinks=False):
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
    files: Iterable[Path],
    process_records: Iterable[Mapping[str, object]] = (),
) -> tuple[str, ...]:
    needles = {candidate.name.encode(), str(candidate).encode()}
    consumers: list[str] = []
    for path in files:
        if path == candidate or _is_under(path, candidate):
            continue
        try:
            if path.stat().st_size > _MAX_DISCOVERY_FILE_BYTES:
                continue
            payload = path.read_bytes()
        except OSError:
            continue
        if any(needle and needle in payload for needle in needles):
            consumers.append(str(path.absolute()))
    for process in process_records:
        command = str(process.get("command", "")).encode("utf-8", errors="surrogateescape")
        if any(needle and needle in command for needle in needles):
            consumers.append(f"process:{process.get('pid', 'unknown')}")
    return tuple(sorted(set(consumers)))


def _runtime_path_records(roots: InventoryRoots) -> list[Mapping[str, object]]:
    candidates: list[Path] = [
        roots.home / ".dan",
        roots.home / ".jarvis",
        roots.tmp_root / "claude-loud-thinking",
    ]
    if roots.tmp_root.is_dir():
        candidates.extend(sorted(roots.tmp_root.glob("dan-*")))
    return _records_for_roots(_unique_paths(candidates), roots.excludes)


def _decision_for(surface: str, record: Mapping[str, object], roots: InventoryRoots) -> str:
    status = str(record.get("status", ""))
    path = str(record.get("path", record.get("producer_path", "")))
    lowered = " ".join(
        (
            path,
            str(record.get("command", "")),
            str(record.get("label", "")),
            str(record.get("format", "")),
            str(record.get("request_format", "")),
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
    if "probe-error" in status or "probe-unavailable" in status:
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
        if "dan-cli-speech-intent" in lowered:
            return "retain-as-target-machine-contract"
        if "jarvis-http-voice-intent" in lowered:
            return "replace-with-voice-service-contract-in-task8"
        return "migrate-to-dan-speak-or-disable-in-task11"
    if surface == "request_formats":
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
            _repository_record(self._runner, path, self._roots.excludes)
            for path in repository_paths
        ]
        _, branch = _git_output(self._runner, self._roots.repo_root, ("branch", "--show-current"))
        _, head = _git_output(self._runner, self._roots.repo_root, ("rev-parse", "HEAD"))
        selected_base_ref = branch or "HEAD"
        producers, request_formats, scanned_files = _producer_records(self._roots)
        processes = _process_records(self._runner)

        voice_assets = _records_for_roots(_voice_asset_roots(self._roots), self._roots.excludes)
        config_sources = [
            inspect_path(path).to_mapping() for path in _config_source_paths(self._roots)
        ]
        skills = _records_for_roots(self._roots.active_skill_roots(), self._roots.excludes)
        hooks = _records_for_roots(_hook_roots(self._roots), self._roots.excludes)
        runtime_paths = _runtime_path_records(self._roots)
        input_materials = _find_input_materials(
            self._roots,
            scanned_files,
            processes,
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
        return forbidden in value or any(
            _contains_key(child, forbidden) for child in value.values()
        )
    if isinstance(value, list):
        return any(_contains_key(child, forbidden) for child in value)
    return False


def validate_manifest(manifest: Mapping[str, object]) -> list[str]:
    errors: list[str] = []
    if manifest.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
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
                    if not isinstance(row, Mapping):
                        errors.append(f"surface {name}[{index}] must be an object")
                    elif not str(row.get("decision", "")).strip():
                        errors.append(f"surface {name}[{index}] has no decision")
    if _contains_key(manifest, "contents"):
        errors.append("manifest must not contain file or row contents")
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
