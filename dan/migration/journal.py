"""Append-only cutover journal (Task 12).

Every cutover run owns one private directory ``~/.dan/migration/cutover-<UTC>Z/``
(mode 0700) holding ``journal.jsonl`` (mode 0600) plus atomically written
reports. The journal is strictly append-only: the inverse of every mutation is
recorded *before* the mutation executes, and a ``phase-committed`` marker seals
each completed phase so an interrupted run can resume from the last sealed
phase without repeating work.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path


class CutoverPhase(StrEnum):
    INVENTORIED = "inventoried"
    INTAKE_CLOSED = "intake_closed"
    QUEUE_QUIESCENT = "queue_quiescent"
    RUNTIME_STOPPED = "runtime_stopped"
    DATABASES_BACKED_UP = "databases_backed_up"
    DATABASES_MIGRATED = "databases_migrated"
    PATHS_MOVED = "paths_moved"
    ADAPTERS_INSTALLED = "adapters_installed"
    LAUNCHD_INSTALLED = "launchd_installed"
    COLD_STARTED = "cold_started"
    VERIFIED = "verified"


@dataclass(frozen=True)
class JournalEntry:
    phase: CutoverPhase
    operation: str
    source: str | None
    destination: str | None
    before_sha256: str | None
    after_sha256: str | None
    rollback_operation: str


PHASE_COMMITTED = "phase-committed"


def utc_stamp(now: datetime | None = None) -> str:
    moment = now or datetime.now(timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


class JournalError(RuntimeError):
    """The journal contract was violated."""


class Journal:
    """One append-only journal file inside a private per-run directory."""

    FILENAME = "journal.jsonl"

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        if not self.directory.is_dir():
            raise JournalError(f"journal directory does not exist: {self.directory}")
        self.path = self.directory / self.FILENAME

    # ------------------------------------------------------------ creation
    @classmethod
    def create(cls, migration_root: Path, *, now: datetime | None = None) -> "Journal":
        migration_root = Path(migration_root)
        migration_root.mkdir(parents=True, exist_ok=True)
        stamp = utc_stamp(now)
        directory = migration_root / f"cutover-{stamp}"
        suffix = 0
        while directory.exists():
            suffix += 1
            directory = migration_root / f"cutover-{stamp}-{suffix}"
        directory.mkdir(mode=0o700)
        os.chmod(directory, 0o700)
        journal = cls(directory)
        journal.path.touch(mode=0o600)
        os.chmod(journal.path, 0o600)
        return journal

    @classmethod
    def open(cls, directory: Path) -> "Journal":
        journal = cls(directory)
        if not journal.path.is_file():
            raise JournalError(f"journal file missing: {journal.path}")
        return journal

    # ------------------------------------------------------------ appending
    def append(self, entry: JournalEntry) -> None:
        payload = asdict(entry)
        payload["phase"] = entry.phase.value
        payload["recorded_at"] = datetime.now(timezone.utc).isoformat()
        line = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        # O_APPEND: physically append-only; existing bytes are never rewritten.
        descriptor = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(descriptor, (line + "\n").encode("utf-8"))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def commit_phase(self, phase: CutoverPhase) -> None:
        self.append(
            JournalEntry(
                phase=phase,
                operation=PHASE_COMMITTED,
                source=None,
                destination=None,
                before_sha256=None,
                after_sha256=None,
                rollback_operation="none",
            )
        )

    # ------------------------------------------------------------ reading
    def raw_entries(self) -> list[dict]:
        if not self.path.is_file():
            return []
        rows: list[dict] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return rows

    def entries(self) -> list[JournalEntry]:
        result: list[JournalEntry] = []
        for row in self.raw_entries():
            result.append(
                JournalEntry(
                    phase=CutoverPhase(row["phase"]),
                    operation=str(row["operation"]),
                    source=row.get("source"),
                    destination=row.get("destination"),
                    before_sha256=row.get("before_sha256"),
                    after_sha256=row.get("after_sha256"),
                    rollback_operation=str(row["rollback_operation"]),
                )
            )
        return result

    def committed_phases(self) -> list[CutoverPhase]:
        return [
            entry.phase
            for entry in self.entries()
            if entry.operation == PHASE_COMMITTED
        ]

    # ------------------------------------------------------------ reports
    def write_report(self, name: str, payload: dict) -> Path:
        """Atomically write a private JSON report next to the journal."""

        if "/" in name or name.startswith("."):
            raise JournalError(f"unsafe report name: {name!r}")
        target = self.directory / name
        descriptor, temporary = tempfile.mkstemp(dir=self.directory, prefix=f".{name}.")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=1, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, target)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
        return target


__all__ = [
    "CutoverPhase",
    "Journal",
    "JournalEntry",
    "JournalError",
    "PHASE_COMMITTED",
    "utc_stamp",
]
