# Static Panel Cockpit

This cockpit is a static development view for inspecting a local Jarvis v4.1
daemon. It is not the final macOS MenuBar panel, and it is not a source of truth.
The daemon owns state; the page renders daemon responses and sends only
explicit user intents to existing localhost API routes.

## Open

For browser development, serve the static cockpit from the fixed local
development origin:

```bash
cd jarvis/panel/assets
python3 -m http.server 41800
```

Then open:

```text
http://127.0.0.1:41800
```

Opening `jarvis/panel/assets/index.html` directly is also supported for manual
inspection. Browsers send that as `Origin: null`, which the local daemon accepts
only for this static cockpit development path.

The API base defaults to `http://127.0.0.1:41741`. Edit the Base field in the
cockpit when a temporary daemon is bound to a different localhost port. The API
base must include the scheme:

```text
http://127.0.0.1:<daemon-port>
```

A bare value such as `127.0.0.1:<daemon-port>` is invalid in the browser. It is
treated as a relative URL and will hit the static cockpit server instead of the
Jarvis daemon.

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

Local CORS is intentionally limited to cockpit development origins:
`http://127.0.0.1:41800`, `http://localhost:41800`, and direct file opens that
send `Origin: null`. It does not use wildcard CORS and it does not allow
credentials.

This is not auth or CSRF hardening. A future production or native panel still
needs proper transport protection and request authorization.

## Troubleshooting

- Daemon offline: verify the daemon is running and that the Base field matches
  the daemon host and port.
- Browser dev CORS: serve the cockpit with `python3 -m http.server 41800` from
  `jarvis/panel/assets`, then open `http://127.0.0.1:41800`.
- Wrong API base shape: include `http://` in the Base field. Bare
  `127.0.0.1:<port>` is a relative URL, not a daemon URL.
- CORS or local file fetch errors: use the same localhost API base that the
  daemon exposes, and check the compact JSON error box in the affected section.
- Wrong API base URL: edit the Base field and click Refresh.
- Empty sections: check whether the daemon has been started and whether the
  corresponding API route requires `app.started`.
