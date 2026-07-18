# DAN Release 1 Audit Remediation — Batch 1 Data and Cutover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure that backup, migration, cutover, resume, and rollback protect the complete SQLite family, block new intake through a real gate, use the actual data migrator, and leave fsynced, reconcilable evidence of every operation.

**Architecture:** One library defines the exact `main/-wal/-shm/-journal` family; another produces deterministic database proofs. `IntakeGate` is durable state in `dan.db` and applies only to new intake. Cutover requires an explicit `CutoverHostAdapter` before the first mutation. The journal uses an FSM and append-only transitions; resume reconciles the recorded state against disk. Rollback executes recorded inverse operations on verified paths.

**Tech Stack:** Python 3.11+, SQLite Backup API/WAL, dataclasses, Protocol/StrEnum, JSONL, fsync, pytest, ruff.

## Global Constraints

- This batch begins only after Batch 0 is GREEN and a checkpoint is bound to the current SHA.
- The active release line remains `agent/dan-release1-integration`. This batch does not change the persistent Claude CLI transport, persona, voice configuration, panel, tag, or production install.
- Do not operate on real databases, invoke `launchctl`, or touch production. Every test uses fixtures and a temporary HOME.
- Every automated command creates a fresh root with `mktemp -d /private/tmp/dan-batch1.XXXXXX`, then uses `<root>/home` as isolated `HOME` and `<root>/evidence` as `DAN_RELEASE_EVIDENCE_ROOT`. A non-fixture evidence root must be an absolute, pre-created, owner-only `0700` directory with symlink-free ancestry, outside the repository, active `~/.dan`, `~/.claude`, runtime config, database, and existing operational migration roots. Using the real HOME or an active operational journal root requires a separately authorized cutover/deployment command.
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
DAN_BATCH1_ROOT="$(mktemp -d /private/tmp/dan-batch1.XXXXXX)"
mkdir -m 700 "$DAN_BATCH1_ROOT/home" "$DAN_BATCH1_ROOT/evidence"
export HOME="$DAN_BATCH1_ROOT/home"
export DAN_RELEASE_EVIDENCE_ROOT="$DAN_BATCH1_ROOT/evidence"
export DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1
export PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1
export RUFF_CACHE_DIR="$DAN_RELEASE_EVIDENCE_ROOT/ruff-cache"
.venv/bin/python -m pytest -q -p tests.audio_guard_plugin -p no:cacheprovider \
  tests/test_sqlite_family.py
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
DAN_BATCH1_ROOT="$(mktemp -d /private/tmp/dan-batch1.XXXXXX)"
mkdir -m 700 "$DAN_BATCH1_ROOT/home" "$DAN_BATCH1_ROOT/evidence"
export HOME="$DAN_BATCH1_ROOT/home"
export DAN_RELEASE_EVIDENCE_ROOT="$DAN_BATCH1_ROOT/evidence"
export DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1
export PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1
export RUFF_CACHE_DIR="$DAN_RELEASE_EVIDENCE_ROOT/ruff-cache"
.venv/bin/python -m pytest -q -p tests.audio_guard_plugin -p no:cacheprovider \
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
    monkeypatch.setattr(
        sqlite_backup,
        "_checkpoint_database",
        lambda _: (1, 10, 4),
        raising=False,
    )
    with pytest.raises(IncompleteCheckpointError):
        backup_database(source_fixture(tmp_path), tmp_path / "copy.db")


def test_length_framing_distinguishes_field_and_row_boundaries(tmp_path: Path) -> None:
    split_fields = make_database(tmp_path / "split.db", rows=(("ab", "c"),))
    joined_fields = make_database(tmp_path / "joined.db", rows=(("a", "bc"),))
    split_rows = make_database(tmp_path / "rows.db", rows=(("a",), ("b",)))
    joined_rows = make_database(tmp_path / "one-row.db", rows=(("ab",),))
    assert prove_database(split_fields).canonical_data_sha256 != prove_database(joined_fields).canonical_data_sha256
    assert prove_database(split_rows).canonical_data_sha256 != prove_database(joined_rows).canonical_data_sha256
```

Add tests distinguishing SQLite NULL/integer/real/text/blob types, missing required tables, FK violations, second-lsof writer detection and incomplete `log != checkpointed`.

- [ ] **Step 2: Verify RED**

```bash
DAN_BATCH1_ROOT="$(mktemp -d /private/tmp/dan-batch1.XXXXXX)"
mkdir -m 700 "$DAN_BATCH1_ROOT/home" "$DAN_BATCH1_ROOT/evidence"
export HOME="$DAN_BATCH1_ROOT/home"
export DAN_RELEASE_EVIDENCE_ROOT="$DAN_BATCH1_ROOT/evidence"
export DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1
export PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1
export RUFF_CACHE_DIR="$DAN_RELEASE_EVIDENCE_ROOT/ruff-cache"
.venv/bin/python -m pytest -q -p tests.audio_guard_plugin -p no:cacheprovider \
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
        payload = b"n"
    elif isinstance(value, bytes):
        payload = b"b" + value
    elif isinstance(value, int):
        payload = b"i" + str(value).encode("ascii")
    elif isinstance(value, float):
        payload = b"r" + value.hex().encode("ascii")
    else:
        payload = b"t" + str(value).encode("utf-8")
    return len(payload).to_bytes(8, "big") + payload


def encode_sqlite_row(values: Sequence[object]) -> bytes:
    payload = b"".join(encode_sqlite_value(value) for value in values)
    return len(payload).to_bytes(8, "big") + payload
```

Length-frame table names, schema SQL, column names, every typed field, and every complete row. Sort tables/columns deterministically and rows by the tuple of encoded values. `prove_database()` must require `integrity_check == "ok"`, empty `foreign_key_check`, and every required table. `compare_database_proofs()` raises on schema, count or digest drift.

`backup_database()` must:

1. validate exact family and lsof handles;
2. open a separately authorized source connection with SQLite URI `mode=rw`;
3. run `PRAGMA wal_checkpoint(TRUNCATE)` and require `busy == 0` plus either `log == checkpointed` or the exact non-WAL result `(0, -1, -1)`;
4. close the writable checkpoint connection completely;
5. repeat the full-family lsof/quiescence check immediately before the snapshot;
6. open the source with SQLite URI `mode=ro`, calculate its proof, and use that read-only connection as the Backup API source;
7. backup to a temporary target under the destination directory and prove the copy;
8. fsync the completed file and destination directory before atomic install;
9. remove the complete temporary family on failure.

- [ ] **Step 4: Verify GREEN and focused regression**

```bash
DAN_BATCH1_ROOT="$(mktemp -d /private/tmp/dan-batch1.XXXXXX)"
mkdir -m 700 "$DAN_BATCH1_ROOT/home" "$DAN_BATCH1_ROOT/evidence"
export HOME="$DAN_BATCH1_ROOT/home"
export DAN_RELEASE_EVIDENCE_ROOT="$DAN_BATCH1_ROOT/evidence"
export DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1
export PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1
export RUFF_CACHE_DIR="$DAN_RELEASE_EVIDENCE_ROOT/ruff-cache"
.venv/bin/python -m pytest -q -p tests.audio_guard_plugin -p no:cacheprovider \
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

```bash
DAN_BATCH1_ROOT="$(mktemp -d /private/tmp/dan-batch1.XXXXXX)"
mkdir -m 700 "$DAN_BATCH1_ROOT/home" "$DAN_BATCH1_ROOT/evidence"
export HOME="$DAN_BATCH1_ROOT/home"
export DAN_RELEASE_EVIDENCE_ROOT="$DAN_BATCH1_ROOT/evidence"
export DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1
export PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1
export RUFF_CACHE_DIR="$DAN_RELEASE_EVIDENCE_ROOT/ruff-cache"
.venv/bin/python -m pytest -q -p tests.audio_guard_plugin -p no:cacheprovider \
  tests/test_legacy_data_migration.py tests/test_db_schema.py
```

Expected: the unexpected FTS table is accepted or the new proof fields are absent. Only then implement:

```python
MEMORY_FTS5_SIDECARS = frozenset({
    "memory_fts_data",
    "memory_fts_idx",
    "memory_fts_content",
    "memory_fts_docsize",
    "memory_fts_config",
})
```

`_validate_memory_schema()` must reject every unexpected table including any other `memory_fts_*`. Extend the current `DatabaseMigrationReport` additively with `jarvis_source_proof`, `memory_source_proof`, and `target_proof: SQLiteProof`; retain its current `backup`, `jarvis_rows_preserved`, and `memory` fields. Extend `MemoryMigrationReport` only with evidence needed to explain its current imported/merged/rejected counts. The renderer emits integrity, FK status, schema/count/digest evidence but no raw user rows.

- [ ] **Step 3: Verify GREEN**

```bash
DAN_BATCH1_ROOT="$(mktemp -d /private/tmp/dan-batch1.XXXXXX)"
mkdir -m 700 "$DAN_BATCH1_ROOT/home" "$DAN_BATCH1_ROOT/evidence"
export HOME="$DAN_BATCH1_ROOT/home"
export DAN_RELEASE_EVIDENCE_ROOT="$DAN_BATCH1_ROOT/evidence"
export DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1
export PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1
export RUFF_CACHE_DIR="$DAN_RELEASE_EVIDENCE_ROOT/ruff-cache"
.venv/bin/python -m pytest -q -p tests.audio_guard_plugin -p no:cacheprovider \
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
- Modify: `dan/api/__init__.py`
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
    assert app.conn is not None
    assert app.event_store is not None
    app.intake_gate.close(operation_id="op-1", reason="cutover")
    before_turns = int(app.conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0])
    before_events = len(app.event_store.list_after(0, limit=500))
    with pytest.raises(IntakeClosedError):
        app.handle_text_input(text="blocked")
    assert int(app.conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0]) == before_turns
    assert len(app.event_store.list_after(0, limit=500)) == before_events


def test_operation_admitted_before_close_may_finish(gate: IntakeGate) -> None:
    with gate.admit(source="text"):
        gate.close(operation_id="op-1", reason="cutover")
    assert gate.snapshot().state is IntakeState.CLOSED
```

Also test restart persistence, idempotent same-operation close, mismatched open rejection, cancel/status availability and stable API `503/intake_closed`.

- [ ] **Step 2: Verify RED**

```bash
DAN_BATCH1_ROOT="$(mktemp -d /private/tmp/dan-batch1.XXXXXX)"
mkdir -m 700 "$DAN_BATCH1_ROOT/home" "$DAN_BATCH1_ROOT/evidence"
export HOME="$DAN_BATCH1_ROOT/home"
export DAN_RELEASE_EVIDENCE_ROOT="$DAN_BATCH1_ROOT/evidence"
export DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1
export PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1
export RUFF_CACHE_DIR="$DAN_RELEASE_EVIDENCE_ROOT/ruff-cache"
.venv/bin/python -m pytest -q -p tests.audio_guard_plugin -p no:cacheprovider \
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
    active_leases: int


class IntakeGate:
    def snapshot(self) -> IntakeSnapshot: ...
    def close(self, *, operation_id: str, reason: str) -> IntakeSnapshot: ...
    def open(self, *, operation_id: str) -> IntakeSnapshot: ...
    def admit(self, *, source: str) -> ContextManager[IntakeLease]: ...
    def wait_until_drained(self, *, timeout: float) -> bool: ...
```

`LATEST_SCHEMA_VERSION = 6`; one-row table stores state, operation id, reason, revision and timestamp. `DaemonApp.handle_text_input`, `_start_voice_turn` and external `post_voice_speak` acquire a lease before their first write. Do not guard internal TTS for an already admitted turn.

Routes:

- `GET /runtime/intake`
- `POST /runtime/intake/close`
- `POST /runtime/intake/open`

Wire those exact routes into the explicit dispatcher in `dan/daemon/lifecycle.py`; do not change the existing `/runtime/restart` route or its implementation. Add matching typed methods to `DaemonClient` in Task 1.5. A close response is successful only when the persisted row is closed for the requested operation and `active_leases == 0` after the bounded drain.

- [ ] **Step 4: Verify GREEN and review failure ordering**

```bash
DAN_BATCH1_ROOT="$(mktemp -d /private/tmp/dan-batch1.XXXXXX)"
mkdir -m 700 "$DAN_BATCH1_ROOT/home" "$DAN_BATCH1_ROOT/evidence"
export HOME="$DAN_BATCH1_ROOT/home"
export DAN_RELEASE_EVIDENCE_ROOT="$DAN_BATCH1_ROOT/evidence"
export DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1
export PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1
export RUFF_CACHE_DIR="$DAN_RELEASE_EVIDENCE_ROOT/ruff-cache"
.venv/bin/python -m pytest -q -p tests.audio_guard_plugin -p no:cacheprovider \
  tests/test_intake_gate.py tests/test_intake_api.py tests/test_text_turn_pipeline.py \
  tests/test_voice_api_contract.py tests/test_db_schema.py
.venv/bin/ruff check dan/daemon/intake.py dan/api/routes_intake.py dan/api/__init__.py dan/daemon/app.py \
  dan/api/routes_voice.py dan/daemon/lifecycle.py dan/store/migrations.py tests/test_intake_gate.py
git diff --check
```

## Task 1.5: Require an explicit host adapter before cutover mutation

**Files:**

- Create: `dan/migration/host_adapter.py`
- Modify: `dan/migration/cutover.py`
- Modify: `dan/migration/cutover_cli.py`
- Modify: `dan/migration/rollback.py`
- Modify: `dan/api/client.py`
- Read/consume: `dan/release/evidence.py` (created by Batch 0)
- Read: `dan/release/producer_ids.py` (sole Batch 0 producer-ID authority)
- Modify: `tests/cutover_helpers.py`
- Create: `tests/test_cutover_host_adapter.py`
- Create: `tests/test_cutover_cli.py`
- Modify: `tests/test_cutover_preconditions.py`

- [ ] **Step 1: Write RED fail-before-mutation tests**

```python
def test_apply_without_host_adapter_blocks_before_journal_or_mutation(cutover_fixture) -> None:
    engine = cutover_fixture.engine(host=None)
    before = cutover_fixture.tree_hash()
    with pytest.raises(MissingHostAdapter):
        engine.apply(manifest_sha256=cutover_fixture.manifest.sha256)
    assert list(cutover_fixture.evidence_root.iterdir()) == []
    assert cutover_fixture.tree_hash() == before


def test_dry_run_without_host_adapter_is_nonmutating(cutover_fixture) -> None:
    before = cutover_fixture.tree_hash()
    plan = cutover_fixture.engine(host=None).plan()
    assert isinstance(plan, dict)
    assert plan["pending_destructive_operations"]
    assert list(cutover_fixture.evidence_root.iterdir()) == []
    assert cutover_fixture.tree_hash() == before


def test_cli_apply_builds_injected_host_only_after_exact_sha_checks(cli_fixture) -> None:
    result = cutover_main(cli_fixture.apply_argv(), host_factory=cli_fixture.host_factory)
    assert result == 0
    assert cli_fixture.host_factory.calls == [(cli_fixture.manifest, cli_fixture.probe)]
    assert cli_fixture.host.started_dan == 1


def test_system_host_bootstraps_exact_bound_plist_once(system_host_fixture) -> None:
    binding = system_host_fixture.dan_plist_binding()
    system_host_fixture.host.start_runtime(
        target=RuntimeTarget.DAN,
        root=system_host_fixture.new_root,
        plists=(binding,),
    )
    assert system_host_fixture.runner.argv == [[
        "launchctl",
        "bootstrap",
        f"gui/{system_host_fixture.uid}",
        str(binding.path),
    ]]
```

- [ ] **Step 2: Verify RED and implement the protocol**

```bash
DAN_BATCH1_ROOT="$(mktemp -d /private/tmp/dan-batch1.XXXXXX)"
mkdir -m 700 "$DAN_BATCH1_ROOT/home" "$DAN_BATCH1_ROOT/evidence"
export HOME="$DAN_BATCH1_ROOT/home"
export DAN_RELEASE_EVIDENCE_ROOT="$DAN_BATCH1_ROOT/evidence"
export DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1
export PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1
export RUFF_CACHE_DIR="$DAN_RELEASE_EVIDENCE_ROOT/ruff-cache"
.venv/bin/python -m pytest -q -p tests.audio_guard_plugin -p no:cacheprovider \
  tests/test_cutover_host_adapter.py tests/test_cutover_cli.py \
  tests/test_cutover_preconditions.py
```

Expected: missing host adapter/CLI injection interfaces and current double-start behavior fail the new tests. Only then implement:

```python
class RuntimeTarget(StrEnum):
    LEGACY = "legacy"
    DAN = "dan"


@dataclass(frozen=True)
class LaunchdBinding:
    label: str
    path: Path
    sha256: str


class CutoverHostAdapter(Protocol):
    def close_intake(
        self, *, operation_id: str, reason: str, target: RuntimeTarget
    ) -> Mapping[str, object]: ...
    def intake_state(self, *, target: RuntimeTarget) -> Mapping[str, object]: ...
    def open_intake(self, *, operation_id: str, target: RuntimeTarget) -> Mapping[str, object]: ...
    def stop_runtime(
        self, *, target: RuntimeTarget, labels: Sequence[str]
    ) -> Mapping[str, object]: ...
    def start_runtime(
        self, *, target: RuntimeTarget, root: Path, plists: Sequence[LaunchdBinding]
    ) -> Mapping[str, object]: ...
```

Implement an executable `SystemCutoverHostAdapter`, not another refusal shim:

- construct its `DaemonClient` from `load_config(manifest.home / ".dan/config.toml")` and the existing `DaemonClient.from_config()` token-loading path;
- add `DaemonClient.intake_state()`, `close_intake(operation_id, reason)` and `open_intake(operation_id)` for the three exact `/runtime/intake` routes;
- close intake, poll the persisted state until it is closed for the same operation and has zero active leases, and fail closed on an unavailable or malformed route;
- stop only manifest labels with `launchctl bootout gui/<uid>/<label>` and verify legacy labels, processes, and listeners are absent through `SystemProbe`;
- start DAN only with `launchctl bootstrap gui/<uid> <home>/Library/LaunchAgents/com.dan.dand.plist`, after validating that exact regular non-symlink plist, its bound installed hash, `WorkingDirectory == root`, and root-bound program arguments; then require `/health` to report `service="dand"`, `ok=true`, and `started=true`;
- never fall back to `kickstart`, `pkill`, a marker file, a shell string, or a second bootstrap.

Change `CutoverEngine.__init__` to take `host: CutoverHostAdapter | None`, `evidence_root: Path | None`, and the existing `resume_journal`; remove the separate `launchctl` and `runtime_starter` injections. Reuse Batch 0's `active_evidence_roots_from_environment()` plus `validate_evidence_root()` rather than adding another validator. `apply(*, manifest_sha256, ...)` checks for a host and validates the pre-created root before `Journal.create(evidence_root)`, queue cancellation, or any source-tree write. A resume journal must be a direct `cutover-*` child of that root and its header must name the same root. `prepare()`, `plan()` and CLI dry-run remain read-only and do not construct a system host. `CutoverFixture` creates `<tmp_path>/evidence` as empty mode `0700`, exposes it as `evidence_root`, and creates journals there rather than beneath fixture HOME.

The apply CLI keeps the current exact `--manifest-sha256` contract and gains `--evidence-root` (defaulting only from `DAN_RELEASE_EVIDENCE_ROOT`). `cutover_main(argv=None, *, host_factory=build_system_cutover_host_adapter)` and `rollback_main(...)` may call a factory with the exact `(manifest, probe)` only for apply/rollback after all SHA and refusal checks; plan, preflight, status, and rollback dry-run never call it. A non-fixture mutation uses the default system factory. A fixture mutation refuses the default system factory and requires the deterministic factory explicitly injected by `tests/cutover_helpers.py`, so fixture mode can never import or invoke system launch control. Add unit tests for the exact `bootout` argv, exact single `bootstrap` argv, non-zero launchctl exit, plist symlink/hash/root drift, malformed health, a factory that must not be reached during read-only commands, and a fixture mutation offered the default factory.

Wire `_phase_intake_closed()` to call `host.close_intake(operation_id=journal.header.operation_id, reason="release1-cutover", target=RuntimeTarget.LEGACY)` exactly once and journal the validated returned snapshot; remove the two current note-only fake closures. Fix the phase ownership that currently starts DAN twice: `_phase_runtime_stopped()` calls `host.stop_runtime()` once for the exact legacy-label tuple; `_phase_launchd_installed()` only installs and proves the plist; `_phase_cold_started()` records its inverse first and calls `host.start_runtime(target=RuntimeTarget.DAN, root=manifest.new_root, plists=(LaunchdBinding(DAND_LABEL, home / DAND_PLIST_RELPATH, installed_plist_sha256),))` exactly once. Rollback uses the same semantic host methods, never a generic `launchctl(*args)` callback.

- [ ] **Step 3: Verify GREEN**

```bash
DAN_BATCH1_ROOT="$(mktemp -d /private/tmp/dan-batch1.XXXXXX)"
mkdir -m 700 "$DAN_BATCH1_ROOT/home" "$DAN_BATCH1_ROOT/evidence"
export HOME="$DAN_BATCH1_ROOT/home"
export DAN_RELEASE_EVIDENCE_ROOT="$DAN_BATCH1_ROOT/evidence"
export DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1
export PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1
export RUFF_CACHE_DIR="$DAN_RELEASE_EVIDENCE_ROOT/ruff-cache"
.venv/bin/python -m pytest -q -p tests.audio_guard_plugin -p no:cacheprovider \
  tests/test_cutover_host_adapter.py tests/test_cutover_cli.py \
  tests/test_cutover_preconditions.py
.venv/bin/ruff check dan/migration/host_adapter.py dan/migration/cutover.py \
  dan/migration/cutover_cli.py dan/migration/rollback.py dan/api/client.py \
  tests/test_cutover_host_adapter.py tests/test_cutover_cli.py
git diff --check
```

## Task 1.6: Add durable journal states and resume reconciliation

**Files:**

- Modify: `dan/migration/journal.py`
- Modify: `dan/migration/cutover.py`
- Modify: `dan/migration/cutover_cli.py`
- Modify: `tests/cutover_helpers.py`
- Create: `tests/test_cutover_journal.py`
- Create: `tests/test_cutover_resume_reconciliation.py`
- Modify: `tests/test_cutover_state_machine.py`

- [ ] **Step 1: Write RED FSM and fsync tests**

```python
def test_resume_rejects_non_prefix_committed_phases(journal: Journal) -> None:
    journal.commit_phase(CutoverPhase.INVENTORIED)
    journal.commit_phase(CutoverPhase.DATABASES_MIGRATED)
    with pytest.raises(InvalidJournalSequence):
        journal.resume_state(tuple(CutoverPhase))


@pytest.mark.parametrize("phase", tuple(CutoverPhase))
def test_resume_blocks_when_committed_phase_evidence_drifts(
    cutover_fixture, phase: CutoverPhase
) -> None:
    with pytest.raises(CutoverInterrupted):
        cutover_fixture.apply(interrupt_after=phase)
    journal = cutover_fixture.latest_journal_dir()
    cutover_fixture.corrupt_phase_evidence(phase, journal)
    before = cutover_fixture.tree_hash()
    with pytest.raises(CutoverBlocked, match=f"resume evidence mismatch: {phase.value}"):
        cutover_fixture.resume(journal)
    assert cutover_fixture.tree_hash() == before
```

For the same phase parameterization, add a valid-prefix test that resumes to completion, preserves the existing journal byte prefix, commits every phase once, and proves the fixture host mutation counters did not repeat any committed action. Mock `os.fsync` to prove both file descriptor and parent directory are synced for create, append, state and report writes.

Verify RED before implementing:

```bash
DAN_BATCH1_ROOT="$(mktemp -d /private/tmp/dan-batch1.XXXXXX)"
mkdir -m 700 "$DAN_BATCH1_ROOT/home" "$DAN_BATCH1_ROOT/evidence"
export HOME="$DAN_BATCH1_ROOT/home"
export DAN_RELEASE_EVIDENCE_ROOT="$DAN_BATCH1_ROOT/evidence"
export DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1
export PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1
export RUFF_CACHE_DIR="$DAN_RELEASE_EVIDENCE_ROOT/ruff-cache"
.venv/bin/python -m pytest -q -p tests.audio_guard_plugin -p no:cacheprovider \
  tests/test_cutover_journal.py tests/test_cutover_resume_reconciliation.py \
  tests/test_cutover_state_machine.py
```

Expected: the missing FSM/header/reconciler and blind committed-phase skipping fail.

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

The journal starts with a schema-versioned header containing `operation_id`, `manifest_sha256`, and `evidence_root`; Task 1.8 extends it with the rollback-target-manifest hash before a mutating cutover is eligible for rehearsal. Creation, every append, every state transition, and every atomic report fsync both the file and its parent directory. The parser rejects duplicate commits, missing prefixes, impossible transitions, a header anywhere but first, and an unknown schema. Old journals remain readable for inspection but resume as `BLOCKED`. The CLI status denominator is `len(CutoverPhase)`, never a stale literal.

Before skipping any committed phase, reconcile it against current evidence. The reconciler is aware of later committed phases, so it validates the phase's durable outcome rather than incorrectly requiring an intermediate process state:

- `INVENTORIED`: manifest SHA plus every inventory path, type, and hash;
- `INTAKE_CLOSED`: until `INTAKE_REOPENED`, the current authoritative source, staged, or installed gate row is closed for the journal operation; after reopening, the committed close/open transition and current open row must carry that same operation; while a closed runtime is alive, the host snapshot must agree and report zero active leases;
- `QUEUE_QUIESCENT`: no active request or speaking marker, plus exact request-file backup paths and hashes;
- `RUNTIME_STOPPED`: every legacy label, process, and listener is absent and every parked plist matches its recorded hash;
- `DATABASES_BACKED_UP`: every recorded four-member source family, backup member, proof, and digest agrees;
- `DATABASES_MIGRATED`: the staging database proof and closed-intake operation agree;
- `PATHS_MOVED`: exact source/destination tree digests and untouched donor hashes agree;
- `ADAPTERS_INSTALLED`: every install-report target, mode, and hash agrees;
- `LAUNCHD_INSTALLED`: the installed plist hash agrees; no bootstrap is expected before `COLD_STARTED`, and exactly the one bound bootstrap is expected after it;
- `COLD_STARTED`: exactly the DAN target is running and its plist/root binding and health agree; intake remains closed unless `INTAKE_REOPENED` is already committed;
- `VERIFIED`: installed database proof, migration report, donors, and journal completeness agree; intake remains closed unless the later reopen phase is committed;
- `INTAKE_REOPENED`: host and installed database both report open with the same completed operation.

Append `INTAKE_REOPENED` after `VERIFIED`. A mismatch appends and fsyncs `BLOCKED` evidence, performs no next source mutation, and leaves intake closed. A resume with a valid prefix continues with the same `operation_id`; it never creates another journal or repeats a committed mutation.

- [ ] **Step 3: Verify GREEN**

```bash
DAN_BATCH1_ROOT="$(mktemp -d /private/tmp/dan-batch1.XXXXXX)"
mkdir -m 700 "$DAN_BATCH1_ROOT/home" "$DAN_BATCH1_ROOT/evidence"
export HOME="$DAN_BATCH1_ROOT/home"
export DAN_RELEASE_EVIDENCE_ROOT="$DAN_BATCH1_ROOT/evidence"
export DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1
export PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1
export RUFF_CACHE_DIR="$DAN_RELEASE_EVIDENCE_ROOT/ruff-cache"
.venv/bin/python -m pytest -q -p tests.audio_guard_plugin -p no:cacheprovider \
  tests/test_cutover_journal.py tests/test_cutover_resume_reconciliation.py \
  tests/test_cutover_state_machine.py
.venv/bin/ruff check dan/migration/journal.py dan/migration/cutover.py \
  dan/migration/cutover_cli.py \
  tests/test_cutover_journal.py tests/test_cutover_resume_reconciliation.py
git diff --check
```

## Task 1.7: Connect the real Task 3 migrator to cutover

**Files:**

- Modify: `dan/migration/cutover.py`
- Modify: `tests/fixtures/cutover/manifest.json`
- Modify: `tests/cutover_helpers.py`
- Read only: `tests/fixtures/memory_v1.sql`
- Create: `tests/test_cutover_intake_handoff.py`
- Modify: `tests/test_cutover_state_machine.py`
- Modify: `tests/test_cutover_no_replay.py`

- [ ] **Step 1: Write RED integration tests**

```python
def test_cutover_uses_task3_migrator_and_preserves_full_schema(cutover_fixture) -> None:
    report = cutover_fixture.apply()
    migration = report.database_migration
    assert migration is not None
    assert migration.jarvis_rows_preserved is True
    assert "requests" in migration.target_proof.table_schemas
    assert "memory_items" in migration.target_proof.table_schemas


def test_manifest_v2_requires_one_jarvis_and_one_memory_database(tmp_path: Path) -> None:
    manifest = manifest_with_duplicate_role(tmp_path, "jarvis")
    with pytest.raises(ManifestRoleError):
        CutoverManifest.load(manifest, root=tmp_path)


def test_cold_started_database_rejects_intake_until_verified_resume(cutover_fixture) -> None:
    with pytest.raises(CutoverInterrupted):
        cutover_fixture.apply(interrupt_after=CutoverPhase.COLD_STARTED)
    journal = cutover_fixture.latest_journal_dir()
    operation_id = Journal.open(journal).header.operation_id

    with cutover_fixture.daemon_for_installed_database() as app:
        assert app.conn is not None
        before = int(app.conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0])
        with pytest.raises(IntakeClosedError):
            app.handle_text_input(text="blocked during cutover")
        assert int(app.conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0]) == before
        assert app.intake_gate.snapshot().operation_id == operation_id

    cutover_fixture.resume(journal)
    with cutover_fixture.daemon_for_installed_database() as app:
        result = app.handle_text_input(text="accepted after verification")
        assert result.turn_id
```

- [ ] **Step 2: Implement role-bound sources**

First verify RED:

```bash
DAN_BATCH1_ROOT="$(mktemp -d /private/tmp/dan-batch1.XXXXXX)"
mkdir -m 700 "$DAN_BATCH1_ROOT/home" "$DAN_BATCH1_ROOT/evidence"
export HOME="$DAN_BATCH1_ROOT/home"
export DAN_RELEASE_EVIDENCE_ROOT="$DAN_BATCH1_ROOT/evidence"
export DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1
export PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1
export RUFF_CACHE_DIR="$DAN_RELEASE_EVIDENCE_ROOT/ruff-cache"
.venv/bin/python -m pytest -q -p tests.audio_guard_plugin -p no:cacheprovider \
  tests/test_cutover_state_machine.py tests/test_cutover_no_replay.py \
  tests/test_cutover_intake_handoff.py tests/test_legacy_data_migration.py
```

Expected: manifest v1/toy migration and the open staged intake violate the new assertions. Only then implement:

```python
class DatabaseRole(StrEnum):
    JARVIS = "jarvis"
    MEMORY = "memory"


@dataclass(frozen=True)
class DatabaseSource:
    path: Path
    role: DatabaseRole
```

Manifest v2 resolves each source beneath the manifest root and requires exactly one `jarvis` role plus exactly one `memory` role. Update `tests/cutover_helpers.py` to build the jarvis source and to create the memory source by executing the existing `tests/fixtures/memory_v1.sql`; do not invent another schema fixture.

`_phase_databases_backed_up()` records each role, complete family, proof, and backup path. `_phase_databases_migrated()` removes the hand-written toy target and calls the existing current interface exactly as `migrate_databases(jarvis_backup, memory_backup, staging_database)`. Extend `CutoverReport` with `database_migration: DatabaseMigrationReport | None`, using the Task 1.3 `jarvis_rows_preserved` and `target_proof` fields shown above. `_phase_verified()` compares the staging proof with the installed target.

The journal header's `operation_id` is the only intake handoff ID. Immediately after migration and before installing the database, use the Task 1.4 gate against the staging connection to persist `CLOSED`, that operation ID, and reason `release1-cutover`; fsync and prove that state. `COLD_STARTED` must therefore start a database that is already closed. `_phase_verified()` runs while it remains closed. Only the new `INTAKE_REOPENED` phase calls `host.open_intake(operation_id=..., target=RuntimeTarget.DAN)`, verifies the installed row is open for the same operation, and commits the phase. Any interruption through `VERIFIED`, including a process restart, stays closed.

Add `CutoverFixture.daemon_for_installed_database()` in `tests/cutover_helpers.py`. It builds the real `DaemonApp` against the installed fixture database with deterministic test brain dependencies and voice/audio/microphone disabled, and never starts a listener, TTS process, or launchd job. Before database install, journal all four exact target-family paths and their inverse operations.

- [ ] **Step 3: Verify GREEN and no-replay invariants**

```bash
DAN_BATCH1_ROOT="$(mktemp -d /private/tmp/dan-batch1.XXXXXX)"
mkdir -m 700 "$DAN_BATCH1_ROOT/home" "$DAN_BATCH1_ROOT/evidence"
export HOME="$DAN_BATCH1_ROOT/home"
export DAN_RELEASE_EVIDENCE_ROOT="$DAN_BATCH1_ROOT/evidence"
export DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1
export PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1
export RUFF_CACHE_DIR="$DAN_RELEASE_EVIDENCE_ROOT/ruff-cache"
.venv/bin/python -m pytest -q -p tests.audio_guard_plugin -p no:cacheprovider \
  tests/test_cutover_state_machine.py tests/test_cutover_no_replay.py \
  tests/test_cutover_intake_handoff.py tests/test_legacy_data_migration.py
.venv/bin/ruff check dan/migration/cutover.py tests/cutover_helpers.py \
  tests/test_cutover_state_machine.py tests/test_cutover_intake_handoff.py
git diff --check
```

## Task 1.8: Restore or remove complete database families during rollback

**Files:**

- Create: `dan/migration/rollback_targets.py`
- Modify: `dan/migration/rollback.py`
- Modify: `dan/migration/cutover.py`
- Modify: `dan/migration/cutover_cli.py`
- Modify: `tests/cutover_helpers.py`
- Create: `tests/test_rollback_targets.py`
- Modify: `tests/test_cutover_rollback.py`
- Modify: `tests/test_cutover_no_replay.py`

- [ ] **Step 1: Write RED exact-target and complete-family tests**

```python
def test_rollback_remove_cleans_entire_authorized_database_family(
    rollback_target_fixture,
) -> None:
    family = rollback_target_fixture.create_complete_family("dan.db")
    targets = rollback_target_fixture.authorize_remove_family(family)
    report = rollback_target_fixture.execute(targets)
    assert all(not path.exists() for path in family.members())
    assert set(report.removed) == {str(path) for path in family.members()}


def test_rollback_refuses_traversal_in_manifest_before_any_write(cutover_fixture) -> None:
    manifest = cutover_fixture.write_manifest_payload(home="../outside")
    with pytest.raises(CutoverBlocked, match="unsafe manifest path"):
        CutoverManifest.load(manifest, root=cutover_fixture.root)


def test_rollback_refuses_hash_drift_and_leaves_intake_closed(cutover_fixture) -> None:
    report = cutover_fixture.apply()
    cutover_fixture.replace_authorized_target_with_same_type_different_bytes(report.journal)
    with pytest.raises(UnsafeRollbackTarget, match="hash drift"):
        cutover_fixture.rollback(report.journal)
    assert cutover_fixture.host.intake_is_closed
```

Also test traversal injected into a journal row, absolute and non-normalized paths, an ancestor replaced by a symlink, final-target symlinks, file-to-directory and directory-to-file swaps, mode drift for executable/plist targets, a parent swap between validation and mutation, and a target swap immediately before unlink/rename. The outside sentinel must remain byte-identical in every traversal, symlink, and TOCTOU test. Cover byte-for-byte restore of a hash-bound quiescent family, main-only restore with all three explicit sidecars removed, intake reopening only after proof and legacy-runtime verification, and every failure after authenticated rollback intake closure leaving intake closed plus the journal `BLOCKED`.

- [ ] **Step 2: Implement inverse execution against exact paths**

First verify RED:

```bash
DAN_BATCH1_ROOT="$(mktemp -d /private/tmp/dan-batch1.XXXXXX)"
mkdir -m 700 "$DAN_BATCH1_ROOT/home" "$DAN_BATCH1_ROOT/evidence"
export HOME="$DAN_BATCH1_ROOT/home"
export DAN_RELEASE_EVIDENCE_ROOT="$DAN_BATCH1_ROOT/evidence"
export DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1
export PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1
export RUFF_CACHE_DIR="$DAN_RELEASE_EVIDENCE_ROOT/ruff-cache"
.venv/bin/python -m pytest -q -p tests.audio_guard_plugin -p no:cacheprovider \
  tests/test_rollback_targets.py tests/test_cutover_rollback.py \
  tests/test_cutover_no_replay.py tests/test_cutover_cli.py
```

Expected: raw journal paths, incomplete family handling, and missing symlink/hash/TOCTOU checks fail. Only then implement:

```python
@dataclass(frozen=True)
class BoundPathState:
    kind: PathKind
    sha256: str | None
    mode: int | None


@dataclass(frozen=True)
class RollbackTarget:
    target_id: str
    path: Path
    authorized_root: Path
    pre_state: BoundPathState
```

Before the first source mutation, derive the complete tuple of targets from the manifest and concrete plans, serialize canonical `rollback-targets.json`, extend the current journal header schema with its SHA-256, and refuse every mutating cutover/resume whose header lacks that binding. Include only:

- the four exact paths for every source, staging, installed, and backup database family;
- the exact source and destination of each planned tree/request/plist move;
- every exact `InstallPlan` target and its concrete backup path;
- the exact DAN plist and each concrete legacy plist named by the manifest.

Journal entries reference `target_id`; strings inside later journal rows are evidence, never authority. Before each forward mutation, append its inverse plus `target_id`, any `peer_target_id`, and the expected post-mutation kind/mode/hash while that hash is already knowable (source tree for a move, staged bytes for an install, `InstallPlan` output for an adapter). At `VERIFIED`, seal the final four-member installed database state so runtime-created sidecars have explicit hashes. Rollback validates the latest sealed post-state before an inverse and the target manifest's `pre_state` after it. There is no generic "allowed root" that turns an arbitrary descendant into an authorized rollback target.

Harden `CutoverManifest._resolve()` at intake: reject absolute, empty, `.`, `..`, duplicate-separator, NUL, and non-normalized path text; reject symlinked existing ancestry; and prove lexical containment beneath the resolved manifest root. Apply the same rules to the external evidence root, which must already exist as an owner-owned `0700` directory with symlink-free ancestry and must not overlap the repo, active `~/.dan`, active `~/.claude`, voice config, any source/target database family, or any existing operational migration root.

Before every inverse, look up its immutable target, walk from its authorized root using held directory descriptors with `O_DIRECTORY | O_NOFOLLOW`, use `lstat`/`fstat` to compare device/inode, type, mode, and regular-file or canonical-tree hash, and revalidate immediately before mutation. Perform unlink and rename relative to those held descriptors (`dir_fd`/`renameat` semantics); never resolve the path and mutate it later by name. Any mismatch records `BLOCKED` and stops before the next inverse.

```python
def perform_rollback(
    *,
    journal_dir: Path,
    manifest: CutoverManifest,
    home: Path,
    host: CutoverHostAdapter | None = None,
    manifest_sha256: str,
    apply_changes: bool = False,
) -> RollbackReport:
    ...
```

Remove dead `_refuse_*` paths and the raw-path `_undo()` contract. A mutating rollback requires CLI `--manifest-sha256` equal to both the manifest and journal header, a system or fixture host, a valid journal header, and an unchanged rollback-target-manifest hash before it contacts the host. Invalid unauthenticated structure blocks without host or source mutation. After authentication it closes new intake, stops DAN once through the host, restores or removes each exact family member, and never replays cancelled/speaking work. It starts only the exact restored legacy plist bindings through `host.start_runtime(target=RuntimeTarget.LEGACY, ...)`; only after restored proofs, zero active/speaking work, and host verification succeed may it reopen legacy intake for the journal operation. Any later failure leaves intake closed and fsyncs `BLOCKED`.

- [ ] **Step 3: Verify GREEN for rollback safety**

```bash
DAN_BATCH1_ROOT="$(mktemp -d /private/tmp/dan-batch1.XXXXXX)"
mkdir -m 700 "$DAN_BATCH1_ROOT/home" "$DAN_BATCH1_ROOT/evidence"
export HOME="$DAN_BATCH1_ROOT/home"
export DAN_RELEASE_EVIDENCE_ROOT="$DAN_BATCH1_ROOT/evidence"
export DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1
export PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1
export RUFF_CACHE_DIR="$DAN_RELEASE_EVIDENCE_ROOT/ruff-cache"
.venv/bin/python -m pytest -q -p tests.audio_guard_plugin -p no:cacheprovider \
  tests/test_rollback_targets.py tests/test_cutover_rollback.py \
  tests/test_cutover_no_replay.py tests/test_cutover_cli.py
.venv/bin/ruff check dan/migration/rollback_targets.py dan/migration/rollback.py \
  dan/migration/cutover.py dan/migration/cutover_cli.py \
  tests/test_rollback_targets.py tests/test_cutover_rollback.py
git diff --check
```

## Task 1.9: Produce SHA-bound rollback rehearsal evidence

**Files:**

- Read/consume: `dan/release/evidence.py` (created by Batch 0)
- Create: `dan/release/cutover_rehearsal.py`
- Create: `scripts/dan-cutover-rehearsal`
- Modify: `tests/cutover_helpers.py`
- Create: `tests/test_cutover_rehearsal.py`
- Read only: `tests/fixtures/cutover/manifest.json`
- Read only: `tests/fixtures/memory_v1.sql`

- [ ] **Step 1: Write RED producer and containment tests**

```python
def test_rehearsal_derives_green_envelope_from_both_successful_drills(
    rehearsal_fixture,
) -> None:
    report = rehearsal_fixture.run(fixture_exit=0, manual_reconciled=True)
    assert report.kind == "rollback_rehearsal"
    assert report.producer_id == "dan-cutover-rehearsal:v1"
    assert report.subject_sha == rehearsal_fixture.head
    assert report.artifact_sha256 == rehearsal_fixture.diff_sha256
    assert {item.role for item in report.input_evidence} >= {
        "subject_diff",
        "fixture_recipe",
        "fixture_manifest",
        "memory_v1_schema",
        "cutover_helpers",
        "manual_drill_request",
        "manual_before_tree",
        "manual_after_tree",
    }
    assert report.status == "green"


def test_rehearsal_cannot_self_attest_status_or_subject(rehearsal_fixture) -> None:
    options = rehearsal_fixture.parser_option_strings()
    assert "--status" not in options
    assert "--subject-sha" not in options
    assert "--producer-id" not in options


def test_rehearsal_rejects_active_home_and_repo_evidence_root(rehearsal_fixture) -> None:
    for forbidden in (rehearsal_fixture.active_home, rehearsal_fixture.repo):
        with pytest.raises(UnsafeEvidenceRoot):
            rehearsal_fixture.run(evidence_root=forbidden)
```

Also test a failing fixture recipe, manual apply failure, rollback failure, unequal before/after tree hashes, invalid journal reconciliation, missing/invalid manual authorization input, HEAD or diff changing during the run, pre-existing output, non-empty/symlinked isolated HOME, a system-host factory being reached, output outside `DAN_RELEASE_EVIDENCE_ROOT`, and fail-closed `unknown` for any unrecognized result field. The active HOME tree hash must remain unchanged in success and every failure case.

- [ ] **Step 2: Implement one fixed rehearsal producer**

First verify RED:

```bash
DAN_BATCH1_ROOT="$(mktemp -d /private/tmp/dan-batch1.XXXXXX)"
mkdir -m 700 "$DAN_BATCH1_ROOT/home" "$DAN_BATCH1_ROOT/evidence"
export HOME="$DAN_BATCH1_ROOT/home"
export DAN_RELEASE_EVIDENCE_ROOT="$DAN_BATCH1_ROOT/evidence"
export DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1
export PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1
export RUFF_CACHE_DIR="$DAN_RELEASE_EVIDENCE_ROOT/ruff-cache"
.venv/bin/python -m pytest -q -p tests.audio_guard_plugin -p no:cacheprovider \
  tests/test_cutover_rehearsal.py
```

Expected: the producer module/script are missing and no SHA-bound derived envelope exists. Only then implement.

`scripts/dan-cutover-rehearsal` exposes only:

```text
--repo <absolute-or-resolvable-repository>
--isolated-home <fresh-empty-owner-only-directory>
--manual-drill-request <canonical-json-authorization>
--evidence-output <new-json-path-under-DAN_RELEASE_EVIDENCE_ROOT>
```

The canonical request has schema version 1, kind `dan-cutover-isolated-manual-drill`, the current subject SHA, current diff SHA-256, an authorization ID, and scope `fixture-only-no-launchctl-no-active-home`. It is an authorization input, never a caller-supplied result. Hash it into `input_evidence`.

At start and finish, derive `subject_sha` from `git rev-parse HEAD` and derive `subject_diff_sha256` from a canonical stream containing `git status --porcelain=v2 -z`, `git diff --binary --no-ext-diff HEAD`, plus path/content hashes for listed untracked files. A moved HEAD or changed diff makes the report red. The caller cannot override either value.

The fixed fixture recipe is exactly `tests/test_cutover_cli.py`, `tests/test_cutover_state_machine.py`, `tests/test_cutover_resume_reconciliation.py`, `tests/test_cutover_intake_handoff.py`, `tests/test_rollback_targets.py`, `tests/test_cutover_rollback.py`, and `tests/test_cutover_no_replay.py`. Run it with `Path(sys.executable).resolve()`, `-p tests.audio_guard_plugin`, an isolated HOME/XDG/TMP/runtime tree, `DAN_DISABLE_AUDIO=1`, `DAN_DISABLE_MIC=1`, and `DAN_CONFIG`/`VOICE_CONFIG_DIR` removed. Hash the fixed argv as `fixture_recipe`.

The separately authorized manual drill then uses `tests/cutover_helpers.py` plus the two repository fixtures to build a fresh tree strictly beneath `--isolated-home`; invokes the real `cutover_main()` and `rollback_main()` contracts with the exact manifest SHA, an external per-run journal directory, and only the fixture host; and requires the final tree hash, database proofs, journal FSM, and intake state to equal the recorded pre-cutover state. Every subprocess receives that same isolated environment. It must fail if `SystemCutoverHostAdapter`, launchctl, TTS, microphone, network, active HOME, or an active database path is reached.

Consume Batch 0's frozen `ReleaseEvidenceEnvelope`, `active_evidence_roots_from_environment()`, `validate_evidence_root()`, `canonical_envelope_sha256()`, and `write_evidence_envelope_exclusive()` interfaces without modifying their schema or `CORE_EVIDENCE_PRODUCERS`. Import `ROLLBACK_REHEARSAL_PRODUCER_ID` from the sole Batch 0 `dan/release/producer_ids.py` authority; the rehearsal module and Batch 5 registry must not define another literal. Emit kind `rollback_rehearsal`; `artifact_sha256` is the derived subject diff hash. Record hashes, not stdout, database rows, tokens, or HOME contents. Derive `green` only when both drills reconcile, HEAD/diff stay fixed, every input hash validates, and `unknown_evidence` is empty. Missing or malformed evidence yields `unknown`; a recognized failed invariant yields `red`. The output is exclusive mode `0600`, file and parent are fsynced, and the validated evidence root is external, absolute, pre-created `0700`, symlink-free, and disjoint from all roots listed in Global Constraints.

- [ ] **Step 3: Verify GREEN and run the full Batch 1 gate**

```bash
DAN_BATCH1_ROOT="$(mktemp -d /private/tmp/dan-batch1.XXXXXX)"
mkdir -m 700 "$DAN_BATCH1_ROOT/home" "$DAN_BATCH1_ROOT/evidence"
export HOME="$DAN_BATCH1_ROOT/home"
export DAN_RELEASE_EVIDENCE_ROOT="$DAN_BATCH1_ROOT/evidence"
export DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1
export PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1
export RUFF_CACHE_DIR="$DAN_RELEASE_EVIDENCE_ROOT/ruff-cache"
.venv/bin/python -m pytest -q -p tests.audio_guard_plugin -p no:cacheprovider \
  tests/test_sqlite_family.py tests/test_sqlite_validation.py tests/test_sqlite_backup.py \
  tests/test_legacy_data_migration.py tests/test_db_schema.py \
  tests/test_intake_gate.py tests/test_intake_api.py tests/test_text_turn_pipeline.py \
  tests/test_voice_api_contract.py tests/test_cutover_preconditions.py \
  tests/test_cutover_host_adapter.py tests/test_cutover_cli.py \
  tests/test_cutover_journal.py tests/test_cutover_resume_reconciliation.py \
  tests/test_cutover_state_machine.py tests/test_cutover_intake_handoff.py \
  tests/test_rollback_targets.py tests/test_cutover_rollback.py \
  tests/test_cutover_no_replay.py tests/test_cutover_rehearsal.py
.venv/bin/ruff check dan/migration dan/release/cutover_rehearsal.py \
  scripts/dan-cutover-rehearsal \
  dan/daemon/intake.py dan/api/routes_intake.py dan/api/client.py \
  dan/store/migrations.py tests/cutover_helpers.py tests/test_sqlite_family.py \
  tests/test_sqlite_validation.py tests/test_intake_gate.py tests/test_cutover_journal.py \
  tests/test_cutover_intake_handoff.py tests/test_rollback_targets.py \
  tests/test_cutover_rehearsal.py
git diff --check
```

Expected: all pass in a fresh isolated HOME and external evidence root. Reviewers inspect failure ordering, symlink/TOCTOU containment, operation-ID handoff, single bootstrap, resume reconciliation, and producer derivation rather than only happy paths. The automated gate never performs a live cutover, live rollback, launchctl mutation, audio action, or active-HOME write; the isolated manual drill runs later only under its separate explicit authorization.
