# DAN Panel

The menu bar panel is a **pure HTTP client** of a running `dand`. It has no
runtime of its own: it does not play audio, does not start processes, does not
touch launchd.

Shell: `NSStatusItem` + a borderless `NSPanel` + `WKWebView` that loads the
cockpit assets from the daemon origin (`GET /panel/index.html`) —
`dan/panel/menubar_app.py`, assets in `dan/panel/assets/`.

Start:

- production: launchd agent `com.dan.panel` → `~/.dan/bin/dan-panel`
  (`KeepAlive`; see `docs/CO-JEST-GDZIE.md`);
- from the repo: `scripts/dan-panel` (needs the panel extra:
  `.venv/bin/pip install -e '.[panel]'`).

Window size comes from `[panel].width` / `[panel].height` in the runtime config
(`~/.dan/config.toml`) — no size is hardcoded here.

## States

| State | Meaning | What to do |
|---|---|---|
| online | the daemon answers the API, the panel streams events | nothing — keep working |
| `daemon offline` | the daemon is not answering on the configured port | see "Offline" below |
| voice paused | the broker takes no new items from the queue; the current utterance finishes | "Resume voice" when you want it to continue |
| restart required | a settings change is waiting for a daemon restart | "Safe DAN restart" |

The voice section shows: the broker state, the item currently being spoken and
the queue contents (source, session, status — see `docs/GLOS-I-KOLEJKA.md`).

## Buttons

| Button | Call | Effect |
|---|---|---|
| "Pause voice" | `POST /voice/pause` | the broker stops taking new items; the queue stays |
| "Resume voice" | `POST /voice/resume` | the broker goes back to consuming the queue |
| "Skip current" | `POST /voice/queue/current/cancel` | cuts only the current utterance; the rest of the queue keeps playing, and the session stays speakable (skip, not flush) |
| "Safe DAN restart" | `POST /runtime/restart` | the daemon wraps up its current work, exits with code 86, launchd (`KeepAlive`) brings it back up |
| "Send" / memory / events | conversation and memory API | normal text work |

## What "offline" means

"Offline" = the panel got no response from the daemon. The panel **does not
resurrect** `dand` — deliberately. Bringing the process up is launchd's job
(`KeepAlive`), not a UI control's. When the panel shows offline:

1. `dan doctor --json` — full diagnosis (works without the daemon too);
2. if the daemon should come up: launchd will do it on its own after a
   `restart`/crash, or start `~/.dan/bin/dand` manually;
3. further diagnostics: `docs/ODZYSKIWANIE.md`.
