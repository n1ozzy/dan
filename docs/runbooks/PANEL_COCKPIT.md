# Static Panel Cockpit

This cockpit is a static development view for inspecting a local Jarvis v4.1
daemon. It is not the final macOS MenuBar panel, and it is not a source of truth.
The daemon owns state; the page renders daemon responses and sends only
explicit user intents to existing localhost API routes.

## Open

```bash
open jarvis/panel/assets/index.html
```

The API base defaults to `http://127.0.0.1:41741`. Edit the Base field in the
cockpit when a temporary daemon is bound to a different localhost port.

## Start A Temporary Daemon

Use the existing smoke scripts when you want an isolated temporary daemon:

```bash
scripts/smoke-text-runtime.sh
scripts/smoke-tools-approvals.sh
scripts/smoke-memory-runtime.sh
```

For normal local development, start `jarvisd` through the existing CLI/runtime
entry point for this repo and then open the static file above. The cockpit does
not start, stop, supervise, or clean up any process.

## Sections

- Header: daemon online/offline state, current runtime state, full refresh.
- Health: `service`, `state`, `started`, `schema_version`, `brain_adapter`, and
  `voice_enabled` when the daemon exposes them.
- Input: sends typed text through `POST /input/text` and shows `final_text`.
- History: lists conversations from `GET /conversations` and turns from
  `GET /turns?conversation_id=...`.
- Memory: lists active blocks from `GET /memory?active_only=true`, creates a
  block with `POST /memory`, and soft-disables with `DELETE /memory/{id}`.
- Tools: lists registered tools and pending approvals.
- Approvals: approve, reject, and execute-approved actions require explicit
  clicks. Approval alone does not execute.
- Events: polls `GET /events?after_id=0&limit=50`; no streaming path is used.
- Runtime: reads `GET /runtime/processes` and shows conflict count plus
  report-only status.

## Intentional Non-Goals

- no WebSocket
- no voice
- no native MenuBar
- no launchd
- no direct provider calls
- no source-of-truth state in the panel
- no tool auto-execution
- no runtime cleanup buttons

## Safety

The cockpit never calls providers directly and never executes tools directly.
It only calls the existing approval execute endpoint after a user clicks the
separate execute-approved button. Runtime conflicts are display-only. Memory
disable is a soft disable through the existing memory API, not a hard delete.

## Troubleshooting

- Daemon offline: verify the daemon is running and that the Base field matches
  the daemon host and port.
- CORS or local file fetch errors: use the same localhost API base that the
  daemon exposes, and check the compact JSON error box in the affected section.
- Wrong API base URL: edit the Base field and click Refresh.
- Empty sections: check whether the daemon has been started and whether the
  corresponding API route requires `app.started`.
