# Task 3 fix report

Status: DONE_WITH_CONCERNS

Base commit: `632c66a1e19ad08d3db5c46007ae56314421a3ce`

## Scope and ownership

- Worktree: `/Users/n1_ozzy/Documents/dev/DAN-task3-wt`
- Branch: `agent/dan-task3`
- Only Task 3 implementation, tests, and this report were changed.
- No other worktree was touched and Task 4 was not started.

## Finding verification before edits

All review findings were checked against the implementation before production edits:

1. Critical semantic merge: `legacy_data.py` selected duplicates using only `title + body`.
   A parametrized RED regression changed `kind`, `priority`, `active`, and `metadata` independently;
   all four cases were incorrectly reported as merged.
2. Timestamp loss: `_iso()` called `.replace(microsecond=0)`. A RED regression imported
   `1720051203.123456` and `1720051204.654321`; both arrived without fractional seconds.
3. Target quiescence: an existing target was read and opened without
   `assert_quiescent_database()`. A real `BEGIN IMMEDIATE` connection plus deterministic lsof
   evidence was accepted instead of raising `ActiveWriterError`.
4. Orphan `compiled_contexts`: only missing summaries were rejected. A row referencing a missing
   legacy conversation was imported.
5. Review gate: no prior Task 3 report or gate evidence existed in the worktree.
6. Minor reporting: the sanitized report exposed totals only, so merged/rejected classes could not
   be compared from operator evidence without querying the database manually.

RED command and result:

```bash
pytest -q tests/test_legacy_data_migration.py
# 8 failed, 4 passed in 1.10s
```

## Fixes

- Memory blocks merge only when `kind`, `title`, `body`, normalized `priority`, normalized `active`,
  and semantically parsed JSON metadata all match. Same-title/body rows with any semantic difference
  are imported separately, preserving source semantics and lineage.
- Legacy REAL timestamps are rendered with six-digit microsecond precision.
- Every existing target must pass the same concrete DB/WAL/SHM quiescence check before provenance
  is read or migrations/imports open it.
- `compiled_contexts.conversation_id` must resolve through the imported legacy conversation map;
  orphans are rejected as `missing legacy conversation`. Imported rows also record the mapped target
  conversation ID in provenance metadata.
- `MemoryMigrationReport` and the sanitized operator report now contain deterministic classes grouped
  by source table, outcome, reason, and count. No row text or filesystem path is exposed.

## Regression coverage

- `test_same_title_and_body_only_merge_when_all_semantics_match` covers each semantic field called
  out by the Critical finding.
- `test_fractional_source_timestamps_preserve_microseconds` covers fractional timestamp retention.
- `test_existing_target_with_active_immediate_writer_is_rejected` covers target quiescence.
- `test_orphaned_compiled_context_is_rejected_with_auditable_reason` covers relational provenance.
- The main lineage test now verifies the exact report classes used by the review gate.

## Real-schema review gate

The live `~/.jarvis/jarvis.db` was excluded because `lsof` showed an active Python owner. It was not
passed to backup or migration code. The Jarvis source was the offline real backup
`~/.jarvis/backups/jarvis-persona-cleanup-2026-07-13.db`. The real legacy
`~/.dan/memory.db` had no open handles and was read only through SQLite Backup API into disposable
sources. No migration was run against either origin.

Gate command:

```bash
python - <<'PY'
# Inline harness: create read-only SQLite Backup API snapshots, then for A and B independently run
# migrate_databases(source copies, target), inspect counts/integrity/classes, rerun against the same
# target for idempotence, compare A with B, and verify both origin SHA-256 values are unchanged.
PY
```

Disposable gate root:
`/var/folders/_l/ng_v2knn45bgfcll6sq05j2h0000gn/T/dan-task3-real-schema-gate-a9a3cgnt`
(removed after evidence capture; no disposable private DB copies remain).

Origin evidence before and after gate:

- Offline Jarvis backup SHA-256:
  `be35c43f9fc3aacaf9933ce47a3ed147b6a8a57bfcafa10c9cbe64c5f25448d2`
- Real legacy memory SHA-256:
  `03bfafac07ef714b107ecfcaea66fe96e129ad37c217f80dacef85775f1571fb`
- Both hashes were unchanged after both rehearsals.
- Both source copies returned `PRAGMA integrity_check = ok`.

Results for both independent rehearsals A and B:

- First import: `669 imported, 0 merged, 0 rejected`.
- Outcome classes: `conversations/imported=340`, `turns/imported=329`.
- Merged/rejected class set: empty in both real datasets and equal across A/B.
- Jarvis rows preserved: `true`.
- Target `PRAGMA integrity_check`: `ok` after first and second migration.
- Key counts: conversations `30 -> 370`, turns `360 -> 689`, schema version rows `2 -> 4`,
  `migration_record_map=669`, `migration_sources=2`; all other inherited table counts were preserved.
- Second import on each target: `0 imported, 0 merged, 0 rejected`, empty classes, and every target
  table count identical to the first pass.
- A/B first reports, merged/rejected classes, and complete target count mappings were identical.

The real dataset contains no memory-block merge or rejection rows, so non-empty merged/rejected class
behavior is covered by the controlled fixture regressions rather than fabricated into the real copy.

## Verification

```bash
pytest -q tests/test_sqlite_backup.py tests/test_legacy_data_migration.py \
  tests/test_db_schema.py tests/test_daemon_db_concurrency.py
# 41 passed in 1.97s

ruff check jarvis/migration tests/test_sqlite_backup.py tests/test_legacy_data_migration.py
# unavailable: no ruff executable and `python -m ruff` reports `No module named ruff`

python -m compileall -q jarvis/migration \
  tests/test_sqlite_backup.py tests/test_legacy_data_migration.py
# COMPILE_OK

git diff --check
# exit 0
```

## Deferred minor and risks

- Explicit `memory_fts` ledger outcome is deferred. The brief fixes
  `migration_record_map.outcome` to `imported|merged|rejected`; the source FTS table is a derived
  external-content index, not a semantic record source. Calling it `rejected` would corrupt rejection
  totals, while adding `skipped/rebuilt` would change the specified lineage schema and require a new
  migration contract. The schema validator already proves it is the known derived index, and the new
  class report makes every actual imported/merged/rejected record auditable. A future scoped schema
  change may add source-object outcomes without lying in the record ledger.
- The quiescence contract has the normal lsof-to-open race. Final cutover still needs the Task 12
  intake stop/process stop protocol; Task 3 now enforces the required pre-open check on both sources
  and existing targets.
- The gate proves migration of a real offline Jarvis schema/data snapshot and the current real legacy
  memory schema/data. It deliberately does not claim a migration rehearsal of the currently active
  live Jarvis database.
