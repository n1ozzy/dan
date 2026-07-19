"""Fail-closed cleanup of the exact legacy ``repo/jarvis`` checkout namespace."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import secrets
import stat
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Literal

from dan.release.evidence import (
    UnsafeEvidenceRoot,
    ValidatedEvidenceRoot,
    active_evidence_roots_from_environment,
    validate_evidence_root,
)

SAFE_CACHE_NAMES = frozenset({"__pycache__", ".DS_Store"})

CacheKind = Literal["pyc", "pycache", "ds_store"]
ReportMode = Literal["plan", "apply-safe-cache"]
ReportStatus = Literal["ready", "prepared", "blocked", "applied"]


class UnsafeCleanupTarget(ValueError):
    """The requested repository or legacy root is not the exact physical target."""


class UnsafeCleanupPlan(ValueError):
    """The checkout changed or contains anything outside the cache-only policy."""


class UnsafeReportOutput(ValueError):
    """The report path is not an exclusive file beneath the evidence root."""


@dataclass(frozen=True, slots=True)
class _Identity:
    device: int
    inode: int
    mode: int


@dataclass(frozen=True, slots=True)
class HygieneItem:
    path: Path
    relative_path: str
    kind: CacheKind
    _identity: _Identity = field(repr=False)


@dataclass(frozen=True, slots=True)
class HygieneSkippedEntry:
    path: Path
    relative_path: str
    reason: str
    _identity: _Identity = field(repr=False)


@dataclass(frozen=True, slots=True)
class CheckoutHygieneFinding:
    repo: Path
    legacy_root: Path
    legacy_namespace_present: bool


@dataclass(frozen=True, slots=True)
class HygienePlan:
    repo: Path
    legacy_root: Path
    items: tuple[HygieneItem, ...]
    skipped: tuple[HygieneSkippedEntry, ...]
    _repo_identity: _Identity = field(repr=False)
    _legacy_identity: _Identity = field(repr=False)

    @property
    def eligible(self) -> bool:
        return not self.skipped


@dataclass(frozen=True, slots=True)
class HygieneApplyResult:
    removed: tuple[str, ...]
    quarantine_path: Path
    _quarantine_identity: _Identity = field(repr=False)


@dataclass(frozen=True, slots=True)
class HygieneReport:
    repo: Path
    legacy_root: Path
    mode: ReportMode
    planned: tuple[tuple[str, CacheKind], ...]
    skipped: tuple[tuple[str, str], ...]
    removed: tuple[str, ...]
    status: ReportStatus
    quarantine_path: Path | None = None
    transaction_id: str | None = None
    completion_path: Path | None = None
    intent_sha256: str | None = None
    legacy_namespace_present: bool = True
    schema_version: Literal[2] = 2

    def as_json_object(self) -> dict[str, object]:
        """Return the single canonical JSON representation of this report."""

        return {
            "legacy_namespace_present": self.legacy_namespace_present,
            "legacy_root": str(self.legacy_root),
            "completion_path": (
                str(self.completion_path) if self.completion_path is not None else None
            ),
            "intent_sha256": self.intent_sha256,
            "mode": self.mode,
            "planned": [
                {"kind": kind, "path": path} for path, kind in self.planned
            ],
            "removed": list(self.removed),
            "quarantine_path": (
                str(self.quarantine_path) if self.quarantine_path is not None else None
            ),
            "repo": str(self.repo),
            "schema_version": self.schema_version,
            "skipped": [
                {"path": path, "reason": reason} for path, reason in self.skipped
            ],
            "status": self.status,
            "transaction_id": self.transaction_id,
        }


def _identity(details: os.stat_result) -> _Identity:
    return _Identity(
        device=details.st_dev,
        inode=details.st_ino,
        mode=details.st_mode,
    )


def _same_identity(left: _Identity, right: _Identity) -> bool:
    return (
        left.device,
        left.inode,
        stat.S_IFMT(left.mode),
    ) == (
        right.device,
        right.inode,
        stat.S_IFMT(right.mode),
    )


def _close_preserving_primary(descriptor: int, primary: BaseException) -> None:
    """Close one owned fd without replacing the operation's primary failure."""

    try:
        os.close(descriptor)
    except OSError as exc:
        primary.add_note(f"secondary descriptor close failure for fd {descriptor}: {exc}")


def _close_owned_descriptors(
    descriptors: list[int], primary: BaseException | None = None
) -> None:
    """Close every fd in reverse order while retaining the first real failure."""

    close_primary = primary
    while descriptors:
        descriptor = descriptors.pop()
        try:
            os.close(descriptor)
        except OSError as exc:
            if close_primary is None:
                close_primary = exc
            else:
                close_primary.add_note(
                    f"secondary descriptor close failure for fd {descriptor}: {exc}"
                )
    if primary is None and close_primary is not None:
        raise close_primary


def _directory_open_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )


def _open_absolute_directory(path: Path) -> int:
    if not path.is_absolute():
        raise UnsafeCleanupTarget("directory anchor must be absolute")
    owned: list[int] = []
    try:
        descriptor = os.open(Path(path.anchor), _directory_open_flags())
        owned.append(descriptor)
        for component in path.parts[1:]:
            child = os.open(component, _directory_open_flags(), dir_fd=descriptor)
            owned.append(child)
            os.fstat(child)
            os.close(descriptor)
            owned.remove(descriptor)
            descriptor = child
        owned.remove(descriptor)
        return descriptor
    except BaseException as primary:
        _close_owned_descriptors(owned, primary)
        raise

def _rename_exclusive(
    source_descriptor: int,
    source_name: str,
    destination_descriptor: int,
    destination_name: str,
) -> None:
    """Perform macOS renameatx_np(RENAME_EXCL), never replacing a destination."""

    try:
        function = ctypes.CDLL(None, use_errno=True).renameatx_np
    except AttributeError as exc:
        raise OSError(errno.ENOTSUP, "exclusive rename is unavailable") from exc
    function.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    function.restype = ctypes.c_int
    result = function(
        source_descriptor,
        os.fsencode(source_name),
        destination_descriptor,
        os.fsencode(destination_name),
        0x00000004,
    )
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number), destination_name)


def _absolute_without_aliases(path: Path, *, label: str) -> Path:
    raw = path.expanduser()
    if ".." in raw.parts:
        raise UnsafeCleanupTarget(f"{label} path is not normalized: {path}")
    absolute = Path(os.path.abspath(os.fspath(raw)))
    try:
        resolved = raw.resolve(strict=True)
    except OSError as exc:
        raise UnsafeCleanupTarget(f"{label} is missing: {path}") from exc
    if absolute != resolved:
        raise UnsafeCleanupTarget(f"{label} is symlinked or aliased: {path}")
    return resolved


def _resolved_repo(repo: Path) -> tuple[Path, _Identity]:
    resolved = _absolute_without_aliases(repo, label="repository")
    try:
        details = resolved.lstat()
    except OSError as exc:
        raise UnsafeCleanupTarget(f"cannot inspect repository: {resolved}") from exc
    if not stat.S_ISDIR(details.st_mode) or stat.S_ISLNK(details.st_mode):
        raise UnsafeCleanupTarget("repository must be a physical directory")
    return resolved, _identity(details)


def _exact_roots(
    repo: Path, legacy_root: Path
) -> tuple[Path, Path, _Identity, _Identity]:
    resolved_repo, repo_identity = _resolved_repo(repo)
    raw_legacy = legacy_root.expanduser()
    if raw_legacy.name != "jarvis" or ".." in raw_legacy.parts:
        raise UnsafeCleanupTarget(str(legacy_root))
    expected = resolved_repo / "jarvis"
    absolute = Path(os.path.abspath(os.fspath(raw_legacy)))
    if absolute != expected:
        raise UnsafeCleanupTarget(str(legacy_root))
    resolved_legacy = _absolute_without_aliases(raw_legacy, label="legacy root")
    if resolved_legacy != expected:
        raise UnsafeCleanupTarget(str(legacy_root))
    try:
        legacy_details = resolved_legacy.lstat()
    except OSError as exc:
        raise UnsafeCleanupTarget(f"cannot inspect legacy root: {resolved_legacy}") from exc
    if not stat.S_ISDIR(legacy_details.st_mode) or stat.S_ISLNK(legacy_details.st_mode):
        raise UnsafeCleanupTarget("legacy root must be a physical directory")
    return resolved_repo, resolved_legacy, repo_identity, _identity(legacy_details)


def _open_roots(
    repo: Path,
    legacy_root: Path,
    *,
    expected_repo_identity: _Identity,
    expected_legacy_identity: _Identity,
) -> tuple[int, int, _Identity, _Identity]:
    try:
        repo_descriptor = _open_absolute_directory(repo)
    except OSError as exc:
        raise UnsafeCleanupTarget(f"cannot anchor repository: {repo}") from exc
    legacy_descriptor: int | None = None
    try:
        repo_details = os.fstat(repo_descriptor)
        repo_identity = _identity(repo_details)
        if not _same_identity(repo_identity, expected_repo_identity):
            raise UnsafeCleanupTarget("repository identity changed while anchoring")
        legacy_details = os.stat("jarvis", dir_fd=repo_descriptor, follow_symlinks=False)
        if not stat.S_ISDIR(legacy_details.st_mode) or stat.S_ISLNK(legacy_details.st_mode):
            raise UnsafeCleanupTarget("legacy root must be a physical directory")
        if legacy_details.st_dev != repo_details.st_dev:
            raise UnsafeCleanupTarget("legacy root must not cross a filesystem boundary")
        if not _same_identity(_identity(legacy_details), expected_legacy_identity):
            raise UnsafeCleanupTarget("legacy root identity changed while anchoring")
        legacy_descriptor = os.open(
            "jarvis", _directory_open_flags(), dir_fd=repo_descriptor
        )
        opened_details = os.fstat(legacy_descriptor)
        if not _same_identity(_identity(legacy_details), _identity(opened_details)):
            raise UnsafeCleanupTarget("legacy root identity changed while anchoring")
        return (
            repo_descriptor,
            legacy_descriptor,
            repo_identity,
            _identity(opened_details),
        )
    except BaseException as primary:
        if legacy_descriptor is not None:
            _close_preserving_primary(legacy_descriptor, primary)
        _close_preserving_primary(repo_descriptor, primary)
        raise


def scan_checkout_hygiene(repo: Path) -> CheckoutHygieneFinding:
    """Detect the physical ``repo/jarvis`` entry without consulting imports."""

    resolved_repo, expected_repo_identity = _resolved_repo(repo)
    descriptor = _open_absolute_directory(resolved_repo)
    owned = [descriptor]
    try:
        if not _same_identity(_identity(os.fstat(descriptor)), expected_repo_identity):
            raise UnsafeCleanupTarget("repository identity changed while anchoring")
        try:
            os.stat("jarvis", dir_fd=descriptor, follow_symlinks=False)
        except FileNotFoundError:
            present = False
        else:
            present = True
    except BaseException as primary:
        _close_owned_descriptors(owned, primary)
        raise
    else:
        _close_owned_descriptors(owned)
    return CheckoutHygieneFinding(
        repo=resolved_repo,
        legacy_root=resolved_repo / "jarvis",
        legacy_namespace_present=present,
    )


def _relative_path(parts: tuple[str, ...]) -> str:
    return PurePosixPath("jarvis", *parts).as_posix()


def _enumerate_entries(
    *, repo: Path, legacy_descriptor: int, legacy_identity: _Identity
) -> tuple[tuple[HygieneItem, ...], tuple[HygieneSkippedEntry, ...]]:
    items: list[HygieneItem] = []
    skipped: list[HygieneSkippedEntry] = []

    def walk(descriptor: int, parent_parts: tuple[str, ...]) -> None:
        try:
            names = sorted(os.listdir(descriptor))
        except OSError as exc:
            raise UnsafeCleanupPlan(
                f"cannot enumerate anchored legacy directory: {_relative_path(parent_parts)}"
            ) from exc
        for name in names:
            if name in {"", ".", ".."} or "/" in name:
                raise UnsafeCleanupPlan("legacy entry name is not normalized")
            parts = (*parent_parts, name)
            relative = _relative_path(parts)
            path = repo.joinpath(*PurePosixPath(relative).parts)
            try:
                details = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            except OSError as exc:
                raise UnsafeCleanupPlan(f"cannot inspect legacy entry: {relative}") from exc
            identity = _identity(details)
            if stat.S_ISLNK(details.st_mode):
                skipped.append(
                    HygieneSkippedEntry(path, relative, "symlink", identity)
                )
                continue
            if stat.S_ISREG(details.st_mode):
                if name == ".DS_Store":
                    items.append(HygieneItem(path, relative, "ds_store", identity))
                elif name.endswith(".pyc"):
                    items.append(HygieneItem(path, relative, "pyc", identity))
                else:
                    skipped.append(
                        HygieneSkippedEntry(path, relative, "non-cache-file", identity)
                    )
                continue
            if stat.S_ISDIR(details.st_mode):
                if details.st_dev != legacy_identity.device:
                    skipped.append(
                        HygieneSkippedEntry(path, relative, "mount-point", identity)
                    )
                    continue
                if name == "__pycache__":
                    items.append(HygieneItem(path, relative, "pycache", identity))
                else:
                    skipped.append(
                        HygieneSkippedEntry(
                            path, relative, "non-cache-directory", identity
                        )
                    )
                try:
                    child = os.open(name, _directory_open_flags(), dir_fd=descriptor)
                except OSError as exc:
                    raise UnsafeCleanupPlan(
                        f"cannot anchor legacy directory: {relative}"
                    ) from exc
                try:
                    if not _same_identity(identity, _identity(os.fstat(child))):
                        raise UnsafeCleanupPlan(
                            f"legacy directory identity changed while scanning: {relative}"
                        )
                    walk(child, parts)
                finally:
                    os.close(child)
                continue
            skipped.append(
                HygieneSkippedEntry(path, relative, "special-file", identity)
            )

    walk(legacy_descriptor, ())
    return (
        tuple(sorted(items, key=lambda item: item.relative_path)),
        tuple(sorted(skipped, key=lambda item: item.relative_path)),
    )


def plan_safe_cache_removal(*, repo: Path, legacy_root: Path) -> HygienePlan:
    """Build an exact, non-mutating cache-only plan for ``repo/jarvis``."""

    (
        resolved_repo,
        resolved_legacy,
        expected_repo_identity,
        expected_legacy_identity,
    ) = _exact_roots(repo, legacy_root)
    repo_descriptor, legacy_descriptor, repo_identity, legacy_identity = _open_roots(
        resolved_repo,
        resolved_legacy,
        expected_repo_identity=expected_repo_identity,
        expected_legacy_identity=expected_legacy_identity,
    )
    owned = [repo_descriptor, legacy_descriptor]
    try:
        items, skipped = _enumerate_entries(
            repo=resolved_repo,
            legacy_descriptor=legacy_descriptor,
            legacy_identity=legacy_identity,
        )
    except BaseException as primary:
        _close_owned_descriptors(owned, primary)
        raise
    else:
        _close_owned_descriptors(owned)
    return HygienePlan(
        repo=resolved_repo,
        legacy_root=resolved_legacy,
        items=items,
        skipped=skipped,
        _repo_identity=repo_identity,
        _legacy_identity=legacy_identity,
    )


def _entry_state(
    items: tuple[HygieneItem, ...], skipped: tuple[HygieneSkippedEntry, ...]
) -> tuple[tuple[str, str, _Identity], ...]:
    rows = [
        (item.relative_path, f"item:{item.kind}", item._identity) for item in items
    ]
    rows.extend(
        (item.relative_path, f"skipped:{item.reason}", item._identity)
        for item in skipped
    )
    return tuple(sorted(rows, key=lambda row: row[0]))


def _verify_root_link(repo_descriptor: int, plan: HygienePlan) -> None:
    try:
        details = os.stat("jarvis", dir_fd=repo_descriptor, follow_symlinks=False)
    except OSError as exc:
        raise UnsafeCleanupTarget("legacy root identity changed") from exc
    if not _same_identity(_identity(details), plan._legacy_identity):
        raise UnsafeCleanupTarget("legacy root identity changed")


def _item_parts(item: HygieneItem) -> tuple[str, ...]:
    parts = PurePosixPath(item.relative_path).parts
    if not parts or parts[0] != "jarvis" or any(part in {"", ".", ".."} for part in parts):
        raise UnsafeCleanupPlan("planned path is not normalized beneath jarvis")
    return parts[1:]


def _private_name(prefix: str) -> str:
    return f".dan-checkout-hygiene-{prefix}-{secrets.token_hex(16)}"


def apply_safe_cache_removal(
    plan: HygienePlan, *, quarantine_name: str | None = None
) -> HygieneApplyResult:
    """Atomically remove the live root by exclusive whole-root quarantine."""

    if not isinstance(plan, HygienePlan):
        raise TypeError("plan must be a HygienePlan")
    if not plan.eligible:
        raise UnsafeCleanupPlan("cleanup plan contains non-cache blockers")
    (
        resolved_repo,
        resolved_legacy,
        expected_repo_identity,
        expected_legacy_identity,
    ) = _exact_roots(plan.repo, plan.legacy_root)
    repo_descriptor, legacy_descriptor, repo_identity, legacy_identity = _open_roots(
        resolved_repo,
        resolved_legacy,
        expected_repo_identity=expected_repo_identity,
        expected_legacy_identity=expected_legacy_identity,
    )
    owned = [repo_descriptor, legacy_descriptor]
    quarantine = quarantine_name or _private_name(f"{resolved_repo.name}-quarantine")
    if quarantine in {"", ".", ".."} or "/" in quarantine:
        _close_owned_descriptors(owned)
        raise UnsafeCleanupPlan("quarantine name must be one private path component")
    parent_descriptor: int | None = None
    staged_descriptor: int | None = None
    try:
        if not _same_identity(repo_identity, plan._repo_identity):
            raise UnsafeCleanupTarget("repository identity changed")
        if not _same_identity(legacy_identity, plan._legacy_identity):
            raise UnsafeCleanupTarget("legacy root identity changed")

        first_items, first_skipped = _enumerate_entries(
            repo=plan.repo,
            legacy_descriptor=legacy_descriptor,
            legacy_identity=legacy_identity,
        )
        second_items, second_skipped = _enumerate_entries(
            repo=plan.repo,
            legacy_descriptor=legacy_descriptor,
            legacy_identity=legacy_identity,
        )
        planned_state = _entry_state(plan.items, plan.skipped)
        if (
            _entry_state(first_items, first_skipped) != planned_state
            or _entry_state(second_items, second_skipped) != planned_state
        ):
            raise UnsafeCleanupPlan("checkout changed after the cleanup plan was built")

        parent_descriptor = _open_absolute_directory(resolved_repo.parent)
        owned.append(parent_descriptor)
        live_repo = os.stat(
            resolved_repo.name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if not _same_identity(_identity(live_repo), plan._repo_identity):
            raise UnsafeCleanupTarget("repository is no longer reachable by its exact name")

        _verify_root_link(repo_descriptor, plan)
        try:
            _rename_exclusive(
                repo_descriptor,
                "jarvis",
                parent_descriptor,
                quarantine,
            )
        except OSError as exc:
            raise UnsafeCleanupPlan("could not exclusively quarantine legacy root") from exc

        staged_descriptor = os.open(
            quarantine,
            _directory_open_flags(),
            dir_fd=parent_descriptor,
        )
        owned.append(staged_descriptor)
        staged_identity = _identity(os.fstat(staged_descriptor))
        staged_items, staged_skipped = _enumerate_entries(
            repo=plan.repo,
            legacy_descriptor=staged_descriptor,
            legacy_identity=staged_identity,
        )
        try:
            os.stat("jarvis", dir_fd=repo_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            live_name_absent = True
        else:
            live_name_absent = False

        if (
            not _same_identity(staged_identity, plan._legacy_identity)
            or _entry_state(staged_items, staged_skipped) != planned_state
            or not live_name_absent
        ):
            failure = UnsafeCleanupPlan(
                "quarantined legacy root does not match the stable cleanup plan"
            )
            if live_name_absent:
                try:
                    _rename_exclusive(
                        parent_descriptor,
                        quarantine,
                        repo_descriptor,
                        "jarvis",
                    )
                except OSError as exc:
                    failure.add_note(
                        f"exclusive restore failed; quarantine requires reconciliation: {exc}"
                    )
            else:
                failure.add_note(
                    "live jarvis name is occupied; quarantine requires reconciliation"
                )
            raise failure

        os.fsync(repo_descriptor)
        os.fsync(parent_descriptor)

        removed_files = sorted(
            item.relative_path
            for item in plan.items
            if item.kind in {"pyc", "ds_store"}
        )
        removed_directories = [
            item.relative_path
            for item in sorted(
                (item for item in plan.items if item.kind == "pycache"),
                key=lambda item: (-len(_item_parts(item)), item.relative_path),
            )
        ]
        result = HygieneApplyResult(
            removed=tuple((*removed_files, *removed_directories, "jarvis")),
            quarantine_path=resolved_repo.parent / quarantine,
            _quarantine_identity=staged_identity,
        )
    except BaseException as primary:
        _close_owned_descriptors(owned, primary)
        raise
    else:
        _close_owned_descriptors(owned)
        return result


def build_hygiene_report(
    plan: HygienePlan,
    *,
    mode: ReportMode,
    removed: tuple[str, ...] | None = None,
    quarantine_path: Path | None = None,
    transaction_id: str | None = None,
    completion_path: Path | None = None,
    intent_sha256: str | None = None,
) -> HygieneReport:
    """Build the deterministic report for plan, blocked apply, or completed apply."""

    if mode not in {"plan", "apply-safe-cache"}:
        raise ValueError(f"invalid checkout hygiene mode: {mode}")
    if plan.skipped:
        status: ReportStatus = "blocked"
    elif mode == "apply-safe-cache" and removed is not None:
        status = "applied"
    elif mode == "apply-safe-cache":
        status = "prepared"
    else:
        status = "ready"
    return HygieneReport(
        repo=plan.repo,
        legacy_root=plan.legacy_root,
        mode=mode,
        planned=tuple((item.relative_path, item.kind) for item in plan.items),
        skipped=tuple((item.relative_path, item.reason) for item in plan.skipped),
        removed=removed or (),
        status=status,
        quarantine_path=quarantine_path,
        transaction_id=transaction_id,
        completion_path=completion_path,
        intent_sha256=intent_sha256,
    )


def _canonical_report_bytes(report: HygieneReport) -> bytes:
    return (
        json.dumps(
            report.as_json_object(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            raise OSError("report write made no progress")
        offset += written


def _validated_report_root(*, repo: Path) -> ValidatedEvidenceRoot:
    value = os.environ.get("DAN_RELEASE_EVIDENCE_ROOT")
    if not value:
        raise UnsafeReportOutput("DAN_RELEASE_EVIDENCE_ROOT is required")
    try:
        return validate_evidence_root(
            Path(value).expanduser(),
            active_roots=active_evidence_roots_from_environment(repo=repo),
        )
    except UnsafeEvidenceRoot as exc:
        raise UnsafeReportOutput(str(exc)) from exc


def _remove_owned_report_leaf(
    parent_descriptor: int, name: str, identity: _Identity
) -> None:
    details = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    if not _same_identity(_identity(details), identity):
        return
    cleanup_name = _private_name("report-cleanup")
    _rename_exclusive(
        parent_descriptor,
        name,
        parent_descriptor,
        cleanup_name,
    )
    quarantined = os.stat(
        cleanup_name,
        dir_fd=parent_descriptor,
        follow_symlinks=False,
    )
    if not _same_identity(_identity(quarantined), identity):
        raise UnsafeReportOutput("report cleanup quarantined wrong identity")
    os.unlink(cleanup_name, dir_fd=parent_descriptor)
    os.fsync(parent_descriptor)


class _ReportReservation:
    def __init__(
        self,
        *,
        parent_descriptor: int,
        descriptor: int,
        name: str,
        identity: _Identity,
        root_path: Path,
        root_identity: _Identity,
        parent_parts: tuple[str, ...],
        parent_identity: _Identity,
    ) -> None:
        self._parent_descriptor = parent_descriptor
        self._descriptor = descriptor
        self._name = name
        self._identity = identity
        self._root_path = root_path
        self._root_identity = root_identity
        self._parent_parts = parent_parts
        self._parent_identity = parent_identity
        self._closed = False

    def verify_reachable(self) -> None:
        descriptors: list[int] = []
        try:
            current = _open_absolute_directory(self._root_path)
            descriptors.append(current)
            if not _same_identity(_identity(os.fstat(current)), self._root_identity):
                raise UnsafeReportOutput("validated evidence root identity changed")
            for component in self._parent_parts:
                child = os.open(component, _directory_open_flags(), dir_fd=current)
                descriptors.append(child)
                os.fstat(child)
                current = child
            if not _same_identity(_identity(os.fstat(current)), self._parent_identity):
                raise UnsafeReportOutput("report output parent is no longer reachable")
            details = os.stat(self._name, dir_fd=current, follow_symlinks=False)
            if not _same_identity(_identity(details), self._identity):
                raise UnsafeReportOutput("report output name changed identity")
        except BaseException as primary:
            _close_owned_descriptors(descriptors, primary)
            if isinstance(primary, OSError):
                raise UnsafeReportOutput(
                    "report output is no longer reachable"
                ) from primary
            raise
        else:
            _close_owned_descriptors(descriptors)

    def _write(self, report: HygieneReport) -> None:
        if self._closed:
            raise UnsafeReportOutput("report reservation is closed")
        payload = _canonical_report_bytes(report)
        _write_all(self._descriptor, payload)
        os.fsync(self._descriptor)
        os.fsync(self._parent_descriptor)

    def finish(self, report: HygieneReport) -> None:
        try:
            self.verify_reachable()
            self._write(report)
            self.verify_reachable()
            self.close()
        except BaseException as primary:
            if not self._closed:
                try:
                    self.abort()
                except BaseException as cleanup_error:
                    primary.add_note(f"secondary report abort failure: {cleanup_error}")
            raise

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        _close_owned_descriptors([self._parent_descriptor, self._descriptor])

    def abort(self) -> None:
        if self._closed:
            return
        errors: list[BaseException] = []
        try:
            os.close(self._descriptor)
        except OSError as exc:
            errors.append(exc)
        try:
            _remove_owned_report_leaf(
                self._parent_descriptor, self._name, self._identity
            )
        except FileNotFoundError:
            pass
        except BaseException as exc:
            errors.append(exc)
        try:
            os.close(self._parent_descriptor)
        except OSError as exc:
            errors.append(exc)
        self._closed = True
        if errors:
            raise UnsafeReportOutput("could not safely clean failed report output") from errors[0]


def _reserve_report_output(output: Path, *, repo: Path) -> _ReportReservation:
    resolved_repo, _ = _resolved_repo(repo)
    root = _validated_report_root(repo=resolved_repo)
    raw = output.expanduser()
    if not raw.is_absolute() or ".." in raw.parts:
        raise UnsafeReportOutput("report output must be an absolute normalized path")
    try:
        relative = raw.relative_to(root.path)
    except ValueError as exc:
        raise UnsafeReportOutput(
            "report output is outside the validated evidence root"
        ) from exc
    parts = relative.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise UnsafeReportOutput("invalid report output path")

    try:
        parent_descriptor = _open_absolute_directory(root.path)
    except (OSError, UnsafeCleanupTarget) as exc:
        raise UnsafeReportOutput("cannot anchor validated evidence root") from exc
    owned = [parent_descriptor]
    try:
        root_details = os.fstat(parent_descriptor)
        root_identity = _identity(root_details)
        if (root_identity.device, root_identity.inode) != (root.device, root.inode):
            raise UnsafeReportOutput("validated evidence root identity changed")
        for component in parts[:-1]:
            child = os.open(
                component, _directory_open_flags(), dir_fd=parent_descriptor
            )
            owned.append(child)
            os.fstat(child)
            os.close(parent_descriptor)
            owned.remove(parent_descriptor)
            parent_descriptor = child
        parent_identity = _identity(os.fstat(parent_descriptor))
        name = parts[-1]
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(name, flags, 0o600, dir_fd=parent_descriptor)
        except FileExistsError:
            details = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
            if stat.S_ISLNK(details.st_mode):
                raise UnsafeReportOutput(
                    "report output must not be a symlink"
                ) from None
            raise

        created_identity: _Identity | None = None
        try:
            details = os.fstat(descriptor)
            created_identity = _identity(details)
            if not stat.S_ISREG(details.st_mode):
                raise UnsafeReportOutput("report output must be a regular file")
            os.fchmod(descriptor, 0o600)
            details = os.fstat(descriptor)
            if not _same_identity(_identity(details), created_identity):
                raise UnsafeReportOutput(
                    "report output identity changed during reservation"
                )
        except BaseException as primary:
            cleanup_errors: list[BaseException] = []
            if created_identity is None:
                try:
                    created_identity = _identity(os.stat(descriptor))
                except OSError as cleanup_error:
                    cleanup_errors.append(cleanup_error)
            _close_preserving_primary(descriptor, primary)
            if created_identity is not None:
                try:
                    _remove_owned_report_leaf(
                        parent_descriptor, name, created_identity
                    )
                except FileNotFoundError:
                    pass
                except BaseException as cleanup_error:
                    cleanup_errors.append(cleanup_error)
            error = UnsafeReportOutput(
                "could not initialize exclusive report output"
            )
            for cleanup_error in cleanup_errors:
                error.add_note(f"report cleanup failed: {cleanup_error}")
            raise error from primary

        reservation = _ReportReservation(
            parent_descriptor=parent_descriptor,
            descriptor=descriptor,
            name=name,
            identity=_identity(details),
            root_path=root.path,
            root_identity=root_identity,
            parent_parts=tuple(parts[:-1]),
            parent_identity=parent_identity,
        )
        owned.remove(parent_descriptor)
        return reservation
    except FileExistsError as primary:
        _close_owned_descriptors(owned, primary)
        raise
    except UnsafeReportOutput as primary:
        _close_owned_descriptors(owned, primary)
        raise
    except OSError as primary:
        _close_owned_descriptors(owned, primary)
        raise UnsafeReportOutput(
            "report output ancestry must exist beneath the evidence root without symlinks"
        ) from primary


def write_hygiene_report_exclusive(
    output: Path,
    report: HygieneReport,
    *,
    repo: Path,
) -> None:
    """Exclusively create, flush, and fsync one canonical JSON report."""

    reservation = _reserve_report_output(output, repo=repo)
    try:
        reservation.finish(report)
    except BaseException:
        if not reservation._closed:
            reservation.abort()
        raise


def run_checkout_hygiene(
    *,
    repo: Path,
    legacy_root: Path,
    output: Path,
    apply_safe_cache: bool,
) -> HygieneReport:
    """Plan or safely apply checkout cleanup and write the exclusive report."""

    plan = plan_safe_cache_removal(repo=repo, legacy_root=legacy_root)
    mode: ReportMode = "apply-safe-cache" if apply_safe_cache else "plan"
    reservation = _reserve_report_output(output, repo=plan.repo)
    intent: _ReportReservation | None = None
    try:
        if not apply_safe_cache or not plan.eligible:
            report = build_hygiene_report(plan, mode=mode)
            reservation.finish(report)
            return report
        quarantine_name = _private_name(f"{plan.repo.name}-quarantine")
        quarantine_path = plan.repo.parent / quarantine_name
        transaction_id = secrets.token_hex(32)
        intent_path = output.with_name(f"{output.name}.intent")
        intent = _reserve_report_output(intent_path, repo=plan.repo)
        initial = build_hygiene_report(
            plan,
            mode=mode,
            quarantine_path=quarantine_path,
            transaction_id=transaction_id,
            completion_path=output,
        )
        intent_sha256 = hashlib.sha256(_canonical_report_bytes(initial)).hexdigest()
        intent.finish(initial)
        reservation.verify_reachable()
        result = apply_safe_cache_removal(plan, quarantine_name=quarantine_name)
        report = build_hygiene_report(
            plan,
            mode=mode,
            removed=result.removed,
            quarantine_path=result.quarantine_path,
            transaction_id=transaction_id,
            completion_path=output,
            intent_sha256=intent_sha256,
        )
        reservation.finish(report)
        return report
    except BaseException as primary:
        if intent is not None and not intent._closed:
            try:
                intent.abort()
            except BaseException as cleanup_error:
                primary.add_note(f"secondary intent cleanup failure: {cleanup_error}")
        if not reservation._closed:
            try:
                reservation.abort()
            except BaseException as cleanup_error:
                primary.add_note(f"secondary completion cleanup failure: {cleanup_error}")
        raise
