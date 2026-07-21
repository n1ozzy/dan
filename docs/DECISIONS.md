# DAN — Architecture Decision Records

> **Status:** the binding architectural decisions of this runtime. Each ADR is
> **Accepted** unless its own header says otherwise. Changing one requires
> superseding it with a new ADR, not editing it away. Names, paths and defaults
> were re-verified against the source on 2026-07-21; ADRs whose *decision* is
> no longer implemented now say so in their own header instead of pretending.
>
> Format per ADR: **Context** (why this comes up) · **Decision** (what is fixed)
> · **Consequences** (what follows). Cross-references point at
> [CONTRACTS.md](CONTRACTS.md), [TURN_PIPELINE.md](TURN_PIPELINE.md),
> [AUDIO_RUNTIME.md](AUDIO_RUNTIME.md),
> [LAUNCH_SUPERVISION.md](LAUNCH_SUPERVISION.md),
> [SECURITY_MODEL.md](SECURITY_MODEL.md),
> [PANEL_CONTRACT.md](PANEL_CONTRACT.md).
>
> The `docs/adr/` directory holds later, individually-numbered ADRs; its
> numbering is separate from the ADR-0xx series below.

---

## ADR-001 — `dand` owns all truth

**Status:** Accepted

**Context.** The old script-based `dan` system spread truth across `/tmp` files,
in-memory process state, and the panel. Restarts lost history; components
disagreed.

**Decision.** A single local daemon, **`dand`**, owns all state: conversation,
memory, events, history, voice queue, listening leases, audio snapshots,
approvals, tool runs and worker jobs. Every other component is a client.

**Consequences.** There is exactly one source of truth. Clients hold no
authoritative state. State survives restarts because it lives in the daemon's DB
([ADR-004](#adr-004)). See [PRODUCT.md](PRODUCT.md).

---

## ADR-002 — The panel is a thin client

**Status:** Accepted

**Context.** The old panel read `/tmp/dan-voice/state.json` and toggled
`/tmp/dan-listen/PTT` directly, because there was no daemon to ask.

**Decision.** The macOS panel only renders daemon state and sends intents
(`POST /input/text`, `/voice/ptt/*`, settings, approvals). It owns no canonical
state and, when the daemon is offline, shows an offline state.

**Consequences.** UI changes can never corrupt truth. The panel and any future
client are interchangeable views. See [PANEL_CONTRACT.md](PANEL_CONTRACT.md).

---

## ADR-003 — Brain adapters are stateless

**Status:** Accepted

**Context.** Provider CLIs (Claude, Codex) keep their own server-side sessions.
Treating those as memory makes DAN's context non-deterministic and
unportable.

**Decision.** A brain adapter is a stateless function
`BrainRequest → BrainResponse`. DAN assembles all context from its own DB +
config. The provider session is **not** DAN's memory (`brain.provider_sessions_are_memory` is pinned to `false` by the config loader).

**Consequences.** Brains are swappable and testable (mock adapter by default).
The same DB state deterministically produces the same `BrainRequest`. Adapters
cannot speak, write memory, or touch the panel. See
[SECURITY_MODEL.md](SECURITY_MODEL.md) §5 and
[CONTRACTS.md](CONTRACTS.md) §4–§5.

---

## ADR-004 — The SQLite event store is the source of truth

**Status:** Accepted

**Context.** Append-only history is needed to reconstruct any turn and to debug
the system from one place.

**Decision.** State lives in SQLite at `~/.dan/dan.db` (`[database].path`). The
`events` table is append-only and authoritative for history. The schema is
applied at startup and migrations are idempotent; an existing DB is never
destroyed.

**Consequences.** Any turn's lifecycle is reconstructable by filtering events on
`correlation_id`. Events are never mutated or deleted. See
[CONTRACTS.md](CONTRACTS.md) §2 and [TURN_PIPELINE.md](TURN_PIPELINE.md) §6.

---

## ADR-005 — The voice broker is the sole speaker

**Status:** Accepted

**Context.** Pre-broker `dan` had multiple components calling `afplay`
independently → overlapping audio, echo, hung queues ("brak jednego dyrygenta").

**Decision.** Exactly one component — the voice broker — plays audio. It drains
the persisted `voice_queue`. No worker, adapter, panel or hook ever calls a
player.

**Consequences.** No overlapping or duplicate speech. There is no direct player
call anywhere outside the player adapter / test fixtures, and every producer
(CLI, panel, hooks, skills, other agents) goes through `dan speak` / the API.
See [AUDIO_RUNTIME.md](AUDIO_RUNTIME.md) and
[docs/adr/001-dand-single-owner.md](adr/001-dand-single-owner.md).

---

## ADR-006 — PTT is a `ListeningLease`, not a file

**Status:** Accepted

**Context.** The old listener treated the existence of `/tmp/dan-listen/PTT` as
"is listening". A crashed process could leave the flag in either state, with no
expiry and no distinction between momentary and sticky listening.

**Decision.** Listening is governed by a `ListeningLease` row in the DB, with a
`hold` vs `locked` mode and an expiry. A button release clears a `hold` lease but
not a `locked` one; stale leases expire.

**Consequences.** Listening state is durable, inspectable and self-healing. No
raw `/tmp` flag is the source of truth. See [AUDIO_RUNTIME.md](AUDIO_RUNTIME.md)
§2 and [CONTRACTS.md](CONTRACTS.md) §8.

---

## ADR-007 — launchd has one official label

**Status:** Accepted

**Context.** The old setup had several autostart agents (`com.ozzy.jarvis`,
`com.dan.voice-broker`, `com.dan.xtts-server`) that could race for the mic and
speaker.

**Decision.** There is exactly one official label: **`com.dan.dand`**
(`RuntimeSupervisor.OFFICIAL_LABEL`, `[launchd].label`). The
`RuntimeSupervisor` detects legacy labels/processes and **reports** them. It
**never kills** anything automatically. Install scripts are never auto-run and
print exactly what they will do.

**Consequences.** Conflicts are surfaced, not silently fought. The human decides
what to stop. (The separate child supervisor in `dan/daemon/supervisor.py` may
terminate a supervised child of a *previous incarnation of this same daemon* —
see [docs/adr/001-dand-single-owner.md](adr/001-dand-single-owner.md), which
also records that this reclaim does not currently fire in production.) See
[LAUNCH_SUPERVISION.md](LAUNCH_SUPERVISION.md) and
[CONTRACTS.md](CONTRACTS.md) §14.

---

## ADR-008 — `/tmp` is compatibility transport only

**Status:** Accepted

**Context.** `dan` used `/tmp/dan-*` for the listen log, PTT flag, voice
requests, broker state and control files — i.e. as its de-facto database. `/tmp`
is volatile and non-transactional.

**Decision.** `/tmp` may be used only as a compatibility transport to bridge to
legacy components if ever needed. It is **never** a source of truth. No pipeline
step reads `/tmp` for canonical state.

**Consequences.** Truth survives reboots and races. Bridges to `/tmp`, if any,
are explicitly second-class. See [CONTRACTS.md](CONTRACTS.md) cross-cutting
invariants.

---

## ADR-009 — Workers cannot speak or write memory facts directly

**Status:** Accepted

**Context.** A background worker that can talk or commit memory can act on the
world without a human in the loop and pollute long-term context.

**Decision.** A `WorkerJob` result is a **memory candidate**, never a fact and
never speech. Promotion to a committed `MemoryBlock` requires a human or an
explicit policy. Workers never enqueue a `VoiceRequest`.

**Consequences.** Workers advise; they do not act on the world. Memory stays
curated. See [SECURITY_MODEL.md](SECURITY_MODEL.md) and
[CONTRACTS.md](CONTRACTS.md) §6, §13.

**Status of the mechanism (2026-07-21).** Enforced structurally: a `Worker` gets
a `WorkerJob` and returns a `WorkerResult` — it holds no DB handle, no event
store, no memory manager and no voice queue, so it *cannot* speak or write.
`WorkerBroker` writes the candidate as an INACTIVE `memory_blocks` row
(`metadata.candidate = true`), and only active blocks ever enter a
`BrainRequest`. The "explicit policy" half is the config switch
`memory.worker_candidates_require_promotion`: when it is false — the value in
the shipped example config — the broker immediately calls
`promote_candidate(promoted_by="policy")`, so there is no human step. Set it to
true to require one.

---

## ADR-010 — Tools require a registry plus an approval policy

**Status: SUPERSEDED by [ADR-022](#adr-022) (2026-07-08). HISTORICAL — the
approval half was never merely disabled by config, it was removed from the
code. Do not read the Decision below as current behaviour; current behaviour is
in [CONTRACTS.md](CONTRACTS.md) §10 and [SECURITY_MODEL.md](SECURITY_MODEL.md).**

**Context.** The old command path ran with `--dangerously-skip-permissions`,
relying on push-to-talk as the only safety brake.

**Decision (historical).** Every tool is registered with a permission class.
Reads are allowed; writes, shell and network require approval; destructive is
blocked unless explicitly enabled. A rejected/blocked `ToolCall` never executes.
Secrets are redacted in event payloads.

**What survives.** The registry, the risk class on every registered tool, the
durable `tool_run` per execution, and the secret redaction. **What does not:**
`ToolPermissionPolicy.decide()` returns ALLOW unconditionally,
`ToolRegistry.request_tool()` ignores the policy/gate arguments, and no code
path turns a tool call into an approval. Containment now lives inside the
individual tools.

**Consequences.** Every executed tool still leaves an auditable `tool_run`, but
"no silent over-reach" is no longer guaranteed by a gate — it is guaranteed, to
the extent it is, by each tool's own checks. See
[SECURITY_MODEL.md](SECURITY_MODEL.md) and
[CONTRACTS.md](CONTRACTS.md) §10–§11.

---

## ADR-011 — Panel text and voice transcript use the same `TurnOrchestrator`

**Status:** Accepted

**Context.** The old system had a separate voice loop (`auto_jarvis`) distinct
from any text path, so the two could (and did) drift.

**Decision.** Typed panel input and accepted voice transcripts enter the **same**
`TurnOrchestrator`, differing only in the turn's `source` (`text` | `panel` |
`cli` | `api` | `voice`). There is no separate "voice brain".

**Consequences.** One pipeline, one event stream, one set of guarantees for both
modalities. Tests for the text turn also protect the voice turn. See
[TURN_PIPELINE.md](TURN_PIPELINE.md) §1, §4.

---

## ADR-012 — `AudioDeviceManager` owns input/output device state

**Status:** Accepted

**Context.** Scattered device handling led to wrong-mic capture and bluetooth
surprises.

**Decision.** A single `AudioDeviceManager` owns device selection and policy
(preferred input `Mikrofon (MacBook Air)`, output follows the system default,
bluetooth mic warns/disabled). Voice and STT code consult the manager; they never
choose devices themselves.

**Consequences.** Predictable capture and playback routing, captured as
`AudioDeviceState` snapshots. See [AUDIO_RUNTIME.md](AUDIO_RUNTIME.md) §6 and
[CONTRACTS.md](CONTRACTS.md) §9.

---

## ADR-013 — Legacy DAN runtime is detected and reported, never auto-killed

**Status:** Accepted

**Context.** The 2026-06-30 diagnostic shows the legacy voice stack
(`voice_broker.py`, `listen_ozzy.py`, `auto_jarvis.py`) running (started by
hand), a `com.dan.voice-broker.plist` installed in `~/Library/LaunchAgents`, and
live `/tmp/dan-*` state. Automatically killing processes, unloading agents or
deleting `/tmp`/plists would seize the user's live audio setup and break the
per-prompt human gate.

**Decision.** The `RuntimeSupervisor` detects legacy labels, processes and
`/tmp` artifacts and surfaces them as warnings + `RuntimeProcessObservation`s. It
**never** kills, unloads or deletes. Cleanup helpers are diagnose-and-print only
(Prompt 24) and are run manually by the human.

**Consequences.** No surprise mic/speaker seizure; the human decides what to stop
and when. See [LEGACY_RUNTIME_FINDINGS.md](LEGACY_RUNTIME_FINDINGS.md) and
[LAUNCH_SUPERVISION.md](LAUNCH_SUPERVISION.md).

---

## ADR-014 — launchd artifacts avoid the `~/Documents` TCC trap

**Status:** Accepted

**Context.** The legacy `com.dan.voice-broker` agent thrashed with hundreds of
`/bin/zsh: can't open input file: …/dan/tools/jarvis/start-voice-broker.sh`
because launchd (under KeepAlive) could not read a script located under
`~/Documents` (macOS TCC sandbox).

**Decision.** The official agent, its executable and its logs live **outside
`~/Documents`** — under `~/.dan` (`[runtime].home`; logs `~/.dan/logs`, pid
`~/.dan/runtime/dand.pid`, binary `~/.dan/bin/dand`). The label is exactly
`com.dan.dand` (distinct from the legacy `com.ozzy.jarvis` /
`com.dan.voice-broker`). Install scripts print what they will do and are never
auto-run.

**Consequences.** No TCC thrash, stable log location, no one-letter label
confusion. See [LAUNCH_SUPERVISION.md](LAUNCH_SUPERVISION.md) and
[LEGACY_RUNTIME_FINDINGS.md](LEGACY_RUNTIME_FINDINGS.md) §2, §10.

---

## ADR-015 — Worker job lifecycle uses worker_jobs for state and events for history

**Status:** Accepted

**Context.** Prompt 03 established `worker_jobs` as the canonical table for
worker job state. Prompt 04 established EventStore as the single append-only
event history mechanism.

**Decision.**

- `worker_jobs` is the canonical worker job state table.
- `worker.job.*` entries in the general `events` table are the canonical worker
  job lifecycle history.
- There is no `job_events` table.
- Future job history requirements extend EventStore, not a parallel event
  table, unless a later ADR supersedes this.

**Consequences.** Job state and job history remain separate without creating a
second event system: state is read from `worker_jobs`; history is replayed from
`events`.

---

## ADR-016 — Runtime state names are canonical and finite

**Status:** Accepted

**Context.** Prompt 05 implemented the canonical `RuntimeStateMachine`. Earlier
planning docs still named transient concepts as runtime states and mentioned a
separate turn-step timeline.

**Decision.**

- RuntimeState persisted values are exactly: `BOOTING`, `IDLE`, `LISTENING`,
  `TRANSCRIBING`, `THINKING`, `TOOLING`, `SPEAKING`, `INTERRUPTED`, `ERROR`,
  `STOPPING`. Still true verbatim in `dan/daemon/state_machine.py`.
- `WAITING_APPROVAL` and `WORKING` are not runtime states.
- Tool work inside a turn is `TOOLING`; the runtime never parks waiting for a
  human (there is no approval step — ADR-010/ADR-022).
- Worker activity is represented by `worker_jobs` plus `worker.job.*` events,
  not runtime state expansion.
- Turn history is represented by `turn.*` events and `turns` state, not a
  `turn_steps` table.

**Consequences.** Daemon/API code must expose only the canonical `RuntimeState`
set. The panel must render only the canonical `RuntimeState` set. Future
runtime states require a new ADR and tests.

---

## ADR-017 — `ui_read` observes only the frontmost app and focused window, via a daemon-owned backend

**Status:** Accepted, except for the PermissionPolicy step in the last bullet —
that layer no longer gates anything (ADR-010/ADR-022).

**Context.** FAZA D1 (MASTER_PLAN) adds read-only Accessibility. The §3
matrix row says `ui_read` | user **A (approved surfaces)** | model AP |
auto B, but "approved surfaces" had no concrete definition, and the project
has zero runtime dependencies (no pyobjc).

**Decision.**

- **Approved surfaces in D1 are exactly the frontmost application and its
  focused window.** The tools (`ui_active_app`, `ui_read_window`) expose
  nothing broader — no other apps, no other windows, no system-wide UI tree.
  Widening the surface requires a new ADR, not a config flag.
- The adapter is a pluggable, daemon-owned backend
  (`dan/macos/accessibility.py`): `ax` (real AXUIElement via **ctypes**,
  keeping the zero-dependency rule) or `fake` (deterministic fixture for
  tests/smoke, announced as `backend: "fake"` in every payload). An unknown
  backend name fails the daemon at startup — no silent fallback.
- **Secure text fields are stripped at the tool layer**, not (only) in the
  backend: every snapshot passes `sanitize_window_snapshot`, which drops
  values of `AXSecureTextField` elements and clips element counts and text
  lengths. A buggy backend cannot leak a password into tool_runs. The `ax`
  backend additionally never copies secure values in the first place.
- The model never talks to AX. Tools go through ToolRegistry → ToolRunRecorder
  → EventStore like every other tool. (The `PermissionPolicy` step this bullet
  originally named is gone: the policy allows everything, so the sanitizer and
  the surface limits above are the whole guarantee.)

**Consequences.** TCC onboarding is a documented human step
([runbooks/ACCESSIBILITY_TCC.md](runbooks/ACCESSIBILITY_TCC.md)); without the
grant reads fail cleanly and the daemon keeps running. D2 (`ui_act`) reuses the
adapter; its approval gate was never built (see ADR-018).

---

## ADR-018 — `ui_act` uses AX-only actions, never touching credentials

**Status:** Accepted EXCEPT the approval gate. **The "every `ui_act` request
crosses ApprovalGate" rule is NOT implemented (verified 2026-07-21):**
`ui_click` / `ui_type` / `ui_focus_app` are ordinary registered tools that the
orchestrator executes directly, and the permission policy allows everything.
The AX-only restriction, the secure-field refusal, the control-character ban
and the `MAX_TYPE_CHARS` cap ARE implemented in
`dan/tools/ui_tool.py` + `dan/macos/accessibility.py`. Re-introducing the gate
needs a new ADR and real code, not a config flag.

**Context.** FAZA D2 adds UI actions (`ui_click`, `ui_type`, `ui_focus_app`)
on top of the D1 adapter. The capability inventory calls unattended UI
control "a model with a mouse"; the operator contract forbids the runtime from
owning or extracting credentials.

**Decision.**

- **Actions are AX API calls only**: `AXPress` for clicks, setting `AXValue`
  for typing, `AXFrontmost` for focus. No CGEvent synthetic keyboard/mouse
  input in D2 — a hotkey injector is a different risk shape and would need
  its own ADR.
- ~~**Every `ui_act` request crosses ApprovalGate**, including direct user
  commands (§3: user AP / model AP / auto B).~~ **Not in effect** — see the
  status note above. Earned per-surface trust stays a §6 future.
- **Typing into secure text fields is refused twice**: the `ax` actor checks
  the focused element's role/subrole before setting a value, and the tool
  layer enforces the same rule against any backend. Typed text is never
  echoed back in tool output (it already lives, redacted, in the tool
  input); `ui_type` is capped at `MAX_TYPE_CHARS`.
- `ui_focus_app` minimally widens the D1 surface: it resolves a pid from
  on-screen window **owner names only** (CGWindowList, no window contents,
  no TCC beyond Accessibility) and raises that app. Observation of other
  apps' UI remains out of scope (ADR-017 unchanged).
- Backend knob: `security.ui_act_backend`, empty inherits
  `security.ui_read_backend`; unknown names fail the daemon at startup.
- The fake actor records every performed action, so tests and
  `scripts/smoke-ui-act.sh` can prove nothing executed before approval.

**Consequences.** D2 gives Jarvis hands that move only after a human click
per action. Setting `AXValue` types "atomically" (no per-keystroke events),
which some apps may not honor — if real-world coverage disappoints, a
CGEvent path arrives only via a new ADR.

---

## ADR-019 — `GET /stream` is a token-gated, read-only websocket that never carries bulk tool output

**Status:** Accepted

**Context.** FAZA D3 (MASTER_PLAN §7.1) moves the cockpit from event polling
to a live push channel before D4 screen events arrive. The daemon is a
zero-dependency stdlib `ThreadingHTTPServer`; the transport token (C1)
guards mutating routes; and D2 left a recorded caveat: `ui_read_window`
tool output is on-screen text, persisted (redacted) in `tool_runs` and in
`tool.finished` event payloads.

**Decision.**

- **`GET /stream` upgrades to RFC 6455 implemented in-repo**
  (`dan/api/websocket.py`, ctypes-free stdlib only) on the connection
  thread the server already dedicates. No new runtime dependency.
- **The handshake is fail-closed behind the transport token.** Browsers
  cannot set `X-DAN-Token` on a WebSocket connect, so the cockpit sends
  it as a `dan-token.<token>` subprotocol entry next to `dan.v1`;
  CLI/tests use the header. When `security.api_token_required` is on, no token
  means 401 before any upgrade. **That flag defaults to `false`** in
  `SecurityConfig` and in the shipped example config, so on a default install
  the stream is NOT token-gated — turn it on if that matters. The token
  subprotocol is never echoed back.
- **The stream is strictly read-only.** It pushes persisted events (the
  same append-only store `GET /events` reads). Any client TEXT/BINARY
  frame closes the connection with 1003; unmasked or malformed client
  frames close with 1002. Approvals and actions stay on the POST routes.
- **Bulk tool output does not ride the stream.** Event payload key
  `output` (today: `tool.finished`, incl. `ui_read_window` screen text) is
  replaced with `output_omitted: true`. Consumers fetch details over the
  HTTP API. This is the conscious decision D2 required — the default is
  *not* to push screen text; widening needs a new ADR.
- **Redaction is applied twice**: events are redacted at append time, and
  the stream re-runs `redact_secrets` on every payload it ships, so even a
  row that reached the DB outside `EventStore.append` leaves redacted.
- Reading the handler's buffered `rfile` under a socket timeout permanently
  poisons `SocketIO`; the session drains handshake leftovers once
  (non-blocking) and then uses `select()` + `recv()` on the raw socket.

**Consequences.** The cockpit gets live events (poll-bounded latency;
`STREAM_POLL_INTERVAL_SECONDS` is 0.05 s today) with reconnect/backoff, and D4
screen events have a transport that is not HTTP polling. Streaming latency is
poll-bounded, not event-driven; if D4 needs tighter latency, an EventBus
wake-up can be added without changing the wire contract.

**Update (2026-07-21):** the omission is no longer stream-only. Both the stream
and `GET /events` now ship payloads through the same
`safe_event_payload_for_client` (`dan/api/event_safety.py`), which redacts
high-risk keys and sets `output_omitted: true`. The sentence that used to close
this ADR — "`GET /events` keeps returning full payloads (including
`tool.finished.output`) as before" — is false; that route was tightened too.

---

## ADR-020 — `screen_read` D4 is narrow-only: `screencapture` + Vision-via-ctypes in a crash-isolated subprocess, pixels never persist

**Status:** Accepted

**Context.** FAZA D4 (MASTER_PLAN) gives the operator eyes: capture +
on-device OCR, risk class `screen_read`. The permission model (§3) defines
two shapes — narrow (current window / named region: user A, model AP,
auto B) and broad (full display / continuous: user AP). ScreenCaptureKit
and Vision are Objective-C-only frameworks, the project has zero runtime
dependencies (no pyobjc — the D1/ADR-017 precedent), and the capability
inventory warns that the screen routinely contains secrets.

**Decision.**

- **D4 implements the narrow shape only**: `screen_read_window` (frontmost
  window, id resolved from CGWindowList — window *number* and owner only,
  never titles or contents, so the ADR-017 surface is unchanged) and
  `screen_ocr_region` (explicit bounded x/y/width/height). No full-display
  tool, no continuous capture — the broad shape requires a new ADR.
- **Capture goes through Apple's `/usr/sbin/screencapture`** (itself built
  on ScreenCaptureKit) — that binary *is* the D4 "bridge". Driving SCK's
  async ObjC API from ctypes would mean hand-rolled blocks and dispatch —
  disproportionate ABI risk for zero functional gain.
- **OCR is Vision `VNRecognizeTextRequest` driven through ctypes
  `objc_msgSend`, executed in a short-lived subprocess**
  (`python -m dan.macos.screen --ocr <png>`). Vision has no C API; the
  ObjC bridge is the riskiest code in D4, so it never runs inside `dand` —
  a segfault costs one tool run, not the daemon. The bridge uses request
  defaults only (no blocks, no queues) and returns observations in Vision's
  natural order.
- **Pixels are transient**: captures land as 0600 PNGs under the
  daemon-owned runtime dir (`~/.dan/runtime`) and are deleted right after OCR, success or
  failure. Only OCR *text* leaves the adapter — clipped at the tool layer
  (240 lines × 512 chars), redacted by ToolRunRecorder/EventStore as every
  tool output, and never carried by the D3 stream (ADR-019 omits bulk
  output).
- Backend knob `security.screen_read_backend`: `native` (default, needs the
  Screen Recording TCC grant — runbooks/SCREEN_RECORDING_TCC.md) or `fake`
  (deterministic fixture whose lines include a secret-shaped token, so every
  test/smoke run proves redaction). Unknown names fail the daemon at
  startup.

**Consequences.** "Look at my terminal / read this error" works with an
auditable tool-run trail (not a permission gate — see ADR-010/ADR-022); D4
needs its own TCC grant (Screen Recording, separate from Accessibility). The Vision bridge and its subprocess isolation
were verified live on a rendered PNG; the full native capture path awaits
the TCC grant (live gate). OCR text quality and ordering are Vision's —
no geometric re-sorting in D4.

---

## ADR-021 — Terminal profile D5: fixed-script osascript bridge, read and paste split, paste never submits

**Status:** Accepted EXCEPT the permission treatment. `terminal_read` and
`terminal_write` remain two distinct risk classes on two distinct tools, but
neither is gated: the policy allows everything (ADR-010/ADR-022), so
"no source ever gets a plain allow" for `terminal_write` is NOT true today.
The fixed-script bridge, the closed target set, the paste-never-submits rule,
the control-character rejection and the output clipping ARE implemented.

**Context.** FAZA D5 (MASTER_PLAN, dawne 21D) gives the operator a
terminal profile: "observe terminal state, paste prepared commands"
(MACOS_OPERATOR_CONTRACT.md). The capability inventory §9 defers raw
AppleScript as "a shell in a trenchcoat", allowing it only when a concrete
need exceeds Shortcuts and only with its own contract plus a
`shell_write`-grade risk treatment. Reading a terminal's contents has no
Shortcuts-shaped alternative, and terminal output routinely contains
secrets. The ui_read / ui_act precedent (ADR-017/018) demands that
observing and mutating never share a risk class.

**Decision.**

- **Two new permission classes, never merged.** `terminal_read`
  (user A / model AP / auto B — the ui_read / narrow-screen_read row) and
  `terminal_write` (user AP / model AP / auto B — the shell_write-grade
  treatment §9 requires: a pasted command is one Enter away from
  execution, so no source ever gets a plain allow).
- **The bridge executes only fixed AppleScript constants** via
  `/usr/bin/osascript` (`dan/macos/terminal.py`). Parameters travel
  through the `run` handler argv and are never interpolated into script
  source — no injection surface. Targets form the closed set
  {Terminal, iTerm2}; any other app name is an error, not a fallback.
  This is the sanctioned narrow exception to the §9 deferral; generic
  AppleScript execution stays deferred.
- **The observed surface is the front window / current session** of the
  explicitly named terminal app. Unlike ADR-017's frontmost-app rule for
  `ui_read`, the terminal app does not need global focus — "look at my
  terminal" is the use case — but the surface is still exactly one
  session, named per call. `tell application` auto-launches its target,
  so the bridge refuses to address an app that is not running (checked
  with `pgrep -qx` before osascript ever spawns).
- **Paste never submits.** `terminal_paste` uses iTerm2's
  `write text ... newline NO`; pressing Enter stays with the human. Terminal.app has no paste-without-execute verb, so
  pasting into Terminal.app is unsupported (the fake backend mirrors the
  refusal). Paste payloads are one bounded printable line
  (max 4096 chars); control characters — including the newline that would
  submit despite `newline NO`, the tab that triggers completion and
  escape sequences — are rejected at the adapter AND the tool layer (the
  D2 two-layer precedent). An auto-submitting or multi-line path requires
  a new ADR, not a flag.
- **Terminal text is treated as secret-bearing bulk output**: clipped at
  the tool layer (240 lines × 512 chars), redacted by
  ToolRunRecorder/EventStore like every tool output, never carried by the
  D3 stream (ADR-019 omits bulk output). The paste tool does not echo its
  text back in the result.
- Backend knob `security.terminal_backend`: `osascript` (default,
  needs the per-app Automation TCC grant —
  runbooks/TERMINAL_AUTOMATION_TCC.md) or `fake` (deterministic fixture
  whose lines include a secret-shaped token, so every test/smoke run
  proves redaction). Unknown names fail the daemon at startup.

**Consequences.** "Read my terminal / prepare this command for me" works
with an auditable tool-run trail and zero new dependencies. The
Automation grant is per (host app → target app) pair and separate from
Accessibility and Screen Recording. The osascript path needs a live gate
(running iTerm2/Terminal + Automation grant) — probe:
`python -m dan.macos.terminal`. Reading scrollback history, pasting
into Terminal.app, multi-line paste and any submit path all remain out of
scope pending their own ADRs.

---

## ADR-022 — Security bypass mode: all tools auto-approved for local testing and development (Ozzy explicit)

**Status:** Accepted

**Context.** ADR-010 established a permission matrix where reads are allowed,
writes/shell/network require approval, and destructive tools are blocked by
default. This regime is appropriate for production and untrusted sources. On
2026-07-08, the operator (Ozzy) explicitly configured local runtime security to
bypass approvals for accelerated development and testing on a single-user
machine.

**Decision.** With full awareness and intent, the Jarvis instance is configured
with `[security] auto_approve_mode = "all"` + `destructive_tools_enabled = true`
+ all approval flags (`require_approval_for_shell`, `require_approval_for_file_write`,
`require_approval_for_network`) set to `false`. Every tool request from every
source (user, model, worker) is auto-approved. Every tool run is still recorded
in the audit log with the risk class of the registered tool (the policy object
itself decides nothing — see the correction below). This mode is a
**conscious operational trade-off**, valid only in a
single-user, offline, air-gapped development environment under direct human
supervision.

**Consequences.** Development and testing iterate faster with no approval
prompts. The security matrix (ADR-010 matrix row / permission classes) remains
defined and auditable but unenforced; a future gate re-enabling approval will
require no code change — only `jarvis.toml` reversion. The ToolPermissionPolicy
still assigns risk classifications to every call, so real-world deployment,
shared machines, or untrusted input demands immediate re-lock (approval required
for mutations + destructive blocked). See [SECURITY_MODEL.md](SECURITY_MODEL.md)
§1, `jarvis/tools/permissions.py`, and `[security]` config in `jarvis.toml`.

> **CORRECTION (2026-07-21) — the re-lock described above does not exist.**
>
> "a future gate re-enabling approval will require no code change — only
> `jarvis.toml` reversion" is false, and it is the most dangerous sentence in
> this file: it tells a reader that flipping config keys restores protection.
> It does not. `ToolPermissionPolicy.decide()` (`dan/tools/permissions.py`)
> contains no branch at all — it returns ALLOW for every risk class and every
> source, and never reads `auto_approve_mode`, `destructive_tools_enabled`,
> `trusted_scopes` or any `require_approval_for_*` flag. Those keys are parsed,
> stored and rendered as runtime state; nothing consults them. `ApprovalGate` is
> not in the execution path. Re-locking means **writing the gate**, not
> reverting a config file.
>
> This ADR also predates the rename: it is `dan/tools/permissions.py` and
> `~/.dan/config.toml`, not `jarvis/…` / `jarvis.toml`.
>
> What actually constrains a tool today lives inside the individual tools —
> and one of those constraints was removed on this branch, see
> `security.shell_read_unrestricted`. Read
> [SECURITY_MODEL.md](SECURITY_MODEL.md) §2,
> [MACOS_PERMISSION_MODEL.md](MACOS_PERMISSION_MODEL.md) (whole document is
> marked unbuilt) and
> `reviews/2026-07-21-restart-orphan-shell-review.md` §2–§5, §12.

---

## Decision log

| ADR | Title | Status |
|-----|-------|--------|
| 001 | `dand` owns all truth | Accepted |
| 002 | The panel is a thin client | Accepted |
| 003 | Brain adapters are stateless | Accepted |
| 004 | The SQLite event store is the source of truth | Accepted |
| 005 | The voice broker is the sole speaker | Accepted |
| 006 | PTT is a `ListeningLease`, not a file | Accepted |
| 007 | launchd has one official label (`com.dan.dand`) | Accepted |
| 008 | `/tmp` is compatibility transport only | Accepted |
| 009 | Workers cannot speak or write memory facts directly | Accepted |
| 010 | Tools require a registry plus an approval policy | **Superseded by 022**; the approval half is gone from the code |
| 011 | Panel text and voice transcript use the same `TurnOrchestrator` | Accepted |
| 012 | `AudioDeviceManager` owns input/output device state | Accepted |
| 013 | Legacy DAN runtime is detected and reported, never auto-killed | Accepted |
| 014 | launchd artifacts avoid the `~/Documents` TCC trap (`~/.dan`) | Accepted |
| 015 | Worker job lifecycle uses worker_jobs for state and events for history | Accepted |
| 016 | Runtime state names are canonical and finite | Accepted |
| 017 | `ui_read` observes only the frontmost app and focused window, via a daemon-owned backend | Accepted **except** the PermissionPolicy step it names — that layer gates nothing |
| 018 | `ui_act` uses AX-only actions, never touching credentials | Accepted **except** the approval gate — it was never built; the AX-only, secure-field, control-character and length limits are real |
| 019 | `GET /stream` is a token-gated, read-only websocket that never carries bulk tool output | Accepted |
| 020 | `screen_read` D4 is narrow-only: `screencapture` + Vision-via-ctypes in a crash-isolated subprocess, pixels never persist | Accepted |
| 021 | Terminal profile D5: fixed-script osascript bridge, read and paste split, paste never submits | Accepted **except** the permission treatment — `terminal_write` is not gated; the bridge, the closed target set and paste-never-submits are real |
| 022 | Security bypass mode: all tools auto-approved for local testing and development (Ozzy explicit) | Accepted (supersedes ADR-010) |

> ADR-013 and ADR-014 were added by the Prompt 00B inventory, grounded in
> [LEGACY_RUNTIME_FINDINGS.md](LEGACY_RUNTIME_FINDINGS.md). Further migration
> decisions will be appended as additional ADRs.
