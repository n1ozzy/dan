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

```bash
python3 -m pytest --collect-only -q
python3 scripts/dan-test-baseline --expect-collected 2347
```

## Fresh baseline evidence

The final Task 2 run collected `2347` nodes: `2347` isolated and `0`
`live_manual`. It completed in `363.835` seconds with `2074` passed and `273`
sanitized failing node IDs. The baseline therefore remains a truthful failed
baseline for the pre-existing suite debt; the failure count decreased from the
previous `286`-ID report and no Task 3 test is included.

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
