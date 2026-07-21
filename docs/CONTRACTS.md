# DAN — Runtime Contracts

> **Status:** binding data contracts of the `dand` daemon, re-verified against
> the source on 2026-07-21. Names, owning modules, persistence requirements and
> forbidden behaviors below are binding. Nothing here may be contradicted
> without an [ADR](DECISIONS.md) update.

> **No approval gate in the tool path (verified 2026-07-21).**
> `ToolPermissionPolicy.decide()` returns ALLOW unconditionally and
> `ToolRegistry.request_tool()` ignores its `permission_policy` / `source` /
> `approval_gate` arguments (`dan/tools/permissions.py`,
> `dan/tools/registry.py`). `DaemonApp.request_tool()` does the same — it
> `del source` and executes. Tool calls, from the model and from the API, run
> immediately. `approvals` rows, the `approval.*` events and the
> `awaiting_approval` turn status still exist as compatibility surfaces reachable
> only through the explicit approve/execute routes; they are NOT the normal path
> and must not be read as a gate. What actually refuses work lives inside the
> individual tools (approved roots, the `shell_read` allowlist, scrubbed env, git
> hardening, output bounds) — see [SECURITY_MODEL.md](SECURITY_MODEL.md).

## How to read this document

Every contract is specified with the same six-part template:

- **Owner module** — the single package responsible for creating/mutating it.
- **Persistence** — which DB table backs it, or "derived / not persisted".
- **Required fields** — fields that must exist on every instance.
- **Allowed states / statuses** — the legal lifecycle values.
- **Emitted events** — events produced when it is created/changed.
- **Forbidden behavior** — things that must never happen to/with it.

### Event-name convention

Every event name is defined once, in `dan/events/types.py::EventType` — that
enum is the authority, not this document. Events marked **(frozen)** below may
not be renamed; families marked **(family)** group several enum members with a
shared prefix. The frozen events are:

`state.changed`, `input.text.received`, `input.voice.transcribed`,
`turn.started`, `turn.finished`, `brain.requested`, `brain.responded`,
`brain.failed`, `voice.speak.cancelled`, `memory.updated`.

### Persistence: the canonical tables

All persistence is SQLite in `~/.dan/dan.db` ([ADR-004](DECISIONS.md#adr-004);
path from `[database].path`, `dan/config.py`). The table set is defined by
`dan/store/schema.sql`:

`schema_version`, `intake_gate`, `intake_leases`, `events`, `conversations`,
`turns`, `memory_blocks`, `memory_archive_documents`,
`memory_archive_sync_state`, `memory_archive_fts`, `memory_observations`,
`memory_candidates`, `memory_items`, `memory_evidence`, `memory_topics`,
`memory_usage_events`, `memory_review_decisions`, `settings`, `worker_jobs`,
`approvals`, `tool_runs`, `voice_queue`, `cancelled_turns`, `listening_leases`,
`audio_device_snapshots`, `runtime_process_observations`.

There is no `turn_steps` table. Turn state lives in `turns`; turn
lifecycle history is represented by `turn.*` events in `events`. Do not add a
separate turn timeline table unless a future ADR explicitly supersedes
[ADR-016](DECISIONS.md#adr-016).

Worker job state and history are intentionally separate:

- State: `worker_jobs`.
- History: `events`, using `worker.job.*` event types.
- There is no `job_events` table. Do not add one unless a future ADR
  explicitly supersedes [ADR-015](DECISIONS.md#adr-015).

---

## 1. Turn

The unit of "one input → full response". The pipeline's spine.

- **Owner module:** `dan/turns` (`TurnOrchestrator` + turn repository).
- **Persistence:** `turns` stores current turn state. Turn lifecycle history is
  recorded in the general append-only `events` table via `turn.*` event types.
  There is no `turn_steps` table. **Required** — a turn must survive a
  daemon/DB reload.
- **Required fields** (`dan/turns/models.py`, `turns` table):
  - `id` — stable identifier. It is also the turn's `correlation_id`: the
    orchestrator sets `correlation_id = turn.id` for every event it appends.
  - `conversation_id` — the owning `Conversation`.
  - `source` — `text` | `voice` | `panel` | `cli` | `api` (`TurnSource`); every
    value reuses the same orchestrator ([ADR-011](DECISIONS.md#adr-011)).
  - `input_text` — the user input that opened the turn.
  - `status` — see states below.
  - `created_at`, `updated_at`.
  - `final_text` — the answer, set when the turn finishes (nullable until then).
  - `brain_adapter`, `brain_model`, `context_snapshot`, `error`, `metadata`.
- **Allowed statuses:** `received` → `started` → `context_built` →
  `brain_requested` → `brain_responded` → (`finished` | `failed` |
  `cancelled`).
  `awaiting_approval` is a legacy status that the normal pipeline never sets:
  model tool calls execute inside the turn and it finishes. Only the legacy
  approve/execute path can still consume a turn left in that status (§12).
- **Emitted events:** `turn.started` (frozen), `turn.finished` (frozen), and
  related `turn.*` lifecycle events in `events`.
- **Forbidden behavior:**
  - No component other than the orchestrator creates or mutates turns.
  - Brain adapters and workers never write turns.
  - A turn is never created without being persisted (no in-memory-only turns).

---

## 2. Event

The append-only record of everything that happens. The event store **is** the
source of truth for history ([ADR-004](DECISIONS.md#adr-004)).

- **Owner module:** `dan/events` (types/models/bus) + `dan/store/event_store.py`.
- **Persistence:** `events`. **Append-only** — rows are never updated or deleted.
- **Required fields** (`dan/events/models.py`):
  - `id` — monotonic, used by `list_after(after_id, …)`.
  - `created_at` — creation timestamp.
  - `type` — dotted event name (e.g. `turn.started`).
  - `source` — the module/actor that appended it.
  - `payload` — JSON, **secret-redacted before write**.
  - `correlation_id` — nullable; groups related events.
  - `turn_id` — nullable; links the event to a turn.
- **Allowed states:** none — an event is immutable once appended.
- **Emitted events:** n/a (it *is* the event). The store API is
  `append(type, source, payload, correlation_id=None, turn_id=None) → Event`,
  `list_after(after_id, limit=100)`, `latest(limit=100)`,
  `subscribe(callback) → unsubscribe`.
- **Client projection:** `GET /events` and the websocket stream both ship
  payloads through `safe_event_payload_for_client`
  (`dan/api/event_safety.py`): high-risk keys are replaced with the redaction
  placeholder and any payload carrying an `output` key gets
  `output_omitted: true`. The durable row keeps the full (redacted) payload.
- **Forbidden behavior:**
  - Never mutate or delete an event.
  - Never persist unredacted secrets in `payload`
    ([SECURITY_MODEL.md](SECURITY_MODEL.md); `dan/security/redaction.py`).
  - Never treat a `/tmp` file as an event source of truth
    ([ADR-008](DECISIONS.md#adr-008)).

---

## 3. Conversation

A durable, cross-session grouping of turns.

- **Owner module:** `dan/turns` (conversation repository).
- **Persistence:** `conversations`. **Required** — history persists across
  restarts and panel reopen.
- **Required fields:**
  - `id`.
  - `title` — nullable / derivable.
  - `status` — see below.
  - `created_at`, `updated_at`.
- **Allowed statuses:** `active` | `archived`.
- **Emitted events:** none of its own. `EventType` has no `conversation.*`
  member; conversation activity is visible through the `turn.*` events that
  carry the turn's ids.
- **Forbidden behavior:**
  - The panel never owns or caches conversation state as canonical
    ([ADR-002](DECISIONS.md#adr-002)).
  - A provider session is never treated as the conversation
    ([ADR-003](DECISIONS.md#adr-003)).

---

## 4. BrainRequest

The fully-formed input handed to the brain adapter. DAN — not the provider —
assembles all authoritative context.

- **Owner module:** `dan/brain` (`context_builder` assembles it,
  `manager` dispatches it).
- **Persistence:** **Derived / not persisted as its own row.** It is built from
  DB + config on demand; its occurrence is recorded via the `brain.requested`
  event. Building it from the same DB state must be
  deterministic.
- **Required fields** (`dan/brain/base.py`):
  - `turn_id`, `conversation_id` (the turn id doubles as the correlation id).
  - `input_text` — the user input for this generation.
  - `context_messages` — the conversation context selected from the DB; the
    persona/canon and runtime-state messages are assembled into this list by
    `ContextBuilder`, not passed as a separate `system_prompt` field.
  - `memory_blocks` — **active** blocks only, within a max character budget.
  - `available_tools` — the registered tool specs offered to the model.
  - `settings`, `metadata`.
- **Allowed states:** none — it is a value object passed by value.
- **Emitted events:** `brain.requested` (frozen).
- **Forbidden behavior:**
  - The adapter may checkpoint its provider session only for deterministic
    crash recovery; that checkpoint is execution state, never DAN's memory.
  - Disabled memory must never be included
    ([ADR-009](DECISIONS.md#adr-009)).
  - Context authority must come from DB/config. Provider history may continue
    execution but cannot override DAN's memory or the canonical persona.

---

## 5. BrainResponse

The result returned by a brain adapter.

- **Owner module:** `dan/brain` (adapters produce it; manager normalizes it).
- **Persistence:** **Derived / not persisted as its own row.** Its content lands
  on the `Turn` (`final_text`) and in `events` (`brain.responded`).
- **Required fields** (`dan/brain/base.py`):
  - `text` — the response body (may stream in deltas; final text is canonical).
  - `speech_text` — nullable; the spoken form when it differs from `text`.
  - `tool_calls` — requested `BrainToolCall`s (executed in-turn, §10).
  - `model` — which model produced it.
  - `usage` — token counters (`BrainUsage`).
  - `raw_metadata` — adapter-specific extras.
- **Allowed outcomes:** a response object means success. Failure is an
  exception, not a field: `BrainAdapterError` (adapter unavailable, subprocess
  error, timeout) and its subclass `BrainGenerationCancelled` (barge-in), which
  the orchestrator maps to a `cancelled` turn rather than a failed one.
- **Emitted events:** `brain.responded` (frozen) on success,
  `brain.failed` (frozen) on failure.
- **Forbidden behavior:**
  - The adapter never speaks (no audio) and never writes memory facts
    ([ADR-005](DECISIONS.md#adr-005), [ADR-009](DECISIONS.md#adr-009)).
  - The adapter never invokes the panel.

---

## 6. MemoryBlock

A unit of DAN-owned long-term context. Memory is the daemon's, not the
provider's.

- **Owner module:** `dan/memory` (`manager` + `policies`).
- **Persistence:** `memory_blocks`. **Required.**
- **Required fields** (`dan/memory/manager.py`, `memory_blocks` table):
  - `id`.
  - `kind` — one of `identity`, `user_preference`, `project`, `fact`,
    `summary`, `temporary` (`dan/memory/policies.py::MEMORY_KINDS`).
  - `title` and `body` — the text; both count against the character budget.
  - `active` — boolean; only active blocks enter context.
  - `priority` — for budgeted selection.
  - `created_at`, `updated_at`.
  - `source_event_id` — nullable; the event that produced it.
  - `metadata` — carries `candidate` / `proposed_by` / `promoted_by` for the
    candidate lifecycle.
- **Allowed states:** `active` | inactive. Candidate-vs-committed is tracked in
  `metadata.candidate` (a worker result is an inactive *candidate* until a human
  or policy promotes it — `promote_candidate`).
- **Emitted events:** `memory.updated` (frozen).
- **Forbidden behavior:**
  - Workers never write committed memory facts directly — they only produce
    candidates ([ADR-009](DECISIONS.md#adr-009)). The broker, not the worker,
    writes the inactive candidate row; it auto-promotes it (`promoted_by:
    "policy"`) only when `memory.worker_candidates_require_promotion = false`,
    which is what the shipped example config sets.
  - Inactive blocks are never injected into a `BrainRequest`.

---

## 7. VoiceRequest

A request to *say something*. Enqueued in the DB; played only by the broker.

- **Owner module:** `dan/voice` (`queue` + `broker`).
- **Persistence:** `voice_queue`. **Required** — queued items recover after a
  restart.
- **Required fields** (`voice_queue` table, `dan/store/schema.sql`):
  - `id`, `created_at`, `updated_at`.
  - `text` — what to speak.
  - `priority` — ordering within the queue.
  - `status` — see below.
  - `turn_id` — nullable; provenance.
  - `source`, `session_id`, `participant`, `persona` — who asked and as whom.
  - `lane` — `live` | `normal` | `background`.
  - `utterance_index` — order within the session.
  - `render_snapshot_json` — the immutable resolved render contract (engine,
    version, voice/style, speed, gain, mastering, DSP, pronunciations + their
    sha256, asset hashes, config revision). DB triggers reject an incomplete
    snapshot on insert and any attempt to change it after insert.
  - `voice_id`, `interrupt_policy`, `error`, `metadata_json`.
  - Timing/attestation: `synthesis_started_at`, `synthesis_completed_at`,
    `playback_started_at`, `playback_completed_at`, `spoken_at`,
    `playback_confirmed`.
- **Allowed statuses:** `queued` → `synthesizing` → `speaking` → (`done` |
  `cancelled` | `failed`); `synthesizing` may fall back to `queued`. The
  transition set is enforced by the `voice_queue_status_transition` trigger.
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

- **Owner module:** `dan/voice/listening.py`.
- **Persistence:** `listening_leases`. **Required.**
- **Required fields** (`listening_leases` table):
  - `id`.
  - `mode` — `hold` (button held) | `locked` (sticky).
  - `source` — `ptt` | `global_hotkey` | `lock` (`ALLOWED_SOURCES`); never the
    model, never an automation source.
  - `status` — `active` | `released` | `expired`.
  - `created_at`, `updated_at`.
  - `expires_at` — for stale-lease expiry.
  - `released_at` — nullable; set on release.
  - `owner_process`, `turn_id`, `metadata_json` — nullable context.
- **Allowed states:** `active` (sub-modes `hold` / `locked`) → (`expired` |
  `released`).
- **Emitted events:** `listening.lease.created`, `listening.lease.released`,
  `listening.lease.expired` (the manager also owns lazy expiry; a
  `ListeningLeaseSweeper` thread runs it on a timer so a crashed client cannot
  leave the mic hot). Releasing a `hold` lease must promptly request the
  recorder to stop.
- **Forbidden behavior:**
  - No raw `/tmp` flag is the source of truth for "is listening"
    ([ADR-006](DECISIONS.md#adr-006), [ADR-008](DECISIONS.md#adr-008)).
  - A button release (`hold`) must not clear a `locked` lease.
  - A stale lease must expire rather than listen forever.

---

## 9. AudioDeviceState

The owned view of input/output audio devices.

- **Owner module:** `dan/audio` (`AudioDeviceManager`).
- **Persistence:** `audio_device_snapshots` (point-in-time snapshots).
- **Required fields** (`audio_device_snapshots` table):
  - `input_device_name` / `input_device_uid` — selected input.
  - `output_device_name` / `output_device_uid` — selected output.
  - `preferred_input` — policy preference; the value lives in
    `[audio].preferred_input` (`config/dan.example.toml`), not here.
  - `output_policy`, `bluetooth_microphone_allowed` — the applied policy.
  - `warning` — e.g. bluetooth-mic warning; nullable.
  - `created_at`.
- **Allowed states:** describes device selection; "current" is the latest
  snapshot. Policy: output follows the system default; a bluetooth microphone
  warns or is disabled by default.
- **Emitted events:** `audio.devices.snapshot`.
- **Forbidden behavior:**
  - Voice/STT components never pick devices directly — the
    `AudioDeviceManager` owns device state ([ADR-012](DECISIONS.md#adr-012)).

---

## 10. ToolCall

A request to run a registered tool. It executes immediately; there is no
approval gate in front of it.

- **Owner module:** `dan/tools` (`registry`). Every call is recorded by
  `ToolRunRecorder`; the orchestrator runs model calls inside the turn.
- **Persistence:** `tool_runs` (every attempted call records a run).
- **Required fields** (`ToolRequest` / `ToolResult`, `dan/tools/registry.py`):
  - Request: `id`, `tool_name`, `arguments`, `requested_by` (`"model"` for
    model calls, the caller's name over the API), `turn_id` (nullable),
    `metadata`.
  - Result: `id`, `tool_name`, `status`, `output` (nullable), `error`
    (nullable), `approval_id` (nullable; only the legacy approve/execute path
    ever sets it).
  - The `tool_runs` row adds `risk` — taken from the registered tool spec, never
    from the model — plus `created_at`, `finished_at` and the redacted
    `input_json` / `output_json`.
- **Allowed statuses:** the `tool_runs` row moves `requested` → `started` →
  (`finished` | `failed`). A `ToolResult` is `finished` or `failed`.
- **Emitted events:** `tool.requested`, `tool.started`, `tool.finished`,
  `tool.failed` (and `tool.rejected` on the legacy path) — names in
  `dan/events/types.py`.
- **Forbidden behavior:**
  - Secrets never appear unredacted in event payloads or in `tool_runs`
    (`redact_secrets` + the `PERSIST_MAX_STRING_CHARS` cap).
  - A recorded `ToolRun` is not replayed to satisfy continuation. Duplicate
    execute conflicts instead of running the handler or brain continuation again.
  - The model never declares its own risk class; `tool_call_parser` discards any
    `risk` field in model output.
- **What actually constrains a tool** (there is nothing in front of it):
  containment lives inside the tool implementations — approved-root checks in
  `file_read`/`file_write`/`shell_read`, the exact-match `shell_read`
  allowlist (opt-out: `security.shell_read_unrestricted`), the scrubbed
  environment and git hardening, size/time bounds, and the secure-field and
  control-character refusals in `ui_*`/`terminal_*`. `destructive_tools_enabled`
  and the `require_approval_for_*` flags are NOT consulted by the permission
  policy; they are configuration compatibility fields rendered as runtime state.

---

## 11. Approval (compatibility surface — NOT in the tool path)

A persisted decision record. **Nothing in the normal pipeline creates one:**
`ToolRegistry.request_tool()` and `DaemonApp.request_tool()` execute directly,
and the orchestrator runs model tool calls in-turn. `ApprovalGate` remains for
historical rows, for the memory-save proposal path, and for the explicit
approve/execute routes. Its own docstring says "Release 1 tool execution does
not call this gate."

- **Owner module:** `dan/tools/registry.py` (`ApprovalGate`); the HTTP surface
  lives in `dan/api/routes_runtime.py` — there is no `routes_approvals.py`.
- **Persistence:** `approvals`.
- **Fields** (`approvals` table):
  - `id`, `created_at`, `decided_at`.
  - `status` — see below.
  - `risk`, `requested_by`, `action_type` (e.g. `tool:<name>`).
  - `payload` / `metadata` — redacted JSON; `payload.tool_name`,
    `turn_id`/`correlation_id` live here. There is no `subject` and no
    `decided_by` column.
  - `decision_reason` — nullable; rationale for the decision.
- **Allowed statuses:** `pending` → (`approved` | `rejected`). `expired` exists
  as an event name only. The runtime never leaves the canonical state set to
  wait for one; `WAITING_APPROVAL` is not a runtime state.
- **Emitted events:** `approval.created`, `approval.approved`,
  `approval.rejected`.
- **Forbidden behavior:**
  - Approval alone never executes a tool and never continues a turn — execution
    is the separate explicit approve-and-execute call.
  - Do not rebuild a gate out of this section. If an approval requirement is
    ever wanted again, it needs a new ADR and real code in the tool path.

## 12. One-shot Tool Result Continuation (legacy approve/execute path)

The continuation contract for a turn that was left in `awaiting_approval`.
The normal pipeline never produces such a turn (§10, §1); this section
documents `TurnOrchestrator.continue_after_tool_result`, which is reachable only
through an explicit execute-approved call.

- **Owner module:** `dan/turns` (`TurnOrchestrator`) coordinated from
  `dan/daemon/app.py` after explicit approved execution.
- **Persistence:** no new table. The existing `tool_runs` row records execution,
  the original `turns` row stores the updated final answer or continuation
  failure metadata, and the single append-only `events` stream records
  `brain.requested`, `brain.responded`, `brain.failed`, `error.raised`, and
  `turn.finished` as applicable. There is no second event timeline and no
  `turn_steps` table.
- **Required fields:** continuation metadata includes `approval_id`,
  `tool_name`, `tool_run_id` when available, `previous_status`,
  `continuation_eligible`, `result_class`, and
  `user_approved_and_executed`.
- **Allowed states:** applies only when the approval has a `turn_id`, explicit
  execute-approved recorded a successful `ToolRun`, the original turn is still
  `awaiting_approval`, and the tool result class is one-shot continuation
  eligible. Success updates that same turn to `finished`. A failed continuation
  drives the turn to `failed` and a barge-in cancellation to `cancelled`, both
  with `tool_result_continuation.status` in the turn metadata — a turn is never
  left dangling in `awaiting_approval` (FIX-05 case 3).
- **Emitted events:** existing event types only. Continuation payloads are
  redacted by `EventStore` before persistence.
- **Forbidden behavior:**
  - Approve alone does not execute and does not continue.
  - Continuation never re-executes the tool.
  - Duplicate execute never creates another `ToolRun` or continuation brain
    call.
  - Approvals without a turn ID and approvals tied to non-awaiting turns preserve
    execute-approved behavior without forced continuation.
  - Future result classes `requires_user_presence`,
    `external_communication_pending`, `operator_session_started`,
    `live_visual_control_session`, and `worker_job_started` are reserved design
    space, not implementation commitments here.

---

## 13. WorkerJob

An async background job (e.g. a Codex/Claude worker). Workers advise; they do
not act on the world.

- **Owner module:** `dan/workers` (`broker` + worker adapters).
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

## macOS operator layer pointer

The macOS operator layer is architectural scope for DAN, but concrete operator
APIs are not defined in this document yet. One-shot tools and longer
`OperatorSession` flows go through `ToolRegistry` + `ToolRunRecorder` +
`EventStore`; they are NOT mediated by `PermissionPolicy` or `ApprovalGate`,
which no longer sit in the execution path (§10, §11). The model does not
directly operate the Mac — it can only call registered tools, and each tool
enforces its own containment. Examples in the operator contract are not concrete
implementation commitments until a later scoped prompt, contract, test plan,
and permission model promotes them.

See [MACOS_OPERATOR_CONTRACT.md](MACOS_OPERATOR_CONTRACT.md) for the product and
security contract before adding concrete operator contracts here.

---

## 14. RuntimeProcessObservation

What the supervisor sees about how DAN was launched and which legacy
processes/labels exist. Observation only — never an action.

- **Owner module:** `dan/runtime/supervisor.py` (models in
  `dan/runtime/models.py`).
- **Persistence:** `runtime_process_observations` (snapshots).
- **Required fields** (`RuntimeProcessObservation`):
  - `id`, `created_at`.
  - `label` — the matched family, e.g. `legacy_voice_broker` or
    `official_dand_launch_agent`.
  - `pid`, `process_name`, `command` — nullable; command redacted and clipped
    to `MAX_COMMAND_CHARS`.
  - `kind` — `process` | `launch_agent` | `temp_artifact` | `startup` |
    `warning`.
  - `status` (e.g. `running`, `installed`, `present`), `risk`
    (`info`…`critical`), `details`.
- **Startup snapshot** (`RuntimeStartupSnapshot`, a separate shape) carries
  `launch_mode`, `official_label` (`com.dan.dand`),
  `official_plist_installed`, `official_plist_loaded`, `legacy_launch_agents`,
  `legacy_temp_artifacts` and `warnings`. What counts as legacy is listed once,
  in [LAUNCH_SUPERVISION.md](LAUNCH_SUPERVISION.md) §3.
- **Allowed states:** describes an observation; "current" is the latest
  snapshot, surfaced in `/state` and `/runtime/processes`.
- **Emitted events:** `runtime.process.observed`,
  `runtime.legacy.conflict.detected`.
- **Forbidden behavior:**
  - **Never kill a process automatically** ([ADR-007](DECISIONS.md#adr-007)).
  - An observation is never treated as authority to act — it only warns the
    human.

---

## Cross-cutting invariants

1. **Truth lives in `dand`'s DB** (`~/.dan/dan.db`). Every contract above is
   created, owned and mutated by exactly one module, and (unless explicitly
   "derived") persisted in exactly one table.
2. **`/tmp` is never a source of truth** — only a compatibility transport.
3. **Secrets are redacted before any event write.**
4. **Speaking is exclusive to the voice broker; workers and adapters are mute.**
5. **The same `TurnOrchestrator` serves panel text and voice transcripts.**
