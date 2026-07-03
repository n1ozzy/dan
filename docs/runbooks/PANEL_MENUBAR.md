# Jarvis menu-bar panel (H1 shell)

Native macOS shell for the cockpit: `NSStatusItem` ("J" in the menu bar)
opening a borderless `NSPanel` (widget card) with a `WKWebView` that
renders the SAME static cockpit assets the browser uses
(`jarvis/panel/assets/`). Shape frozen by PANEL_CONTRACT §5.

The shell is a **thin client** (ADR-002) and adds **zero authority**:

- It renders `index.html` from disk and lets the cockpit JS talk to the
  daemon over HTTP/WS exactly like a browser tab (CORS origin of a
  `file://` page is `null`, which the daemon's allow-list already
  covers; the WebSocket authenticates with the `jarvis-token.<token>`
  subprotocol as everywhere else).
- Its only convenience is seeding the cockpit's `localStorage` token key
  with the transport token from `~/.jarvis/runtime/api-token` — the same
  file the CLI reads. No new endpoints, no panel-owned state, no second
  speaker.
- Daemon offline ⇒ the cockpit's own offline/"stream off" indicator shows;
  the shell fabricates nothing.

## Install (once per venv)

```sh
.venv/bin/pip install -e '.[panel]'
```

Pins: `pyobjc-framework-Cocoa==12.2.1`, `pyobjc-framework-WebKit==12.2.1`
(GUI-only; the daemon and test suite never import them — lazy import in
`jarvis/panel/menubar_app.py`).

## Run

```sh
scripts/jarvis-panel            # config resolution mirrors scripts/jarvisd
scripts/jarvis-panel --probe    # exit 0 = PyObjC + assets OK, 2 = not
```

- **Left-click** the "J" status item: toggle the widget card
  (size from `[panel] width/height`, default 480×760). The card hides on
  a click outside (global mouse-down monitor) and when it loses key focus.
- **Right-click**: menu with **Quit Jarvis Panel** (⌘Q also works while
  the panel has focus).

## State border (widget chrome)

The card is a borderless, non-activating `NSPanel` (no system popover
bubble, no arrow): a transparent window whose WKWebView layer carries the
whole geometry — corner radius 12 plus a 2pt state border drawn by the
shell, NOT by the cockpit HTML/CSS: teal = daemon online, amber = approvals
pending, red = daemon unreachable. A daemon thread polls `GET /health`
every ~3 s (`STATUS_POLL_SECONDS`) and repaints the layer on the main
thread. The web document stays chromeless — its own state signals are the
state pill, the offline hero, and the approvals badge/nudge.

## Config

`[panel]` section (`jarvis/config.py:PanelConfig`): `api_base_url`
(informational default `http://127.0.0.1:41741`), `width`, `height`.

Known limitation: the cockpit JS starts at its own built-in default API
base; a non-default daemon port must be typed into the cockpit's API base
field, same as in a browser.

## Troubleshooting

- `PanelShellError: PyObjC is not installed` → run the install step above.
- Blank popover → check the daemon is up (`curl 127.0.0.1:41741/health`)
  and the token file exists (`~/.jarvis/runtime/api-token`).
