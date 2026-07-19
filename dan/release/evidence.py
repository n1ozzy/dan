"""Strict, versioned evidence envelope shared by all Release 1 producers."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import stat
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Literal, Protocol, TypeAlias, cast

JsonScalar: TypeAlias = str | int | bool | None
JsonValue: TypeAlias = (
    JsonScalar
    | list["JsonValue"]
    | tuple["JsonValue", ...]
    | dict[str, "JsonValue"]
    | Mapping[str, "JsonValue"]
)
_SHA256_LENGTH = 64
_PRIVATE_TEMP_ATTEMPTS = 128
_PRIVATE_TEMP_PREFIX = ".dan-evidence-"
_ENVELOPE_KEYS = {
    "schema_version",
    "kind",
    "producer_id",
    "created_at_utc",
    "subject_sha",
    "artifact_sha256",
    "status",
    "finding_codes",
    "unknown_evidence",
    "input_evidence",
    "result",
    "report_sha256",
}


class UnsafeEvidenceRoot(ValueError):
    """The proposed evidence location overlaps mutable or protected state."""


class InvalidEvidenceEnvelope(ValueError):
    """Evidence bytes do not satisfy the strict versioned contract."""


class _PublicationGuard(Protocol):
    def before_link(self) -> None: ...

    def before_commit(self) -> None: ...


@dataclass(frozen=True)
class _CallablePublicationGuard:
    callback: Callable[[], None]

    def before_link(self) -> None:
        self.callback()

    def before_commit(self) -> None:
        self.callback()


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
    device: int
    inode: int


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

    def __post_init__(self) -> None:
        frozen = _freeze_json_value(self.result, location="result")
        if not isinstance(frozen, Mapping):
            raise InvalidEvidenceEnvelope("result must be an object")
        object.__setattr__(self, "result", frozen)


def active_evidence_roots_from_environment(*, repo: Path) -> ActiveEvidenceRoots:
    """Resolve every active tree that release evidence must stay outside."""

    home = Path(os.environ.get("HOME", str(Path.home()))).expanduser()
    home_dan = home / ".dan"
    home_config = home / ".config"
    return ActiveEvidenceRoots(
        repo=repo,
        home_dan=home_dan,
        home_config=home_config,
        home_claude=home / ".claude",
        dan_config=Path(os.environ.get("DAN_CONFIG", str(home_dan / "config.toml"))),
        voice_config=Path(
            os.environ.get("VOICE_CONFIG_DIR", str(home_config / "voice"))
        ),
        runtime=Path(
            os.environ.get("DAN_RUNTIME_DIR", str(home_dan / "runtime"))
        ),
        database=Path(
            os.environ.get("DAN_DB_PATH", str(home_dan / "dan.sqlite3"))
        ),
    )


def _directory_open_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )


def _open_absolute_directory_nofollow(path: Path) -> int:
    if not path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts[1:]):
        raise UnsafeEvidenceRoot("evidence path must be absolute and normalized")
    descriptor = os.open("/", _directory_open_flags())
    try:
        for component in path.parts[1:]:
            child = os.open(component, _directory_open_flags(), dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except OSError as exc:
        os.close(descriptor)
        raise UnsafeEvidenceRoot(
            f"evidence directory ancestry is missing, non-directory, or symlinked: {path}"
        ) from exc


def _open_validated_root(root: ValidatedEvidenceRoot) -> int:
    descriptor = _open_absolute_directory_nofollow(root.path)
    try:
        details = os.fstat(descriptor)
    except BaseException as error:
        _close_preserving_primary(
            descriptor,
            error,
            context="validated evidence root descriptor close failed",
        )
        raise
    if (details.st_dev, details.st_ino) != (root.device, root.inode):
        error = UnsafeEvidenceRoot("validated evidence root identity changed")
        _close_preserving_primary(
            descriptor,
            error,
            context="validated evidence root descriptor close failed",
        )
        raise error
    if stat.S_IMODE(details.st_mode) != 0o700:
        error = UnsafeEvidenceRoot("validated evidence root mode changed")
        _close_preserving_primary(
            descriptor,
            error,
            context="validated evidence root descriptor close failed",
        )
        raise error
    return descriptor


def _paths_overlap(left: Path, right: Path) -> bool:
    return left == right or left.is_relative_to(right) or right.is_relative_to(left)


def validate_evidence_root(
    root: Path,
    *,
    active_roots: ActiveEvidenceRoots,
) -> ValidatedEvidenceRoot:
    """Validate an existing private root without creating or repairing it."""

    raw = root.expanduser()
    if not raw.is_absolute():
        raise UnsafeEvidenceRoot("evidence root must be absolute")
    try:
        descriptor = _open_absolute_directory_nofollow(raw)
        details = os.fstat(descriptor)
    except (OSError, UnsafeEvidenceRoot) as exc:
        raise UnsafeEvidenceRoot("evidence root must be an existing non-symlink directory") from exc
    finally:
        if "descriptor" in locals():
            os.close(descriptor)
    mode = stat.S_IMODE(details.st_mode)
    if mode != 0o700:
        raise UnsafeEvidenceRoot(f"evidence root mode must be 0700, got {mode:04o}")

    for protected in (
        active_roots.repo,
        active_roots.home_dan,
        active_roots.home_config,
        active_roots.home_claude,
        active_roots.dan_config,
        active_roots.voice_config,
        active_roots.runtime,
        active_roots.database,
    ):
        normalized = protected.expanduser().resolve(strict=False)
        if _paths_overlap(raw, normalized):
            raise UnsafeEvidenceRoot(
                f"evidence root overlaps protected path: {normalized}"
            )
    return ValidatedEvidenceRoot(path=raw, device=details.st_dev, inode=details.st_ino)


def _is_hex(value: object, *, lengths: tuple[int, ...]) -> bool:
    return (
        isinstance(value, str)
        and len(value) in lengths
        and all(character in "0123456789abcdef" for character in value)
    )


def _freeze_json_value(value: object, *, location: str) -> JsonValue:
    if value is None or isinstance(value, (str, bool)):
        return cast(JsonValue, value)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, (list, tuple)):
        return tuple(
            _freeze_json_value(child, location=f"{location}[{index}]")
            for index, child in enumerate(value)
        )
    if isinstance(value, Mapping):
        result: dict[str, JsonValue] = {}
        for key, child in list(value.items()):
            if not isinstance(key, str):
                raise InvalidEvidenceEnvelope(f"{location} keys must be strings")
            result[key] = _freeze_json_value(child, location=f"{location}.{key}")
        return MappingProxyType(result)
    raise InvalidEvidenceEnvelope(f"unsupported JSON value at {location}")


def _plain_json_value(value: JsonValue) -> JsonValue:
    if isinstance(value, Mapping):
        return {key: _plain_json_value(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_json_value(child) for child in value]
    return value


def _envelope_payload(
    envelope: ReleaseEvidenceEnvelope,
    *,
    include_report_sha256: bool,
) -> dict[str, JsonValue]:
    payload: dict[str, JsonValue] = {
        "schema_version": envelope.schema_version,
        "kind": envelope.kind,
        "producer_id": envelope.producer_id,
        "created_at_utc": envelope.created_at_utc,
        "subject_sha": envelope.subject_sha,
        "artifact_sha256": envelope.artifact_sha256,
        "status": envelope.status,
        "finding_codes": list(envelope.finding_codes),
        "unknown_evidence": list(envelope.unknown_evidence),
        "input_evidence": [
            {"role": item.role, "sha256": item.sha256}
            for item in envelope.input_evidence
        ],
        "result": _plain_json_value(cast(JsonValue, envelope.result)),
    }
    if include_report_sha256:
        payload["report_sha256"] = envelope.report_sha256
    return payload


def _canonical_json_bytes(value: Mapping[str, JsonValue]) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise InvalidEvidenceEnvelope("value is not canonical JSON") from exc


def canonical_envelope_sha256(envelope: ReleaseEvidenceEnvelope) -> str:
    """Hash canonical UTF-8 bytes with ``report_sha256`` omitted."""

    payload = _envelope_payload(envelope, include_report_sha256=False)
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def _validate_timestamp(value: object) -> None:
    if not isinstance(value, str) or not value:
        raise InvalidEvidenceEnvelope("created_at_utc must be a non-empty string")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise InvalidEvidenceEnvelope("created_at_utc is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise InvalidEvidenceEnvelope("created_at_utc must include a UTC offset")
    if parsed.utcoffset().total_seconds() != 0:
        raise InvalidEvidenceEnvelope("created_at_utc must be UTC")


def _validate_envelope(envelope: ReleaseEvidenceEnvelope) -> None:
    if envelope.schema_version != 1 or isinstance(envelope.schema_version, bool):
        raise InvalidEvidenceEnvelope("schema_version must be 1")
    if not envelope.kind or not envelope.producer_id:
        raise InvalidEvidenceEnvelope("kind and producer_id must be non-empty")
    _validate_timestamp(envelope.created_at_utc)
    if not _is_hex(envelope.subject_sha, lengths=(40, 64)):
        raise InvalidEvidenceEnvelope("subject_sha must be a lowercase SHA")
    if envelope.artifact_sha256 is not None and not _is_hex(
        envelope.artifact_sha256, lengths=(_SHA256_LENGTH,)
    ):
        raise InvalidEvidenceEnvelope("artifact_sha256 must be null or SHA-256")
    if envelope.status not in {"green", "red", "unknown"}:
        raise InvalidEvidenceEnvelope("invalid status")
    for name, values in (
        ("finding_codes", envelope.finding_codes),
        ("unknown_evidence", envelope.unknown_evidence),
    ):
        if not isinstance(values, tuple) or not all(
            isinstance(value, str) and value for value in values
        ):
            raise InvalidEvidenceEnvelope(f"{name} must be a tuple of strings")
    if not isinstance(envelope.input_evidence, tuple):
        raise InvalidEvidenceEnvelope("input_evidence must be a tuple")
    roles: set[str] = set()
    for item in envelope.input_evidence:
        if not isinstance(item, EvidenceInput) or not item.role or item.role in roles:
            raise InvalidEvidenceEnvelope("input evidence roles must be unique")
        if not _is_hex(item.sha256, lengths=(_SHA256_LENGTH,)):
            raise InvalidEvidenceEnvelope("input evidence hash must be SHA-256")
        roles.add(item.role)
    _freeze_json_value(envelope.result, location="result")
    if not _is_hex(envelope.report_sha256, lengths=(_SHA256_LENGTH,)):
        raise InvalidEvidenceEnvelope("report_sha256 must be SHA-256")
    if envelope.report_sha256 != canonical_envelope_sha256(envelope):
        raise InvalidEvidenceEnvelope("report_sha256 does not match canonical envelope")


def _relative_evidence_parts(path: Path, root: ValidatedEvidenceRoot) -> tuple[str, ...]:
    raw = path.expanduser()
    if not raw.is_absolute():
        raise UnsafeEvidenceRoot("evidence output path must be absolute")
    try:
        relative = raw.relative_to(root.path)
    except ValueError as exc:
        raise UnsafeEvidenceRoot("evidence output is outside validated root") from exc
    parts = relative.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise UnsafeEvidenceRoot("invalid evidence output name")
    return parts


def _open_evidence_parent(
    path: Path,
    root: ValidatedEvidenceRoot,
) -> tuple[int, str]:
    parts = _relative_evidence_parts(path, root)
    descriptor = _open_validated_root(root)
    try:
        for component in parts[:-1]:
            child = os.open(component, _directory_open_flags(), dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor, parts[-1]
    except OSError as exc:
        os.close(descriptor)
        raise UnsafeEvidenceRoot(
            "evidence output parent must exist beneath the validated root without symlinks"
        ) from exc


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            raise OSError("evidence write made no progress")
        offset += written


def _inode_identity(details: os.stat_result) -> tuple[int, int]:
    return details.st_dev, details.st_ino


def _close_preserving_primary(
    descriptor: int,
    primary_error: BaseException,
    *,
    context: str,
) -> None:
    try:
        os.close(descriptor)
    except OSError as close_error:
        primary_error.add_note(f"{context}: {close_error}")


def _open_private_temp(parent_descriptor: int) -> tuple[str, int, tuple[int, int]]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    for _ in range(_PRIVATE_TEMP_ATTEMPTS):
        name = f"{_PRIVATE_TEMP_PREFIX}{secrets.token_hex(16)}.tmp"
        try:
            descriptor = os.open(name, flags, 0o000, dir_fd=parent_descriptor)
        except FileExistsError:
            continue
        try:
            details = os.fstat(descriptor)
        except BaseException as error:
            cleanup_errors: list[str] = []
            removed = False
            try:
                identity = _inode_identity(os.stat(descriptor))
                removed = _unlink_owned_name(parent_descriptor, name, identity)
            except (OSError, UnsafeEvidenceRoot) as exc:
                cleanup_errors.append(f"temporary cleanup failed: {exc}")
            if removed:
                try:
                    os.fsync(parent_descriptor)
                except OSError as exc:
                    cleanup_errors.append(
                        f"cleanup directory fsync failed: {exc}"
                    )
            try:
                os.close(descriptor)
            except OSError as exc:
                cleanup_errors.append(f"temporary descriptor close failed: {exc}")
            if cleanup_errors:
                error.add_note("; ".join(cleanup_errors))
            raise
        return name, descriptor, _inode_identity(details)
    raise FileExistsError("could not allocate a private evidence temporary file")


def _unlink_owned_name(
    parent_descriptor: int,
    name: str,
    identity: tuple[int, int],
) -> bool:
    quarantine = f"{_PRIVATE_TEMP_PREFIX}{secrets.token_hex(16)}.cleanup"
    try:
        os.rename(
            name,
            quarantine,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
        )
    except FileNotFoundError:
        return False
    details = os.stat(
        quarantine,
        dir_fd=parent_descriptor,
        follow_symlinks=False,
    )
    if _inode_identity(details) != identity:
        try:
            os.link(
                quarantine,
                name,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except OSError as exc:
            raise UnsafeEvidenceRoot(
                f"replaced evidence path retained as: {quarantine}"
            ) from exc
        os.unlink(quarantine, dir_fd=parent_descriptor)
        raise UnsafeEvidenceRoot(
            f"refusing to unlink replaced evidence path: {name}"
        )
    os.unlink(quarantine, dir_fd=parent_descriptor)
    return True


def _unlink_name_if_owned(
    parent_descriptor: int,
    name: str,
    identity: tuple[int, int],
) -> bool:
    try:
        details = os.stat(
            name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return False
    if _inode_identity(details) != identity:
        return False
    return _unlink_owned_name(parent_descriptor, name, identity)


def write_evidence_envelope_exclusive(
    path: Path,
    envelope: ReleaseEvidenceEnvelope,
    *,
    evidence_root: ValidatedEvidenceRoot,
    transaction_guard: Callable[[], None] | None = None,
    publication_guard: _PublicationGuard | None = None,
) -> None:
    """Create one canonical mode-0600 envelope and never overwrite history.

    A publication guard runs immediately before the link and after the durable
    directory sync. ``transaction_guard`` retains the original callable API and
    is adapted to those same two phases.
    """

    if transaction_guard is not None and publication_guard is not None:
        raise TypeError("transaction_guard and publication_guard are mutually exclusive")
    guard: _PublicationGuard | None = publication_guard
    if transaction_guard is not None:
        guard = _CallablePublicationGuard(transaction_guard)

    _validate_envelope(envelope)
    payload = _envelope_payload(envelope, include_report_sha256=True)
    encoded = _canonical_json_bytes(payload) + b"\n"
    parent_descriptor, name = _open_evidence_parent(path, evidence_root)
    try:
        rollback_descriptor = os.dup(parent_descriptor)
    except BaseException as error:
        _close_preserving_primary(
            parent_descriptor,
            error,
            context="parent descriptor close failed",
        )
        raise
    temporary_descriptor: int | None = None
    temporary_name: str | None = None
    temporary_identity: tuple[int, int] | None = None
    namespace_mutation_attempted = False
    primary_error: BaseException | None = None
    primary_traceback = None
    cleanup_errors: list[str] = []
    try:
        temporary_name, temporary_descriptor, temporary_identity = (
            _open_private_temp(parent_descriptor)
        )
        namespace_mutation_attempted = True
        _write_all(temporary_descriptor, encoded)
        os.fchmod(temporary_descriptor, 0o600)
        os.fsync(temporary_descriptor)
        completed_details = os.fstat(temporary_descriptor)
        if not stat.S_ISREG(completed_details.st_mode):
            raise UnsafeEvidenceRoot("evidence output must be a regular file")
        if stat.S_IMODE(completed_details.st_mode) != 0o600:
            raise UnsafeEvidenceRoot("evidence output mode changed before publication")
        if _inode_identity(completed_details) != temporary_identity:
            raise UnsafeEvidenceRoot("temporary evidence identity changed during write")
        descriptor_to_close = temporary_descriptor
        temporary_descriptor = None
        os.close(descriptor_to_close)
        if guard is not None:
            guard.before_link()
        temporary_details = os.stat(
            temporary_name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if _inode_identity(temporary_details) != temporary_identity:
            raise UnsafeEvidenceRoot(
                "temporary evidence identity changed before publication"
            )
        if not stat.S_ISREG(temporary_details.st_mode):
            raise UnsafeEvidenceRoot("evidence output must be a regular file")
        if stat.S_IMODE(temporary_details.st_mode) != 0o600:
            raise UnsafeEvidenceRoot("evidence output mode changed before publication")
        if temporary_details.st_nlink != 1:
            raise UnsafeEvidenceRoot(
                "temporary evidence has unexpected hard links before publication"
            )

        link_error: BaseException | None = None
        link_traceback = None
        try:
            os.link(
                temporary_name,
                name,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except BaseException as error:
            link_error = error
            link_traceback = error.__traceback__

        final_details: os.stat_result | None
        try:
            final_details = os.stat(
                name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            final_details = None
        except BaseException as reconciliation_error:
            if link_error is not None:
                link_error.add_note(
                    "final identity reconciliation failed: "
                    f"{reconciliation_error}"
                )
                raise link_error.with_traceback(link_traceback) from None
            raise UnsafeEvidenceRoot(
                "could not verify published evidence identity"
            ) from reconciliation_error

        final_is_owned = (
            final_details is not None
            and _inode_identity(final_details) == temporary_identity
        )
        if link_error is not None:
            raise link_error.with_traceback(link_traceback)
        if not final_is_owned:
            raise UnsafeEvidenceRoot(
                "published evidence identity changed before commit"
            )

        _unlink_owned_name(
            parent_descriptor,
            temporary_name,
            temporary_identity,
        )
        temporary_name = None
        os.fsync(parent_descriptor)
        if guard is not None:
            guard.before_commit()
        descriptor_to_close = parent_descriptor
        parent_descriptor = None
        os.close(descriptor_to_close)
    except BaseException as error:
        primary_error = error
        primary_traceback = error.__traceback__
        removed = False
        if temporary_identity is not None:
            try:
                removed |= _unlink_name_if_owned(
                    rollback_descriptor,
                    name,
                    temporary_identity,
                )
            except (OSError, UnsafeEvidenceRoot) as exc:
                cleanup_errors.append(f"final cleanup failed: {exc}")
        if temporary_name is not None and temporary_identity is not None:
            try:
                removed |= _unlink_name_if_owned(
                    rollback_descriptor,
                    temporary_name,
                    temporary_identity,
                )
            except (OSError, UnsafeEvidenceRoot) as exc:
                cleanup_errors.append(f"temporary cleanup failed: {exc}")
        if removed or namespace_mutation_attempted:
            try:
                os.fsync(rollback_descriptor)
            except BaseException as exc:
                cleanup_errors.append(f"cleanup directory fsync failed: {exc}")
    finally:
        if temporary_descriptor is not None:
            descriptor_to_close = temporary_descriptor
            temporary_descriptor = None
            try:
                os.close(descriptor_to_close)
            except BaseException as exc:
                cleanup_errors.append(f"temporary descriptor close failed: {exc}")
        if parent_descriptor is not None:
            descriptor_to_close = parent_descriptor
            parent_descriptor = None
            try:
                os.close(descriptor_to_close)
            except BaseException as exc:
                cleanup_errors.append(f"parent descriptor close failed: {exc}")
        descriptor_to_close = rollback_descriptor
        rollback_descriptor = None
        try:
            os.close(descriptor_to_close)
        except BaseException as exc:
            cleanup_errors.append(f"rollback descriptor close failed: {exc}")
    if primary_error is not None:
        for cleanup_error in cleanup_errors:
            primary_error.add_note(cleanup_error)
        raise primary_error.with_traceback(primary_traceback)
    if cleanup_errors:
        raise OSError(
            "evidence publication committed; post-commit cleanup failed: "
            + "; ".join(cleanup_errors)
        )


def _object_without_duplicates(pairs: Sequence[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise InvalidEvidenceEnvelope(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _parse_string_tuple(value: object, *, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise InvalidEvidenceEnvelope(f"{name} must be an array of strings")
    return tuple(value)


def _envelope_from_mapping(payload: Mapping[str, object]) -> ReleaseEvidenceEnvelope:
    if set(payload) != _ENVELOPE_KEYS:
        raise InvalidEvidenceEnvelope("evidence envelope keys do not match schema")
    inputs_raw = payload["input_evidence"]
    if not isinstance(inputs_raw, list):
        raise InvalidEvidenceEnvelope("input_evidence must be an array")
    inputs: list[EvidenceInput] = []
    for raw in inputs_raw:
        if not isinstance(raw, Mapping) or set(raw) != {"role", "sha256"}:
            raise InvalidEvidenceEnvelope("invalid input_evidence record")
        role = raw["role"]
        sha256 = raw["sha256"]
        if not isinstance(role, str) or not isinstance(sha256, str):
            raise InvalidEvidenceEnvelope("invalid input_evidence types")
        inputs.append(EvidenceInput(role=role, sha256=sha256))
    result = payload["result"]
    if not isinstance(result, Mapping):
        raise InvalidEvidenceEnvelope("result must be an object")
    artifact = payload["artifact_sha256"]
    if artifact is not None and not isinstance(artifact, str):
        raise InvalidEvidenceEnvelope("artifact_sha256 must be null or string")
    strings = {
        name: payload[name]
        for name in (
            "kind",
            "producer_id",
            "created_at_utc",
            "subject_sha",
            "status",
            "report_sha256",
        )
    }
    if not all(isinstance(value, str) for value in strings.values()):
        raise InvalidEvidenceEnvelope("envelope string field has wrong type")
    envelope = ReleaseEvidenceEnvelope(
        schema_version=cast(Literal[1], payload["schema_version"]),
        kind=cast(str, strings["kind"]),
        producer_id=cast(str, strings["producer_id"]),
        created_at_utc=cast(str, strings["created_at_utc"]),
        subject_sha=cast(str, strings["subject_sha"]),
        artifact_sha256=artifact,
        status=cast(Literal["green", "red", "unknown"], strings["status"]),
        finding_codes=_parse_string_tuple(
            payload["finding_codes"], name="finding_codes"
        ),
        unknown_evidence=_parse_string_tuple(
            payload["unknown_evidence"], name="unknown_evidence"
        ),
        input_evidence=tuple(inputs),
        result=cast(Mapping[str, JsonValue], _freeze_json_value(result, location="result")),
        report_sha256=cast(str, strings["report_sha256"]),
    )
    _validate_envelope(envelope)
    return envelope

def read_evidence_envelope(
    path: Path,
    *,
    evidence_root: ValidatedEvidenceRoot,
    expected_kind: str,
    expected_producer_id: str | None = None,
) -> ReleaseEvidenceEnvelope:
    """Read only canonical, owner-only evidence with exact expectations."""

    parent_descriptor, name = _open_evidence_parent(path, evidence_root)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=parent_descriptor)
    except OSError as exc:
        os.close(parent_descriptor)
        raise InvalidEvidenceEnvelope("cannot inspect evidence file") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise InvalidEvidenceEnvelope("evidence path must be a regular file")
        if stat.S_IMODE(before.st_mode) != 0o600:
            raise InvalidEvidenceEnvelope("evidence file mode must be 0600")
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
            raise InvalidEvidenceEnvelope("evidence file changed while it was read")
        encoded = b"".join(chunks)
        text = encoded.decode("utf-8", errors="strict")
        parsed = json.loads(text, object_pairs_hook=_object_without_duplicates)
    except InvalidEvidenceEnvelope:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidEvidenceEnvelope("evidence is not strict UTF-8 JSON") from exc
    finally:
        os.close(descriptor)
        os.close(parent_descriptor)
    if not isinstance(parsed, Mapping):
        raise InvalidEvidenceEnvelope("evidence root must be an object")
    envelope = _envelope_from_mapping(parsed)
    canonical = _canonical_json_bytes(
        _envelope_payload(envelope, include_report_sha256=True)
    ) + b"\n"
    if encoded != canonical:
        raise InvalidEvidenceEnvelope("evidence bytes are not canonical")
    if envelope.kind != expected_kind:
        raise InvalidEvidenceEnvelope("evidence kind does not match expectation")
    if expected_producer_id is not None and envelope.producer_id != expected_producer_id:
        raise InvalidEvidenceEnvelope("producer_id does not match expectation")
    return envelope
