# Jarvis v4.1 — Product Definition

> **Status:** FROZEN (Prompt 00A — architecture & contract freeze).
> This document describes *what* Jarvis v4.1 is and *why*. The binding data
> shapes live in [CONTRACTS.md](CONTRACTS.md); the binding decisions live in
> [DECISIONS.md](DECISIONS.md).

---

## 1. What Jarvis is

Jarvis is a **local, single-user voice + text assistant** running natively on
macOS (Apple Silicon). It is a successor to the `dan` prototype, rebuilt around
one principle: **a single local daemon owns all truth.**

The daemon is named **`jarvisd`**. Everything else — the macOS panel, the brain
adapters, the voice broker, the workers — is a participant that talks to
`jarvisd`. None of them is the system of record.

Jarvis is *not* a cloud service, *not* multi-tenant, and *not* a chat wrapper.
It is a long-lived process that holds conversation state, memory, an event
history, a voice queue, a tool registry, an approval gate, and worker jobs, and
exposes them over a local API.

Jarvis is also intended to be a **local Mac operator**, not just a chat
assistant. The ability to observe and act through controlled macOS capabilities
is core product scope; see [MACOS_OPERATOR_CONTRACT.md](MACOS_OPERATOR_CONTRACT.md).

---

## 2. Goals

- **One source of truth.** Conversation, memory, events, voice queue, leases,
  approvals and worker jobs live in a single SQLite database owned by `jarvisd`;
  worker job state lives in `worker_jobs`, while lifecycle history lives in
  `events` as `worker.job.*`.
  ([ADR-001](DECISIONS.md#adr-001), [ADR-004](DECISIONS.md#adr-004))
- **Thin panel.** The macOS menu-bar panel renders daemon state and sends
  intents. It never computes or stores canonical state. If `jarvisd` is down,
  the panel shows "offline" — it does not improvise. ([ADR-002](DECISIONS.md#adr-002))
- **Stateless brains.** A brain adapter (Claude CLI, Codex CLI, mock) receives a
  fully-formed `BrainRequest` and returns a `BrainResponse`. The provider's own
  session is *not* Jarvis memory. ([ADR-003](DECISIONS.md#adr-003))
- **One speaker.** Only the voice broker emits audio. Nothing else calls a
  player. ([ADR-005](DECISIONS.md#adr-005))
- **One listening contract.** Push-to-talk is a `ListeningLease` in the
  database, not the presence of a file. ([ADR-006](DECISIONS.md#adr-006))
- **One launch identity.** Exactly one official launchd label,
  `com.ozzy.jarvisd`. Legacy labels are detected and reported, never silently
  adopted. ([ADR-007](DECISIONS.md#adr-007))
- **Auditable tools.** Every tool runs through a registry plus an approval
  policy. Rejected calls never execute; secrets never land unredacted in the
  event log. ([ADR-010](DECISIONS.md#adr-010))
- **Local Mac operator.** macOS operator capability classes such as
  Accessibility, screen capture, OCR, browser flow assistance, external
  communication, and credential/user-presence flows are mediated by `jarvisd`,
  not by a model acting directly. Specific tools inside those classes require
  later scoped contracts, tests, and permission policy.
- **Workers advise, they do not act on the world.** A worker job produces a
  *memory candidate*, never a spoken sentence and never a committed memory fact.
  ([ADR-009](DECISIONS.md#adr-009))
- **Same pipeline for every input.** Typed panel text and a voice transcript
  enter the *same* `TurnOrchestrator`. ([ADR-011](DECISIONS.md#adr-011))

---

## 3. Non-goals (for the MVP)

- No Docker / no Linux. macOS-native only (microphone + Metal are required and
  unavailable in a VM).
- No multi-user, no remote access, no authentication beyond "localhost only".
- No automatic killing of legacy processes. Jarvis *detects and reports*
  conflicts; the human decides. ([ADR-007](DECISIONS.md#adr-007))
- No automatic launchd installation. Install scripts print exactly what they
  would do and require an explicit human run.
- No destructive cleanup of the old `dan` repo. Ever, automatically.
- No live voice loop is started by this build. Voice components are implemented
  and tested with mocks; turning on real audio is a separate, deliberate step.

---

## 4. The actors

| Actor | Role | Speaks? | Owns state? |
|-------|------|---------|-------------|
| **`jarvisd`** | The daemon. Owns the DB, the event store, the state machine, the API. | — | **Yes — all of it.** |
| **Panel** | macOS menu-bar client (PyObjC + WKWebView). Renders state, sends intents. | No | No |
| **Brain adapter** | Stateless function: `BrainRequest → BrainResponse`. | No | No |
| **Voice broker** | The *only* component that plays audio. Drains the persisted voice queue. | **Yes — exclusively.** | No (reads/writes the DB queue) |
| **Worker** | Async background job (e.g. a Codex/Claude agent). Produces memory *candidates*. | **No** | No |
| **RuntimeSupervisor** | Observes launch mode and legacy conflicts. Never kills. | No | No (records observations) |

The old `dan` system had three "voices" (DAN-robot, DAN-głos, "thoughts hook")
each able to call a player independently. v4.1 collapses speaking authority into
the voice broker alone.

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
      │   (optional) ToolCall ──► ApprovalGate ──► tool run                 │
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

| Concern | Path |
|---------|------|
| Runtime root | `~/.jarvis` |
| Database (source of truth) | `~/.jarvis/jarvis.db` |
| Logs | `~/.jarvis/logs/jarvisd.log` |
| PID file | `~/.jarvis/runtime/jarvisd.pid` |
| Official launchd label | `com.ozzy.jarvisd` |

`/tmp` is **compatibility transport only** — a place to bridge to legacy
components if ever needed. It is never a source of truth.
([ADR-008](DECISIONS.md#adr-008))

---

## 7. Bounded contexts (where things live)

| Context | Package | Responsibility |
|---------|---------|----------------|
| Daemon | `jarvis/daemon` | Process lifecycle, state machine, app wiring |
| Store | `jarvis/store` | SQLite, migrations, repositories, event store |
| Events | `jarvis/events` | Event types, models, in-process bus |
| API | `jarvis/api` | HTTP + WebSocket routes |
| Runtime | `jarvis/runtime` | Launch-mode + legacy-conflict supervision |
| Turns | `jarvis/turns` | `TurnOrchestrator`, turn/conversation models |
| Brain | `jarvis/brain` | Stateless adapters, context builder, manager |
| Memory | `jarvis/memory` | Memory blocks, policies, context selection |
| Tools | `jarvis/tools` | Registry, permissions, approval gate, tools |
| Workers | `jarvis/workers` | Job broker, worker adapters, `worker_jobs` state and `worker.job.*` events |
| Audio | `jarvis/audio` | Device manager + policy |
| Voice | `jarvis/voice` | Listening leases, voice queue, TTS broker, STT/VAD/anti-echo |
| Panel | `jarvis/panel` | macOS menu-bar client (thin) |

These are the v4.1 contexts; the package skeleton is created in Prompt 01.

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
