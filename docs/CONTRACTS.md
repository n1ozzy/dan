# Jarvis v4.1 — Runtime Contracts (FROZEN)

> **Status:** FROZEN (Prompt 00A). These are the canonical data contracts of
> `jarvisd`. Names, owning modules, persistence requirements and forbidden
> behaviors below are binding. Field *types* and *additional* optional fields
> may be refined by later prompts, but nothing here may be contradicted without
> an [ADR](DECISIONS.md) update.

## How to read this document

Every contract is specified with the same six-part template:

- **Owner module** — the single package responsible for creating/mutating it.
- **Persistence** — which DB table backs it, or "derived / not persisted".
- **Required fields** — fields that must exist on every instance.
- **Allowed states / statuses** — the legal lifecycle values.
- **Emitted events** — events produced when it is created/changed.
- **Forbidden behavior** — things that must never happen to/with it.

### Event-name convention

Events whose names are **frozen** (they appear verbatim in the build sequence)
are marked **(frozen)**. Event families marked **(family)** are guaranteed to
exist but their exact final names are pinned in the implementation prompt that
owns them. The frozen events are:

`state.changed`, `input.text.received`, `input.voice.transcribed`,
`turn.started`, `turn.finished`, `brain.requested`, `brain.responded`,
`brain.failed`, `voice.speak.cancelled`, `memory.updated`.

### Persistence: the canonical tables

All persistence is SQLite in `~/.jarvis/jarvis.db` ([ADR-004](DECISIONS.md#adr-004)).
The frozen table set is:

`schema_version`, `events`, `conversations`, `turns`, `turn_steps`,
`memory_blocks`, `settings`, `voice_queue`, `listening_leases`,
`audio_device_snapshots`, `worker_jobs`, `tool_runs`, `approvals`,
`runtime_process_observations`.

Worker job state and history are intentionally separate:

- State: `worker_jobs`.
- History: `events`, using `worker.job.*` event types.
- There is no `job_events` table in v4.1. Do not add one unless a future ADR
  explicitly supersedes [ADR-015](DECISIONS.md#adr-015).

---

## 1. Turn

The unit of "one input → full response". The pipeline's spine.

- **Owner module:** `jarvis/turns` (`TurnOrchestrator` + turn repository).
- **Persistence:** `turns` (one row per turn); per-step detail in `turn_steps`.
  **Required** — a turn must survive a daemon/DB reload.
- **Required fields:**
  - `id` — stable identifier.
  - `conversation_id` — the owning `Conversation`.
  - `source` — `panel_text` | `voice_transcript` (extensible; both reuse the
    same orchestrator — [ADR-011](DECISIONS.md#adr-011)).
  - `input_text` — the user input that opened the turn.
  - `status` — see states below.
  - `created_at`, `updated_at`.
  - `response_text` — set when the brain responds (nullable until then).
  - `correlation_id` — ties together every event/step of this turn.
- **Allowed statuses:** `pending` → `running` → (`finished` | `failed` |
  `interrupted`).
- **Emitted events:** `turn.started` (frozen), `turn.finished` (frozen),
  and per-step records in `turn_steps`. A failed turn finishes with a failure
  payload; it still emits `turn.finished`.
- **Forbidden behavior:**
  - No component other than the orchestrator creates or mutates turns.
  - Brain adapters and workers never write turns.
  - A turn is never created without being persisted (no in-memory-only turns).

---

## 2. Event

The append-only record of everything that happens. The event store **is** the
source of truth for history ([ADR-004](DECISIONS.md#adr-004)).

- **Owner module:** `jarvis/events` (types/models/bus) + `jarvis/store/event_store.py`.
- **Persistence:** `events`. **Append-only** — rows are never updated or deleted.
- **Required fields:**
  - `id` — monotonic, used by `list_after(after_id, …)`.
  - `type` — dotted event name (e.g. `turn.started`).
  - `source` — the module/actor that appended it.
  - `payload` — JSON, **secret-redacted by policy helper before write**.
  - `correlation_id` — nullable; groups related events.
  - `turn_id` — nullable; links the event to a turn.
  - `ts` — creation timestamp.
- **Allowed states:** none — an event is immutable once appended.
- **Emitted events:** n/a (it *is* the event). The store API is
  `append(type, source, payload, correlation_id=None, turn_id=None) → Event`,
  `list_after(after_id, limit=100)`, `latest(limit=100)`,
  `subscribe(callback) → unsubscribe`.
- **Forbidden behavior:**
  - Never mutate or delete an event.
  - Never persist unredacted secrets in `payload` ([ADR-010](DECISIONS.md#adr-010)).
  - Never treat a `/tmp` file as an event source of truth
    ([ADR-008](DECISIONS.md#adr-008)).

---

## 3. Conversation

A durable, cross-session grouping of turns.

- **Owner module:** `jarvis/turns` (conversation repository).
- **Persistence:** `conversations`. **Required** — history persists across
  restarts and panel reopen.
- **Required fields:**
  - `id`.
  - `title` — nullable / derivable.
  - `status` — see below.
  - `created_at`, `updated_at`.
- **Allowed statuses:** `active` | `archived`.
- **Emitted events:** conversation lifecycle **(family)** — e.g.
  `conversation.created` / `conversation.updated`.
- **Forbidden behavior:**
  - The panel never owns or caches conversation state as canonical
    ([ADR-002](DECISIONS.md#adr-002)).
  - A provider session is never treated as the conversation
    ([ADR-003](DECISIONS.md#adr-003)).

---

## 4. BrainRequest

The fully-formed input handed to a stateless brain adapter. Jarvis — not the
provider — assembles all context.

- **Owner module:** `jarvis/brain` (`context_builder` assembles it,
  `manager` dispatches it).
- **Persistence:** **Derived / not persisted as its own row.** It is built from
  DB + config on demand; its occurrence is recorded via the `brain.requested`
  event and `turn_steps`. Building it from the same DB state must be
  deterministic.
- **Required fields:**
  - `conversation_id`, `turn_id`, `correlation_id`.
  - `system_prompt` / `persona` — from config (`config/persona`).
  - `messages` — the conversation context selected from the DB.
  - `memory` — **active** `MemoryBlock`s only, within a max character budget.
  - `settings` — at least the selected brain `model` and `effort`.
- **Allowed states:** none — it is a value object passed by value.
- **Emitted events:** `brain.requested` (frozen).
- **Forbidden behavior:**
  - The adapter must not mutate or persist it as session state
    ([ADR-003](DECISIONS.md#adr-003)).
  - Disabled memory must never be included
    ([ADR-009](DECISIONS.md#adr-009)).
  - Context must come from the DB/config, never from a provider's hidden
    server-side history.

---

## 5. BrainResponse

The result returned by a brain adapter.

- **Owner module:** `jarvis/brain` (adapters produce it; manager normalizes it).
- **Persistence:** **Derived / not persisted as its own row.** Its content lands
  on the `Turn` (`response_text`) and in `events` (`brain.responded`) /
  `turn_steps`.
- **Required fields:**
  - `text` — the response body (may stream in deltas; final text is canonical).
  - `model` — which model produced it.
  - `finish_reason` — normal completion vs. truncation/error.
  - `error` — nullable; populated on failure (see error kinds below).
  - timing/usage — optional but recommended.
- **Allowed statuses:** `ok` | `failed`. Failure kinds include
  `adapter_unavailable` (CLI missing) and `BrainAdapterError` (subprocess error
  / timeout).
- **Emitted events:** `brain.responded` (frozen) on success,
  `brain.failed` (frozen) on failure.
- **Forbidden behavior:**
  - The adapter never speaks (no audio) and never writes memory facts
    ([ADR-005](DECISIONS.md#adr-005), [ADR-009](DECISIONS.md#adr-009)).
  - The adapter never invokes the panel.

---

## 6. MemoryBlock

A unit of Jarvis-owned long-term context. Memory is the daemon's, not the
provider's.

- **Owner module:** `jarvis/memory` (`manager` + `policies`).
- **Persistence:** `memory_blocks`. **Required.**
- **Required fields:**
  - `id`.
  - `kind` — category of memory (e.g. fact, preference, persona note).
  - `content` — the text.
  - `enabled` — boolean; only enabled blocks enter context.
  - `priority` / `order` — for budgeted selection.
  - `source` — who proposed it (human, worker candidate, system).
  - `created_at`, `updated_at`.
- **Allowed states:** `enabled` | `disabled`. Candidate-vs-committed is tracked
  via `source` / a pending flag (a worker result is a *candidate* until a human
  or policy promotes it).
- **Emitted events:** `memory.updated` (frozen).
- **Forbidden behavior:**
  - Workers never write committed memory facts directly — they only produce
    candidates ([ADR-009](DECISIONS.md#adr-009)).
  - Disabled blocks are never injected into a `BrainRequest`.

---

## 7. VoiceRequest

A request to *say something*. Enqueued in the DB; played only by the broker.

- **Owner module:** `jarvis/voice` (`queue` + `broker`).
- **Persistence:** `voice_queue`. **Required** — queued items recover after a
  restart.
- **Required fields:**
  - `id`.
  - `text` — what to speak.
  - `priority` — ordering within the queue.
  - `status` — see below.
  - `turn_id` / `correlation_id` — nullable; provenance.
  - `engine` / `voice` — optional; selection metadata.
  - `created_at`.
- **Allowed statuses:** `queued` → `speaking` → (`done` | `cancelled` |
  `failed`).
- **Emitted events:** voice playback lifecycle **(family)** —
  e.g. `voice.speak.queued` / `voice.speak.started` / `voice.speak.finished` —
  plus `voice.speak.cancelled` (frozen) on interrupt/barge-in.
- **Forbidden behavior:**
  - Only the broker plays speech — nothing else calls a player
    ([ADR-005](DECISIONS.md#adr-005)).
  - Workers never enqueue-and-speak on their own authority
    ([ADR-009](DECISIONS.md#adr-009)).
  - No duplicate playback of the same request.

---

## 8. ListeningLease

The push-to-talk / listening contract. A lease in the DB — **never** a raw file
flag ([ADR-006](DECISIONS.md#adr-006)).

- **Owner module:** `jarvis/voice/listening.py`.
- **Persistence:** `listening_leases`. **Required.**
- **Required fields:**
  - `id`.
  - `mode` — `hold` (button held) | `locked` (sticky).
  - `source` — `ptt` | `lock`.
  - `created_at`.
  - `expires_at` — for stale-lease expiry.
  - `released_at` — nullable; set on release.
- **Allowed states:** `active` (sub-modes `hold` / `locked`) → (`expired` |
  `released`).
- **Emitted events:** listening lifecycle **(family)** — e.g.
  `voice.listening.started` / `voice.listening.stopped`. Releasing a `hold`
  lease must promptly request the (mock) recorder to stop.
- **Forbidden behavior:**
  - No raw `/tmp` flag is the source of truth for "is listening"
    ([ADR-006](DECISIONS.md#adr-006), [ADR-008](DECISIONS.md#adr-008)).
  - A button release (`hold`) must not clear a `locked` lease.
  - A stale lease must expire rather than listen forever.

---

## 9. AudioDeviceState

The owned view of input/output audio devices.

- **Owner module:** `jarvis/audio` (`AudioDeviceManager`).
- **Persistence:** `audio_device_snapshots` (point-in-time snapshots).
- **Required fields:**
  - `input_device` — selected input.
  - `output_device` — selected output.
  - `preferred_input` — policy preference (default: `Mikrofon (MacBook Air)`).
  - `warnings` — e.g. bluetooth-mic warning.
  - `ts`.
- **Allowed states:** describes device selection; "current" is the latest
  snapshot. Policy: output follows the system default; a bluetooth microphone
  warns or is disabled by default.
- **Emitted events:** device-change **(family)** — e.g. `audio.device.changed`.
- **Forbidden behavior:**
  - Voice/STT components never pick devices directly — the
    `AudioDeviceManager` owns device state ([ADR-012](DECISIONS.md#adr-012)).

---

## 10. ToolCall

A request to run a registered tool, gated by permission policy.

- **Owner module:** `jarvis/tools` (`registry` + `permissions`; executed via the
  approval gate).
- **Persistence:** `tool_runs` (an approved/attempted call records a run).
- **Required fields:**
  - `id`.
  - `tool` — registered tool name.
  - `args` — **secret-redacted in any event payload**.
  - `permission` — the permission class required (see
    [SECURITY_MODEL.md](SECURITY_MODEL.md)).
  - `approval_id` — nullable; set when an `Approval` is required.
  - `status` — see below.
  - `result` — **redacted**; nullable until run.
  - `turn_id` / `correlation_id`.
  - `created_at`.
- **Allowed statuses:** `proposed` → (`approved` | `rejected` | `blocked`) →
  (`running` → (`succeeded` | `failed`)). `blocked` = forbidden by policy
  (e.g. destructive without explicit enable).
- **Emitted events:** tool lifecycle **(family)** — e.g. `tool.proposed` /
  `tool.run.started` / `tool.run.finished` / `tool.rejected`.
- **Forbidden behavior:**
  - A rejected or blocked call **never executes** ([ADR-010](DECISIONS.md#adr-010)).
  - Destructive operations never run unless explicitly enabled.
  - Secrets never appear unredacted in event payloads.

---

## 11. Approval

A human (or policy) decision authorizing a gated action.

- **Owner module:** `jarvis/tools` (`ApprovalGate`) + `jarvis/api/routes_approvals.py`.
- **Persistence:** `approvals`. **Required.**
- **Required fields:**
  - `id`.
  - `subject` — what is being approved (e.g. a `ToolCall` reference).
  - `status` — see below.
  - `requested_by`, `decided_by` — provenance.
  - `created_at`, `decided_at`.
  - `reason` — nullable; rationale for the decision.
- **Allowed statuses:** `pending` → (`approved` | `rejected` | `expired`).
  While a turn waits on one, the daemon is in `WAITING_APPROVAL`.
- **Emitted events:** approval lifecycle **(family)** — e.g.
  `approval.requested` / `approval.granted` / `approval.rejected`.
- **Forbidden behavior:**
  - The gated action never runs before `approved`.
  - Destructive actions are never auto-approved.

---

## 12. WorkerJob

An async background job (e.g. a Codex/Claude worker). Workers advise; they do
not act on the world.

- **Owner module:** `jarvis/workers` (`broker` + worker adapters).
- **Persistence:** `worker_jobs` stores current job state. Worker lifecycle
  history is recorded in the general append-only `events` table via
  `worker.job.*` event types. There is no `job_events` table. **Required.**
- **Required fields:**
  - `id`.
  - `type`.
  - `worker_kind` — e.g. `codex` | `claude` (extensible).
  - `prompt` — what to do.
  - `status` — see below.
  - `requested_by`.
  - `result_summary` — nullable; may point toward a **memory candidate**, not a fact.
  - `artifact_refs_json`.
  - `created_at`, `started_at`, `finished_at`.
  - `metadata_json`.
- **Allowed statuses:** `queued` → `running` → (`succeeded` | `failed` |
  `cancelled`).
- **Emitted events:** worker job lifecycle **(family)**, visible in the single
  EventStore stream — e.g. `worker.job.created`, `worker.job.started`,
  `worker.job.progress`, `worker.job.finished`, `worker.job.failed`,
  `worker.job.cancelled` (recorded in `events`).
- **Forbidden behavior:**
  - A worker **never speaks** (no `VoiceRequest` on its own authority)
    ([ADR-005](DECISIONS.md#adr-005)).
  - A worker **never writes a memory fact directly** — its result is a
    candidate ([ADR-009](DECISIONS.md#adr-009)).
  - Do not add a parallel job-history table; future job history requirements
    extend EventStore unless a later ADR supersedes ADR-015.

---

## 13. RuntimeProcessObservation

What the supervisor sees about how Jarvis was launched and which legacy
processes/labels exist. Observation only — never an action.

- **Owner module:** `jarvis/runtime/supervisor.py`.
- **Persistence:** `runtime_process_observations` (snapshots).
- **Required fields:**
  - `id`.
  - `launch_mode` — `cli` | `launchd` | `unknown`.
  - `official_label_present` — whether `com.ozzy.jarvisd` is loaded.
  - `legacy_labels` — detected old labels (`com.ozzy.jarvis`,
    `com.dan.voice-broker`, `com.dan.xtts-server`).
  - `legacy_processes` — detected old processes (`auto_jarvis.py`,
    `listen_ozzy.py`, `voice_broker.py`, `xtts_server.py`, `dan_panel_web.py`).
  - `warnings` — human-readable conflict notes.
  - `ts`.
- **Allowed states:** describes an observation; "current" is the latest
  snapshot, surfaced in `/state` and `/runtime/processes`.
- **Emitted events:** runtime observation **(family)** — e.g. `runtime.observed`.
- **Forbidden behavior:**
  - **Never kill a process automatically** ([ADR-007](DECISIONS.md#adr-007)).
  - An observation is never treated as authority to act — it only warns the
    human.

---

## Cross-cutting invariants

1. **Truth lives in `jarvisd`'s DB.** Every contract above is created, owned and
   mutated by exactly one module, and (unless explicitly "derived") persisted in
   exactly one table.
2. **`/tmp` is never a source of truth** — only a compatibility transport.
3. **Secrets are redacted before any event write.**
4. **Speaking is exclusive to the voice broker; workers and adapters are mute.**
5. **The same `TurnOrchestrator` serves panel text and voice transcripts.**
