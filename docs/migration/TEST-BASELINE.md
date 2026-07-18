# DAN Release 1 test baseline

`scripts/dan-test-baseline` is the Release 1 full-suite gate.  It collects
before execution, assigns every node exactly one safety class, refuses any
`live_manual` node, and runs every remaining node ID explicitly under a
disposable `HOME`, runtime directory, and database path.

## Count ledger

The original verified collection was `2176`.  The reconciled Task 2 gate is:

`2176 + 32 (Task 1) = 2208`

`2208 + 41 = 2249`

`2249 + 29 = 2278`

`2278 + 49 = 2327` committed pre-Task-2 nodes

`2327 + 19 (Task 2 safety contracts) = 2346`

`2346 + 1 (Task 2 hermetic transport-token regression) = 2347`

`2347` is therefore the only accepted collection count for this Task 2 commit.
The additional test is required because the existing transport-token fixture
could invoke the production Claude adapter during an automatic baseline; it now
uses the repository's hermetic test adapter and asserts that no production call
occurs.  Task 3 is not included.  A future change must state its own delta
before it changes this argument.

The independent-review follow-up adds `10` Task 2 regression contracts for
ancestor `conftest` and plugin fixtures, controlled child Python execution,
sanitized failure IDs, set comparison, and status invariants:

`2347 + 10 (Task 2 FIX FIRST regressions) = 2357`

`2357` is the accepted follow-up collection count. Task 3 remains excluded.

```bash
python3 -m pytest --collect-only -q
python3 scripts/dan-test-baseline --expect-collected 2357 \
  --compare-report .superpowers/sdd/task-2-reviewed-baseline-2347.json
```

## Fresh baseline evidence

The final Task 2 follow-up collected `2357` nodes: `2357` isolated and `0`
`live_manual`. It completed in `363.941` seconds with `2085` passed and `272`
sanitized failing node IDs. The canonical set comparison against the preserved
`2347`/`273` report found `0` new IDs and removed only
`tests/test_db_schema.py::test_runtime_files_do_not_contain_forbidden_legacy_strings`.

The committed, sorted sanitized failure ledger is
[`TEST-BASELINE-failures.txt`](TEST-BASELINE-failures.txt). It contains no raw
parameter payloads; parametrized IDs use stable `param-<sha256-prefix>` tokens.

## Isolation and report contract

The child process receives `DAN_TEST_MODE=1`, `DAN_DISABLE_AUDIO=1`, and
`DAN_DISABLE_MIC=1`, plus temporary `HOME`, runtime, and SQLite paths.
Unmarked calls to audio/microphone binaries, `launchctl`, `/tmp/dan-*`, real
home databases, or the live voice port fail the safety audit; an explicit
`live_manual` mark also refuses the automatic run.  Nothing is silently
skipped.

The private report is atomically written to
`~/.dan/migration/test-baseline.json` with mode `0600`.  It contains only
counts, duration, status, and sanitized failing pytest node IDs.  Verify it
without re-running the suite:

```bash
python3 scripts/dan-test-baseline --verify-report ~/.dan/migration/test-baseline.json
```

## Task 14 Step 1 gate run (2026-07-18)

The frozen release branch (post Jarvis-M1 recast) collected `2621` tests:
`268` failed and `2353` passed in `393.61` seconds; the canonical comparison
against the preserved report found `0` new and `0` removed IDs
(`{"new": [], "removed": []}`), so the known-failure contract holds unchanged.
The `-m 'not live_manual'` verification pass on the same tree reported `267`
failed / `2354` passed — the same known set with one known-flaky ID passing in
that run; no new failures in either pass.
