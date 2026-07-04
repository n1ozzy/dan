# Jarvis Memory OS Architecture

Classification: current.

This document describes the target architecture for Jarvis Memory OS. It is a
design reference, not an implementation report. The current runtime still uses
v0 `memory_blocks` plus `MemoryManager`, manual Memory API/CLI operations, and
active-only `ContextBuilder` injection.

Data model ADR: `docs/adr/ADR-001-memory-os-data-model.md`.

## Design Goal

Jarvis needs a professional local memory system, not a single table that stores
whatever a model happened to say. The target system is evidence-backed,
layered, auditable, approval-aware, topic-document based, dedupe/revision
capable, and retrieval-explainable.

The core product rule is: no memory becomes active truth without provenance and
a visible lifecycle.

## Current Baseline

The current repo already has useful v0 pieces:

- memory_blocks are v0 semantic memory items.
- `memory_blocks` stores durable Jarvis-owned memory rows.
- `MemoryManager` owns create, update, disable, and active block selection.
- Manual Memory API/CLI operations expose memory management.
- ContextBuilder currently injects active memory.
- `ContextBuilder` injects active memory only.
- Disabled memory is excluded from future brain requests.
- EventStore records `memory.updated`.
- Providers do not own Jarvis memory.
- Workers cannot write committed memory facts directly.
- The memory_save/tool approval path may exist but must be characterized later
  by reality tests before any design assumes its exact behavior.

These pieces are not yet automatic assistant memory. They are the seed for the
future semantic memory layer.

## Target Components

### Memory Inbox

Memory Inbox is the buffer between observations and active memory. It receives
raw observations, creates candidates, classifies sensitivity, detects likely
duplicates or conflicts, and routes each candidate to approval, rejection, or a
future low-risk auto-candidate policy.

It prevents model-originated writes from silently becoming long-term truth.

### Evidence Ledger

The evidence ledger links memory to source material: conversation, turn, event,
manual source, quote, confidence, sensitivity, and review decision.

Active memory without evidence is invalid by contract. Evidence records should
be inspectable from the panel and usable in audits.

### Memory Items

Memory items are active or inactive semantic/procedural claims with lifecycle,
scope, namespace, source policy, confidence, sensitivity, and supersession
metadata.

The current `memory_blocks` table maps only to a v0 subset of this idea.

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

MemoryCompiler is the future context compiler for `BrainRequest`. It chooses
which memory to include under a budget, orders it by task relevance, and records
why each item was included or excluded.

The compiler should combine symbolic filters, namespaces, topic routing,
recency, evidence confidence, manual pins, and semantic similarity. Vector
similarity alone is not enough.

### Memory Audit

Memory Audit is the future ability to show which memories were used in a
response and why. It should connect usage events to the turn, memory item, rank,
reason, and resulting response.

## Target Data Model

ADR-001 records the future data-model direction. MEMORY-SCHEMA-DESIGN-01 makes
no schema change in this task and no migration in this task; it only documents
the staged model and migration boundary.
No schema change in MEMORY-SCHEMA-DESIGN-01.

Future schema work should be scoped explicitly and may introduce these
structures:

- `memory_observations` for raw source observations.
- `memory_candidates` for reviewable proposed memories.
- `memory_items` for active or lifecycle-managed semantic/procedural claims.
- `memory_evidence` for provenance links and quote/evidence.
- `memory_topics` for topic documents / consolidation units.
- `memory_usage_events` for retrieval/audit trail.
- `memory_review_decisions` for approve/reject/edit/merge decisions.

No schema change in this task. No migration in this task. This task does not add
those tables. Any future schema work must be deliberate, tested, and separately
scoped.

MEMORY-SCHEMA-01 later added these Memory OS v1 tables additively. It did not
migrate `memory_blocks` data, change current retrieval behavior, or cut over
ContextBuilder to a new compiler, and it did not bump the core schema version.

## Migration Path

Future migration from v0 memory must:

- preserve current memory_blocks.
- introduce additive structures later.
- keep `memory_blocks` as source of truth until explicit cutover.
- map `memory_blocks` to future `memory_items` as v0 semantic items.
- create `source_unknown` or manual evidence when old provenance is missing.
- maintain ContextBuilder compatibility during transition.
- future cutover must be explicit and tested.
- keep the old `memory_blocks` path readable until tested cutover.

The transition should keep existing manual memory usable while new observation,
candidate, evidence, topic, and usage-audit structures are added around it.

## Governance Operators

Future memory behavior should be expressed as explicit operators:

- `ingest_observation`
- `classify_candidate`
- `approve_candidate`
- `activate_memory`
- `revise_memory`
- `supersede_memory`
- `forget_memory`
- `retrieve_memory`
- `compile_context`
- `audit_usage`

Each operator should have input, decision, state transition, emitted event, and
tests. This keeps memory as governed product state rather than model folklore.

## Write Modes

### Hot Path

The hot path may detect narrow candidates: explicit "remember this", clear
low-risk preferences, explicit project decisions, or a model-originated
`memory_save` proposal.

Hot path work should not perform heavy summarization or broad profile building.

### Background Consolidator

The future consolidator handles dedupe, topic document updates, summaries,
conflict detection, memory decay, and review queue cleanup.

The first version should be manual CLI/runbook driven before it becomes a
worker. Automatic background memory must not bypass approval or evidence rules.

## Panel UX Direction

Future panel memory should have four views:

- Memory Inbox: approve, edit and approve, reject, merge, mark temporary.
- Active Memory: claims, scope, confidence, last used, evidence, controls.
- Topic Documents: consolidated project/user topics with evidence links.
- Memory Audit: what was used in a response, why, and how to correct it.

The panel remains only a client. It renders daemon state and submits intents; it
does not own canonical memory.

## Rollout Sequence

Recommended future phase plan:

1. Contract
2. Reality tests
3. ADR/data model
4. Additive schema
5. Memory Inbox
6. Evidence ledger
7. memory_save v2
8. MemoryCompiler
9. Topic Documents
10. Governance/dedupe
11. Audit API
12. Panel UX
13. Auto-candidates
14. Manual consolidator
15. Privacy/forgetting

This sequence keeps runtime behavior visible and reviewable at each step.

## Non-Goals For This Design Task

This design task does not implement:

- auto-memory extraction.
- new tables or migrations.
- `memory_save` behavior changes.
- topic documents.
- dedupe or revision engine.
- MemoryCompiler.
- panel UI.
- voice/STT/TTS/broker behavior.
- daemon or provider startup.

All live/manual smoke claims must stay distinct from mock/unit tests.
