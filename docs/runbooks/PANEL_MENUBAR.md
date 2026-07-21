# DAN menu-bar panel (H1 shell)

Native macOS shell for the cockpit: `NSStatusItem` ("J" in the menu bar)
opening a borderless `NSPanel` (widget card) with a `WKWebView` that
renders the SAME static cockpit assets the browser uses
(`dan/panel/assets/`). Shape frozen by PANEL_CONTRACT §5.

The shell is a **thin client** (ADR-002) and adds **zero authority**:

- It renders `index.html` from disk and lets the cockpit JS talk to the
  daemon over HTTP/WS exactly like a browser tab (CORS origin of a
  `file://` page is `null`, which the daemon's allow-list already
  covers; the WebSocket authenticates with the `dan-token.<token>`
  subprotocol as everywhere else).
- Its only convenience is seeding the cockpit's `localStorage` token key
  with the transport token from `~/.dan/runtime/api-token` — the same
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
`dan/panel/menubar_app.py`).

## Run

```sh
scripts/dan-panel            # config resolution mirrors scripts/dand
scripts/dan-panel --probe    # exit 0 = PyObjC + assets OK, 2 = not
```

> **Since 2026-07-21 the panel normally runs under launchd**, label
> `com.dan.panel` → `~/.dan/bin/dan-panel` (plist
> `~/Library/LaunchAgents/com.dan.panel.plist`). The commands above are the
> manual/dev path; use them for probing, not to start a second instance next to
> the launchd one. Ownership table: `docs/CO-JEST-GDZIE.md`. The old rumps
> widget and its `com.dan.panels` plist were quarantined in the same cutover.

- **Left-click** the "J" status item: toggle the widget card (size from
  `[panel] width/height`). The card hides when it loses key focus
  (`windowDidResignKey`) — the shell installs no global event monitor.
- **Right-click**: menu with **Quit DAN Panel** (⌘Q also works while
  the panel has focus).

## Widget chrome

The card is a borderless, non-activating `NSPanel` (no system popover
bubble, no arrow): a transparent window whose WKWebView layer carries the
whole geometry. The native layer only clips — corner radius plus the window
shadow. The animated state frame is drawn by CSS inside the cockpit and driven
by the state the page already fetches, so the shell polls nothing and paints no
state colour.

## Config

`[panel]` section (`dan/config.py:PanelConfig`): `api_base_url`
(informational default `http://127.0.0.1:41741`), `width`, `height`.

Known limitation: the cockpit JS starts at its own built-in default API
base; a non-default daemon port must be typed into the cockpit's API base
field, same as in a browser.

## Troubleshooting

- `PanelShellError: PyObjC is not installed` → run the install step above.
- Blank popover → check the daemon is up (`curl 127.0.0.1:41741/health`)
  and the token file exists (`~/.dan/runtime/api-token`).
