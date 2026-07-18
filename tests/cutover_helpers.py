"""Synthetic cutover fixture: tree builder and test harnesses.

Everything here is fixture-only tooling. It builds a fully synthetic legacy
host layout (old jarvis home, legacy SQLite databases, launchd plists, donor
checkouts) inside a temporary root and drives dan.migration.cutover/rollback
against it with injected fakes. No live process, port, database or real home
path is ever touched.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "cutover"
MANIFEST_PATH = FIXTURE_DIR / "manifest.json"


# --------------------------------------------------------------------- tree
def build_cutover_tree(root: Path) -> None:
    """Materialize the synthetic source tree the committed manifest describes."""

    home = root / "home"
    jarvis_home = home / ".jarvis"
    (jarvis_home / "bin").mkdir(parents=True)
    (jarvis_home / "backups").mkdir()
    (jarvis_home / "requests").mkdir()
    (jarvis_home / "jarvis.toml").write_text(
        '[voice]\nengine = "supertonic"\n', encoding="utf-8"
    )
    (jarvis_home / "model_cache.json").write_text('{"models": []}\n', encoding="utf-8")
    (jarvis_home / "bin" / "jarvisd").write_bytes(b"#!/bin/sh\nexit 0\n")
    (jarvis_home / "backups" / "2026-06-01.meta").write_text(
        "historical backup metadata\n", encoding="utf-8"
    )
    (jarvis_home / "requests" / "req-001.json").write_text(
        '{"id": "req-001", "text": "stale request"}\n', encoding="utf-8"
    )
    _create_legacy_database(jarvis_home / "jarvis.db")

    dan_home = home / ".dan"
    dan_home.mkdir()
    (dan_home / "preexisting-note.txt").write_text(
        "kept non-DB file\n", encoding="utf-8"
    )

    agents = home / "Library" / "LaunchAgents"
    agents.mkdir(parents=True)
    for label in ("com.jarvis.jarvisd", "com.dan.voicebroker"):
        (agents / f"{label}.plist").write_text(
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
            f"<plist><dict><key>Label</key><string>{label}</string></dict></plist>\n",
            encoding="utf-8",
        )

    dev = home / "Documents" / "dev"
    (dev / "dan" / "config" / "persona").mkdir(parents=True)
    (dev / "dan" / "config" / "persona" / "DAN.md").write_text(
        "DAN_CANON_VERSION: 1\nsynthetic persona canon\n", encoding="utf-8"
    )
    (dev / "dan" / "tools").mkdir()
    (dev / "dan" / "tools" / "legacy-broker.py").write_text(
        "# synthetic legacy broker source\n", encoding="utf-8"
    )
    (dev / "jarvis").mkdir()
    (dev / "jarvis" / "README.md").write_text(
        "synthetic accepted integration tree\n", encoding="utf-8"
    )
    (dev / "jarvis" / "pyproject.toml").write_text(
        '[project]\nname = "dan-runtime"\n', encoding="utf-8"
    )
    for donor in ("DANv2", "menubar-controller"):
        (dev / donor).mkdir()
        (dev / donor / "donor.txt").write_text(
            f"{donor} stays untouched\n", encoding="utf-8"
        )


def _create_legacy_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE requests (
              id TEXT PRIMARY KEY,
              status TEXT NOT NULL CHECK (
                status IN ('queued', 'synthesizing', 'speaking',
                           'done', 'cancelled', 'failed')
              ),
              play_count INTEGER NOT NULL DEFAULT 0,
              text TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE runtime_state (key TEXT PRIMARY KEY, value TEXT);
            INSERT INTO runtime_state (key, value) VALUES ('speaking', NULL);
            CREATE TABLE intake_gate (
              singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
              state TEXT NOT NULL CHECK (state IN ('open', 'closed')),
              operation_id TEXT,
              reason TEXT,
              closed_at TEXT,
              reopened_at TEXT,
              reopen_policy TEXT NOT NULL DEFAULT 'daemon'
                CHECK (reopen_policy IN ('daemon', 'external'))
            );
            INSERT INTO intake_gate (
              singleton, state, operation_id, reason, closed_at, reopened_at,
              reopen_policy
            ) VALUES (1, 'open', NULL, NULL, NULL, NULL, 'daemon');
            CREATE TABLE intake_leases (
              token TEXT PRIMARY KEY,
              channel TEXT NOT NULL,
              owner_pid INTEGER NOT NULL,
              acquired_at TEXT NOT NULL
            );
            INSERT INTO requests (id, status, play_count, text)
              VALUES ('req-done-1', 'done', 1, 'finished utterance');
            """
        )
        connection.commit()
    finally:
        connection.close()


def manifest_sha256() -> str:
    return hashlib.sha256(MANIFEST_PATH.read_bytes()).hexdigest()


def tree_hash(home: Path, *, exclude: tuple[str, ...] = ()) -> str:
    """Deterministic digest of paths + contents under home, minus exclusions."""

    excluded = [home / relative for relative in exclude]
    digest = hashlib.sha256()
    for path in sorted(home.rglob("*")):
        if any(path == item or item in path.parents for item in excluded):
            continue
        relative = path.relative_to(home).as_posix()
        digest.update(relative.encode("utf-8"))
        if path.is_symlink():
            digest.update(b"L" + str(path.readlink()).encode("utf-8"))
        elif path.is_file():
            digest.update(b"F" + hashlib.sha256(path.read_bytes()).digest())
        elif path.is_dir():
            digest.update(b"D")
    return digest.hexdigest()


TREE_HASH_EXCLUDE = (
    ".dan/migration",
    "Documents/DAN-migration-backups",
)


# ------------------------------------------------------------------- fakes
@dataclass
class FakeLaunchctl:
    """Records launchctl intents; never runs the real thing."""

    calls: list[tuple[str, ...]] = field(default_factory=list)

    def __call__(self, *arguments: str) -> None:
        if arguments and arguments[0] not in {"bootout", "bootstrap"}:
            raise AssertionError(f"unexpected launchctl verb: {arguments!r}")
        self.calls.append(tuple(arguments))


@dataclass
class FakeRuntime:
    """Records cold start / stop intents for the new runtime."""

    started: list[Path] = field(default_factory=list)
    stopped: int = 0

    def start(self, new_root: Path) -> dict[str, str]:
        self.started.append(Path(new_root))
        return {"health": "ok", "root": str(new_root)}

    def stop(self) -> None:
        self.stopped += 1


class FakeHostAdapter:
    def __init__(
        self,
        database: Path,
        launchctl: FakeLaunchctl,
        runtime: FakeRuntime,
    ) -> None:
        self.database = Path(database)
        self.launchctl = launchctl
        self.runtime = runtime
        self.validations = 0

    @property
    def intake_database(self) -> Path:
        return self.database

    def validate(self, manifest, home: Path, *, operation_id: str) -> None:
        del manifest, home, operation_id
        self.validations += 1

    def close_intake(
        self,
        *,
        operation_id: str,
        reason: str,
        before_close,
    ) -> None:
        from dan.daemon.intake import IntakeGate

        connection = sqlite3.connect(self.database)
        try:
            def record_state(state) -> None:
                closed_at, reopened_at = connection.execute(
                    "SELECT closed_at, reopened_at FROM intake_gate WHERE singleton = 1"
                ).fetchone()
                before_close(
                    self.database,
                    {
                        "state": state.state,
                        "operation_id": state.operation_id,
                        "reason": state.reason,
                        "reopen_policy": state.reopen_policy,
                        "closed_at": closed_at,
                        "reopened_at": reopened_at,
                    },
                )

            IntakeGate(connection).close(
                operation_id=operation_id,
                reason=reason,
                reopen_policy="external",
                before_close=record_state,
            )
        finally:
            connection.close()

    def wait_for_intake_drain(self) -> None:
        from dan.daemon.intake import IntakeGate

        connection = sqlite3.connect(self.database)
        try:
            IntakeGate(connection).wait_for_drain(timeout_seconds=0)
        finally:
            connection.close()

    def stop_launch_agent(self, agent) -> None:
        self.launchctl("bootout", agent.label)

    def bootstrap_launch_agent(self, *, label: str, plist: Path) -> None:
        del plist
        self.launchctl("bootstrap", label)

    def start_runtime(self, new_root: Path) -> dict[str, str]:
        return self.runtime.start(new_root)

    def reopen_intake(self, *, database: Path, operation_id: str) -> None:
        from dan.daemon.intake import IntakeGate

        connection = sqlite3.connect(database)
        try:
            IntakeGate(connection).reopen(operation_id=operation_id)
        finally:
            connection.close()


# ---------------------------------------------------------------- harnesses
class CutoverHarness:
    """Precondition-level harness backing the `cutover` pytest fixture."""

    def __init__(self, root: Path) -> None:
        from dan.migration.cutover import CutoverManifest
        from dan.migration.runtime_probe import FakeProbe

        build_cutover_tree(root)
        self.root = root
        self.home = root / "home"
        self.manifest = CutoverManifest.load(MANIFEST_PATH, root=root)
        self.probe = FakeProbe()
        self.launchctl = FakeLaunchctl()
        self.runtime = FakeRuntime()
        self.host_adapter = FakeHostAdapter(
            self.home / ".jarvis" / "jarvis.db",
            self.launchctl,
            self.runtime,
        )

    # -- fixture mutators used by the plan's test bodies -------------------
    def fixture_queue(self, state: str) -> None:
        connection = sqlite3.connect(self.home / ".jarvis" / "jarvis.db")
        try:
            connection.execute(
                "INSERT INTO requests (id, status, play_count, text) VALUES (?, ?, 0, ?)",
                (f"req-{state}", state, f"synthetic {state} request"),
            )
            connection.commit()
        finally:
            connection.close()

    def fixture_writer(self, pid: int, path: str) -> None:
        from dan.migration.runtime_probe import ProbedHandle

        resolved = Path(path.replace("~", str(self.home), 1))
        self.probe.add_db_handle(resolved, ProbedHandle(pid=pid, command="python"))

    def _engine(self):
        from dan.migration.cutover import CutoverEngine

        return CutoverEngine(
            manifest=self.manifest,
            home=self.home,
            probe=self.probe,
            host_adapter=self.host_adapter,
        )

    def prepare(self) -> None:
        self._engine().prepare()


class CutoverFixture(CutoverHarness):
    """Full apply/rollback harness backing the `cutover_fixture` fixture."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.before_non_intake_hash = tree_hash(
            self.home,
            exclude=(*TREE_HASH_EXCLUDE, ".jarvis/jarvis.db"),
        )
        self.before_intake_dump = self.intake_database_dump()

    # -- observations ------------------------------------------------------
    def tree_hash(self) -> str:
        return tree_hash(self.home, exclude=TREE_HASH_EXCLUDE)

    def tree_hash_without_intake_database(self) -> str:
        return tree_hash(
            self.home,
            exclude=(*TREE_HASH_EXCLUDE, ".jarvis/jarvis.db"),
        )

    def intake_database_dump(self) -> str:
        connection = sqlite3.connect(self._legacy_db())
        try:
            return "\n".join(connection.iterdump())
        finally:
            connection.close()

    def _legacy_db(self) -> Path:
        restored = self.home / ".jarvis" / "jarvis.db"
        if restored.exists():
            return restored
        moved = self.home / "Documents" / "DAN-migration-backups"
        candidates = sorted(moved.rglob("jarvis.db"))
        if candidates:
            return candidates[-1]
        raise FileNotFoundError("legacy jarvis.db not found in fixture tree")

    def request(self, request_id: str):
        connection = sqlite3.connect(self._legacy_db())
        try:
            row = connection.execute(
                "SELECT id, status, play_count FROM requests WHERE id = ?",
                (request_id,),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise LookupError(request_id)

        @dataclass(frozen=True)
        class RequestRow:
            id: str
            status: str
            play_count: int

        return RequestRow(id=row[0], status=row[1], play_count=int(row[2]))

    def play_count(self, request_id: str) -> int:
        return self.request(request_id).play_count

    def runtime_state(self):
        connection = sqlite3.connect(self._legacy_db())
        try:
            row = connection.execute(
                "SELECT value FROM runtime_state WHERE key = 'speaking'"
            ).fetchone()
        finally:
            connection.close()

        @dataclass(frozen=True)
        class RuntimeState:
            speaking: str | None

        return RuntimeState(speaking=row[0] if row else None)

    # -- scenario setup ----------------------------------------------------
    def speaking_request(self) -> str:
        request_id = "req-speaking-1"
        connection = sqlite3.connect(self.home / ".jarvis" / "jarvis.db")
        try:
            connection.execute(
                "INSERT INTO requests (id, status, play_count, text) VALUES (?, 'speaking', 0, ?)",
                (request_id, "interrupted utterance"),
            )
            connection.execute(
                "UPDATE runtime_state SET value = ? WHERE key = 'speaking'",
                (request_id,),
            )
            connection.commit()
        finally:
            connection.close()
        return request_id

    # -- drive the engine --------------------------------------------------
    def apply(self, *, cancel_in_flight: bool = False, interrupt_after=None):
        engine = self._engine()
        return engine.apply(
            manifest_sha256=manifest_sha256(),
            cancel_in_flight=cancel_in_flight,
            interrupt_after=interrupt_after,
        )

    def resume(self, journal_dir: Path, *, cancel_in_flight: bool = False):
        from dan.migration.cutover import CutoverEngine

        engine = CutoverEngine(
            manifest=self.manifest,
            home=self.home,
            probe=self.probe,
            host_adapter=self.host_adapter,
            resume_journal=journal_dir,
        )
        return engine.apply(
            manifest_sha256=manifest_sha256(),
            cancel_in_flight=cancel_in_flight,
        )

    def rollback(self, journal_dir: Path):
        from dan.migration.rollback import perform_rollback

        return perform_rollback(
            journal_dir=Path(journal_dir),
            manifest=self.manifest,
            home=self.home,
            launchctl=self.launchctl,
            runtime_stopper=self.runtime.stop,
            apply_changes=True,
        )

    def latest_journal_dir(self) -> Path:
        candidates = sorted((self.home / ".dan" / "migration").glob("cutover-*"))
        if not candidates:
            raise FileNotFoundError("no cutover journal directory yet")
        return candidates[-1]

    def journal_bytes(self, journal_dir: Path) -> bytes:
        return (Path(journal_dir) / "journal.jsonl").read_bytes()

    def journal_entries(self, journal_dir: Path) -> list[dict]:
        lines = self.journal_bytes(journal_dir).decode("utf-8").splitlines()
        return [json.loads(line) for line in lines if line.strip()]
