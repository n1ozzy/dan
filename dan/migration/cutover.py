"""Journaled, resumable, dry-run-first cutover engine (Task 12).

This module is TOOLING for the future Release 1 cutover (Task 14). It never
touches the live system by itself:

- every observation goes through an injected runtime probe (read-only);
- every ``launchctl`` intent and runtime cold start goes through an injected
  executor — the defaults REFUSE and name Task 14;
- every mutation is journaled with its inverse *before* it executes;
- mutation at all requires ``apply(manifest_sha256=...)`` with the exact
  SHA-256 of the decision manifest; everything else is dry-run.

An unrecognized process or database handle always blocks the cutover.
Nothing is ever killed and no donor tree is ever deleted.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from dan.install import InstallPlan
from dan.migration.journal import CutoverPhase, Journal, JournalEntry, utc_stamp
from dan.migration.sqlite_backup import backup_database

ACTIVE_STATUSES = ("queued", "synthesizing", "speaking")
COMPLETE_DECISIONS = frozenset({"migrated", "disabled", "rejected"})
FILE_DECISIONS = frozenset({"import", "retain", "reject"})
DAND_PLIST_RELPATH = "Library/LaunchAgents/com.dan.dand.plist"
DAND_LABEL = "com.dan.dand"


class CutoverBlocked(RuntimeError):
    """A precondition or safety invariant refuses the cutover."""


class CutoverInterrupted(RuntimeError):
    """Deliberate interruption point (crash simulation for resume tests)."""


# ----------------------------------------------------------------- manifest
@dataclass
class ProducerRow:
    name: str
    decision: str | None
    reason: str


@dataclass(frozen=True)
class FileDecision:
    path: Path
    decision: str
    reason: str


@dataclass(frozen=True)
class LaunchAgent:
    label: str
    plist: Path


class CutoverManifest:
    """Decision manifest resolved against a fixture/host root."""

    def __init__(self, path: Path, root: Path, data: dict) -> None:
        self.path = Path(path)
        self.root = Path(root)
        self.sha256 = hashlib.sha256(self.path.read_bytes()).hexdigest()
        if data.get("schema_version") != 1:
            raise CutoverBlocked(f"unsupported manifest schema: {data.get('schema_version')!r}")
        self.home = self._resolve(data["home"])
        self.producers: dict[str, ProducerRow] = {
            name: ProducerRow(name=name, decision=row.get("decision"), reason=row.get("reason", ""))
            for name, row in data.get("producers", {}).items()
        }
        self.databases: list[Path] = [
            self._resolve(entry["path"]) for entry in data.get("databases", [])
        ]
        paths = data.get("paths", {})
        self.old_dan = self._resolve(paths["old_dan"])
        self.old_jarvis = self._resolve(paths["old_jarvis"])
        self.new_root = self._resolve(paths["new_root"])
        self.backup_root = self._resolve(paths["backup_root"])
        self.donors: list[Path] = [self._resolve(item) for item in paths.get("donors", [])]
        self.launch_agents: list[LaunchAgent] = [
            LaunchAgent(label=row["label"], plist=self._resolve(row["plist"]))
            for row in data.get("launch_agents", [])
        ]
        self.request_files_dir = self._resolve(data["request_files_dir"])
        self.legacy_process_names: tuple[str, ...] = tuple(data.get("legacy_process_names", []))
        self.files: list[FileDecision] = [
            FileDecision(
                path=self._resolve(row["path"]),
                decision=str(row.get("decision", "")),
                reason=str(row.get("reason", "")),
            )
            for row in data.get("files", [])
        ]

    def _resolve(self, relative: str) -> Path:
        candidate = Path(relative)
        if candidate.is_absolute():
            raise CutoverBlocked(f"manifest paths must be root-relative: {relative!r}")
        return self.root / candidate

    @classmethod
    def load(cls, path: Path, *, root: Path) -> "CutoverManifest":
        path = Path(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(path, Path(root), data)


# ------------------------------------------------------------------ engine
def _refuse_launchctl(*arguments: str) -> None:
    raise CutoverBlocked(
        "no launchctl executor injected — real bootout/bootstrap is Task 14 "
        f"(refused: {' '.join(arguments)})"
    )


def _refuse_runtime_start(new_root: Path) -> dict:
    raise CutoverBlocked(
        f"no runtime starter injected — real cold start from {new_root} is Task 14"
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class CutoverReport:
    journal: Path
    phases: list[str] = field(default_factory=list)
    report_path: Path | None = None


class CutoverEngine:
    """Runs the 11 cutover phases against one manifest-described tree."""

    def __init__(
        self,
        *,
        manifest: CutoverManifest,
        home: Path,
        probe,
        launchctl: Callable[..., None] | None = None,
        runtime_starter: Callable[[Path], dict] | None = None,
        resume_journal: Path | None = None,
        now: datetime | None = None,
    ) -> None:
        self.manifest = manifest
        self.home = Path(home)
        self.probe = probe
        self._launchctl = launchctl or _refuse_launchctl
        self._runtime_starter = runtime_starter or _refuse_runtime_start
        self._resume_journal = Path(resume_journal) if resume_journal else None
        self._now = now

    # ------------------------------------------------------------ queries
    def _connect(self, database: Path) -> sqlite3.Connection:
        return sqlite3.connect(database)

    def _active_requests(self, database: Path) -> list[tuple[str, str]]:
        if not database.is_file():
            return []
        connection = self._connect(database)
        try:
            placeholders = ",".join("?" for _ in ACTIVE_STATUSES)
            rows = connection.execute(
                f"SELECT id, status FROM requests WHERE status IN ({placeholders})"
                " ORDER BY id",
                ACTIVE_STATUSES,
            ).fetchall()
        finally:
            connection.close()
        return [(str(row[0]), str(row[1])) for row in rows]

    def _speaking_value(self, database: Path) -> str | None:
        if not database.is_file():
            return None
        connection = self._connect(database)
        try:
            row = connection.execute(
                "SELECT value FROM runtime_state WHERE key = 'speaking'"
            ).fetchone()
        finally:
            connection.close()
        return row[0] if row else None

    # ------------------------------------------------------- preconditions
    def precondition_failures(self, *, allow_active_queue: bool = False) -> list[str]:
        failures: list[str] = []
        for name, producer in sorted(self.manifest.producers.items()):
            if producer.decision not in COMPLETE_DECISIONS:
                failures.append(
                    f"producer {name} has no complete decision "
                    f"(found {producer.decision!r}); every producer row must decide"
                )
        for row in self.manifest.files:
            if row.decision not in FILE_DECISIONS:
                failures.append(
                    f"file {row.path} has no import/retain/reject decision "
                    f"(found {row.decision!r})"
                )
        for database in self.manifest.databases:
            if not allow_active_queue:
                for request_id, status in self._active_requests(database):
                    failures.append(
                        f"non-quiescent queue: request {request_id} is {status} in {database}"
                    )
            for handle in self.probe.db_handles(database):
                failures.append(
                    f"live database writer pid {handle.pid} ({handle.command}) "
                    f"holds {database}"
                )
        for process in self.probe.processes():
            if any(name in process.command for name in self.manifest.legacy_process_names):
                failures.append(
                    f"legacy process still running: pid {process.pid} {process.command}"
                )
            else:
                failures.append(
                    "unrecognized process observed: "
                    f"pid {process.pid} {process.command} — blocking, never killing"
                )
        for listener in self.probe.listeners():
            failures.append(
                f"unexpected listener pid {listener.pid} ({listener.command}) "
                f"on port {listener.port}"
            )
        return failures

    def prepare(self) -> None:
        failures = self.precondition_failures()
        if failures:
            raise CutoverBlocked("; ".join(failures))

    # ------------------------------------------------------------ dry run
    def plan(self) -> dict:
        """Read-only description of state and pending destructive operations."""

        def _describe(path: Path) -> dict:
            return {"path": str(path), "present": path.exists()}

        db_counts: dict[str, dict[str, int]] = {}
        for database in self.manifest.databases:
            if database.is_file():
                connection = self._connect(database)
                try:
                    names = connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                        " AND name NOT LIKE 'sqlite_%'"
                    ).fetchall()
                    db_counts[str(database)] = {
                        str(row[0]): int(
                            connection.execute(
                                f'SELECT COUNT(*) FROM "{row[0]}"'
                            ).fetchone()[0]
                        )
                        for row in names
                    }
                finally:
                    connection.close()
            else:
                db_counts[str(database)] = {}
        pending = [
            f"close old intake at {self.manifest.request_files_dir} and new intake",
            *(
                f"cancel in-flight request {request_id} ({status}) in {database}"
                for database in self.manifest.databases
                for request_id, status in self._active_requests(database)
            ),
            *(
                f"move request files from {self.manifest.request_files_dir} into journal backup"
                for _ in (1,)
            ),
            *(
                f"bootout {agent.label} and park {agent.plist}"
                for agent in self.manifest.launch_agents
            ),
            *(
                f"checkpoint+backup+verify {database}"
                for database in self.manifest.databases
            ),
            f"migrate database copies into {self.home / '.dan' / 'dan.db'}",
            f"move {self.manifest.old_dan} -> {self.manifest.backup_root}/<STAMP>/dev-dan",
            f"move {self.manifest.old_jarvis} -> {self.manifest.new_root} (case-safe temp hop)",
            "install host adapters (InstallPlan, backup-first)",
            f"install {DAND_PLIST_RELPATH} and bootstrap {DAND_LABEL}",
            f"cold start runtime from {self.manifest.new_root}",
            "verify migrated data, donors and journal completeness",
        ]
        return {
            "manifest": str(self.manifest.path),
            "manifest_sha256": self.manifest.sha256,
            "home": str(self.home),
            "producers": {
                name: {"decision": row.decision, "reason": row.reason}
                for name, row in sorted(self.manifest.producers.items())
            },
            "files": [
                {"path": str(row.path), "decision": row.decision, "reason": row.reason}
                for row in self.manifest.files
            ],
            "paths": {
                "old_dan": _describe(self.manifest.old_dan),
                "old_jarvis": _describe(self.manifest.old_jarvis),
                "new_root": _describe(self.manifest.new_root),
                "backup_root": _describe(self.manifest.backup_root),
                "donors": [_describe(path) for path in self.manifest.donors],
            },
            "launch_agents": [
                {"label": agent.label, "plist": _describe(agent.plist)}
                for agent in self.manifest.launch_agents
            ],
            "processes": [
                {"pid": process.pid, "command": process.command}
                for process in self.probe.processes()
            ],
            "db_counts": db_counts,
            "precondition_failures": self.precondition_failures(),
            "pending_destructive_operations": pending,
        }

    # -------------------------------------------------------------- apply
    def apply(
        self,
        *,
        manifest_sha256: str,
        cancel_in_flight: bool = False,
        interrupt_after: CutoverPhase | None = None,
    ) -> CutoverReport:
        if manifest_sha256 != self.manifest.sha256:
            raise CutoverBlocked(
                "stale manifest SHA-256: apply requires the exact digest "
                f"{self.manifest.sha256}, got {manifest_sha256}"
            )
        failures = self.precondition_failures(allow_active_queue=cancel_in_flight)
        if failures:
            raise CutoverBlocked("; ".join(failures))

        if self._resume_journal is not None:
            journal = Journal.open(self._resume_journal)
        else:
            journal = Journal.create(self.home / ".dan" / "migration", now=self._now)
        committed = set(journal.committed_phases())

        phase_handlers: list[tuple[CutoverPhase, Callable[[Journal], None]]] = [
            (CutoverPhase.INVENTORIED, self._phase_inventoried),
            (CutoverPhase.INTAKE_CLOSED, self._phase_intake_closed),
            (
                CutoverPhase.QUEUE_QUIESCENT,
                lambda j: self._phase_queue_quiescent(j, cancel_in_flight=cancel_in_flight),
            ),
            (CutoverPhase.RUNTIME_STOPPED, self._phase_runtime_stopped),
            (CutoverPhase.DATABASES_BACKED_UP, self._phase_databases_backed_up),
            (CutoverPhase.DATABASES_MIGRATED, self._phase_databases_migrated),
            (CutoverPhase.PATHS_MOVED, self._phase_paths_moved),
            (CutoverPhase.ADAPTERS_INSTALLED, self._phase_adapters_installed),
            (CutoverPhase.LAUNCHD_INSTALLED, self._phase_launchd_installed),
            (CutoverPhase.COLD_STARTED, self._phase_cold_started),
            (CutoverPhase.VERIFIED, self._phase_verified),
        ]
        report = CutoverReport(journal=journal.directory)
        for phase, handler in phase_handlers:
            if phase in committed:
                report.phases.append(phase.value)
                continue
            handler(journal)
            journal.commit_phase(phase)
            report.phases.append(phase.value)
            if interrupt_after is phase:
                raise CutoverInterrupted(f"interrupted after {phase.value}")
        report.report_path = journal.directory / "report.json"
        return report

    # ------------------------------------------------------------- phases
    def _note(self, journal: Journal, phase: CutoverPhase, text: str) -> None:
        journal.append(
            JournalEntry(
                phase=phase,
                operation="note",
                source=text,
                destination=None,
                before_sha256=None,
                after_sha256=None,
                rollback_operation="none",
            )
        )

    def _journal_move(
        self,
        journal: Journal,
        phase: CutoverPhase,
        operation: str,
        source: Path,
        destination: Path,
    ) -> None:
        """Record the inverse, then move. Files hash; trees journal by path."""

        before = _sha256_file(source) if source.is_file() else None
        journal.append(
            JournalEntry(
                phase=phase,
                operation=operation,
                source=str(source),
                destination=str(destination),
                before_sha256=before,
                after_sha256=before,
                rollback_operation="move-back",
            )
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        _case_safe_rename(source, destination)

    def _phase_inventoried(self, journal: Journal) -> None:
        inventory = {
            "old_dan": self.manifest.old_dan.exists(),
            "old_jarvis": self.manifest.old_jarvis.exists(),
            "donors": {str(path): path.exists() for path in self.manifest.donors},
            "databases": {str(path): path.is_file() for path in self.manifest.databases},
            "launch_agents": {
                agent.label: agent.plist.is_file() for agent in self.manifest.launch_agents
            },
        }
        missing = [
            str(path)
            for path in (
                self.manifest.old_dan,
                self.manifest.old_jarvis,
                *self.manifest.donors,
                *self.manifest.databases,
            )
            if not path.exists()
        ]
        if missing:
            raise CutoverBlocked(f"inventory missing required sources: {', '.join(missing)}")
        journal.write_report("inventory.json", inventory)
        self._note(journal, CutoverPhase.INVENTORIED, "inventory recorded")

    def _phase_intake_closed(self, journal: Journal) -> None:
        journal.append(
            JournalEntry(
                phase=CutoverPhase.INTAKE_CLOSED,
                operation="close-intake",
                source=str(self.manifest.request_files_dir),
                destination=None,
                before_sha256=None,
                after_sha256=None,
                rollback_operation="reopen-intake",
            )
        )
        journal.append(
            JournalEntry(
                phase=CutoverPhase.INTAKE_CLOSED,
                operation="close-intake",
                source=str(self.home / ".dan"),
                destination=None,
                before_sha256=None,
                after_sha256=None,
                rollback_operation="reopen-intake",
            )
        )

    def _phase_queue_quiescent(self, journal: Journal, *, cancel_in_flight: bool) -> None:
        for database in self.manifest.databases:
            active = self._active_requests(database)
            if active and not cancel_in_flight:
                described = ", ".join(f"{rid} ({status})" for rid, status in active)
                raise CutoverBlocked(
                    f"non-quiescent queue in {database}: {described}; "
                    "pass cancel_in_flight to drain with explicit cancellation"
                )
            if active:
                connection = self._connect(database)
                try:
                    for request_id, _status in active:
                        # Inverse recorded first — and the inverse of a
                        # cancellation is NEVER a replay.
                        journal.append(
                            JournalEntry(
                                phase=CutoverPhase.QUEUE_QUIESCENT,
                                operation="cancel-request",
                                source=request_id,
                                destination=str(database),
                                before_sha256=None,
                                after_sha256=None,
                                rollback_operation="never-replay",
                            )
                        )
                        connection.execute(
                            "UPDATE requests SET status = 'cancelled' WHERE id = ?",
                            (request_id,),
                        )
                    if self._speaking_value(database) is not None:
                        journal.append(
                            JournalEntry(
                                phase=CutoverPhase.QUEUE_QUIESCENT,
                                operation="clear-speaking",
                                source=str(database),
                                destination=None,
                                before_sha256=None,
                                after_sha256=None,
                                rollback_operation="never-replay",
                            )
                        )
                        connection.execute(
                            "UPDATE runtime_state SET value = NULL WHERE key = 'speaking'"
                        )
                    connection.commit()
                finally:
                    connection.close()
            if self._speaking_value(database) is not None:
                raise CutoverBlocked(f"speaking marker still set in {database}")

        # Old request files: recorded, then moved — never blindly removed.
        request_backup = journal.directory / "request-files"
        moved: list[dict[str, str]] = []
        if self.manifest.request_files_dir.is_dir():
            for item in sorted(self.manifest.request_files_dir.iterdir()):
                destination = request_backup / item.name
                moved.append({"source": str(item), "destination": str(destination)})
                self._journal_move(
                    journal, CutoverPhase.QUEUE_QUIESCENT, "move", item, destination
                )
        backup_manifest = {
            "request_files": moved,
            "files": [
                {
                    "path": str(row.path),
                    "present": row.path.exists(),
                    "sha256": _sha256_file(row.path) if row.path.is_file() else None,
                    "decision": row.decision,
                    "reason": row.reason,
                }
                for row in self.manifest.files
            ],
        }
        journal.write_report("backup-manifest.json", backup_manifest)

    def _phase_runtime_stopped(self, journal: Journal) -> None:
        plist_backup = journal.directory / "launchagents"
        for agent in self.manifest.launch_agents:
            journal.append(
                JournalEntry(
                    phase=CutoverPhase.RUNTIME_STOPPED,
                    operation="bootout",
                    source=agent.label,
                    destination=str(agent.plist),
                    before_sha256=None,
                    after_sha256=None,
                    rollback_operation="bootstrap",
                )
            )
            self._launchctl("bootout", agent.label)
            if agent.plist.is_file():
                self._journal_move(
                    journal,
                    CutoverPhase.RUNTIME_STOPPED,
                    "move",
                    agent.plist,
                    plist_backup / agent.plist.name,
                )
        # Proof of absence through the probe — an unrecognized survivor blocks.
        failures = []
        for process in self.probe.processes():
            failures.append(f"process still present: pid {process.pid} {process.command}")
        for database in self.manifest.databases:
            for handle in self.probe.db_handles(database):
                failures.append(
                    f"database writer still present: pid {handle.pid} on {database}"
                )
        for listener in self.probe.listeners():
            failures.append(f"listener still present: port {listener.port}")
        if failures:
            raise CutoverBlocked("; ".join(failures))

    def _phase_databases_backed_up(self, journal: Journal) -> None:
        backups: list[dict] = []
        backup_dir = journal.directory / "db-backups"
        for database in self.manifest.databases:
            destination = backup_dir / database.name
            journal.append(
                JournalEntry(
                    phase=CutoverPhase.DATABASES_BACKED_UP,
                    operation="backup-db",
                    source=str(database),
                    destination=str(destination),
                    before_sha256=_sha256_file(database),
                    after_sha256=None,
                    rollback_operation="none",
                )
            )
            report = backup_database(database, destination, approved_pids={os.getpid()})
            backups.append(
                {
                    "source": report.source,
                    "destination": report.destination,
                    "integrity": report.integrity,
                    "counts": dict(report.source_counts),
                    "sha256": report.sha256,
                }
            )
        journal.write_report("db-backups.json", {"backups": backups})

    def _phase_databases_migrated(self, journal: Journal) -> None:
        staging_dir = journal.directory / "staging"
        staging_dir.mkdir(mode=0o700, exist_ok=True)
        staging = staging_dir / "dan.db"
        staging.unlink(missing_ok=True)
        target = sqlite3.connect(staging)
        try:
            target.executescript(
                """
                CREATE TABLE requests (
                  id TEXT PRIMARY KEY,
                  status TEXT NOT NULL,
                  play_count INTEGER NOT NULL DEFAULT 0,
                  text TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE runtime_state (key TEXT PRIMARY KEY, value TEXT);
                INSERT INTO runtime_state (key, value) VALUES ('speaking', NULL);
                """
            )
            for database in self.manifest.databases:
                copy = journal.directory / "db-backups" / database.name
                source = sqlite3.connect(copy)
                try:
                    rows = source.execute(
                        "SELECT id, status, play_count, text FROM requests"
                    ).fetchall()
                finally:
                    source.close()
                for row in rows:
                    if row[1] in ACTIVE_STATUSES:
                        raise CutoverBlocked(
                            f"migration found active request {row[0]} ({row[1]}) "
                            f"in backup of {database}; queue was not quiescent"
                        )
                target.executemany(
                    "INSERT INTO requests (id, status, play_count, text)"
                    " VALUES (?, ?, ?, ?)",
                    rows,
                )
            target.commit()
            integrity = target.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise CutoverBlocked(f"migrated database integrity: {integrity}")
        finally:
            target.close()
        os.chmod(staging, 0o600)

        destination = self.home / ".dan" / "dan.db"
        if destination.exists():
            raise CutoverBlocked(f"target database already exists: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        journal.append(
            JournalEntry(
                phase=CutoverPhase.DATABASES_MIGRATED,
                operation="install-db",
                source=str(staging),
                destination=str(destination),
                before_sha256=None,
                after_sha256=_sha256_file(staging),
                rollback_operation="remove",
            )
        )
        temporary = destination.parent / f".{destination.name}.cutover-tmp"
        shutil.copy2(staging, temporary)
        os.replace(temporary, destination)

    def _phase_paths_moved(self, journal: Journal) -> None:
        stamp = utc_stamp(self._now)
        parked = self.manifest.backup_root / stamp / "dev-dan"
        # Order matters on a case-insensitive filesystem: park dev/dan first,
        # only then may dev/jarvis become dev/DAN.
        self._journal_move(
            journal, CutoverPhase.PATHS_MOVED, "move-tree", self.manifest.old_dan, parked
        )
        self._journal_move(
            journal,
            CutoverPhase.PATHS_MOVED,
            "move-tree",
            self.manifest.old_jarvis,
            self.manifest.new_root,
        )
        missing_donors = [str(path) for path in self.manifest.donors if not path.exists()]
        if missing_donors:
            raise CutoverBlocked(f"donor trees disappeared: {', '.join(missing_donors)}")

    def _install(self, journal: Journal, *, launchd_only: bool, report_name: str) -> None:
        plan = InstallPlan(self.home, include_launchd=launchd_only)
        if launchd_only:
            plan.items = [item for item in plan.items if item.relpath == DAND_PLIST_RELPATH]
        preflight = plan.preflight()
        if not preflight.ok:
            raise CutoverBlocked(f"install preflight failed: {preflight.to_dict()}")
        staging = journal.directory / "staging" / report_name.replace(".json", "")
        plan.render(staging)
        plan.verify(staging)
        journal.append(
            JournalEntry(
                phase=(
                    CutoverPhase.LAUNCHD_INSTALLED
                    if launchd_only
                    else CutoverPhase.ADAPTERS_INSTALLED
                ),
                operation="install-launchd" if launchd_only else "install-adapters",
                source=str(staging),
                destination=str(self.home),
                before_sha256=None,
                after_sha256=None,
                rollback_operation=f"install-rollback:{report_name}",
            )
        )
        report = plan.apply(journal.directory / "install-backups")
        payload = report.to_dict()
        payload["manifest_path"] = report.manifest_path
        journal.write_report(report_name, payload)

    def _phase_adapters_installed(self, journal: Journal) -> None:
        self._install(journal, launchd_only=False, report_name="install-report-adapters.json")

    def _phase_launchd_installed(self, journal: Journal) -> None:
        self._install(journal, launchd_only=True, report_name="install-report-launchd.json")
        journal.append(
            JournalEntry(
                phase=CutoverPhase.LAUNCHD_INSTALLED,
                operation="bootstrap",
                source=DAND_LABEL,
                destination=str(self.home / DAND_PLIST_RELPATH),
                before_sha256=None,
                after_sha256=None,
                rollback_operation="bootout",
            )
        )
        self._launchctl("bootstrap", DAND_LABEL)

    def _phase_cold_started(self, journal: Journal) -> None:
        journal.append(
            JournalEntry(
                phase=CutoverPhase.COLD_STARTED,
                operation="cold-start",
                source=str(self.manifest.new_root),
                destination=None,
                before_sha256=None,
                after_sha256=None,
                rollback_operation="stop-runtime",
            )
        )
        health = self._runtime_starter(self.manifest.new_root)
        journal.write_report("cold-start.json", {"health": health})

    def _phase_verified(self, journal: Journal) -> None:
        problems: list[str] = []
        target = self.home / ".dan" / "dan.db"
        counts: dict[str, int] = {}
        if not target.is_file():
            problems.append(f"migrated database missing: {target}")
        else:
            connection = self._connect(target)
            try:
                integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
                if integrity != "ok":
                    problems.append(f"dan.db integrity: {integrity}")
                counts["requests"] = int(
                    connection.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
                )
                active = connection.execute(
                    "SELECT COUNT(*) FROM requests WHERE status IN (?, ?, ?)",
                    ACTIVE_STATUSES,
                ).fetchone()[0]
                if int(active):
                    problems.append(f"{active} active request(s) leaked into dan.db")
                replayed = connection.execute(
                    "SELECT COUNT(*) FROM requests"
                    " WHERE status = 'cancelled' AND play_count > 0"
                ).fetchone()[0]
                if int(replayed):
                    problems.append("a cancelled request shows play_count > 0")
            finally:
                connection.close()
        if not self.manifest.new_root.exists():
            problems.append(f"new root missing: {self.manifest.new_root}")
        for donor in self.manifest.donors:
            if not donor.exists():
                problems.append(f"donor missing after cutover: {donor}")
        if problems:
            raise CutoverBlocked("; ".join(problems))
        journal.write_report(
            "report.json",
            {
                "verified": True,
                "dan_db": str(target),
                "counts": counts,
                "new_root": str(self.manifest.new_root),
                "phases": [phase.value for phase in journal.committed_phases()]
                + [CutoverPhase.VERIFIED.value],
            },
        )


# ------------------------------------------------------------- move helper
def _case_safe_rename(source: Path, destination: Path) -> None:
    """Rename through a sibling temp name so case-only renames survive on
    case-insensitive filesystems (dev/dan vs dev/DAN)."""

    if not source.exists():
        raise CutoverBlocked(f"move source missing: {source}")
    if destination.exists() and not _same_file(source, destination):
        raise CutoverBlocked(f"move destination already exists: {destination}")
    temporary = source.parent / f".cutover-rename-{os.getpid()}-{source.name}"
    while temporary.exists():
        temporary = temporary.parent / f".{temporary.name}x"
    os.rename(source, temporary)
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.rename(temporary, destination)
    except BaseException:
        os.rename(temporary, source)
        raise


def _same_file(left: Path, right: Path) -> bool:
    try:
        return left.stat().st_ino == right.stat().st_ino and (
            left.stat().st_dev == right.stat().st_dev
        )
    except OSError:
        return False


__all__ = [
    "ACTIVE_STATUSES",
    "CutoverBlocked",
    "CutoverEngine",
    "CutoverInterrupted",
    "CutoverManifest",
    "CutoverReport",
    "FileDecision",
    "LaunchAgent",
    "ProducerRow",
]
