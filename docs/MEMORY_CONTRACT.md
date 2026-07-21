# Jarvis Memory OS Contract

Classification: authoritative. Authoritative as INTENT — this is the contract
memory work is held to, not a report on what the runtime already enforces.
Status of each rule verified against source 2026-07-21.

**Read this as the contract we want, not as a description of what the runtime
enforces.** Several rules below — most importantly the ones about
model-originated writes needing approval — are NOT enforced by the current code.
Every such rule is now marked. For current behaviour see
`docs/MEMORY_OS_ARCHITECTURE.md` and the source: `dan/memory/`,
`dan/tools/memory_tool.py`, `dan/brain/context_builder.py`.

> ## ⚠️ A THIRD WRITE PATH EXISTS AND IS NOT DESCRIBED ANYWHERE IN THIS FILE
>
> Added 2026-07-21 — `docs/reviews/2026-07-21-docs-vs-code-audit.md` D1–D2.
> Every "write paths that exist today" list below is missing the largest one.
>
> `dan memory sync` (`dan/cli.py:160-172`, `799-818`) calls
> `sync_dan_turns()` (`dan/memory/sync.py:115-181`), which writes the **content
> of every turn — user and assistant — into durable `memory_archive_documents`
> plus an FTS index**. `sync_path()` (`:22-37`) additionally imports Claude and
> Codex JSONL session files and markdown memory files.
>
> That path has no candidate, no evidence, no approval and no lifecycle. **There
> is no forget operation** — a row leaves only via another sync with
> `replace`/`delete_item_ids`.
>
> It is not write-only. `memory_recall` (`dan/tools/memory_recall_tool.py:17-45`,
> risk `safe_read`) is registered in the live daemon (`dan/daemon/app.py:2344`)
> and offered to the model in its tool list; it runs an FTS5 query over that
> archive (`dan/memory/archive.py:342-370`) and returns full `content`. So the
> model can full-text search the owner's transcript history.
>
> This is an owner-privacy question, not a documentation nit. Nothing here is a
> recommendation to change it — it is a statement that the contract above does
> not currently cover it.

`memory_blocks` remain the v0 semantic memory items and are still injected into
every turn. The candidate, evidence and item structures were added beside them
(`dan/store/schema.sql`); usage and topic structures exist as empty tables.

## Product Intent

Jarvis Memory OS is the contract for local, inspectable, evidence-backed memory
that can support a long-running operator without turning provider sessions,
model guesses, or one-off chat residue into hidden product truth.

Manual memory is not the same as automatic assistant memory. Manual memory is
explicit user-managed state entered through approved product surfaces. Automatic
assistant memory is a future capability that must pass through observation,
candidate review, evidence, policy, and audit before anything becomes active.

## Non-Goals

This document is policy, not machinery. It does not change runtime behavior,
schema, daemon, panel, provider, brain adapter, or voice behavior. Writing a
rule here does not implement it — which is why every rule below carries its
real status.

Historical scope note: MEMORY-DESIGN-01, the task that first wrote this file on
2026-07-04, deliberately implemented nothing at all — no auto-memory
extraction, no schema change, no migration, no live validation. Later tasks
(MEMORY-SCHEMA-01, the candidate inbox, the evidence ledger, MemoryCompiler)
built parts of it. What is still unbuilt is the list in "Not Implemented Yet".

## Authority And Scope

Jarvis memory is local, daemon-owned product state. Provider sessions are not
Jarvis memory, and brain adapters are stateless.

This contract is binding for future memory work:

- No model-originated write may create hidden active memory. **NOT ENFORCED
  TODAY** — `memory_save` activates in one call from any source; see "Write
  Paths".
- Active memory must be evidence-backed. ENFORCED for compiled memory: the
  compiler skips any item with `evidence_count < 1`, and `activate_candidate`
  refuses a candidate with no evidence. NOT enforced for `memory_blocks`, whose
  only provenance is an optional `source_event_id` that nothing requires.
- Memory lifecycle and retrieval must be auditable. PARTIAL: lifecycle
  transitions emit events (`memory.candidate.*`, `memory.activated`,
  `memory.updated`); retrieval emits nothing durable.
- Procedural rules that matter for safety must be enforced by docs plus
  guardrail tests, hooks, or runtime policy, not only prompt text.
- Mock/unit tests are not proof of live product behavior.

## Memory Layers

Jarvis Memory OS separates five layers. They must not be collapsed into one
table or one prompt section.

### Working Memory

Working Memory is current runtime state for the active operation: current turn,
current push-to-talk capture, current response assembly, current tool proposal,
current approval, current voice queue activity, or active listener state.

Working Memory may disappear after daemon restart. Important observations can
later become episodic or semantic candidates, but the working state itself is
not durable long-term memory.

### Thread Memory

Thread Memory is the durable memory of a conversation: `conversation_id`, turns,
recent messages, conversation state, active topic, and pending decisions for
that conversation.

Jarvis should persist this through SQLite tables such as `conversations`,
`turns`, `events`, and future active conversation state. If Jarvis is a
long-running operator, the active conversation must survive daemon restart.

### Episodic Memory

Episodic Memory records what happened: bugfix sessions, tool runs, approvals,
manual smokes, incidents, and lessons from prior agent actions.

The primary source is the append-only EventStore. Future `Episode Card` records
may summarize event ranges, commits, status, and lessons, but they must point
back to evidence events instead of duplicating history as a second source of
truth.

### Semantic Memory

Semantic Memory stores facts, preferences, project facts, status claims, and
stable concepts. Current `memory_blocks` are v0 semantic memory items.

Future semantic memory items must carry claim, scope, namespace, lifecycle,
confidence, sensitivity, provenance, dedupe policy, retrieval tags, and
supersession metadata. A memory item without provenance is not active truth.

### Procedural Memory

Procedural Memory stores how Jarvis must work: repo rules, approval boundaries,
scope discipline, smoke interpretation, provider boundaries, and memory write
policy.

Procedural Memory belongs in `AGENTS.md`, `docs/PROJECT_RULES.md`, this
contract, prompt templates, tests, hooks, and runtime guardrails. Prompt text is
context, not enforcement. Safety-critical procedural memory must have tests,
hooks, CI checks, or runtime policy where practical.

## Lifecycle States

Memory-capable records must use explicit lifecycle states:

- `observed` - raw observation exists, not yet a candidate.
- `candidate` - proposed memory extracted from an observation or explicit
  request.
- `needs_review` - candidate requires human or policy review before promotion.
- `approved` - candidate has been approved but is not necessarily active yet.
- `active` - memory is eligible for default retrieval and context injection.
- `rejected` - candidate was refused and must not be promoted.
- `superseded` - memory was replaced by newer memory and must not be retrieved
  by default.
- `disabled` - memory is retained but excluded from default retrieval.
- `forgotten` - memory was removed or tombstoned by explicit forget policy and
  must not be retrieved by default.

Which of these are real: `memory_candidates.status` accepts exactly
`needs_review`, `approved`, `rejected` (`VALID_CANDIDATE_STATUSES`).
`memory_items.status` is only ever written as `active`. `observed`, `superseded`,
`disabled` and `forgotten` are recognised by the compiler's skip logic but are
never produced by any code path.

Events emitted today: `memory.candidate.created`, `memory.candidate.approved`,
`memory.candidate.rejected`, `memory.evidence.created`, `memory.activated`, and
`memory.updated` for `memory_blocks`. Never emitted:
`memory.superseded`, `memory.used_in_context`, `memory.disabled`,
`memory.forgotten`.

## Write Paths

The write paths this contract sanctions, each with its real status:

- Manual panel/API/CLI: explicit user-managed memory operations. EXISTS —
  `POST/PATCH/DELETE /memory` over `memory_blocks`, plus `POST
  /memory/candidates` and the approve / reject / activate routes.
- Model-originated memory_save: a proposal path, not silent activation. The
  tool exists; the "proposal, not activation" half does NOT — see the binding
  rules below.
- Explicit user "remember this": creates a candidate or manual memory with
  evidence and policy checks. NOT IMPLEMENTED — there is no recogniser; the
  user calls the API, or the model calls `memory_save`.
- Future background consolidator: future only, for dedupe, summaries, topic
  updates, conflict detection, and decay. NOT IMPLEMENTED.
- Future topic document consolidation: future only, for turning reviewed claims
  and episodes into maintained topic documents with evidence links. NOT
  IMPLEMENTED — `memory_topics` is an empty table.

Write paths that exist today, concretely:

- Manual panel/API/CLI writes to `memory_blocks`: `POST/PATCH/DELETE /memory`.
- Manual candidate review: `POST /memory/candidates`, then approve / reject /
  activate, with evidence attached before activation.
- `memory_save` tool: creates a candidate plus evidence, approves it and
  activates it into `memory_items` in a single call.

Write paths that do NOT exist: automatic extraction of candidates from
conversation, a "remember this" recogniser, a background consolidator, and topic
document consolidation.

Two binding rules of this contract — **NOT ENFORCED TODAY**:

- a model-originated memory_save cannot silently write active durable memory.
- memory_save requires approval/execution policy before any candidate becomes
  active memory.

**What the runtime actually does, 2026-07-21.** It breaks both rules above.
Earlier revisions of this file printed them as descriptions of behaviour; they
are requirements the code does not meet. `DaemonApp.request_tool` discards the
request source and calls `ToolRegistry.execute_tool` directly;
`ToolRegistry.request_tool` ignores its `approval_gate` argument;
`MemorySaveTool.run` self-approves the candidate it just created and activates
it. The `/approvals` routes return 404
(`tests/test_no_approval_surface.py`). The only guards on a model-originated
save are schema validation, the kind allow-list, the 200/2000-character title
and body caps, and secret redaction.

Hot-path writes should be narrow: explicit remember requests, clear low-risk
preferences, or explicit project decisions. Heavy summarization, dedupe, topic
consolidation, and conflict detection belong to a future background
consolidator or manual CLI/runbook first.

## Approval Policy

**None of this is enforced.** There is no approval gate, no sensitivity
classifier, and no promotion step in the runtime. The rules below are the target.

Sensitive data should require explicit approval before it can become durable
memory: personal data, health, finance, location, relationships, account
identifiers, private identifiers, or other sensitive facts. Today the
`sensitivity` column is written as `"unknown"` by both `memory_save` and the
candidate API unless a caller sets it, and nothing reads it to make a decision.

Secrets ARE handled, and this part is real: `redact_secret_text` runs on
candidate claim, title and reason, on evidence quotes, and again on every string
the compiler returns. Persisted tool payloads are redacted and capped at 4096
characters (`dan/tools/registry.py`).

Target, not current behaviour: the model should only propose a candidate with
rationale and evidence, with a human or explicit policy promoting it. The system
should reject one-off emotion, unsupported inference, and offensive content as a
stored "preference" unless the user explicitly asks for a safe, bounded
project/persona rule.

## Privacy Policy

Jarvis memory must be useful without becoming creepy or unsafe:

- no secret storage.
- no hidden psychological inference.
- no sensitive inference without approval.
- secrets must be rejected or redacted. ENFORCED by redaction.
- forget/disable must prevent default retrieval. The compiler honours
  `disabled`, `superseded` and `forgotten`, but **no operation can set them** —
  `MemoryItemRepository` has no disable/forget/supersede method and no API route
  exists. `memory_blocks` can be disabled (`DELETE /memory/{id}`); `memory_items`
  cannot.

Sensitive memory must have explicit policy before promotion. User profile
claims must be grounded in evidence and scoped to observable product use, not
private speculation.

## Evidence And Provenance

Active memory must be evidence-backed. Every active semantic or procedural
memory item must have provenance:

- `conversation_id` when sourced from a conversation.
- `turn_id` when sourced from a turn.
- `event_id` when sourced from EventStore.
- manual source when entered through panel, API, CLI, or docs.
- quote or evidence excerpt when available.
- confidence.
- sensitivity.

`memory_evidence` carries `conversation_id`, `turn_id`, `event_id`, `quote`,
`weight` and an `observation_id`, and `add_evidence` refuses a row with none of
those locators. `confidence` and `sensitivity` live on the candidate/item, not
the evidence row, and default to `"unknown"`.

Retrieval records nothing. `memory_usage_events` is an empty table, so a user
cannot audit whether a response used a memory item.

## Dedupe, Revision, And Conflict Policy

Memory is not append-only fact spam. Semantic memory must support:

- create — implemented.
- merge evidence — implemented at activation only: `activate_candidate` reuses
  an item with the same `canonical_key` and relinks the new evidence to it.
- revise — not implemented.
- supersede — not implemented (columns exist, nothing writes them).
- reject duplicate — not implemented as a candidate-level refusal; duplicates
  collapse silently at activation via `canonical_key`.
- flag conflict — not implemented; `conflict` status is honoured by the
  compiler but nothing sets it.

`memory_items` carries `canonical_key`, `supersedes`, `superseded_by` and
`last_confirmed_at`; `evidence_count` is computed by join at compile time. There
is no `topic_id`.

When new evidence narrows an older claim instead of simply contradicting it,
split scope instead of deleting useful memory. Example: short step-by-step
instructions during live terminal rescue can coexist with comprehensive reports
when explicitly requested.

## Retrieval Policy

Retrieval must be explainable and scoped. It cannot rely only on nearest-vector
similarity.

Default retrieval rules, with what `MemoryCompiler` actually does:

- namespace-scoped — optional, via `namespace_filter`; unset by default.
- topic-routed — not implemented.
- active only by default — implemented.
- default retrieval excludes disabled, superseded, and forgotten memory —
  implemented in the compiler's skip logic, though no supported operation can
  set those statuses in the first place.
- no secret memory — by redaction, not exclusion.
- no unsupported sensitive inference — not implemented.
- explain why included — only as the constant `reason_selected="eligible"`.
- fit a budgeted MemoryCompiler — implemented (`max_items`, `max_chars`).

`MemoryCompiler` exists and is wired; the contract for it is
`docs/MEMORY_COMPILER.md`. It does not organize by topic documents, episode
cards, pending decisions or user pins — none of those exist.

The compiled prompt shape should preserve hierarchy, for example
`user_preferences`, `project_status`, `relevant_lessons`, and `open_risks`.
It does not: the rendered section is one flat list of
`title` / `claim` / `evidence_count` triples.

## Topic Documents

Topic documents are future consolidation units. They are not implemented:
`memory_topics` is an empty table.

Namespaces in use today are assigned by `memory_save` from the memory kind:
`user/default` for identity and user_preference, `project/default` for project,
`global/<kind>` for the rest. The families below are aspirational.

Future namespace families include:

- `project/jarvis/*`
- `user/ozzy/*`
- `agent/procedural/*`

Example topic documents:

- `project/jarvis/status`
- `project/jarvis/architecture`
- `project/jarvis/voice`
- `project/jarvis/memory`
- `project/jarvis/bugs`
- `project/jarvis/agent_rules`
- `user/ozzy/profile`
- `user/ozzy/communication`
- `agent/procedural/codex`

A topic document may summarize active claims, open issues, evidence pointers,
related episode cards, and last consolidation time. It must link to evidence;
it must not become an untraceable replacement for EventStore or active memory
items.

## Not Implemented Yet (checked against source, 2026-07-21)

- auto-memory extraction is not implemented yet.
- summarization/consolidator is not implemented yet
  (`MemorySummarizer.summarize` raises `NotImplementedError`).
- topic documents are not implemented yet.
- the dedupe/revision engine is not implemented yet beyond the `canonical_key`
  collapse at activation.
- memory audit UI is not implemented yet.
- episode cards are not implemented yet.
- memory usage events are not implemented yet.
- no lifecycle transition after activation is implemented yet: disable,
  supersede, forget.

Implemented, and no longer to be described as future: Memory Inbox candidate
review (repository plus `/memory/candidates` routes), the evidence ledger,
`memory_items` activation, and `MemoryCompiler` including its `ContextBuilder`
wiring and the read-only `POST /memory/compile-preview` route.

Current behaviour therefore is: `MemoryManager` and `memory_blocks` injection on
every turn, plus compiled memory when enabled, plus the manual Memory API/CLI,
with `memory.updated`, `memory.candidate.created/approved/rejected`,
`memory.evidence.created` and `memory.activated` events.

## Migration Path From memory_blocks

The additive schema landed (MEMORY-SCHEMA-01): observations, candidates,
evidence and items are live; topics, usage events and review decisions are empty
tables. No `memory_blocks` row has been migrated into `memory_items`, and the
two paths run side by side.

Future migration work must preserve the rule that disabled memory is excluded
from default retrieval and that provider sessions are not Jarvis memory.
