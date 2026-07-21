# Tools and Approvals

> **Legacy runbook — the approval loop is not in the tool path.**
> `ToolRegistry.request_tool()` ignores its `permission_policy` / `source` /
> `approval_gate` arguments and executes immediately; `ApprovalGate` is never
> called for a tool run. What actually limits a tool is per-tool and is
> documented in [SECURITY_MODEL.md](../SECURITY_MODEL.md) §2.
> This file is kept for the `approvals` table/event shape and the HTTP surface.

## The `approvals` table and its events

`ApprovalGate` (`dan/tools/registry.py`) writes the `approvals` table when
something calls it directly. Row lifecycle:

```text
pending -> approved -> explicit execute
pending -> rejected
```

With an `EventStore` present it appends `approval.created`,
`approval.approved` and `approval.rejected`. A decision event fires exactly once
after the row is updated and carries the approval ID, tool name, requested risk,
final status, decision, decided timestamp, rejection reason and the
turn/correlation IDs when the request had them. `EventStore` redacts obvious
secrets before persistence.

Duplicate approve/reject attempts are refused as non-pending decisions and add
no second decision event.

## API endpoints

```text
GET  /tools
POST /tools/request
GET  /approvals
POST /approvals/{id}/approve
POST /approvals/{id}/reject
POST /approvals/{id}/execute
```

All of them require `app.started`; otherwise they return `503`.

Approve and reject update the row only: approval **does not execute automatically**
and approved tools are not replayed. The explicit execute endpoint runs only an
approval whose status is `approved` — missing approvals return `404`; pending,
rejected, expired, non-approved and already-executed ones return `409`; an
unknown tool name in the payload returns `404`.

Duplicate execution prevention is keyed on `tool_runs.approval_id` — once a run
exists for an approval, a second execute returns `409` without invoking the
handler.

Successful execution returns:

```json
{
  "ok": true,
  "approval_id": "...",
  "tool_run": {},
  "result": {},
  "continuation": {
    "applied": true,
    "status": "finished",
    "turn_id": "...",
    "final_text": "..."
  }
}
```

`continuation` is additive and appears only when a one-shot continuation is
attempted.

## Manual smoke harnesses — NOT ENFORCED TODAY

Both scripts still exist and are still syntax-checked by the test suite, but
they encode the pre-Release-1 approval contract and **fail against the current
code**: `scripts/smoke-tools-approvals.sh` expects `GET /tools` to list
`approval_probe` (it is registered nowhere) and expects
`POST /tools/request` to answer `approval_required` (it executes instead).
Treat every gating claim below as a description of the harness, not of the
runtime.

```bash
scripts/smoke-tools-approvals.sh
SMOKE_KEEP_ARTIFACTS=1 scripts/smoke-tools-approvals.sh
```

```bash
scripts/smoke-tool-continuation.sh
SMOKE_KEEP_ARTIFACTS=1 scripts/smoke-tool-continuation.sh
```

Both start a temporary `dand` with a temporary config, database, runtime home,
logs directory, runtime directory and PID file, make localhost HTTP requests
only, and stop only the child daemon PID they started. Neither starts workers,
voice, audio, the panel or launch supervision. The continuation harness uses a
fake local CLI brain script created inside the smoke directory and wired through
the `claude_cli` adapter config — no real providers, no external network — and
listens on `127.0.0.1:41772`.

What the tools harness was written to prove: `GET /tools` lists `echo`,
`system_status` and `approval_probe`; `POST /tools/request` runs `echo`;
`POST /tools/request` for `approval_probe` creates a pending approval instead of
executing; approve and reject change status only; execute runs the approved
probe exactly once and records a `tool_runs` row carrying the `approval_id`; a
second execute returns `409`; a rejected approval cannot execute; `worker_jobs`
and `voice_queue` stay empty.

What the continuation harness was written to prove: a model-originated
`approval_probe` request produces one pending approval and an
`awaiting_approval` turn; approve does not execute; execute records the run and
applies the one-shot continuation, so the turn becomes `finished` with
`tool_result_continuation` metadata; a duplicate execute returns `409` and
triggers no second continuation.

Neither harness proves:

- no real shell execution
- no file writing
- no network tools
- no worker replay
- no provider tool calling yet

## Troubleshooting

- Port already in use: free `127.0.0.1:41769` (tools) or `127.0.0.1:41772`
  (continuation), or edit the smoke port locally before running.
- Permission denied: `chmod +x scripts/smoke-tools-approvals.sh`.
- Missing `.venv`: the scripts use `.venv/bin/python` when present and fall back
  to `python3` or `python`.
- Daemon health timeout: rerun with `SMOKE_KEEP_ARTIFACTS=1` and inspect
  `daemon.stdout.log` / `daemon.stderr.log` in the printed smoke directory.
