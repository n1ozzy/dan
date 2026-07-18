# DAN Release 1 Audit Remediation — Batch 1 Data and Cutover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure that backup, migration, cutover, resume, and rollback protect the complete SQLite family, block new intake through a real gate, use the actual data migrator, and leave fsynced, reconcilable evidence of every operation.

**Architecture:** One library defines the exact `main/-wal/-shm/-journal` family; another produces deterministic database proofs. `IntakeGate` is durable state in `dan.db` and applies only to new intake. Cutover requires an explicit `CutoverHostAdapter` before the first mutation. The journal uses an FSM and append-only transitions; resume reconciles the recorded state against disk. Rollback executes recorded inverse operations on verified paths.

**Tech Stack:** Python 3.11+, SQLite Backup API/WAL, dataclasses, Protocol/StrEnum, JSONL, fsync, pytest, ruff.

## Global Constraints

- This batch begins only after Batch 0 is GREEN and a checkpoint is bound to the current SHA.
- Do not operate on real databases, invoke `launchctl`, or touch production. Every test uses fixtures and a temporary HOME.
- Do not use globs for SQLite families. Every path must be resolved beneath an explicit allowed root before mutation.
- Manifest v2 requires exactly one database with the `jarvis` role and one with the `memory` role; historical manifests may be read for reporting, but they must not be resumed unless their roles are unambiguous.
- Intake covers new text, voice, and external speak requests. Status, cancellation, flush, gate control, and rollback remain available.
- Closed intake returns HTTP `503` with the stable `intake_closed` code.
- Intake opens only after `VERIFIED`; a failure or resume mismatch leaves it closed and marks the operation `BLOCKED`.
- Byte-for-byte sidecar restoration is permitted only for a hash-bound, quiescent family snapshot. Otherwise, restore the verified main database and explicitly remove the sidecars recorded in the journal.

---

## Task 1.1: Model exact SQLite families

**Files:**

- Create: `dan/migration/sqlite_family.py`
- Create: `tests/test_sqlite_family.py`
- Modify: `tests/test_sqlite_backup.py`

- [ ] **Step 1: Write RED tests**

```python
def test_family_has_exact_four_members(tmp_path: Path) -> None:
    family = resolve_database_family(tmp_path / "dan.db", allowed_roots=(tmp_path,))
    assert family.members() == (
        tmp_path / "dan.db",
        tmp_path / "dan.db-wal",
        tmp_path / "dan.db-shm",
        tmp_path / "dan.db-journal",
    )


def test_family_rejects_path_outside_allowed_root(tmp_path: Path) -> None:
    with pytest.raises(UnsafeDatabasePath):
        resolve_database_family(tmp_path.parent / "outside.db", allowed_roots=(tmp_path,))
```

- [ ] **Step 2: Verify RED**

```bash
env HOME=/private/tmp/dan-partia1-home DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_sqlite_family.py
```

Expected: import failure.

- [ ] **Step 3: Implement the exact family contract**

```python
@dataclass(frozen=True)
class DatabaseFamily:
    main: Path
    wal: Path
    shm: Path
    rollback_journal: Path

    def members(self) -> tuple[Path, Path, Path, Path]:
        return self.main, self.wal, self.shm, self.rollback_journal


def resolve_database_family(database: Path, *, allowed_roots: Sequence[Path]) -> DatabaseFamily:
    main = database.resolve(strict=False)
    roots = tuple(root.resolve(strict=True) for root in allowed_roots)
    if not any(main == root or main.is_relative_to(root) for root in roots):
        raise UnsafeDatabasePath(str(main))
    return DatabaseFamily(main, Path(f"{main}-wal"), Path(f"{main}-shm"), Path(f"{main}-journal"))
```

Do not accept a database path already ending in a sidecar suffix. `existing_family_members()` preserves the four-member order and returns only regular non-symlink files.

- [ ] **Step 4: Verify GREEN and review**

```bash
env HOME=/private/tmp/dan-partia1-home DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_sqlite_family.py tests/test_sqlite_backup.py
.venv/bin/ruff check dan/migration/sqlite_family.py tests/test_sqlite_family.py
git diff --check
```

## Task 1.2: Produce deterministic SQLite proofs and harden snapshots

**Files:**

- Create: `dan/migration/sqlite_validation.py`
- Create: `tests/test_sqlite_validation.py`
- Modify: `dan/migration/sqlite_backup.py`
- Modify: `tests/test_sqlite_backup.py`

- [ ] **Step 1: Write RED proof tests**

```python
def test_canonical_digest_is_stable_across_insertion_order(tmp_path: Path) -> None:
    left = make_database(tmp_path / "left.db", rows=((2, "b"), (1, "a")))
    right = make_database(tmp_path / "right.db", rows=((1, "a"), (2, "b")))
    assert prove_database(left).canonical_data_sha256 == prove_database(right).canonical_data_sha256


def test_backup_rejects_busy_checkpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sqlite_backup, "checkpoint", lambda _: (1, 10, 4))
    with pytest.raises(IncompleteCheckpointError):
        backup_database(source_fixture(tmp_path), tmp_path / "copy.db")
```

Add tests distinguishing SQLite NULL/integer/real/text/blob types, missing required tables, FK violations, second-lsof writer detection and incomplete `log != checkpointed`.

- [ ] **Step 2: Verify RED**

```bash
env HOME=/private/tmp/dan-partia1-home DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_sqlite_validation.py tests/test_sqlite_backup.py
```

Expected: missing proof module and current checkpoint acceptance failures.

- [ ] **Step 3: Implement typed canonical proofs**

```python
@dataclass(frozen=True)
class SQLiteProof:
    integrity: str
    foreign_key_violations: tuple[tuple[object, ...], ...]
    table_schemas: Mapping[str, str]
    table_counts: Mapping[str, int]
    canonical_data_sha256: Mapping[str, str]


def encode_sqlite_value(value: object) -> bytes:
    if value is None:
        return b"n:"
    if isinstance(value, bytes):
        return b"b:" + value.hex().encode("ascii")
    if isinstance(value, int):
        return f"i:{value}".encode("ascii")
    if isinstance(value, float):
        return f"r:{value.hex()}".encode("ascii")
    return b"t:" + str(value).encode("utf-8")
```

Sort tables/columns deterministically and rows by the tuple of encoded values. `prove_database()` must require `integrity_check == "ok"`, empty `foreign_key_check`, and every required table. `compare_database_proofs()` raises on schema, count or digest drift.

`backup_database()` must:

1. validate exact family and lsof handles;
2. open source read-only;
3. run checkpoint and require `(busy == 0 and log == checkpointed)` or valid non-WAL `(-1, -1)`;
4. repeat quiescence check immediately before Backup API;
5. backup to a temporary target under the destination directory;
6. prove source and copy;
7. fsync file and directory before atomic install;
8. remove the complete temporary family on failure.

- [ ] **Step 4: Verify GREEN and focused regression**

```bash
env HOME=/private/tmp/dan-partia1-home DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_sqlite_family.py tests/test_sqlite_validation.py tests/test_sqlite_backup.py
.venv/bin/ruff check dan/migration/sqlite_family.py dan/migration/sqlite_validation.py \
  dan/migration/sqlite_backup.py tests/test_sqlite_validation.py tests/test_sqlite_backup.py
git diff --check
```

## Task 1.3: Harden Task 3 legacy migration and reports

**Files:**

- Modify: `dan/migration/legacy_data.py`
- Modify: `dan/migration/db_report.py`
- Modify: `tests/test_legacy_data_migration.py`
- Modify: `tests/test_db_schema.py`

- [ ] **Step 1: Write RED allowlist and proof tests**

```python
def test_memory_schema_rejects_unknown_memory_fts_prefixed_table(tmp_path: Path) -> None:
    db = memory_fixture(tmp_path)
    execute(db, "CREATE TABLE memory_fts_surprise(payload TEXT)")
    with pytest.raises(UnexpectedMemoryTable):
        migrate_databases(jarvis_fixture(tmp_path), db, tmp_path / "target.db")


def test_migration_proves_required_schema_counts_and_data(tmp_path: Path) -> None:
    _, report = migrate_fixture(tmp_path)
    compare_database_proofs(report.expected_target, report.target, required_tables=report.required_tables)
```

- [ ] **Step 2: Verify RED and implement exact FTS sidecars**

```python
MEMORY_FTS5_SIDECARS = frozenset({
    "memory_fts_data",
    "memory_fts_idx",
    "memory_fts_content",
    "memory_fts_docsize",
    "memory_fts_config",
})
```

`_validate_memory_schema()` must reject every unexpected table including any other `memory_fts_*`. Extend `MemoryMigrationReport` and `DatabaseMigrationReport` additively with source/target proofs. The renderer emits integrity, FK status, schema/count/digest evidence but no raw user rows.

- [ ] **Step 3: Verify GREEN**

```bash
env HOME=/private/tmp/dan-partia1-home DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_legacy_data_migration.py tests/test_db_schema.py
.venv/bin/ruff check dan/migration/legacy_data.py dan/migration/db_report.py \
  tests/test_legacy_data_migration.py
git diff --check
```

## Task 1.4: Add a durable intake gate

**Files:**

- Modify: `dan/store/schema.sql`
- Modify: `dan/store/migrations.py`
- Create: `dan/daemon/intake.py`
- Create: `dan/api/routes_intake.py`
- Modify: `dan/daemon/app.py`
- Modify: `dan/api/routes_voice.py`
- Modify: `dan/daemon/lifecycle.py`
- Create: `tests/test_intake_gate.py`
- Create: `tests/test_intake_api.py`
- Modify: `tests/test_text_turn_pipeline.py`
- Modify: `tests/test_voice_api_contract.py`

- [ ] **Step 1: Write RED lifecycle tests**

```python
def test_closed_intake_rejects_text_before_turn_or_event_write(app: DaemonApp) -> None:
    before = app.store.count_events()
    app.intake_gate.close(operation_id="op-1", reason="cutover")
    with pytest.raises(IntakeClosedError):
        app.handle_text_input("blocked")
    assert app.store.count_events() == before


def test_operation_admitted_before_close_may_finish(gate: IntakeGate) -> None:
    with gate.admit(source="text"):
        gate.close(operation_id="op-1", reason="cutover")
    assert gate.snapshot().state is IntakeState.CLOSED
```

Also test restart persistence, idempotent same-operation close, mismatched open rejection, cancel/status availability and stable API `503/intake_closed`.

- [ ] **Step 2: Verify RED**

```bash
env HOME=/private/tmp/dan-partia1-home DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_intake_gate.py tests/test_intake_api.py tests/test_text_turn_pipeline.py \
  tests/test_voice_api_contract.py
```

- [ ] **Step 3: Implement schema v6 and gate**

```python
class IntakeState(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


@dataclass(frozen=True)
class IntakeSnapshot:
    state: IntakeState
    operation_id: str | None
    reason: str | None
    revision: int
    updated_at: str


class IntakeGate:
    def snapshot(self) -> IntakeSnapshot: ...
    def close(self, *, operation_id: str, reason: str) -> IntakeSnapshot: ...
    def open(self, *, operation_id: str) -> IntakeSnapshot: ...
    def admit(self, *, source: str) -> ContextManager[IntakeLease]: ...
```

`LATEST_SCHEMA_VERSION = 6`; one-row table stores state, operation id, reason, revision and timestamp. `DaemonApp.handle_text_input`, `_start_voice_turn` and external `post_voice_speak` acquire a lease before their first write. Do not guard internal TTS for an already admitted turn.

Routes:

- `GET /runtime/intake`
- `POST /runtime/intake/close`
- `POST /runtime/intake/open`

- [ ] **Step 4: Verify GREEN and review failure ordering**

```bash
env HOME=/private/tmp/dan-partia1-home DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_intake_gate.py tests/test_intake_api.py tests/test_text_turn_pipeline.py \
  tests/test_voice_api_contract.py tests/test_db_schema.py
.venv/bin/ruff check dan/daemon/intake.py dan/api/routes_intake.py dan/daemon/app.py \
  dan/api/routes_voice.py dan/daemon/lifecycle.py dan/store/migrations.py tests/test_intake_gate.py
git diff --check
```

## Task 1.5: Require an explicit host adapter before cutover mutation

**Files:**

- Create: `dan/migration/host_adapter.py`
- Modify: `dan/migration/cutover.py`
- Modify: `dan/migration/rollback.py`
- Modify: `tests/cutover_helpers.py`
- Create: `tests/test_cutover_host_adapter.py`
- Modify: `tests/test_cutover_preconditions.py`

- [ ] **Step 1: Write RED fail-before-mutation tests**

```python
def test_apply_without_host_adapter_blocks_before_journal_or_mutation(tmp_path: Path) -> None:
    engine = cutover_engine(tmp_path, host=None)
    with pytest.raises(MissingHostAdapter):
        engine.apply()
    assert not engine.journal_root.exists()
    assert filesystem_digest(tmp_path) == engine.initial_digest


def test_dry_run_without_host_adapter_is_nonmutating(tmp_path: Path) -> None:
    assert cutover_engine(tmp_path, host=None).plan().mutations == ()
```

- [ ] **Step 2: Verify RED and implement the protocol**

```python
class RuntimeTarget(StrEnum):
    LEGACY = "legacy"
    DAN = "dan"


class CutoverHostAdapter(Protocol):
    def close_intake(self, *, operation_id: str, target: RuntimeTarget) -> Mapping[str, object]: ...
    def intake_state(self, *, target: RuntimeTarget) -> Mapping[str, object]: ...
    def open_intake(self, *, operation_id: str, target: RuntimeTarget) -> Mapping[str, object]: ...
    def stop_runtime(self, *, target: RuntimeTarget) -> None: ...
    def start_runtime(self, *, target: RuntimeTarget, root: Path) -> Mapping[str, object]: ...
    def launchctl(self, *arguments: str) -> None: ...
```

Replace separate `launchctl`/`runtime_starter` injections with `host`. `apply()` checks it before `Journal.create()`, queue cancellation or filesystem write. `prepare()`, `plan()` and dry-run remain read-only without a host.

- [ ] **Step 3: Verify GREEN**

```bash
env HOME=/private/tmp/dan-partia1-home DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_cutover_host_adapter.py tests/test_cutover_preconditions.py
.venv/bin/ruff check dan/migration/host_adapter.py dan/migration/cutover.py \
  dan/migration/rollback.py tests/test_cutover_host_adapter.py
git diff --check
```

## Task 1.6: Add durable journal states and resume reconciliation

**Files:**

- Modify: `dan/migration/journal.py`
- Modify: `dan/migration/cutover.py`
- Create: `tests/test_cutover_journal.py`
- Create: `tests/test_cutover_resume_reconciliation.py`
- Modify: `tests/test_cutover_state_machine.py`

- [ ] **Step 1: Write RED FSM and fsync tests**

```python
def test_resume_rejects_non_prefix_committed_phases(journal: Journal) -> None:
    journal.append_phase_commit(CutoverPhase.INVENTORIED)
    journal.append_phase_commit(CutoverPhase.DATABASES_MIGRATED)
    with pytest.raises(InvalidJournalSequence):
        journal.resume_state(CUTOVER_PHASE_ORDER)


def test_resume_blocks_when_backup_hash_disagrees_with_disk(engine: CutoverEngine) -> None:
    engine.commit_through(CutoverPhase.DATABASES_BACKED_UP)
    engine.backup_path.write_bytes(b"drift")
    assert engine.resume().operation_state is OperationState.BLOCKED
```

Mock `os.fsync` to prove both file descriptor and parent directory are synced for create, append, state and report writes.

- [ ] **Step 2: Implement operation FSM**

```python
class OperationState(StrEnum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMMITTED = "committed"
    ROLLING_BACK = "rolling_back"
    ROLLED_BACK = "rolled_back"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class ResumeState:
    operation_state: OperationState
    completed_prefix: tuple[CutoverPhase, ...]
    next_phase: CutoverPhase | None
```

The parser rejects duplicate commits, missing prefixes, impossible state transitions and unknown schema for resume. It may render old journals read-only, but must mark resume as blocked. Before skipping any committed phase, the engine recomputes and compares its disk evidence. Add `INTAKE_REOPENED` after `VERIFIED`.

- [ ] **Step 3: Verify GREEN**

```bash
env HOME=/private/tmp/dan-partia1-home DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_cutover_journal.py tests/test_cutover_resume_reconciliation.py \
  tests/test_cutover_state_machine.py
.venv/bin/ruff check dan/migration/journal.py dan/migration/cutover.py \
  tests/test_cutover_journal.py tests/test_cutover_resume_reconciliation.py
git diff --check
```

## Task 1.7: Connect the real Task 3 migrator to cutover

**Files:**

- Modify: `dan/migration/cutover.py`
- Modify: `tests/fixtures/cutover/manifest.json`
- Modify: cutover fixture database builders
- Modify: `tests/test_cutover_state_machine.py`
- Modify: `tests/test_cutover_no_replay.py`

- [ ] **Step 1: Write RED integration tests**

```python
def test_cutover_uses_task3_migrator_and_preserves_full_jarvis_schema(engine: CutoverEngine) -> None:
    report = engine.apply()
    assert report.database_migration.jarvis.schema_preserved is True
    assert "requests" in report.database_migration.target.table_schemas
    assert "memory_items" in report.database_migration.target.table_schemas


def test_manifest_v2_requires_one_jarvis_and_one_memory_database(tmp_path: Path) -> None:
    with pytest.raises(ManifestRoleError):
        load_manifest(manifest_with_duplicate_role(tmp_path, "jarvis"))
```

- [ ] **Step 2: Implement role-bound sources**

```python
class DatabaseRole(StrEnum):
    JARVIS = "jarvis"
    MEMORY = "memory"


@dataclass(frozen=True)
class DatabaseSource:
    path: Path
    role: DatabaseRole
```

`_phase_databases_backed_up()` records role and proof. `_phase_databases_migrated()` removes the hand-written toy schema and calls `migrate_databases()` using role-bound backup paths. `_phase_verified()` compares the staging proof with the installed target. Before install, journal all four target family paths and inverse operations.

- [ ] **Step 3: Verify GREEN and no-replay invariants**

```bash
env HOME=/private/tmp/dan-partia1-home DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_cutover_state_machine.py tests/test_cutover_no_replay.py \
  tests/test_legacy_data_migration.py
.venv/bin/ruff check dan/migration/cutover.py tests/test_cutover_state_machine.py
git diff --check
```

## Task 1.8: Restore or remove complete database families during rollback

**Files:**

- Modify: `dan/migration/rollback.py`
- Modify: `dan/migration/cutover.py`
- Modify: `tests/test_cutover_rollback.py`
- Modify: `tests/test_cutover_no_replay.py`

- [ ] **Step 1: Write RED complete-family tests**

```python
def test_rollback_remove_cleans_entire_explicit_database_family(tmp_path: Path) -> None:
    family = create_target_family(tmp_path / "dan.db")
    report = perform_fixture_rollback(tmp_path)
    assert all(not path.exists() for path in family.members())
    assert set(report.removed) == {str(path) for path in family.members()}


def test_rollback_refuses_journal_path_outside_allowed_roots(tmp_path: Path) -> None:
    journal = rollback_journal_with_target(tmp_path.parent / "outside.db")
    with pytest.raises(UnsafeRollbackTarget):
        perform_rollback(journal_dir=journal, manifest=fixture_manifest(), home=tmp_path, apply_changes=True)
```

Also test byte-for-byte restore of a bound quiescent family, main-only restore with explicit sidecar removal, reopen only after proof verification, and failure leaves intake closed plus operation blocked.

- [ ] **Step 2: Implement inverse execution against exact paths**

```python
def perform_rollback(
    *,
    journal_dir: Path,
    manifest: CutoverManifest,
    home: Path,
    host: CutoverHostAdapter | None = None,
    apply_changes: bool = False,
) -> RollbackReport:
    ...
```

Remove dead `_refuse_*` paths. `_undo()` receives the host adapter and an explicit tuple of allowed roots: manifest root, journal backup root and resolved `home/.dan`. Reopen intake only after restored state passes proof comparison and runtime start verification.

- [ ] **Step 3: Verify GREEN and run the full Batch 1 gate**

```bash
env HOME=/private/tmp/dan-partia1-home DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_sqlite_family.py tests/test_sqlite_validation.py tests/test_sqlite_backup.py \
  tests/test_legacy_data_migration.py tests/test_db_schema.py \
  tests/test_intake_gate.py tests/test_intake_api.py tests/test_text_turn_pipeline.py \
  tests/test_voice_api_contract.py tests/test_cutover_preconditions.py \
  tests/test_cutover_host_adapter.py tests/test_cutover_journal.py \
  tests/test_cutover_resume_reconciliation.py tests/test_cutover_state_machine.py \
  tests/test_cutover_rollback.py tests/test_cutover_no_replay.py
.venv/bin/ruff check dan/migration dan/daemon/intake.py dan/api/routes_intake.py \
  dan/store/migrations.py tests/test_sqlite_family.py tests/test_sqlite_validation.py \
  tests/test_intake_gate.py tests/test_cutover_journal.py
git diff --check
```

Expected: all pass in isolated HOME. Reviewers must inspect failure ordering, not only happy paths. No live cutover or rollback drill occurs in this task.
