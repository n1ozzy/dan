"""Journal-driven cutover rollback (Task 12).

Rollback replays the journal in reverse. Its fixed order:

1. close the new intake and stop ``dand`` (through the injected stopper);
2. verify ``speaking`` is null everywhere — an in-flight utterance is never
   resumed, a cancelled request is NEVER replayed;
3. restore adapters/plists/config/databases/paths in exact reverse journal
   order (case-safe temp-hop renames for case-insensitive filesystems);
4. only then report that the old runtime may start again.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from dan.install import InstallEntry, InstallPlan, InstallReport
from dan.migration.cutover import (
    ACTIVE_STATUSES,
    CutoverBlocked,
    CutoverManifest,
    _case_safe_rename,
)
from dan.migration.journal import (
    PHASE_COMMITTED,
    CutoverPhase,
    Journal,
    JournalEntry,
)


class RollbackBlocked(RuntimeError):
    """Rollback refuses to continue: a safety invariant is not met."""


@dataclass
class RollbackReport:
    journal: Path
    undone: list[str] = field(default_factory=list)
    old_runtime_start_allowed: bool = False


def _refuse_launchctl(*arguments: str) -> None:
    raise RollbackBlocked(
        "no launchctl executor injected — real bootstrap/bootout is Task 14 "
        f"(refused: {' '.join(arguments)})"
    )


def _refuse_runtime_stop() -> None:
    raise RollbackBlocked("no runtime stopper injected — stopping real dand is Task 14")


def _speaking_value(database: Path) -> str | None:
    if not database.is_file():
        return None
    connection = sqlite3.connect(database)
    try:
        row = connection.execute(
            "SELECT value FROM runtime_state WHERE key = 'speaking'"
        ).fetchone()
    finally:
        connection.close()
    return row[0] if row else None


def _active_request_count(database: Path) -> int:
    if not database.is_file():
        return 0
    connection = sqlite3.connect(database)
    try:
        placeholders = ",".join("?" for _ in ACTIVE_STATUSES)
        row = connection.execute(
            f"SELECT COUNT(*) FROM requests WHERE status IN ({placeholders})",
            ACTIVE_STATUSES,
        ).fetchone()
    finally:
        connection.close()
    return int(row[0])


def rollback_plan(journal_dir: Path) -> list[str]:
    """Read-only list of inverse operations, newest first."""

    journal = Journal.open(Path(journal_dir))
    pending: list[str] = []
    for entry in reversed(journal.entries()):
        if entry.operation == PHASE_COMMITTED or entry.rollback_operation in {"none"}:
            continue
        pending.append(
            f"{entry.rollback_operation}: {entry.operation}"
            f" {entry.source or ''} -> {entry.destination or ''}".rstrip()
        )
    return pending


def perform_rollback(
    *,
    journal_dir: Path,
    manifest: CutoverManifest,
    home: Path,
    launchctl: Callable[..., None] | None = None,
    runtime_stopper: Callable[[], None] | None = None,
    apply_changes: bool = False,
) -> RollbackReport:
    journal = Journal.open(Path(journal_dir))
    home = Path(home)
    launchctl = launchctl or _refuse_launchctl
    runtime_stopper = runtime_stopper or _refuse_runtime_stop
    report = RollbackReport(journal=journal.directory)

    if not apply_changes:
        report.undone = rollback_plan(journal.directory)
        return report

    entries = journal.entries()
    committed = {
        entry.phase for entry in entries if entry.operation == PHASE_COMMITTED
    }

    # 1. Close new intake and stop the new runtime first.
    _log(journal, "rollback-close-new-intake", str(home / ".dan"))
    if CutoverPhase.COLD_STARTED in committed:
        _log(journal, "rollback-stop-runtime", str(manifest.new_root))
        runtime_stopper()

    # 2. Nothing may still be speaking, nowhere.
    for database in (home / ".dan" / "dan.db", *manifest.databases):
        if _speaking_value(database) is not None:
            raise RollbackBlocked(f"speaking marker still set in {database}")

    # 3. Reverse journal order restores adapters, plists, config, DBs, paths.
    for entry in reversed(entries):
        if entry.operation == PHASE_COMMITTED:
            continue
        _undo(entry, journal=journal, home=home, launchctl=launchctl, report=report)

    # 4. Verify the restored legacy state before green-lighting old runtime.
    for database in manifest.databases:
        if _active_request_count(database):
            raise RollbackBlocked(
                f"active requests present in restored {database}; "
                "an interrupted request must stay cancelled, never replayed"
            )
        if _speaking_value(database) is not None:
            raise RollbackBlocked(f"speaking marker reappeared in {database}")

    report.old_runtime_start_allowed = True
    journal.write_report(
        "rollback-report.json",
        {
            "undone": report.undone,
            "old_runtime_start_allowed": True,
        },
    )
    return report


def _log(journal: Journal, operation: str, source: str) -> None:
    journal.append(
        JournalEntry(
            phase=CutoverPhase.VERIFIED,
            operation=operation,
            source=source,
            destination=None,
            before_sha256=None,
            after_sha256=None,
            rollback_operation="none",
        )
    )


def _undo(
    entry: JournalEntry,
    *,
    journal: Journal,
    home: Path,
    launchctl: Callable[..., None],
    report: RollbackReport,
) -> None:
    operation = entry.rollback_operation
    if operation in {"none", "never-replay", "reopen-intake", "stop-runtime"}:
        # never-replay is deliberate: cancellations are permanent.
        return
    if operation == "move-back":
        destination = Path(entry.destination or "")
        source = Path(entry.source or "")
        if destination.exists():
            _case_safe_rename(destination, source)
            report.undone.append(f"moved back {destination} -> {source}")
        return
    if operation == "remove":
        destination = Path(entry.destination or "")
        if destination.exists():
            destination.unlink()
            report.undone.append(f"removed {destination}")
        return
    if operation == "bootstrap":
        launchctl("bootstrap", entry.source or "")
        report.undone.append(f"bootstrap {entry.source}")
        return
    if operation == "bootout":
        launchctl("bootout", entry.source or "")
        report.undone.append(f"bootout {entry.source}")
        return
    if operation.startswith("install-rollback:"):
        report_name = operation.split(":", 1)[1]
        payload = json.loads((journal.directory / report_name).read_text(encoding="utf-8"))
        install_report = InstallReport(
            home=payload["home"],
            backup_root=payload["backup_root"],
            entries=[
                InstallEntry(
                    path=row["path"],
                    backup=row.get("backup"),
                    sha_before=row.get("sha_before"),
                    sha_after=row["sha_after"],
                    operation=row["operation"],
                    inverse=row["inverse"],
                )
                for row in payload["entries"]
            ],
            dirs_created=list(payload.get("dirs_created", [])),
            manifest_path=payload.get("manifest_path"),
        )
        InstallPlan(home).rollback(install_report)
        report.undone.append(f"install rollback via {report_name}")
        return
    raise RollbackBlocked(f"unknown rollback operation in journal: {operation!r}")


__all__ = ["RollbackBlocked", "RollbackReport", "perform_rollback", "rollback_plan"]
