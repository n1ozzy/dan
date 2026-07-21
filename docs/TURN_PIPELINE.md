# DAN — Turn Pipeline & State Machine

> **Status:** describes the running pipeline; re-verified against
> `dan/turns/orchestrator.py` and `dan/daemon/state_machine.py` on 2026-07-21.
> Defines the runtime state set, the turn lifecycle, and the event sequence.
> Field shapes are in [CONTRACTS.md](CONTRACTS.md).

> **Tools execute inside the turn — there is no approval step.** The
> orchestrator runs the model's tool calls immediately and feeds the results
> back to the model in the same turn (§3, §5.1). `awaiting_approval` is a legacy
> status the pipeline never sets.

---

## 1. One orchestrator for every input

Typed panel text and a transcribed voice utterance are **the same kind of
input** and go through **the same `TurnOrchestrator`** ([ADR-011](DECISIONS.md#adr-011)).
There is no separate "voice brain" path. The only difference is the turn's
`source` field: `text` | `panel` | `cli` | `api` for typed input (`POST
/input/text` accepts exactly that set), `voice` for an accepted transcript.

```
POST /input/text  ──►┐
                     ├──► TurnOrchestrator ──► … ──► (optional) VoiceRequest
voice transcript  ──►┘        (dan/turns)
```

This is the single most important behavioral guarantee of the runtime: it is
what makes panel and voice consistent, debuggable from one event stream, and
impossible to drift apart.

---

## 2. Runtime states (FROZEN)

The daemon state machine (`dan/daemon/state_machine.py`) has exactly these
states:

| State | Meaning |
|-------|---------|
| `BOOTING` | Process starting, wiring dependencies. |
| `IDLE` | Ready, nothing in flight. |
| `LISTENING` | A `ListeningLease` is active; capturing audio. |
| `TRANSCRIBING` | Captured audio is being turned into text. |
| `THINKING` | A turn is open; building context / awaiting the brain/model. |
| `TOOLING` | A round of tool execution inside a turn. |
| `SPEAKING` | The voice broker is playing a `VoiceRequest`. |
| `INTERRUPTED` | Barge-in / cancellation interrupted the current activity. |
| `ERROR` | An unrecoverable error for the current activity. |
| `STOPPING` | Graceful shutdown in progress. |

The canonical persisted `RuntimeState` values are exactly:
`BOOTING`, `IDLE`, `LISTENING`, `TRANSCRIBING`, `THINKING`, `TOOLING`,
`SPEAKING`, `INTERRUPTED`, `ERROR`, `STOPPING`.

`WAITING_APPROVAL` and `WORKING` are not runtime states. Tool work inside a turn
is `TOOLING`; worker job lifecycle is represented by `worker_jobs` state plus
`worker.job.*` events, not runtime state expansion.

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
> `status` (`received`/`started`/`context_built`/`brain_requested`/
> `brain_responded`/`finished`/`failed`/`cancelled`, plus the legacy
> `awaiting_approval`) — the two are related but distinct.

Recovery rules worth knowing: a failed turn goes `… → ERROR → IDLE`, and if
either transition cannot be persisted the machine falls back to
`force_idle()` so the runtime is never stranded outside `IDLE`. `STOPPING` is
never resurrected.

---

## 3. The text turn

`POST /input/text` drives this sequence (`TurnOrchestrator.handle_text`):

```
[runtime must be IDLE, else 409 busy]
      ▼
[Turn persisted: status=received, source=text|panel|cli|api]
      ▼
input.text.received
      ▼
turn.started            (Turn: status=started)
      ▼
state.changed → THINKING
      ▼
[build context]         (context_builder: DB + config + ACTIVE memory only)
      ▼
turn.context.built      (context snapshot attached to the Turn)
      ▼
brain.requested         (BrainRequest assembled by DAN)
      ▼
brain.responded   OR   brain.failed / brain.cancelled
      ▼
[while the response carries tool calls: the §5.1 loop]
      ▼
[persist final_text on Turn: status=finished | failed | cancelled]
      ▼
turn.finished
      ▼
state.changed → IDLE
```

### Guarantees

- **One input → exactly one turn.**
- **A turn only starts from `IDLE`** — a second concurrent turn is refused with
  `TurnOrchestratorBusyError` (HTTP 409), it does not queue.
- **The turn survives a DB reload** (it is persisted before the brain is called).
- **The response appears in the event stream** (`brain.responded` + the turn's
  `final_text`).
- **The mock brain is available for tests** — no real provider is required to
  exercise the pipeline.
- **Tool calls execute inside this turn** — the model's calls run immediately,
  their results are fed back as a continuation `BrainRequest`, and the LAST
  model response is the turn's final answer. No approval row is created and the
  runtime never parks the turn (§5.1).
- **A cancelled generation is not a failure** — barge-in raises
  `BrainGenerationCancelled`, which ends the turn as `cancelled` (events
  `brain.cancelled` + `turn.cancelled`) and settles the runtime back to `IDLE`
  without passing through `ERROR`.
- **Post-completion errors never reclassify a finished turn** — once the turn
  reaches `finished`/`failed`/`cancelled`, a later failed event append or state
  transition cannot rewrite it (FIX-05).

---

## 4. The voice turn

A transcribed utterance becomes input to the *same* pipeline:

```
LISTENING            (ListeningLease active)
      ▼
TRANSCRIBING
      ▼
input.voice.transcribed
      ▼   (accepted transcript, post anti-echo / garbage filter)
turn.started          (source=voice)
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

## 5. Branches: tools, workers, barge-in

These extend a turn without changing its identity. Turn state lives in `turns`;
turn lifecycle history is represented by `turn.*` events in the single
EventStore stream. There is no `turn_steps` table unless a future ADR
supersedes ADR-016.

### 5.1 Tool call — direct in-turn execution

```
THINKING → (the model's response carries tool calls)
        → [speak this response as commentary, not as the final answer]
        → state.changed → TOOLING
        → per call: tool.requested → tool.started → tool.finished | tool.failed
        → state.changed → THINKING
        → brain.requested → brain.responded   (continuation carrying the results)
        → if that response carries tool calls again: repeat
        → turn.finished with the LAST response as final_text
```

Bounds that make the loop terminate (`dan/turns/orchestrator.py`,
`dan/turns/loop_guard.py`):

- `MAX_DIRECT_TOOL_ROUNDS = 8`. Exceeding it fails the turn.
- A `ToolLoopGuard` watches repeated (tool, arguments) pairs: it first warns —
  and the warning is carried into the continuation prompt so the model can see
  it is repeating itself — then cuts the batch, which fails the turn.
- Tool results are redacted and budget-clipped before they re-enter the prompt,
  and the continuation prompt states that tool output is untrusted data, never
  instructions.
- An unregistered tool, non-JSON arguments or a missing registry is recorded as
  a failed call (`tool.failed` + `error.raised`) and the turn continues.

The legacy approve/execute path (`continue_after_tool_result`) still exists for
a turn parked in `awaiting_approval`; only continuation-eligible one-shot
results qualify there. Result classes `requires_user_presence`,
`external_communication_pending`, `operator_session_started`,
`live_visual_control_session` and `worker_job_started` are reserved names, not
implemented behaviour.

### 5.2 Worker job

```
THINKING → (turn dispatches a WorkerJob)
        → worker_jobs row + worker.job.created → worker.job.started
        → worker.job.finished | worker.job.failed | worker.job.cancelled
        → worker result becomes a MEMORY CANDIDATE (never a fact, never speech)
        → back to THINKING / turn.finished
```

### 5.3 Barge-in / interrupt

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
| `turn.context.built` | | the context snapshot is attached |
| `brain.requested` | ✅ | a `BrainRequest` is dispatched |
| `brain.responded` | ✅ | a `BrainResponse` returns ok |
| `brain.failed` | ✅ | brain adapter fails / unavailable |
| `brain.cancelled` | | generation cancelled by barge-in |
| `turn.finished` | ✅ | a turn closes successfully |
| `turn.failed` / `turn.cancelled` | | the other two terminal outcomes |
| `voice.speak.cancelled` | ✅ | a `VoiceRequest` is cancelled (barge-in) |
| `memory.updated` | ✅ | a `MemoryBlock` changes |
| `tool.*` | family | `tool.requested` / `started` / `finished` / `failed` / `rejected` |
| `approval.*` | family | legacy approve/execute path only (§5.1) |
| `worker.job.*` | family | worker job lifecycle in the general `events` table |
| `voice.speak.*` | family | voice queue/playback lifecycle |
| `listening.lease.*` | family | listening lease lifecycle |
| `audio.devices.snapshot` | | device snapshot recorded (only on change) |
| `runtime.process.observed` | family | supervisor observation |
| `error.raised` | | any recorded error |

Canonical names live in `dan/events/types.py::EventType`; this table is a map,
not the authority.

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
