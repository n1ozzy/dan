# DAN (ex-Jarvis v4.1) — Product Definition

> **Status:** FROZEN v4.1 architecture & contract freeze (Prompt 00A), partly
> overtaken by Release 1 (2026-07-18). It still explains *what* the product is
> and *why*; it is not a status report. The binding data shapes live in
> [CONTRACTS.md](CONTRACTS.md); the binding decisions live in
> [DECISIONS.md](DECISIONS.md); the running state lives in [STATUS.md](STATUS.md).
>
> **Renames applied 2026-07-21:** `jarvisd` → `dand`, `com.ozzy.jarvisd` →
> `com.dan.dand`, `~/.jarvis` → `~/.dan`, package `jarvis/` → `dan/`.
>
> **Two guarantees below no longer hold in code:** tools no longer pass through
> an approval policy (Release 1 executes them directly), and the voice loop IS
> live. Both are corrected in place.

---

## 1. What DAN is

DAN is a **local, single-user voice + text assistant** running natively on
macOS (Apple Silicon). It is a successor to the legacy `dan` prototype, rebuilt
around one principle: **a single local daemon owns all truth.**

The daemon is named **`dand`**. Everything else — the macOS panel, the brain
adapters, the voice broker, the workers — is a participant that talks to
`dand`. None of them is the system of record.

DAN is *not* a cloud service, *not* multi-tenant, and *not* a chat wrapper.
It is a long-lived process that holds conversation state, memory, an event
history, a voice queue, a tool registry and worker jobs, and exposes them over a
local API.

DAN is also intended to be a **local Mac operator**, not just a chat
assistant. The ability to observe and act through controlled macOS capabilities
is core product scope; see [MACOS_OPERATOR_CONTRACT.md](MACOS_OPERATOR_CONTRACT.md).

---

## 2. Goals

- **One source of truth.** Conversation, memory, events, voice queue, leases,
  approvals and worker jobs live in a single SQLite database owned by `dand`;
  worker job state lives in `worker_jobs`, while lifecycle history lives in
  `events` as `worker.job.*`.
  ([ADR-001](DECISIONS.md#adr-001), [ADR-004](DECISIONS.md#adr-004))
- **Thin panel.** The macOS menu-bar panel renders daemon state and sends
  intents. It never computes or stores canonical state. If `dand` is down,
  the panel shows "offline" — it does not improvise. ([ADR-002](DECISIONS.md#adr-002))
- **Stateless brains.** A brain adapter (Claude CLI, Codex CLI, mock) receives a
  fully-formed `BrainRequest` and returns a `BrainResponse`. The provider's own
  session is *not* DAN's memory. ([ADR-003](DECISIONS.md#adr-003))
- **One speaker.** Only the voice broker emits audio. Nothing else calls a
  player. ([ADR-005](DECISIONS.md#adr-005))
- **One listening contract.** Push-to-talk is a `ListeningLease` in the
  database, not the presence of a file. ([ADR-006](DECISIONS.md#adr-006))
- **One launch identity.** Exactly one official launchd label,
  `com.dan.dand` (`OFFICIAL_LABEL` in `dan/runtime/supervisor.py`). Legacy labels
  are detected and reported, never silently adopted.
  ([ADR-007](DECISIONS.md#adr-007))
- **Auditable tools.** Every tool runs through the registry and lands in
  `tool_runs` + the event log with secrets redacted.
  **Superseded by Release 1:** the approval policy no longer gates anything —
  tools execute directly, and each tool enforces its own limits (approved roots,
  the `shell_read` allowlist, a scrubbed environment).
  ([ADR-010](DECISIONS.md#adr-010))
- **Local Mac operator.** macOS operator capability classes such as
  Accessibility, screen capture, OCR, browser flow assistance, external
  communication, and credential/user-presence flows are mediated by `dand`,
  not by a model acting directly. Specific tools inside those classes require
  later scoped contracts and tests.
- **Workers advise, they do not act on the world.** A worker job produces a
  *memory candidate*, never a spoken sentence and never a committed memory fact.
  ([ADR-009](DECISIONS.md#adr-009))
- **Same pipeline for every input.** Typed panel text and a voice transcript
  enter the *same* `TurnOrchestrator`. ([ADR-011](DECISIONS.md#adr-011))

---

## 3. Non-goals (for the MVP)

- No Docker / no Linux. macOS-native only (microphone + Metal are required and
  unavailable in a VM).
- No multi-user, no remote access. Local auth is the transport token
  (`X-DAN-Token`) on mutating endpoints and private reads, on top of the
  localhost bind.
- No automatic killing of legacy processes. DAN *detects and reports*
  conflicts; the human decides. ([ADR-007](DECISIONS.md#adr-007))
- No automatic launchd installation. Install scripts print exactly what they
  would do and require an explicit human run.
- No destructive cleanup of the legacy `dan` repo. Ever, automatically.
- *(Was an MVP non-goal, no longer true:)* the live voice loop runs. Since
  Release 1, `dand` owns the microphone, the global PTT hotkey and the voice
  queue, and supervises `supertonic serve` as a child. Tests still mock TTS.

---

## 4. The actors

| Actor | Role | Speaks? | Owns state? |
|-------|------|---------|-------------|
| **`dand`** | The daemon. Owns the DB, the event store, the state machine, the API, the mic and the hotkey. | — | **Yes — all of it.** |
| **Panel** | macOS menu-bar client (PyObjC + WKWebView). Renders state, sends intents. | No | No |
| **Brain adapter** | Stateless function: `BrainRequest → BrainResponse`. | No | No |
| **Voice broker** | The *only* component that plays audio. Drains the persisted voice queue. | **Yes — exclusively.** | No (reads/writes the DB queue) |
| **Worker** | Async background job (e.g. a Codex/Claude agent). Produces memory *candidates*. | **No** | No |
| **RuntimeSupervisor** | Observes launch mode and legacy conflicts. Never kills. | No | No (records observations) |

No worker is wired in the running daemon: `create_daemon_app_from_config` sets
`worker_broker = None`, so the `/workers/*` routes answer with an error until a
broker is registered. The row above is the contract, not a running component.

The legacy `dan` system had three "voices" (DAN-robot, DAN-głos, "thoughts
hook") each able to call a player independently. This design collapses speaking
authority into the voice broker alone.

---

## 5. The shape of a turn (overview)

A *turn* is one user input and the system's full response to it. Both typed text
and transcribed speech produce a turn through the same orchestrator:

```
input (text or voice transcript)
      │
      ▼
  TurnOrchestrator ──► build context (DB + config + active memory)
      │                       │
      │                       ▼
      │                 BrainRequest ──► brain adapter (stateless) ──► BrainResponse
      │                                                                     │
      │   (optional) ToolCall ──► tool run (direct, no gate since Rel. 1)   │
      │   (optional) WorkerJob ──► memory candidate                         │
      ▼                                                                     ▼
  turn.finished ◄───────────────────────────────────────────── response persisted
      │
      ▼
  (optional) VoiceRequest ──► voice queue ──► voice broker ──► speaker
```

The authoritative description is in [TURN_PIPELINE.md](TURN_PIPELINE.md). The
field-level shapes are in [CONTRACTS.md](CONTRACTS.md).

---

## 6. Runtime layout

The runtime root is `~/.dan` and the official launchd label is `com.dan.dand`.
Every path under it — config, database, logs, PID file, API token — is owned and
listed in [CO-JEST-GDZIE.md](CO-JEST-GDZIE.md); this document does not keep a
second copy.

`/tmp` is **compatibility transport only** — a place to bridge to legacy
components if ever needed. It is never a source of truth.
([ADR-008](DECISIONS.md#adr-008))

---

## 7. Bounded contexts (where things live)

| Context | Package | Responsibility |
|---------|---------|----------------|
| Daemon | `dan/daemon` | Process lifecycle, state machine, app wiring, HTTP server |
| Store | `dan/store` | SQLite, migrations, repositories, event store |
| Events | `dan/events` | Event types, models, in-process bus |
| API | `dan/api` | HTTP + WebSocket route handlers |
| Runtime | `dan/runtime` | Launch-mode + legacy-conflict supervision |
| Turns | `dan/turns` | `TurnOrchestrator`, turn/conversation models |
| Brain | `dan/brain` | Stateless adapters, context builder, manager |
| Memory | `dan/memory` | Memory blocks, Memory OS, compiler, context selection |
| Tools | `dan/tools` | Registry, tools, and the retired permissions/approval classes |
| Security | `dan/security` | Secret redaction, local transport token |
| Workers | `dan/workers` | Job broker, worker adapters, `worker_jobs` state and `worker.job.*` events |
| Audio | `dan/audio` | Device manager + policy |
| Voice | `dan/voice` | Listening leases, voice queue, TTS broker, STT/VAD/anti-echo |
| Input | `dan/input` | Global PTT hotkey, macOS event tap |
| macOS | `dan/macos` | Accessibility, screen capture/OCR, terminal bridge |
| Panel | `dan/panel` | macOS menu-bar client (thin) |
| Persona | `config/persona/DAN.md` | The single persona canon (data, not code) |

---

## 8. What this freeze guarantees

After Prompt 00A, the following are **fixed and may not drift** without an ADR
update:

1. The contract names and their owning modules (see [CONTRACTS.md](CONTRACTS.md)).
2. The runtime state set (see [TURN_PIPELINE.md](TURN_PIPELINE.md)).
3. The architectural decisions (see [DECISIONS.md](DECISIONS.md)).

Everything else (exact field types, additional events, internal helpers) is
implementation detail to be filled in by later prompts, but it must not
contradict this freeze.
