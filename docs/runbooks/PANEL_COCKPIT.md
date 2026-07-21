# Static Panel Cockpit

The cockpit assets in `dan/panel/assets/` are also servable as a plain static
page, for browser development against a local `dand`.
It is not the final macOS MenuBar panel, and it is not a source of truth: the
daemon owns state, the page renders daemon responses and sends explicit user
intents to localhost API routes.

**What the page contains is described once, in
[PANEL_CONTRACT.md](../PANEL_CONTRACT.md) §2.** This runbook covers only how to
run it and what its local CORS setup means.

## Open

Serve it from the fixed local development origin:

```bash
cd dan/panel/assets
python3 -m http.server 41800
```

Then open `http://127.0.0.1:41800`.

Opening `dan/panel/assets/index.html` directly also works for manual
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
DAN daemon.

## Start a temporary daemon

Use the existing smoke scripts when you want an isolated temporary daemon:

```bash
scripts/smoke-text-runtime.sh
scripts/smoke-memory-runtime.sh
```

For normal local development, start `dand` through the CLI/runtime entry point
for this repo and then open the page above. The cockpit does not start, stop,
supervise or clean up any process.

## Settings mutations

Settings render from `GET /settings` and a key/value change is saved with
`POST /settings` (transport token required; values are entered as JSON). The
same card switches the brain adapter: the select is populated from
`GET /brain/adapters` and the Switch button posts to `POST /brain/switch`. The
cockpit keeps no local settings copy — every mutation POSTs and then triggers a
re-fetch of daemon truth, as does a `brain.*` event on the stream.

## Live stream

The cockpit connects to `GET /stream` (WebSocket) after the first successful
refresh. The stream is a read-only display feed (ADR-019); every action still
goes through the POST endpoints and the socket never carries a mutation.

Browsers cannot set `X-DAN-Token` on a WebSocket handshake, so the token from
local storage rides along as a `dan-token.<token>` subprotocol entry next to
`dan.v1`. When `security.api_token_required` is on and no token is stored yet,
the stream shows `stream off (token?)` — perform any mutating action once (the
cockpit prompts for the token) and the stream reconnects with it. Streamed
`tool.finished` events never include the bulk `output` payload
(`output_omitted: true`); details stay on the HTTP API.

## Intentional non-goals

- no WebSocket mutations (the stream is read-only; actions use POST routes)
- no voice
- no direct provider calls
- no source-of-truth state in the panel
- no runtime cleanup buttons
- ~~no native MenuBar / no launchd~~ — *superseded 2026-07-21*: the panel ships
  as a native menu-bar shell under launchd label `com.dan.panel`
  ([PANEL_MENUBAR.md](PANEL_MENUBAR.md), `docs/CO-JEST-GDZIE.md`).
- ~~no tool auto-execution~~ — *superseded*: tools requested through the API
  execute immediately (`docs/SECURITY_MODEL.md` §2). The page itself still
  initiates nothing on its own.

## Safety

Every action is an explicit click that POSTs to an existing endpoint. Runtime
conflicts are display-only. Memory disable is a soft disable through the memory
API, not a hard delete.

Local CORS is intentionally limited to cockpit development origins:
`http://127.0.0.1:41800`, `http://localhost:41800`, and direct file opens that
send `Origin: null`. It does not use wildcard CORS and it does not allow
credentials.

This is not auth or CSRF hardening, and CORS never was: it governs who may read
a response, not whose request runs (`docs/SECURITY_MODEL.md` §2).

## Troubleshooting

- Daemon offline: verify the daemon is running and that the Base field matches
  the daemon host and port.
- Browser dev CORS: serve the cockpit with `python3 -m http.server 41800` from
  `dan/panel/assets`, then open `http://127.0.0.1:41800`.
- Wrong API base shape: include `http://` in the Base field. A bare
  `127.0.0.1:<port>` is a relative URL, not a daemon URL.
- Empty sections: check whether the daemon has been started and whether the
  route requires `app.started`.
- PTT test fails with an unknown listening source: use one of the backend-owned
  sources (`ptt`, `global_hotkey`, `lock`). The daemon rejects ad-hoc source
  labels before creating a lease.
