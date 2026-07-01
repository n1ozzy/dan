# Tools and Approvals

Prompt 15A keeps the Prompt 15 safety model and adds explicit parsing for
provider CLI tool-call blocks. It still does not add real shell execution, file
writing, network access, workers, provider-side tool calling, or automatic
execution of model-originated tool requests.

## ToolRegistry

`ToolRegistry` is the only in-process registry for Jarvis tools. It stores tool
metadata, exposes tool specs, checks each request with `ToolPermissionPolicy`,
and runs a handler only when the permission decision is `allow`.

Rejected, blocked, and approval-required requests do not execute their tool
handler.

Brain adapters may return `BrainResponse.tool_calls`. Claude CLI and Codex CLI
adapters can populate those calls by parsing explicit blocks from provider
stdout:

```text
<jarvis_tool_call>{"name":"approval_probe","arguments":{"reason":"demo"}}</jarvis_tool_call>
```

The parser accepts `name` as a required string plus optional `arguments`,
`id`, and `risk` fields. Missing `arguments` becomes `{}`. Malformed JSON,
missing names, and non-object arguments are recorded in adapter metadata and
are not executed.

The text turn pipeline validates each model-originated request against the
registry and records it as an approval request when possible. Model-originated
requests do not call `ToolRegistry.request_tool`, do not run handlers directly
from `BrainResponse`, and do not auto-execute even when the registered tool
risk is `safe_read` or `safe_status`.

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

Approving a request does not execute the tool. Approval does not execute
automatically, and approved tools are not replayed automatically by
`POST /approvals/{id}/approve`; a human or agent must call the explicit execute
endpoint.
An approved tool request does not execute automatically.

For model-originated tool calls, approval is mandatory by policy. The approval
payload stores the tool name, JSON-safe arguments, requesting origin, and turn
ID. A human or explicit client must later approve and call
`POST /approvals/{id}/execute`.

Rejected, pending, missing, duplicate, and blocked approvals do not execute.
`approval_probe` is still a harmless placeholder. After approval, explicit
execution returns `{"ok": true, "message": "approval_probe executed safely"}`
without shell, file, network, process, worker, voice, or provider side effects.

## ToolRunRecorder

`ToolRunRecorder` uses the existing `tool_runs` table. It records requested,
finished, and failed tool runs and may append:

- `tool.requested`
- `tool.finished`
- `tool.failed`

The recorder does not decide permission. It stores audit records around tool
requests that the app has already allowed to run. Explicit approved execution
records `requested`, `started`, and final `finished` or `failed` states with
the `approval_id` on the `tool_runs` row.

Prompt 15 model-originated capture records approval and tool events, but does
not create a finished `tool_runs` row before explicit execution. This keeps
model intent separate from actual execution.

## Model-Originated Tool Requests

`POST /input/text` may receive a `BrainResponse` containing `tool_calls`.
Jarvis captures those calls after `brain.responded` and before `turn.finished`.

The capture policy is conservative:

- every model-originated tool call requires approval;
- safe tools such as `echo` do not auto-execute when requested by a model;
- unknown tools are reported in the turn metadata and event timeline, not
  executed;
- non-JSON-safe arguments fail that tool request only, not the entire turn;
- response JSON includes `tool_calls`, `approvals`, and the final text summary;
- turn metadata includes a `tool_call_capture` summary;
- `voice_queue` and `worker_jobs` are untouched.

This differs from direct human/API requests. `POST /tools/request` still uses
the permission policy directly, so allowed safe tools may execute there. The
model path only records intent and waits for approval plus explicit execution.

Prompt 15A enables explicit CLI stdout parsing into structured
`BrainResponse.tool_calls`. Valid tool-call blocks are removed from visible
response text. If a response contains only tool-call blocks, Jarvis uses the
deterministic visible text `Jarvis requested tool approval.` Malformed blocks
are also removed from visible text and recorded in
`raw_metadata["tool_call_parse_errors"]`.

This is still not autonomous tool use. Model-originated tool calls become
approval records only. Approved tools require a later explicit
`POST /approvals/{id}/execute` call before any handler can run.

## API Endpoints

The daemon exposes:

- `GET /tools`
- `POST /tools/request`
- `GET /approvals`
- `POST /approvals/{id}/approve`
- `POST /approvals/{id}/reject`
- `POST /approvals/{id}/execute`

These endpoints require `app.started`; otherwise they return `503`.

Approve and reject endpoints update approval status only. They do not execute
automatically and do not replay approved tools.

The explicit execute endpoint runs only an approval whose status is `approved`.
Missing approvals return `404`. Pending, rejected, expired, non-approved, or
already-executed approvals return `409`. Unknown tools referenced by an approval
payload return `404`. If the tool is blocked by policy, such as a destructive
tool while `destructive_tools_enabled=false`, the endpoint returns a compact
blocked JSON response and does not create a `tool_runs` row.

Duplicate execution prevention is based on existing `tool_runs.approval_id`.
Once a run exists for an approval, a second execute request returns `409` and
does not invoke the handler again.

Successful execution returns:

```json
{
  "ok": true,
  "approval_id": "...",
  "tool_run": {},
  "result": {}
}
```

## Intentional Non-Goals

Prompt 15 intentionally does not implement:

- shell execution
- file writing
- real file reading
- network tools
- system mutation
- process inspection
- automatic replay execution after approval
- worker integration
- voice or audio integration
- WebSocket or SSE tool streaming
- launchd installation or control

Providers may request tools through `BrainResponse.tool_calls`, but only the
registry, approval gate, and explicit execute endpoint may lead to execution.
Blocked, rejected, unknown, invalid, and merely captured tools never execute.

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
- Approve and reject endpoints update approval status without automatic
  execution.
- `POST /approvals/{id}/execute` executes an approved `approval_probe` exactly
  once and records a finished `tool_runs` row with the `approval_id`.
- A second execute for the same approval returns `409`.
- A rejected approval cannot execute.
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
- no automatic approval replay

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
