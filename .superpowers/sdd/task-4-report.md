# Task 4 - DAN Foundation Release 1 report

## Outcome

`DONE_WITH_CONCERNS`

Task 4 is implemented in the isolated worktree
`$HOME/Documents/dev/DAN-task2-wt` on branch
`agent/dan-release1-integration`, starting from base `503a343`.

The internal runtime was renamed from Jarvis to DAN, the final package and
entrypoints are active, the planned baseline `--compare` path is implemented,
and the final classified run introduced no new failure IDs. Task 5 was not
started. No audio, TTS, microphone, daemon, launchd service, or live runtime was
started. No other worktree was modified.

The concern classification is retained because the accepted baseline still
contains 271 known failures and historical migration/documentation provenance
still contains deliberately narrow legacy names.

## Scope and ownership

- Worktree: `$HOME/Documents/dev/DAN-task2-wt`
- Branch: `agent/dan-release1-integration`
- Base HEAD: `503a343`
- Task ownership: Task 4 only
- Comparison-only dirty worktree: not read from or modified during implementation
- Task 5: not started
- Runtime/audio side effects: none

## Read-only Task 3 reference

Before the first full Task 4 run, the canonical Task 3 baseline was copied
byte-for-byte to:

`.superpowers/sdd/task-3-final-baseline-2379.json`

- Mode: `0400`
- SHA-256: `e56e4a08cc2bc7795a09be0cc2e09b1eb34902bd046af9b700d37bdb1c211bf3`
- Task 3 collected/isolated: `2379 / 2379`
- Task 3 live-manual: `0`
- Task 3 known failures: `272`

Additional immutable evidence snapshots:

- `.superpowers/sdd/task-4-red-baseline-2385.json`, mode `0400`, SHA-256
  `3a877656e2a61569c1254c7569f6de06d936a29f50813c87c4fd62b85fe21e0e`
- `.superpowers/sdd/task-4-final-baseline-2385.json`, mode `0400`, SHA-256
  `f99b5f550eca395e24512958794d81bd3e88cdb45a2d31d7a0e555c9b5983b61`

## TDD evidence

All Python test commands used:

`$HOME/Documents/dev/jarvis/.venv/bin/python`

### RED 1 - final-name contracts

Final-name tests were added before the rename in:

- `tests/test_imports.py`
- `tests/test_scaffold_contracts.py`
- `tests/test_launchd_assets.py`
- `tests/test_test_safety.py`

They required the `dan` package, final scripts and entrypoints, final launchd
label/assets, final runtime paths, import-time isolation from `~/.jarvis`, and
the planned baseline `--compare` behavior.

Focused final-name result before implementation:

`16 failed, 7 passed`

The failures were the expected missing `dan` package/assets/label/paths and old
entrypoint contract.

Focused `--compare` result before implementation:

`2 failed, 30 deselected`

The failures proved that argparse did not expose an exact `--compare` option
and that same-path reference snapshotting was absent.

### RED 2 - first full renamed collection

The first full classified Task 4 run collected the final-name tests and failed
closed against the Task 3 reference:

- Collected/isolated: `2385 / 2385`
- Live-manual: `0`
- Failure IDs: `288`
- New: `17`
- Removed: `1`
- Unchanged: `271`
- Duration: `369.298s`

The 17 new IDs exposed incomplete active-name/path changes. They were fixed,
then the focused regression set passed: `17 passed`.

### RED 3 - migration fixture provenance

The second full classified run reported:

- Failure IDs: `274`
- New: `3`
- Removed: `1`
- Unchanged: `271`
- Duration: `366.761s`

The three new IDs exposed an over-broad rename of historical `auto_jarvis`
migration fixtures to `auto_dan`. The fixtures were restored as legacy source
provenance, then the focused regression set passed: `3 passed`.

### GREEN - final classified comparison

Command:

```text
$HOME/Documents/dev/jarvis/.venv/bin/python scripts/dan-test-baseline --compare $HOME/.dan/migration/test-baseline.json
```

Result: exit `0`.

- Expected collected: `2385`
- Collected/isolated: `2385 / 2385`
- Live-manual: `0`
- Failure IDs: `271`
- New: `0`
- Removed: `1`
- Unchanged: `271`
- Duration: `366.896s`

Removed failure ID:

`tests/test_scaffold_contracts.py::test_runtime_scaffold_avoids_legacy_escape_hatches`

The command snapshotted the reference before overwriting the canonical output,
including the reference-equals-output case. It rejected new failure IDs
fail-closed. Existing strict `--expect-collected` behavior and report
sanitization remain covered by the safety suite.

## Collection delta

Task 3 reference: `2379` tests.

Task 4 final: `2385` tests.

Exact delta: `+6`, comprising four final-name/runtime contract tests and two
baseline `--compare` contract tests.

## Implementation

Primary tracked moves:

- `jarvis/` -> `dan/`
- `config/jarvis.example.toml` -> `config/dan.example.toml`
- `scripts/jarvis` -> `scripts/dan`
- `scripts/jarvis-panel` -> `scripts/dan-panel`
- `scripts/jarvisd` -> `scripts/dand`
- `launchd/com.ozzy.jarvisd.plist.example` ->
  `launchd/com.dan.dand.plist.example`
- `tests/test_jarvis_lifecycle.py` -> `tests/test_dan_lifecycle.py`

The final runtime contract now uses:

- Package: `dan`
- Distribution: `dan-runtime`
- CLI entrypoint: `dan = dan.cli:main`
- Daemon entrypoint: `dand = dan.cli:daemon_main`
- MCP entrypoint: `dan-memory-mcp = dan.mcp.memory_server:main`
- Launchd label: `com.dan.dand`
- Runtime/config root: `~/.dan`
- Config: `~/.dan/config.toml`
- Database: `~/.dan/dan.db`

Imports, package references, entrypoints, active labels, thread names, user
agents, environment variables, runtime paths, panel assets, smoke scripts, and
launchd helpers use the final DAN names. Importing `dan` neither reads nor
migrates live `~/.jarvis` state and does not create `~/.dan`; legacy import is a
future cutover operation.

`scripts/dan-test-baseline --compare REFERENCE` now:

- accepts the exact planned option without argparse abbreviation ambiguity;
- validates the canonical reference report;
- snapshots reference bytes before collection and output writes;
- handles reference and output resolving to the same canonical path;
- runs the full classified collection;
- compares failure IDs against the preserved snapshot;
- returns non-zero when new IDs appear;
- preserves strict `--expect-collected` and sanitization contracts.

Tracked implementation scope before adding report/evidence: 273 files changed,
2759 insertions, 2536 deletions. Most files are mechanical import/package-name
updates required by the package rename.

## Verification

Focused import/scaffold/launchd contract:

`23 passed in 0.19s`

Full baseline safety contract:

`32 passed in 3.42s`

Collection:

`2385 tests collected in 0.33s`

Static checks:

- `python -m compileall -q dan tests`: passed
- `python -m py_compile scripts/dan-test-baseline`: passed
- `git diff --check`: passed

## Narrow legacy-name scan

Command:

```text
rg -n '\b(jarvis|Jarvis|DANv2)\b' dan scripts launchd config tests
```

Result: 90 matching lines in 16 files. There are zero active matches in
`scripts`, `config`, or `launchd`. Every remaining file is path-specific
migration provenance or a narrow regression test:

- `dan/diagnostics/legacy_dan.py`: diagnoses and reports legacy Jarvis assets;
  historical program/category/provenance names are input data.
- `dan/migration/inventory.py`: inventories old Jarvis/DANv2 repositories,
  `~/.jarvis`, old databases/binaries/model caches, and donor voice paths.
- `dan/migration/legacy_data.py`: defines the legacy Jarvis source schema,
  snapshot filename, source IDs, and provenance messages for donor import.
- `dan/runtime/supervisor.py`: detects obsolete `start-jarvis.sh` and
  `com.ozzy.jarvis.plist`; the active official label is `com.dan.dand`.
- `tests/test_imports.py`: negative assertions reject a local `jarvis` package
  or entrypoint and prohibit import-time migration of live `~/.jarvis`.
- `tests/test_launchd_assets.py`: narrow negative assertion forbids the legacy
  launchd label in active assets.
- `tests/test_legacy_dan_report.py`: fixtures and assertions cover the legacy
  Jarvis diagnostic and historical launcher name.
- `tests/test_legacy_data_migration.py`: donor database fixtures, identifiers,
  and messages prove Jarvis-to-DAN migration provenance.
- `tests/test_memory_contract.py`: pins the historical docs namespace
  `project/jarvis/*`; broad documentation migration is outside Task 4.
- `tests/test_migration_inventory.py`: fixtures represent Jarvis/DANv2 source
  roots, old `~/.jarvis` data, and donor model paths.
- `tests/test_migration_inventory_fix_first.py`: old Jarvis repository-root and
  path-hash fixtures are migration inputs.
- `tests/test_migration_inventory_review.py`: historical Jarvis branch refs and
  DANv2 refs are provenance inputs.
- `tests/test_panel_assets.py`: pins historical runbook text `jarvis-token.`;
  active app tests separately require `dan-token.`.
- `tests/test_panel_menubar.py`: pins historical runbook text
  `scripts/jarvis-panel`; active launcher tests require `scripts/dan-panel`.
- `tests/test_scaffold_contracts.py`: pins historical `REVIEW_HANDOFF` text and
  the legacy diagnostic launcher; active scaffold assertions require DAN names.
- `tests/test_smoke_script.py`: historical runbook safety statements assert
  that old smokes do not touch real `~/.jarvis`; active smoke scripts use
  `~/.dan`.

## Remaining risks and concerns

- The accepted classified baseline still contains 271 known failure IDs. Task 4
  introduced zero new IDs and removed one, but it does not claim to fix the
  pre-existing failure set.
- Historical docs and migration provenance intentionally retain old names.
  Their broad editorial migration belongs to later scoped work; changing them
  here would erase source provenance or cross into Task 5/later documentation.
- No daemon, launchd job, audio, TTS, microphone, or live migration was started.
  Runtime cutover is therefore deliberately unperformed and unclaimed.

## Independent review fix wave

Review verdict `FIX FIRST` was addressed after commit `155ce99`.

### Corrections

- `test_active_runtime_payloads_do_not_advertise_approvals` now installs a
  hermetic brain manager before starting the app and carries a tripwire that
  fails if the production Claude CLI adapter is reached. The production
  configuration remains cold-Claude-only; only the automatic test is isolated.
- `dan-test-baseline` now uses mutually exclusive operational modes, so report
  verification/comparison cannot bypass a requested collection run.
- `dand --config PATH` and `dand --config=PATH` place global options before the
  hidden `daemon run` hierarchy and pass the selected path to the daemon
  handler.
- The runtime emits only `<dan_tool_call>`. The parser and speech chunker accept
  legacy `<jarvis_tool_call>` input and suppress complete, split, and malformed
  legacy payloads fail-closed so raw JSON is never spoken.
- Active runbooks, README, CLAUDE contract, script variables, runtime-test
  environment, and active assertions use final DAN names. `JARVIS_REPO` and
  `JARVIS_DB_PATH` are gone from those active surfaces.

### Test evidence

- Regression nodes covering all review findings: `22 passed in 0.42s`.
- Broader safety/CLI/parser/chunker/import/contracts package:
  `173 passed, 1 deselected in 4.66s`. The deselected node is a pre-existing
  forbidden-string baseline failure; the full comparison below is the
  authoritative regression gate.
- `python -m compileall -q dan tests`: passed.
- `git diff --check`: passed.
- Full hermetic baseline comparison against the verified `2385 / 271`
  reference: `2400 collected`, `2400 isolated`, `0 live-manual`, `270`
  failures, `0` new failure IDs, `1` removed failure ID. The removed ID is
  `tests/test_no_approval_surface.py::test_active_runtime_payloads_do_not_advertise_approvals`.
- A final comparison after adding three extra CLI conflict cases used the
  intermediate `2397 / 270` report and returned `0` new, `0` removed, and
  `270` unchanged failure IDs.
- Canonical report: `~/.dan/migration/test-baseline.json`, mode `0600`.
- Immutable task snapshot:
  `.superpowers/sdd/task-4-fix-baseline-2400.json`, mode `0400`.

### Final legacy-name inventory

Command:

```text
rg -n -i '\bjarvis\b|JARVIS_|\.jarvis|jarvisd|com\.ozzy\.jarvis' \
  dan config scripts launchd README.md CLAUDE.md docs/runbooks tests
```

Result after the re-review corrections: 153 matching lines across the paths
below. There are zero matches in active `config`, `scripts`, `launchd`, or
`README.md` files. The one active-runbook match is the exact external operator
source path documented below.
Every remaining path has this explicit disposition:

- Physical current dependency paths: `CLAUDE.md` (1) names the real external
  broker at `Documents/dev/dan/tools/jarvis/voice_broker.py`, and
  `docs/runbooks/G4_LIVE_GATE.md` (1) names the real operator source at
  `Desktop/Jarvis/JARVIS-NEXT-STEPS-FOR-OZZY.md`; renaming either text would
  make the operator command false.
- Legacy diagnostics: `dan/diagnostics/legacy_dan.py` (16) and
  `tests/test_legacy_dan_report.py` (12) identify historical assets and the
  compatibility launcher by their real names.
- Migration provenance/input: `dan/migration/inventory.py` (20),
  `dan/migration/legacy_data.py` (27), `dan/migration/db_report.py` (1),
  `tests/test_legacy_data_migration.py` (30),
  `tests/test_migration_inventory.py` (6),
  `tests/test_migration_inventory_fix_first.py` (4), and
  `tests/test_migration_inventory_review.py` (3) preserve donor paths,
  schemas, source IDs, branch refs, and audit messages.
- Legacy runtime detection: `dan/runtime/supervisor.py` (2) and
  `tests/test_runtime_supervisor.py` (1) detect obsolete process/launchd names
  so cutover can report them.
- Provider input compatibility: `dan/brain/tool_call_parser.py` (2),
  `dan/voice/chunker.py` (2), `tests/test_brain_cli_adapters.py` (5),
  `tests/test_sentence_chunker.py` (3), and
  `tests/test_voice_streaming_contract.py` (1) accept but never emit or speak
  the legacy tool-call tag.
- Negative product-contract assertions and legacy fixtures:
  `tests/test_imports.py` (3), `tests/test_launchd_assets.py` (2),
  `tests/test_memory_contract.py` (1), `tests/test_scaffold_contracts.py` (9),
  and `tests/test_test_safety.py` (1). These matches either reject old active
  names or model old input that must remain detectable.

The earlier 90-line inventory in this report is superseded by this broader,
case-insensitive final inventory and its path-by-path dispositions.

### Re-review minor closure

- Active memory project contracts were renamed from `JARVIS_*.md` to
  `DAN_*.md`; their product titles, package paths, daemon name, and config path
  now use the final DAN surface. Historical handoffs not used as current
  contracts remain explicitly historical.
- The G4 live gate now points to the actual external operator source
  `~/Desktop/Jarvis/JARVIS-NEXT-STEPS-FOR-OZZY.md`. Its exact legacy name is a
  physical dependency exception, not a product rename leak.
- Focused contract verification: `40 passed in 0.05s`.
- Full collection remains exactly `2400 tests`; no node was added or removed by
  the documentation corrections.
