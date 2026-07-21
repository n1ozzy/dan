# E2E MVP Smoke (F1)

> **Partially stale — corrected 2026-07-21.** The harness script still exists,
> but two rows of the criterion map below were written for the approval-gated
> build and no longer describe the code:
>
> - **§6.5 "model tool call: policy(source) → approval → explicit execute →
>   ToolRun → continuation" is FALSE.** `ToolPermissionPolicy.decide()` returns
>   ALLOW unconditionally and `ToolRegistry.request_tool()` executes immediately,
>   ignoring its policy/source/approval arguments. There is no approval step.
> - **§6.10 "rejected approval never executes; duplicate execute = 409"**
>   describes the legacy `/approvals` HTTP surface, not the tool path.
> - Consequently step [4]'s "receives `approval.created` as a live push" and the
>   §6.4 row depend on an event that model-originated tools no longer emit.
>
> Still true and still worth running: §6.6 (`file_read` outside approved roots is
> BLOCKED — enforced inside `dan/tools/file_tool.py`, realpath'd, fail-closed on
> empty roots), §6.13 (no raw secrets in events/DB — redaction plus the
> 4096-char persistence cap are real), §6.7 (transport token), §6.15
> (report-only runtime conflicts). Treat the rest as unverified until re-run.

`scripts/smoke-e2e-mvp.sh` walks the operator acceptance scenario from
MASTER_PLAN §6 against ONE temporary daemon instance (port 41799, temp DB,
fake CLI brain, no providers, no network beyond localhost). It is the
stabilization harness for the MVP-operator milestone: the point is that all
of these behaviors hold at the same time on the same daemon, not just in
isolated per-feature smokes.

## Run

```bash
scripts/smoke-e2e-mvp.sh
```

Set `SMOKE_KEEP_ARTIFACTS=1` to keep the temp directory for inspection.

## What the harness proves (criterion map)

| §6 | Criterion | Proven by |
|----|-----------|-----------|
| 1 | daemon starts and reports health | phase 1 step [1] |
| 2 | one input = one turn; history survives restart | steps [2] and [13] |
| 3 | events explain the turn lifecycle | step [2] (`brain.requested`, `brain.responded`, `turn.finished`) |
| 4 | cockpit sees live truth via stream, not polling | step [4]: a websocket client connected before the tool turn receives `approval.created` as a live push |
| 5 | model tool call: policy(source) → approval → explicit execute → ToolRun → continuation | step [5] |
| 6 | `file_read` outside approved roots = BLOCKED | step [9] |
| 7 | mutating endpoints require the local token | step [3] |
| 10 | rejected approval never executes; duplicate execute = 409 | steps [6] and [8] |
| 11 | brain switch keeps history | steps [10] and [13] (marker present in the post-switch prompt; adapter persisted across restart) |
| 12 | worker job never speaks and never writes memory directly | step [11] (candidate inactive, voice_queue empty, no turns created) |
| 13 | no raw secrets in events/DB | step [7] (secret passed through the tool; `tool_runs`/`events` hold only redacted text) |
| 15 | runtime conflicts are report-only | step [12] |

## Criteria proven elsewhere (not in this harness)

- **§6.6 symlink escape** — unit tests (`tests/test_tool_permissions.py`,
  `tests/test_file_read_tool.py`) cover realpath containment; a temp harness
  cannot exercise it better than the tests already do.
- **§6.8 Accessibility read/act** — `scripts/smoke-ui-read.sh` and
  `scripts/smoke-ui-act.sh` on the fake backend, plus the D1/D2 live gate
  (real TCC grant, real windows) recorded in the project handoff.
- **§6.9 screen capture + OCR** — `scripts/smoke-screen-read.sh` on the fake
  backend, plus the D4 live gate on the real ScreenCaptureKit path.
- **§6.14 launchd manual install, single label** — F2 launchd lifecycle
  scripts (`install-launchd.sh` prints its plan and is never run
  automatically); this smoke never touches launchd.
- **§6.16 pytest + all smokes** — CI habit: `pytest tests -v` plus every
  `scripts/smoke-*.sh` PASS is the phase gate itself.

## Safety

The harness runs entirely against a temporary daemon with a temp database
and temp runtime directory. It never touches `~/.dan`, never loads
launchd, never calls a real provider, and never reads outside its own
workspace (the out-of-roots read is expected to come back `blocked`).
