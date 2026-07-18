# Jarvis v4.1 — Panel Contract (FROZEN)

> **Naming — Release 1 cutover (2026-07-18):** `jarvisd` / `com.ozzy.jarvisd` in this
> doc = today's `dand` / `com.dan.dand`; the contract itself remains in force.

> **Status:** FROZEN (Prompt 00A). Defines the boundary between the macOS panel
> and `jarvisd`. The panel is a **thin client** ([ADR-002](DECISIONS.md#adr-002)):
> it renders daemon state and sends intents. It owns no canonical state.

---

## 1. The rule

> **The panel is a window onto `jarvisd`, not a brain of its own.**

- The panel **renders** state it reads from the daemon.
- The panel **sends intents** (typed text, PTT, settings changes) to the daemon.
- The panel **computes and stores nothing canonical** — no conversation, no
  voice state, no memory, no truth.
- If the daemon is **offline**, the panel shows an **offline** state. It does not
  fabricate or cache-as-truth.

This inverts the old `dan` panel, which read runtime truth from
`/tmp/dan-voice/state.json` and toggled a `/tmp/dan-listen/PTT` file directly —
because there was no daemon to be the source of truth. v4.1 supplies that daemon,
so the panel talks to it instead of to `/tmp`.

---

## 2. What the panel may do

| Intent | How |
|--------|-----|
| Send typed input | `POST /input/text` **only** ([ADR-011](DECISIONS.md#adr-011)) |
| Push-to-talk | `POST /voice/ptt/down` / `POST /voice/ptt/up` **only** |
| Sticky listen | `POST /voice/listen/lock` / `POST /voice/listen/unlock` |
| Read live state | `GET /state`, `GET /stream` (WebSocket) |
| Read history | `GET /events`, conversation/turn reads |
| Read/change settings | `GET /settings`, `POST /settings` |
| Switch brain | brain route (Prompt 20) — persisted in DB settings |
| Read/edit memory | memory route (Prompt 20) — emits `memory.updated` |
| Decide approvals | approval routes (Prompt 12) |
| See audio/voice/jobs/warnings | the matching read endpoints |

The full cockpit (Prompt 19) is a *view* composed entirely from these endpoints:
top status, text input + PTT, live state strip, conversation, voice/audio strip,
brain/tools strip, jobs strip, warnings strip, approval cards, event drawer,
memory drawer.

---

## 3. What the panel must never do (FROZEN)

- **No direct brain calls.** The panel never invokes a brain adapter; it posts
  input to the daemon and the daemon runs the turn.
- **No `/tmp` canonical reads.** The panel never reads `/tmp` as a source of
  truth ([ADR-008](DECISIONS.md#adr-008)).
- **No panel-owned canonical state.** No locally-held conversation/voice/memory
  that the rest of the system trusts.
- **No second speaker.** The panel never plays audio; only the broker speaks
  ([ADR-005](DECISIONS.md#adr-005)).
- **No typed-vs-voice divergence.** Typed input uses the same orchestrator as
  voice ([ADR-011](DECISIONS.md#adr-011)).

---

## 4. Offline behavior

When `jarvisd` is unreachable:

- The panel shows an explicit **offline** indicator.
- Controls that require the daemon are disabled or clearly marked unavailable.
- The panel **does not** queue intents into `/tmp` or local files as a fallback
  truth; it simply waits for the daemon and reconnects.

---

## 5. Shell & rendering (Prompts 18–19)

- macOS menu-bar: PyObjC `NSStatusItem` + `NSPopover` + `WKWebView`.
- Assets: `jarvis/panel/assets/{index.html,app.js,styles.css}`.
- Launcher: `scripts/jarvis-panel`.
- Default size ~`420×620`, primary actions visible without scrolling; width and
  height are configurable.

These are presentation details; none of them grant the panel any authority over
state. The contract in §1–§4 holds regardless of how the UI looks.
