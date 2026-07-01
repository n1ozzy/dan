# Tools and Approvals

Prompt 13 adds the safety model for future Jarvis tools. It does not add real
shell execution, file writing, network access, workers, provider subprocesses,
or approval replay.

## ToolRegistry

`ToolRegistry` is the only in-process registry for Jarvis tools. It stores tool
metadata, exposes tool specs, checks each request with `ToolPermissionPolicy`,
and runs a handler only when the permission decision is `allow`.

Rejected, blocked, and approval-required requests do not execute their tool
handler.

The daemon default registry contains:

- `echo`, risk `safe_read`: returns the request arguments.
- `system_status`, risk `safe_status`: returns a static placeholder message.
- `approval_probe`, risk `shell_read`: approval-required demo tool used by the
  manual smoke harness. It is not a shell tool, does not read files, does not
  write files, does not inspect processes, and does not use network access.

## Permission Categories

Jarvis v4.1 uses these risk values:

- `safe_read`: allowed.
- `safe_status`: allowed.
- `file_read`: allowed only when the placeholder approved-roots policy passes.
- `file_write`: approval required.
- `shell_read`: approval required.
- `shell_write`: approval required.
- `network`: approval required.
- `destructive`: blocked unless `destructive_tools_enabled` is true, then
  approval required.

Unknown risk values are blocked.

## ApprovalGate

`ApprovalGate` uses the existing `approvals` table. It creates pending approval
records, lists pending approvals, and updates pending approvals to `approved` or
`rejected`.

When an `EventStore` is available, it appends concise JSON-safe events:

- `approval.created`
- `approval.approved`
- `approval.rejected`

Obvious secrets are redacted from event payloads.

Approving a request does not execute the tool in Prompt 13. Execution after
approval is intentionally left for a later prompt.

Approving or rejecting `approval_probe` does not replay execution either. It
only proves that approval records and decision endpoints work.

## ToolRunRecorder

`ToolRunRecorder` uses the existing `tool_runs` table. It records requested,
finished, and failed tool runs and may append:

- `tool.requested`
- `tool.finished`
- `tool.failed`

The recorder does not execute tools. It only stores audit records around safe
tool requests that the registry is already allowed to run.

## API Endpoints

The daemon exposes:

- `GET /tools`
- `POST /tools/request`
- `GET /approvals`
- `POST /approvals/{id}/approve`
- `POST /approvals/{id}/reject`

These endpoints require `app.started`; otherwise they return `503`. Approve and
reject endpoints update approval status only. They do not replay or execute the
approved tool.

## Intentional Non-Goals

Prompt 13 intentionally does not implement:

- shell execution
- file writing
- real file reading
- network tools
- system mutation
- process inspection
- replay execution after approval
- worker integration
- voice or audio integration
- WebSocket or SSE tool streaming
- launchd installation or control

Providers may request tools later, but only the registry and approval gate may
allow tool execution. Blocked and rejected tools never execute.

## Manual Smoke

Run the tools and approvals smoke harness manually:

```bash
scripts/smoke-tools-approvals.sh
```

To keep the temporary runtime and logs for inspection:

```bash
SMOKE_KEEP_ARTIFACTS=1 scripts/smoke-tools-approvals.sh
```

The smoke starts a temporary `jarvisd` with a temporary config, database,
runtime home, logs directory, runtime directory, and PID file. It uses the mock
brain adapter, disables voice, disables launch supervision, disables destructive
tools, and makes localhost HTTP requests only.

It proves:

- `GET /tools` returns `echo`, `system_status`, and `approval_probe`.
- `POST /tools/request` executes `echo` and records a finished tool run.
- `POST /tools/request` for `approval_probe` creates a pending approval and
  does not execute the handler.
- `GET /approvals` shows the pending approval.
- Approve and reject endpoints update approval status without replaying
  execution.
- `GET /events` exposes tool and approval events.
- `worker_jobs` and `voice_queue` stay empty.
- The temporary database and runtime home are used instead of real `~/.jarvis`.
- The script stops only the child daemon PID it started.

It does not prove:

- no real shell execution
- no file writing
- no network tools
- no worker replay
- no provider tool calling yet
- no approval replay implementation

The smoke does not start workers, voice, audio, panel, provider subprocesses,
or any launch supervision.

## Troubleshooting

- Port already in use: free `127.0.0.1:41769` or edit the smoke port locally
  before running.
- Permission denied: run `chmod +x scripts/smoke-tools-approvals.sh`.
- Missing `.venv`: the script uses `.venv/bin/python` when present and falls
  back to `python3` or `python`.
- Daemon health timeout: rerun with
  `SMOKE_KEEP_ARTIFACTS=1 scripts/smoke-tools-approvals.sh` and inspect
  `daemon.stdout.log` and `daemon.stderr.log` in the printed smoke directory.
