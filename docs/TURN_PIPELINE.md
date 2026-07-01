# Jarvis v4.1 — Turn Pipeline & State Machine (FROZEN)

> **Status:** FROZEN (Prompt 00A). Defines the runtime state set, the canonical
> turn lifecycle, and the event sequence. Field shapes are in
> [CONTRACTS.md](CONTRACTS.md).

---

## 1. One orchestrator for every input

Typed panel text and a transcribed voice utterance are **the same kind of
input** and go through **the same `TurnOrchestrator`** ([ADR-011](DECISIONS.md#adr-011)).
There is no separate "voice brain" path. The only difference is the turn's
`source` field (`panel_text` vs `voice_transcript`).

```
POST /input/text  ──►┐
                     ├──► TurnOrchestrator ──► … ──► (optional) VoiceRequest
voice transcript  ──►┘        (jarvis/turns)
```

This is the single most important behavioral guarantee of v4.1: it is what makes
panel and voice consistent, debuggable from one event stream, and impossible to
drift apart.

---

## 2. Runtime states (FROZEN)

The daemon state machine (`jarvis/daemon/state_machine.py`) has exactly these
states:

| State | Meaning |
|-------|---------|
| `BOOTING` | Process starting, wiring dependencies. |
| `IDLE` | Ready, nothing in flight. |
| `LISTENING` | A `ListeningLease` is active; capturing audio. |
| `TRANSCRIBING` | Captured audio is being turned into text. |
| `THINKING` | A turn is open; building context / awaiting the brain/model. |
| `TOOLING` | Tool and approval execution periods inside a turn. |
| `SPEAKING` | The voice broker is playing a `VoiceRequest`. |
| `INTERRUPTED` | Barge-in / cancellation interrupted the current activity. |
| `ERROR` | An unrecoverable error for the current activity. |
| `STOPPING` | Graceful shutdown in progress. |

The canonical persisted `RuntimeState` values are exactly:
`BOOTING`, `IDLE`, `LISTENING`, `TRANSCRIBING`, `THINKING`, `TOOLING`,
`SPEAKING`, `INTERRUPTED`, `ERROR`, `STOPPING`.

`WAITING_APPROVAL` and `WORKING` are not v4.1 runtime states. Approval waiting
is represented by `approvals` / `approval.*` / tool events and, when the
runtime is actively waiting as part of a turn, `TOOLING`. Worker job lifecycle
is represented by `worker_jobs` state plus `worker.job.*` events, not runtime
state expansion.

Allowed normal transitions:

- `BOOTING` → `IDLE`
- `IDLE` → `LISTENING`
- `LISTENING` → `TRANSCRIBING`
- `TRANSCRIBING` → `THINKING`
- `IDLE` → `THINKING`
- `THINKING` → `TOOLING`
- `TOOLING` → `THINKING`
- `THINKING` → `SPEAKING`
- `THINKING` → `IDLE`
- `SPEAKING` → `IDLE`
- `SPEAKING` → `INTERRUPTED`
- `INTERRUPTED` → `LISTENING`
- `INTERRUPTED` → `THINKING`
- `ERROR` → `IDLE`

Global transitions: any non-`STOPPING` state may transition to `ERROR` or
`STOPPING`. `STOPPING` is terminal. Same-state transitions are invalid.

**Every transition emits `state.changed`** (frozen) with the previous and next
state in the payload. The state machine is the only writer of the current state.

> Note: states are runtime *activity* status. A `Turn` also has its own
> `status` (`pending`/`running`/`finished`/`failed`/`interrupted`) — the two are
> related but distinct (the daemon can be `SPEAKING` while a turn is already
> `finished`, for example).

---

## 3. The text turn (MVP — Prompt 10)

`POST /input/text` drives this exact sequence:

```
input.text.received
      ▼
turn.started            (Turn persisted: status=running, source=panel_text)
      ▼
state.changed → THINKING
      ▼
[build context]         (context_builder: DB + config + ACTIVE memory only)
      ▼
brain.requested         (BrainRequest assembled by Jarvis)
      ▼
brain.responded   OR   brain.failed
      ▼
[persist response on Turn]
      ▼
turn.finished           (Turn: status=finished | failed)
      ▼
state.changed → IDLE
```

### Guarantees

- **One input → exactly one turn.**
- **The turn survives a DB reload** (it is persisted before the brain is called).
- **The response appears in the event stream** (`brain.responded` + the turn's
  `response_text`).
- **The mock brain is the default in tests** — no real provider is required to
  exercise the pipeline.

---

## 4. The voice turn (Prompt 17)

A transcribed utterance becomes input to the *same* pipeline:

```
LISTENING            (ListeningLease active)
      ▼
TRANSCRIBING
      ▼
input.voice.transcribed
      ▼   (accepted transcript, post anti-echo / garbage filter)
turn.started          (source=voice_transcript)
      ▼
   … identical to §3 from here …
      ▼
turn.finished
      ▼
(optional) VoiceRequest → voice queue → SPEAKING → IDLE
```

A transcript that is filtered (echo of recent TTS, or short garbage
acknowledgement) **does not** open a turn — it is dropped by policy before
`turn.started`. See [AUDIO_RUNTIME.md](AUDIO_RUNTIME.md).

---

## 5. Branches: tools, approvals, workers

These extend a turn without changing its identity. Turn state lives in `turns`;
turn lifecycle history is represented by `turn.*` events in the single
EventStore stream. There is no `turn_steps` table in v4.1 unless a future ADR
supersedes ADR-016.

### 5.1 Tool call (Prompt 12)

```
THINKING → (brain proposes ToolCall)
        → tool.proposed
        → if permission requires approval:
              TOOLING + approval.requested
              → approval.granted | approval.rejected
        → if approved:  TOOLING  →  tool.run.started → tool.run.finished
        → if rejected/blocked: the call NEVER executes
        → back to THINKING / turn.finished
```

### 5.2 Worker job (Prompt 13)

```
THINKING → (turn dispatches a WorkerJob)
        → worker_jobs row + worker.job.created → worker.job.started
        → worker.job.finished | worker.job.failed | worker.job.cancelled
        → worker result becomes a MEMORY CANDIDATE (never a fact, never speech)
        → back to THINKING / turn.finished
```

### 5.3 Barge-in / interrupt (Prompt 17)

```
SPEAKING → (real barge-in detected, under policy)
        → INTERRUPTED  +  voice.speak.cancelled
        → current VoiceRequest: status=cancelled
        → resolve to IDLE or a new turn
```

---

## 6. Event catalogue used by the pipeline

| Event | Frozen? | When |
|-------|:-------:|------|
| `state.changed` | ✅ | every state transition |
| `input.text.received` | ✅ | `POST /input/text` accepted |
| `input.voice.transcribed` | ✅ | a transcript is accepted as input |
| `turn.started` | ✅ | a turn is opened + persisted |
| `brain.requested` | ✅ | a `BrainRequest` is dispatched |
| `brain.responded` | ✅ | a `BrainResponse` returns ok |
| `brain.failed` | ✅ | brain adapter fails / unavailable |
| `turn.finished` | ✅ | a turn closes (finished or failed) |
| `voice.speak.cancelled` | ✅ | a `VoiceRequest` is cancelled (barge-in) |
| `memory.updated` | ✅ | a `MemoryBlock` changes |
| `tool.*` | family | tool proposed/run lifecycle |
| `approval.*` | family | approval requested/decided |
| `worker.job.*` | family | worker job lifecycle in the general `events` table |
| `voice.speak.*` | family | voice queue/playback lifecycle |
| `voice.listening.*` | family | listening lease lifecycle |
| `audio.device.changed` | family | device selection change |
| `runtime.observed` | family | supervisor snapshot |
| `conversation.*` | family | conversation lifecycle |

Correlation: every event belonging to a turn carries the turn's
`correlation_id` (and `turn_id` where applicable), so the full lifecycle of any
input can be reconstructed from the event store alone.

---

## 7. Determinism & replay invariants

- Given the same DB state, `context_builder` produces the **same**
  `BrainRequest` (modulo the brain's own nondeterminism).
- The event store is append-only; the lifecycle of any turn is fully
  reconstructable by filtering events on `correlation_id`.
- No pipeline step depends on a `/tmp` file for truth
  ([ADR-008](DECISIONS.md#adr-008)).
