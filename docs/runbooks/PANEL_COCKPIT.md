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

- Layout: popover-first single-view app, no page scrolling — one view at a
  time (Chat / Approvals / Memory / System) switched by a bottom tab bar;
  views scroll internally. The chat view holds the conversation dropdown,
  "+ new", the daemon state pill, the chat log, and the composer. Tools,
  events, and the "Zaawansowane" group (settings, API base, health, runtime)
  are native `<details>` sections inside the System view, collapsed by
  default. System state is quiet structure: the state pill (red when
  offline), the offline hero in the chat log, and the approvals signals —
  no decorative frames or ambient animation.
- Voice: the composer has a PTT | Nasłuch segmented MODE switch (lock =
  `POST /voice/listen/lock`, back to PTT = `POST /voice/listen/unlock`).
  Normal hold-to-talk lives on the global hotkey in `menubar_app`, not in the
  web view. Any development-only PTT test action that exercises
  `/voice/ptt/down|up` must use a backend-allowed listening source: `ptt`,
  `global_hotkey`, or `lock`. The mic status next to the switch shows a small
  waveform that animates only while the daemon is actually listening (driven by
  `listening.*` stream events).
- Approvals signals: a pulsing count on the Approvals tab plus an amber
  nudge bar inside the chat view ("N zgód czeka — pokaż") that switches to
  the Approvals view; both come from `renderApprovals` and from
  `pending_approval_count` on the `/health` heartbeat — when the two
  disagree (stream down, missed event), the cockpit re-fetches approvals.
- Health: `service`, `state`, `started`, `schema_version`, `brain_adapter`, and
  `voice_enabled` when the daemon exposes them.
- Input: the composer sends typed text through `POST /input/text`; the sent
  message appears immediately as an optimistic user bubble and the reply
  lands in the chat via the post-send history refresh. "+ Nowa" starts a
  fresh conversation (the next send omits `conversation_id`).
- History: lists conversations from `GET /conversations` (titled from the
  first turn's `input_text`, fetched once per conversation with
  `GET /turns?...&limit=1` and cached) and renders the selected conversation
  as a chat: user bubbles from `input_text`, Jarvis bubbles from `final_text`,
  source/status/relative-time meta per turn. Timestamps render as relative
  labels ("2 min temu") with the full date in the tooltip.
- Memory: lists active blocks from `GET /memory?active_only=true`, creates a
  block with `POST /memory`, edits a block's priority with
  `PATCH /memory/{id}`, and soft-disables with `DELETE /memory/{id}`. Rows
  show `metadata.proposed_by`/`metadata.promoted_by` when present, so
  model-proposed blocks are distinguishable from operator-written ones.
- Tools: lists registered tools and pending approvals.
- Settings: renders daemon-owned settings from `GET /settings` and saves a
  key/value change with `POST /settings` (transport token required; values
  are entered as JSON, e.g. `true`, `3`, `"text"`). The same card switches
  the brain adapter: the select is populated from `GET /brain/adapters`
  (current + default shown) and the Switch button posts to `POST /brain/switch`.
  The cockpit keeps no local settings copy — every mutation POSTs and then
  re-fetches daemon truth, and a `brain.*` event on the stream also triggers
  a debounced re-fetch of settings and health.
- Approvals: approve, reject, and execute-approved actions require explicit
  clicks. Approval alone does not execute.
- Events: initial backlog from `GET /events?after_id=0&limit=50`, then live
  read-only push over the `GET /stream` WebSocket (ADR-019). The stream
  indicator shows `live` when connected; the Refresh button still works as a
  polling fallback.
- Runtime: reads `GET /runtime/processes` and shows conflict count plus
  report-only status.

## Live Stream

The cockpit connects to `GET /stream` (WebSocket) after the first successful
refresh. The stream is read-only display: it renders incoming events, updates
the state pill on `state.changed`, and triggers a pending-approvals re-fetch
on `approval.*`/`tool.*` events. All approvals and actions still go through
the existing POST endpoints — the socket never carries a mutation.

Browsers cannot set `X-Jarvis-Token` on a WebSocket handshake, so the token
from local storage rides along as a `jarvis-token.<token>` subprotocol entry
next to `jarvis.v1`. When `security.api_token_required` is on (default) and
no token is stored yet, the stream shows `stream off (token?)` — perform any
mutating action once (the cockpit prompts for the token) and the stream
reconnects with it. Streamed `tool.finished` events never include the bulk
`output` payload (`output_omitted: true`); details stay on the HTTP API.

## Intentional Non-Goals

- no WebSocket mutations (the stream is read-only; actions use POST routes)
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
- PTT test fails with an unknown listening source: use one of the backend-owned
  sources (`ptt`, `global_hotkey`, `lock`). The daemon rejects ad-hoc source
  labels before creating a lease.
