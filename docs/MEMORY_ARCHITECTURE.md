# Jarvis Memory OS Architecture

Classification: current. Current as the *design of record* — this is the live
version of the Memory OS target architecture, not an archived draft.

**It is TARGET-ARCHITECTURE DESIGN, written 2026-07-04. Do not read this
document as a description of current behaviour.** Some of the components
described below as "future" were built afterwards; others were never built. Each
section carries a status line where reality has moved.

Current behaviour is documented in `docs/MEMORY_OS_ARCHITECTURE.md`. The
authority above both documents is the source: `dan/memory/`,
`dan/brain/context_builder.py`, `dan/tools/memory_tool.py`,
`dan/api/routes_memory.py`, `dan/store/schema.sql`.

Data model ADR: `docs/adr/ADR-001-memory-os-data-model.md`.

## Design Goal

Jarvis needs a professional local memory system, not a single table that stores
whatever a model happened to say. The target system is evidence-backed,
layered, auditable, approval-aware, topic-document based, dedupe/revision
capable, and retrieval-explainable.

The core product rule is: no memory becomes active truth without provenance and
a visible lifecycle.

## Current Baseline (rewritten 2026-07-21 from source)

- memory_blocks are v0 semantic memory items: `memory_blocks` stores durable
  DAN-owned v0 memory rows, and `MemoryManager` (`dan/memory/manager.py`) owns
  create, update, disable, and active-block selection, recording
  `memory.updated` on every mutation.
- ContextBuilder currently injects active memory: `ContextBuilder` puts active
  `memory_blocks` into every turn AND, when compiled memory is enabled, a
  separate `compiled_memory` context message produced by `MemoryCompiler`. The
  two paths are independent.
- Disabled `memory_blocks` are excluded from brain requests.
- Memory OS v1 tables exist and are used: `memory_observations`,
  `memory_candidates`, `memory_items`, `memory_evidence`
  (`dan/store/schema.sql`). `memory_topics`, `memory_usage_events` and
  `memory_review_decisions` exist in the schema but no code writes or reads
  them.
- Providers do not own DAN memory; brain adapters are stateless.
- The memory_save/tool approval path has been characterized and does not exist:
  `DaemonApp.request_tool` deletes the request source and calls
  `ToolRegistry.execute_tool` directly, and `ToolRegistry.request_tool` ignores
  its `approval_gate` argument. See "memory_save today" below.

## memory_save today (verified 2026-07-21)

`MemorySaveTool.run` (`dan/tools/memory_tool.py`) creates a candidate plus its
evidence row, approves the candidate itself, and activates it into
`memory_items` with `status='active'` — all inside one tool call, from any
source, with no human step. There is no approval gate in the path and the
`/approvals` HTTP routes return 404 (`tests/test_no_approval_surface.py`).
`MemorySaveTool.propose` (candidate only, no activation) still exists but
nothing in the execution path calls it.

`MemoryItemRepository` exposes only `activate_candidate`, `get_item`,
`list_items` and `list_items_for_compiler`. There is no repository method and no
API route that disables, supersedes or forgets a `memory_items` row. The
compiler honours those statuses, but only a direct database edit can set them.

## Target Components

### Memory Inbox

Status: BUILT (partially). `MemoryCandidateRepository`
(`dan/memory/inbox.py`) plus the `/memory/candidates` routes implement create,
list, get, approve, reject, activate and evidence attach/list. Not built:
sensitivity classification, duplicate/conflict detection, and any auto-candidate
policy. It does NOT prevent model-originated writes from becoming long-term
truth — `memory_save` walks straight through it (see above).

Memory Inbox is the buffer between observations and active memory. It receives
raw observations, creates candidates, classifies sensitivity, detects likely
duplicates or conflicts, and routes each candidate to approval, rejection, or a
future low-risk auto-candidate policy.

### Evidence Ledger

Status: BUILT. `MemoryEvidenceRepository` (`dan/memory/evidence.py`) writes a
`memory_observations` row and a `memory_evidence` row per attachment, requires
at least one locator (source_id / conversation_id / turn_id / event_id / quote),
redacts quotes, and refuses evidence on an already-decided candidate. The
compiler enforces `evidence_count >= 1`. Not built: review-decision records
(`memory_review_decisions` is an empty table) and any panel view.

The evidence ledger links memory to source material: conversation, turn, event,
manual source, quote, confidence, sensitivity, and review decision.

### Memory Items

Status: BUILT as a table and an activation path. `memory_items` carries
lifecycle, scope, namespace, source policy, confidence, sensitivity and
supersession columns, and `memory_blocks` remains a separate v0 surface. Not
built: any lifecycle transition after activation (see "memory_save today").

### Episode Cards

Episode Cards are future summaries of EventStore ranges. They capture what
happened, status, related commits, and lessons, while pointing back to the
append-only events that prove the episode.

They are the bridge between raw event history and useful episodic memory.

### Topic Documents

Topic documents consolidate active claims and evidence for stable domains such
as `project/jarvis/voice`, `project/jarvis/memory`, and
`user/ozzy/communication`.

They are future consolidation units, not implemented runtime behavior.

### MemoryCompiler

Status: BUILT and WIRED. `dan/memory/compiler.py` is a deterministic, read-only
compiler; `ContextBuilder` calls it and renders a `compiled_memory` message.
Contract and deviations: `docs/MEMORY_COMPILER.md`. Ordering uses lifecycle,
provenance, scope/namespace match, confidence, last-confirmed and updated
timestamps. There is no topic routing, no manual pinning and no semantic
similarity.

### Memory Audit

Memory Audit is the future ability to show which memories were used in a
response and why. It should connect usage events to the turn, memory item, rank,
reason, and resulting response.

## Target Data Model

ADR-001 records the data-model direction. MEMORY-SCHEMA-01 has since added all
seven tables additively to `dan/store/schema.sql` without migrating
`memory_blocks` data and without bumping the core schema version:

- `memory_observations` — written, one row per evidence attachment.
- `memory_candidates` — written by the inbox and by `memory_save`.
- `memory_items` — written by `activate_candidate`, read by the compiler.
- `memory_evidence` — written by the evidence repository.
- `memory_topics` — table exists, nothing writes or reads it.
- `memory_usage_events` — table exists, nothing writes or reads it.
- `memory_review_decisions` — table exists, nothing writes or reads it.

The compiler cutover the older text called "future" has happened for the
compiled-memory path: `ContextBuilder` calls `MemoryCompiler` when compiled
memory is enabled. `memory_blocks` injection was not replaced and still runs on
every turn.

### Schema boundary as drawn by MEMORY-SCHEMA-DESIGN-01 (2026-07-04, historical)

Kept because the boundary it drew is still the rule for anyone touching the
schema. Read it as the scope of that design task, not as today's state:

> No schema change in this task. No migration in this task. That task only
> documented the staged model and the migration boundary; any future schema
> work must be deliberate, tested, and separately scoped.

Superseded by MEMORY-SCHEMA-01, which added the seven tables above additively,
without migrating `memory_blocks` data and without bumping the core schema
version. The "deliberate, tested, separately scoped" requirement still stands
for everything after it.

## Migration Path

The additive half is done: the Memory OS v1 tables were added beside
`memory_blocks`, and `memory_blocks` remains the untouched v0 surface with its
own API, CLI and `ContextBuilder` injection.

Migration from v0 memory must, with the status of each rule:

- preserve current memory_blocks — HELD. Nothing has rewritten or dropped a row.
- introduce additive structures later — DONE by MEMORY-SCHEMA-01.
- keep `memory_blocks` as source of truth until explicit cutover — HELD.
- map `memory_blocks` to `memory_items` as v0 semantic items — NOT DONE. No row
  has been mapped, and a migration that does it must create `source_unknown` or
  manual evidence rather than invent provenance.
- create `source_unknown` or manual evidence when old provenance is missing —
  binding, not yet exercised.
- maintain ContextBuilder compatibility during transition — HELD: compiled
  memory is a second, independent context section, not a replacement, so
  `memory_blocks` injection never changed shape.
- future cutover must be explicit and tested — BINDING and not started.
  Retiring the `memory_blocks` path requires that cutover.
- keep the old `memory_blocks` path readable until tested cutover — HELD.

## Governance Operators

Memory behavior should be expressed as explicit operators. Built:
`ingest_observation` (as an evidence side effect), `approve_candidate`,
`activate_memory`, `compile_context`. Not built: `classify_candidate`,
`revise_memory`, `supersede_memory`, `forget_memory`, `audit_usage`.
`retrieve_memory` exists only as `MemoryRetriever` over `memory_blocks`.

Each operator should have input, decision, state transition, emitted event, and
tests. This keeps memory as governed product state rather than model folklore.

## Write Modes

### Hot Path

The only hot-path writer is the `memory_save` tool, and it is not a proposal: it
activates. Nothing detects candidates from conversation text — there is no
auto-extraction and no "remember this" recogniser.

Hot path work should not perform heavy summarization or broad profile building.

### Background Consolidator

The future consolidator handles dedupe, topic document updates, summaries,
conflict detection, memory decay, and review queue cleanup.

The first version should be manual CLI/runbook driven before it becomes a
worker. Automatic background memory must not bypass approval or evidence rules.

## Panel UX Direction

Status: none of this exists. The panel is `dan/panel/menubar_app.py` plus one
asset bundle; it has no memory view at all.

Future panel memory should have four views:

- Memory Inbox: approve, edit and approve, reject, merge, mark temporary.
- Active Memory: claims, scope, confidence, last used, evidence, controls.
- Topic Documents: consolidated project/user topics with evidence links.
- Memory Audit: what was used in a response, why, and how to correct it.

The panel remains only a client. It renders daemon state and submits intents; it
does not own canonical memory.

## Rollout Sequence

Phase plan with status as of 2026-07-21:

1. Contract — done
2. Reality tests — done
3. ADR/data model — done
4. Additive schema — done
5. Memory Inbox — done (storage and API; no classification or dedupe)
6. Evidence ledger — done
7. memory_save v2 — done (writes candidate + evidence + active item)
8. MemoryCompiler — done, and wired into `ContextBuilder`
9. Topic Documents — not built
10. Governance/dedupe — not built
11. Audit API — not built
12. Panel UX — not built
13. Auto-candidates — not built
14. Manual consolidator — not built
15. Privacy/forgetting — not built (redaction is in place; nothing can set a
    memory item to disabled, superseded or forgotten)

This sequence keeps runtime behavior visible and reviewable at each step.

## Non-Goals For The Original Design Task (2026-07-04, historical)

The design task that produced this document deliberately implemented nothing.
That scope note is kept for history only; several of these were built by later
tasks (schema, `memory_save` behaviour, MemoryCompiler). What is still not
implemented is the list in "Rollout Sequence" above.

All live/manual smoke claims must stay distinct from mock/unit tests.
