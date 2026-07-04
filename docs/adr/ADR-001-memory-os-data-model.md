# ADR-001: Memory OS Data Model

Classification: authoritative/current design decision.

Status: accepted for future design direction.

## Problem

`memory_blocks` are v0 semantic memory items. They give Jarvis a current manual
memory surface, but they are not the full Memory OS target.

The current system lacks evidence ledger, candidate inbox, topic documents,
usage audit, dedupe/revision lifecycle. Without those structures, Jarvis cannot
prove where a memory came from, why it was promoted, how it was used, or whether
it replaced an older claim.

## Decision

The future Memory OS data model is staged around these entities:

- `memory_observations`
- `memory_candidates`
- `memory_items`
- `memory_evidence`
- `memory_topics`
- `memory_usage_events`
- `memory_review_decisions`

This ADR does not implement the entities. It records the target shape for a
future additive schema task.

## Entity Responsibilities

`memory_observations` are raw source observations. They may come from a turn,
event, manual input, runbook, or other approved source before any memory claim
is proposed.

`memory_candidates` are reviewable proposed memories. They hold extracted or
explicit proposed claims before approval, rejection, edit, or merge.

`memory_items` are active or lifecycle-managed memories. They are the durable
semantic or procedural claims eligible for retrieval only when lifecycle and
policy allow it.

`memory_evidence` stores provenance links and quote/evidence. Evidence links
memory to the source conversation, turn, event, manual source, excerpt,
confidence, and sensitivity when available.

`memory_topics` are topic documents / consolidation units. They group reviewed
claims and evidence into maintained project, user, or agent-procedural topics.

`memory_usage_events` are the retrieval/audit trail. They record which memory
was considered or included, for which turn, at what rank, and why.

`memory_review_decisions` store approve/reject/edit/merge decisions. They make
the review lifecycle auditable instead of burying policy in prompt text.

## Non-Goals

- No schema change in MEMORY-SCHEMA-DESIGN-01.
- No runtime behavior change.
- No auto-memory implementation.
- No panel implementation.
- No migration implementation.
- No change to `MemoryManager`, `ContextBuilder`, or `memory_save` behavior.

## Migration Strategy

The future migration must be additive-first. New structures should be added
beside current v0 memory rather than replacing it in the same task.

`memory_blocks` remains source of truth until explicit cutover. In plain
contract text: memory_blocks remains source of truth until explicit cutover.
The current manual memory path must stay readable and usable while future
observation, candidate, evidence, topic, usage, and review-decision structures
are introduced around it.

A future migration should map memory_blocks to memory_items as v0 semantic
items. When old rows lack detailed provenance, the migration should create
`source_unknown` or manual evidence records rather than inventing a false
history.

ContextBuilder compatibility must be maintained during transition. Until a
tested compiler cutover exists, current active-memory injection should continue
to behave as it does today.

The old `memory_blocks` path remains readable until a tested cutover proves the
new path can preserve current behavior and rollback requirements.

## Rollback Strategy

Additive schema can be ignored by older runtime. No destructive migration is
allowed before a backup/restore plan exists and is tested.

`memory_blocks` remains source of truth until the explicit cutover task.
Rollback keeps memory_blocks usable, including the current manual memory API,
CLI, and ContextBuilder path.

## Testing Strategy

A future schema task must prove:

- the new tables exist.
- the migration is idempotent.
- old `memory_blocks` still works.
- ContextBuilder behavior is unchanged until compiler cutover.
- rollback keeps `memory_blocks` usable.

Tests for this ADR task only prove the design text and cross-reference exist.

## Open Questions

- Exact namespace format.
- Whether embeddings are added later or never.
- Retention/forgetting semantics.
- Panel UX order.
- Whether topic consolidation is manual-first or worker-backed later.

## Boundary

This ADR authorizes future design direction only. It does not authorize schema
or runtime changes by itself.
