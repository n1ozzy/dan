# Jarvis v4.2 — Master Plan (plan-of-record)

Status: IN FORCE (Ozzy's mandate 2026-07-02: "chciałbym abyś mnie prowadził
[...] sam ogarniesz co i jak" + "dyscyplina w chuj aż dowieziemy Jarvisa").
Date: 2026-07-02. HEAD at the time of writing: `28b1611` (19D-A, 615 tests green).
Product goal (Ozzy's vision): turn DAN into a Jarvis like in Iron Man —
a full macOS operator with voice, memory, and personality, on a disciplined runtime.

This document **replaces**, as plan-of-record:

- the execution sequence from `JARVIS-V4-1-CODEX-MASTER-PROMPT-SEQUENCE.md` (Desktop, GPT 5.5 PRO plan),
- the planning sections of the parent report `info.txt` (Desktop, GPT 5.5 Thinking handoff).

It does not replace the contracts: `docs/CONTRACTS.md`, `docs/SECURITY_MODEL.md`,
`docs/MACOS_OPERATOR_CONTRACT.md`, `docs/TURN_PIPELINE.md` remain the source of truth
for their domains.

---

## 1. Why this document exists

The planning history had two sources that were never reconciled against each other:

1. **Blueprint PRO** (`JARVIS-V4-1-FINAL-MASTER-BLUEPRINT.md` + sequence 00–24):
   product = a local voice-and-text assistant; the MVP included the voice track
   (audio devices, PTT leases, voice queue, anti-echo), WorkerBroker, MenuBar,
   WebSocket, launchd, real file/shell tools.
2. **The Thinking continuation** (prompts 14, 15, 15A/B, 19A–D, 20A/20A-FIX):
   deepened the approval loop **beyond** the blueprint (that was good) and re-pointed
   the product at a **macOS operator** (20A) — which the blueprint knew nothing
   about — while quietly abandoning half of the PRO MVP without a formal verdict.

The result: the repo has module skeletons (`NotImplementedError`) left over from the
PRO scaffold, the docs have the operator contract from Thinking, and no document said
which parts of the PRO MVP survive, which are deferred, and which are killed. This
plan settles that.

**Product decision (Ozzy, confirmed):** Jarvis is a macOS operator.
Voice is an interface, not the foundation — it comes after the operator foundation.
The 20A pivot stands. We settle the PRO MVP against this decision.

---

## 2. Sacred principles (unchanged)

```text
jarvisd owns truth (SQLite)
panel renders truth
brain thinks statelessly — the model NEVER executes, it only proposes
jarvisd executes: ToolRegistry -> PermissionPolicy -> ApprovalGate -> EventStore
provider session is not memory
EventStore = append-only audit timeline with central secret redaction
approve does not execute; execute-approved is separate and explicit
examples != commitments (after 20A-FIX)
/tmp is transport, not memory
the only launchd label: com.ozzy.jarvisd
legacy repo dev/dan: read-only museum
```

Operating rules (the discipline to deliver):

```text
one stage = one scoped prompt = one problem = one small commit
after every stage: git status clean, pytest green
after every flow change: smoke harness (fake CLI brain pattern, not a real provider)
gate review after stages marked GATE — no moving on before the review
docs correction separate from implementation
no "while we're at it" — scope creep = rejected diff
no yes-manning; fact / example / vision / decision / commitment separated explicitly
```

**CI status (FIX-12, 2026-07-05):** an active GitHub Actions workflow (`.github/workflows/ci.yml`) runs `ruff`, the unit tests, and the `smoke matrix` (`smoke-text-runtime`, `smoke-tools-approvals`, `smoke-file-read`, `smoke-stream`) on `push`/`PR` (`ubuntu-latest`).
This is the minimum, with the closed "green after every stage" mandate as the check.

---

## 3. Actual repo state (verified 2026-07-02)

> **UPDATE 2026-07-02 evening — PHASES A–H CLOSED.** The state below
> (615 tests, skeletons, findings F1–F5) is the picture from before the v4.2
> sequence — it stays as a historical record. Current state: **1322 tests, 22/22 smoke**;
> PHASES A–F delivered, voice track G0–G4 (GATE G4 + Gate G passed,
> G5 deferred by decree §7.8, model M1 stays), H1 menu-bar shell
> (NSStatusItem + NSPopover + WKWebView, operator-first cockpit with a
> basic/advanced view), H2 `scripts/jarvis-dan-report` (diagnose-only,
> snapshot in `docs/reviews/2026-07-02-legacy-dan-leftovers.md`), H3 docs
> handoff. Findings F1–F5 settled in PHASE A/B (fail-closed roots,
> realpath containment, source-sensitivity, extended redaction, transport
> token). Orientation for reviewers: `docs/REVIEW_HANDOFF.md`.
> Post-MVP backlog: redesigning the panel contents for the operator
> (model/effort/provider selection and deeper voice settings require new
> daemon endpoints; PTT/listening already run on the existing lease endpoints;
> Ozzy's feedback 2026-07-02).

Works and is tested (615 tests):

- text turn pipeline (`POST /input/text`, CLI, history, conversations),
- brain adapters: mock + Claude CLI + Codex CLI foundation (fake subprocess in tests),
- EventStore with central redaction (`jarvis/security/redaction.py`),
- Memory API/CLI + ContextBuilder (active-only),
- the full approval loop: registry → policy → approval → explicit execute → ToolRun
  → one-shot brain continuation → turn finished,
- the `<jarvis_tool_call>` parser in the CLI adapters (NOTE: the mock does not have
  it — a smoke with model-originated tool calls requires a fake CLI, pattern:
  `scripts/smoke-tool-continuation.sh`),
- `awaiting_approval` without a daemon deadlock (deliberately no RuntimeState.WAITING_APPROVAL),
- static HTML cockpit (polling) + restricted localhost CORS,
- RuntimeSupervisor report-only, no auto-kill,
- 5 smoke harnesses in `scripts/`.

`NotImplementedError` skeletons left over from scaffold 01 (untouched since then):

- `jarvis/api/websocket.py`, `routes_brain.py`, `routes_voice.py`, `routes_audio.py`
- `jarvis/workers/*` (broker, jobs, codex/claude workers)
- `jarvis/voice/*` (broker, queue, tts, stt, vad, anti_echo, listening)
- `jarvis/audio/*` (devices, models, policy)
- `jarvis/panel/menubar_app.py`, `webview_bridge.py`
- `jarvis/tools/shell_tool.py`, `file_tool.py` (38 lines each, no logic)
- `jarvis/turns/policies.py`

Findings from the code review (Fable 5, 2026-07-02) — real defects in merged code:

| # | Finding | Location | Severity |
|---|-----------|---------|------|
| F1 | `file_read` fail-OPEN: empty `approved_roots` (default) ⇒ ALLOW for any path. Breaks SECURITY_MODEL ("allow **within approved roots**") and blueprint PRO §12. | `jarvis/tools/permissions.py:103` | high (latent until a real file tool exists) |
| F2 | Containment without `realpath` — a symlink under an approved root pointing outside the root passes the check. | `jarvis/tools/permissions.py:152` | high (latent, as above) |
| F3 | `PermissionPolicy.decide()` does not accept `source` (`direct_user_command` vs `model_originated` …) — while source-sensitivity is a sacred principle from operator contract §5.4. | `jarvis/tools/permissions.py:58` | design-level — for 20B |
| F4 | Redaction does not catch: `gho_/ghs_/ghu_/ghr_`, Slack `xox[bap]-`, AWS `AKIA…`. | `jarvis/security/redaction.py:66` | low |
| F5 | Zero auth/CSRF on the daemon API — only the 127.0.0.1 bind. Blocks real tools. | `jarvis/config.py:111`, `jarvis/daemon/app.py` | high before PHASE C |

---

## 4. Settling the PRO MVP — verdicts

Every MVP item from the PRO blueprint gets an explicit verdict. "DEFER" has an entry
condition — it is not a euphemism for "never".

| PRO MVP item (prompt) | Verdict | Rationale / entry condition |
|---|---|---|
| Contracts, scaffold, config, schema, events, state machine, API, supervisor, brain, memory, turn pipeline, CLI adapters (00A–11) | **DONE** | delivered, partly in a different order |
| ToolRegistry + ApprovalGate (12) | **DONE+** | done better than PRO: explicit execute-approved instead of auto-execute after approve; plus policy on model tool calls, continuation, redaction |
| Real `shell_tool` / `file_tool` (12) | **KEEP — PHASE C** | an operator without file/shell is a mock-up; entry after hardening (PHASE A) and the permission model (PHASE B) |
| WorkerBroker (13) | **DEFER — PHASE E** | the operator core matters more; entry after 21A/21B, once there is something to delegate |
| AudioDeviceManager (14) | **DEFER — PHASE G** | voice after the foundation (Ozzy's decision); the AudioDeviceState contract in CONTRACTS.md stays |
| ListeningLease / PTT (15) | **DEFER — PHASE G** | as above; the ListeningLease contract stays — do not design from scratch |
| VoiceQueue / TTS broker (16) | **DEFER — PHASE G** | as above; the voice_queue table already exists in the schema — do not touch it |
| Anti-echo / STT / barge-in (17) | **DEFER — PHASE G** | as above |
| MenuBar shell PyObjC (18) | **DEFER — PHASE H** | the static cockpit is enough until the end of the foundation; native panel after e2e |
| Compact cockpit UI (19) | **DONE differently** | as the static HTML cockpit; upgrade to live in PHASE E (WebSocket) |
| Brain switch API (20) | **KEEP — PHASE E** | `routes_brain.py` is a stub; needed before >1 real provider is in use |
| Memory UI / settings UI (20) | **PARTIALLY DONE** | memory API/CLI/cockpit exist; settings UI with PHASE E |
| WebSocket `/stream` (07) | **KEEP — PHASE E** | polling is enough for now; live stream before screen-events (21C) and workers |
| Launchd lifecycle (21) | **KEEP — PHASE F** | after the e2e smoke, before voice; never auto-install |
| E2E MVP smoke (22) | **KEEP — PHASE F** | updated operator scenario (§6) |
| Docs handoff (23) | **ONGOING** | runbooks maintained per stage |
| Legacy DAN cleanup helpers (24) | **DEFER — PHASE H** | unchanged: diagnose-only, never destructive |
| Wake word / always-on / MCP / vector memory / multi-persona / cloud (§17 PRO) | **OUT** | unchanged — non-MVP |

Thinking-era additions absent from PRO — verdict **KEEP, already DONE**: explicit
execute-approved, model tool-call capture, provider tool block parser,
approval decision events, PermissionPolicy on the model path, awaiting_approval,
one-shot continuation, central redaction, operator contract + examples≠commitments.

New relative to both plans (the operator pivot): PHASES B–D below.

### 4a. Register of expectations from legacy DAN (audit 2026-07-02)

Ozzy's decree (§7.6): we carry over no code, logic, or architecture from DAN.
This register is exclusively: **requirements** (what must work, because it worked
and Ozzy expects it) and **facts about third-party tools** (properties of MLX/sox/
whisper discovered empirically — they concern tools we chose anyway,
not DAN's design). Implementation is always clean-room against the v4.1 contracts.

| Item | Nature | Verdict |
|---|---|---|
| First-sound ≤ ~2 s for a voice response (sentence streaming + fillers) | requirement | **KEEP — G0/G3** (streaming contract design in G0) |
| Listening does not cut the user off mid-sentence; the echo of its own TTS does not become a turn | requirement | **KEEP — G4** (mechanism designed from scratch in G0/G4; state via the DB, not /tmp) |
| PTT: button + global hotkey; silence by default, zero always-on | requirement | **KEEP — G2** (ListeningLease already has the `global_hotkey` source) |
| sox: gain BEFORE silence, otherwise VAD cuts off weak words; highpass 80 Hz for hum | tool fact (sox) | **KEEP — G4** |
| Whisper hallucinates on silence/noise — junk filters and a no-speech threshold are needed | tool fact (whisper) | **KEEP — G4** (we will write our own filters) |
| MLX keeps model+stream per thread — MLX synthesis/inference must live in a dedicated thread | tool fact (MLX) | **KEEP — G5** (also applies to MLX STT in G4) |
| TTS chunked per engine + preparing the next chunk while playing | requirement (fluency) | **KEEP — G3** (own design in the broker) |
| The jarvis persona (the only one, our own, no muzzle); persona = data, not state | requirement | **KEEP — DELIVERED (2026-07-08)** (config/persona/jarvis.md; gangus/mentor deleted; see §7.7) |
| Target voice: voice-clone; until then, the available voices of the allowed engines | requirement | **KEEP — G3/G5** (engine set: decision §7.3) |
| Multi-provider brain (groq, qwen, local Bielik, chain) | requirement (future) | **DEFER — after MVP-voice** |
| Work modes normal/auto/plan | superseded | source-sensitive policy (PHASE B) + ApprovalGate — a better model of the same thing |
| `--dangerously-skip-permissions` ("pełne ręce") | sin | **KILL** — replaced by registry+policy+approvals |
| State in /tmp, direct afplay, a panel with its own state, hardcoded paths, DAN's code at all | sin | **KILL** — ADRs 001/002/005/008 + decree §7.6 |

Operational note: legacy DAN **still runs** on this Mac (voice_broker.py,
auto_jarvis.py, listen_ozzy.py loop + com.dan.voice-broker.plist in LaunchAgents,
state as of 2026-07-02). Per ADR-013 we do not kill it automatically. **Entry
condition for PHASE G: Ozzy manually shuts down the legacy runtime** (commands in
`~/Desktop/Jarvis/JARVIS-NEXT-STEPS-FOR-OZZY.md` §5) — otherwise two systems will
fight over the microphone and speaker.

---

## 5. The v4.2 sequence — phases and stages

Numbering starts fresh (the old one was already non-linear: 19D after 20A). Old
numbers in parentheses for continuity with the commit history.

### PHASE A — Hardening the foundation (before any new operator code)

- **A1** — fail-closed policy: `file_read` with empty `approved_roots` ⇒ BLOCKED;
  containment via `os.path.realpath` on both sides; tests for symlink escape
  and an empty root. Fixes F1+F2. Small commit, just the policy code + tests.
- **A2** — redaction gaps: patterns `gho_/ghs_/ghu_/ghr_`, `xox[bap]-`, `AKIA[0-9A-Z]{16}`;
  tests. Fixes F4. A separate small commit.

Gate A: pytest green, smoke-tools-approvals PASS, diff review.

### PHASE B — The operator permission model (docs only) *(formerly 20B)*

- **B1** — `docs/MACOS_CAPABILITIES.md`: an inventory of capability classes
  (Accessibility read / Accessibility act / ScreenCapture+OCR / terminal profile /
  file / shell / network / notifications / …) — each with: macOS framework,
  risk class, approval default, required TCC permission, privacy concern,
  future tool names, implementation status. Classes, not commitments.
- **B2** — `docs/MACOS_PERMISSION_MODEL.md`: the source-sensitive policy design —
  signature `decide(risk, source, tool_name, payload)`; the
  source × risk → decision matrix; user-presence model; the transport token design
  (F5) as a precondition for PHASE C. Designs the fix for F3.

Gate B (GATE — Ozzy review): zero runtime code in this phase; commitment creep check
(§17.6 from info.txt still applies).

### PHASE C — Real foundational tools *(from PRO prompt 12, never done)*

- **C1** — transport auth: a local token (file in `~/.jarvis`, 0600), header
  required for mutating endpoints; the cockpit gets the token; tests. Fixes F5.
- **C2** — `decide()` with a `source` parameter per B2 + rewiring both paths
  (direct and model-originated); matrix tests. Fixes F3.
- **C3** — `file_tool` read-only: real reads within fail-closed approved roots,
  size limits, ToolRun + events + redaction; smoke.
- **C4** — `file_tool` write + `shell_tool` read-only profile: approval-required
  always; a command whitelist for shell_read; smoke.

Gate C (GATE): a full tools+approvals+continuation smoke on the real tools.

### PHASE D — Operator adapters *(formerly 21A–D)*

- **D1** *(21A)* — Accessibility read-only adapter (AXUIElement through jarvisd,
  never through the model); TCC onboarding documented (ADR-014: artifacts outside
  `~/Documents`); smoke with fake data.
- **D2** *(21B)* — Accessibility actions (click, typing) — always approval,
  source-sensitive per B2.
- **D3** — WebSocket `/stream` + live cockpit (moved from PHASE E —
  decision §7.1: screen events in D4 need a stream, not polling).
- **D4** *(21C)* — ScreenCaptureKit + Vision OCR bridge (read-only).
- **D5** *(21D)* — Terminal/iTerm operator profile.

Gate D (GATE): each stage separately + review; D2 requires a working C1 (auth).

### PHASE E — The runtime grows up

- **E1** — brain switch API (`/brain/adapters`, `/brain/current`, `/brain/switch`,
  persisted in settings, history survives a switch).
- **E2** — WorkerBroker + the first worker (mock, then codex/claude);
  a worker does not speak and does not write memory, its result = a memory candidate.
- **E3** — settings UI in the cockpit.
- **E4** — persona: **UPDATED 2026-07-08:** Jarvis — the only persona, our own,
  no muzzle, for Ozzy. Degenerate, vulgar, mercilessly sarcastic.
  Implementation: `config/persona/jarvis.md` (data, not code). Gangus-1/2/3
  and mentor deleted (the E4 plan from 2026-07-02 assumed 4 profiles with
  boundaries; the 2026-07-08 decree consolidates into ONE persona). ContextBuilder
  loads `config/persona/jarvis.md` with no profile selector (fail-closed, the
  daemon does not crash; future profile rotation = Ozzy's decision). The persona
  has no state, does not decide about tools, does not bypass approvals.

### PHASE F — Stabilization

- **F1** — e2e MVP smoke (operator scenario, §6).
- **F2** — launchd lifecycle (install script explicit, never auto; uninstall does not delete the DB).

Gate F (GATE): acceptance criteria §6 met.

### PHASE G — Voice track *(the whole PRO 14–17 package + lessons from DAN, §4a)*

Entry condition: legacy DAN shut down manually by Ozzy (§4a, operational note).

- **G0** — streaming design: the sentence-streaming contract in the brain adapters
  (on_delta → chunk → VoiceRequest) + the fillers policy. Docs-only, because this
  changes the BrainResponse contract — without it first-sound goes back to 8–10 s
  and Ozzy will rightly say the old DAN was faster.
- **G1** — AudioDeviceManager + policy (pin builtin mic, output follows system,
  BT mic warning) — the contracts from CONTRACTS.md, no designing from scratch.
- **G2** — ListeningLease + PTT API (flag + global hotkey) + mock recorder.
- **G3** — VoiceQueue + TTS broker: pluggable engines (the set from decision §7.3:
  Supertonic + Chatterbox; edgeTTS/piper/XTTS banned), per-engine chunking +
  preparing the next chunk while playing (requirement §4a); the broker =
  the only speaker; direct afplay = violation. First real engine: Supertonic;
  a mock engine in tests.
- **G4** — STT: MLX whisper (decree §7.4) + recording (the sox facts from §4a)
  + our own junk filters + anti-echo + barge-in; the transcript goes through the
  same TurnOrchestrator. Clean-room implementation.
Gate G (GATE): voice safety review for the G0–G4 scope (the equivalent of Gate 6
from PRO). It happens BEFORE G5 — it does not wait for the voice-clone (decree §7.8).

- **G5** — **DEFERRED "for someday" (decree §7.8, 2026-07-02).** Chatterbox MLX
  voice-clone (inference in a dedicated thread — the MLX fact from §4a); ultimately
  Jarvis's own voice. Until further notice Jarvis's voice is supertonic M1;
  chatterbox stays in RESERVED_ENGINES. ElevenLabs only if Ozzy
  decrees it.

### PHASE H — Finishing touches

- **H1** — MenuBar shell (PyObjC) — a native panel, still a thin client.
  **DONE 2026-07-02**: NSStatusItem (the JARVIS wordmark as a template icon)
  + NSPopover 480×760 (dark chrome/underlay) + WKWebView on the same
  cockpit assets; token seeded from `~/.jarvis/runtime/api-token`. Operator-first
  cockpit: a basic view (Chat with Enter-to-send, tool Approvals,
  a readable History) + a „Zaawansowane" toggle (API, Daemon state,
  Memory, Tools, Settings, Events, Runtime — with descriptions).
  Backlog (Ozzy's feedback): model/effort/provider selection and voice
  controls (engine/tempo and other settings beyond PTT/listening) require new
  daemon endpoints — a separate stage, not a patch in the panel. PTT/listening
  are already handled by the existing lease endpoints.
- **H2** — legacy DAN cleanup helpers (diagnose-only). **DONE 2026-07-02**:
  `scripts/jarvis-dan-report` (`jarvis/diagnostics/legacy_dan.py`) —
  an inventory of processes/LaunchAgents/repo/tmp/HF cache/TTS split into
  DAN's junk (15.6 GiB pending a decision) vs Jarvis's assets (M1 — do not
  delete); structurally incapable of deleting (source contract test). Snapshot:
  `docs/reviews/2026-07-02-legacy-dan-leftovers.md`.
- **H3** — final docs handoff. **DONE 2026-07-02**: `REVIEW_HANDOFF.md`
  rewritten for the state after PHASES A–H, current-state annotation in §3.

---

## 6. Acceptance criteria — MVP-operator (update of PRO §16)

The MVP-operator passes when:

1. `jarvisd` starts and reports health (launchd or cli).
2. One input (text/CLI/panel) = exactly one Turn; history survives a restart.
3. Events explain the full lifecycle of every turn and every tool.
4. The cockpit shows the same truth as the daemon, live (stream, not polling).
5. A model-originated tool call goes through: policy(source) → approval → explicit
   execute → ToolRun → continuation; never auto-execute.
6. `file_read` outside approved roots = BLOCKED; symlink escape = BLOCKED (tested).
7. Mutating endpoints require the local token.
8. Jarvis reads real UI state (Accessibility read) and performs an approved UI
   action (Accessibility act) exclusively through jarvisd, with a full audit trail.
9. Screen capture+OCR available as a read-only tool, with consent.
10. A rejected approval never executes; a duplicate execute = 409, no second ToolRun.
11. A brain switch preserves history.
12. A worker job never speaks and never writes memory directly.
13. Zero raw secrets in events/logs (redaction tests + manual grep).
14. Launchd install is manual only, one label `com.ozzy.jarvisd`.
15. Legacy conflicts visible in `/runtime/processes` and the cockpit.
16. `pytest tests -v` green; all smoke harnesses PASS.

The voice criteria (PRO §16 items 8–11) move to the MVP-voice milestone after PHASE G.

---

## 7. Decisions (made 2026-07-02, Ozzy's mandate)

1. **WebSocket before screen-capture: YES.** Moved to PHASE D as D3
   (before ScreenCaptureKit/OCR). Screen events over polling is asking
   for lag and dropped state frames.
2. **MenuBar: stays at the end (H1).** The static cockpit does the job through the
   whole foundation; the native panel is finishing work, not infrastructure.
3. **TTS: a broker with pluggable engines — the engine set decreed by
   Ozzy (2026-07-02):** Supertonic (fast/medium quality) + Chatterbox
   (voice-clone) + ultimately his own voice. **Banned: edgeTTS, piper, XTTS.**
   Until the own voice exists: the available voices of the allowed engines. In
   tests exclusively a mock engine. A native macOS engine is not a goal — at
   most an emergency fallback, if Ozzy so decides.
4. **STT: MLX whisper.** Decreed by Ozzy. Fallbacks are not a goal.
5. **Tool selection = Ozzy's decision.** Fable 5 recommends and justifies,
   Ozzy decrees. No tool enters the project outside this path.
6. **Legacy DAN: zero code, zero logic, zero architecture** (Ozzy's decree:
   "nie używamy kodu ani działania i logiki z DANA — bo jest zjebana").
   DAN remains exclusively: (a) a list of user requirements (what must work),
   (b) a museum of warnings (what not to do), (c) a source of **empirical facts
   about third-party tools** that we chose anyway (properties of MLX, sox,
   whisper — see §4a, the "Nature" column). Everything is implemented
   clean-room against the v4.1 contracts.
7. **Persona (E4): a SINGLE persona, jarvis, no muzzle.**
   **UPDATED 2026-07-08:** Ozzy's decree (2026-07-02) assumed
   4 profiles (gangus-1..3 + mentor) with a selector through settings. Actually
   deployed: ONE persona `config/persona/jarvis.md` — degenerate, without
   boundaries, without a muzzle, for Ozzy. Gangus-1/2/3 and mentor deleted.
   Content written fresh in a session with Ozzy (not copy-pasted from persona.py,
   decree §7.6). ContextBuilder loads `jarvis.md` with no profile selector
   (fail-closed). Future profile rotation = a separate Ozzy decision.
8. **G5 voice-clone: DEFERRED "for someday".** Ozzy's decree (2026-07-02,
   after closing Gate G4): supertonic M1 remains Jarvis's voice until further
   notice; chatterbox stays in RESERVED_ENGINES (reserved, not implemented).
   Gate G (voice safety review) happens BEFORE G5 and covers the live G0–G4 scope
   — it does not wait for the voice-clone. Returning to G5 only by a new Ozzy decree.
