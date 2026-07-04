# Jarvis Memory OS Contract

Classification: authoritative.

This document defines the target contract for Jarvis Memory OS before
auto-memory, consolidation, or topic documents are implemented. It does not
change runtime behavior. It does not change the SQLite schema. It does not
change current `MemoryManager` behavior.

`memory_blocks` remain the current v0 semantic memory items. Future tasks must
introduce candidate, evidence, usage, and topic structures deliberately instead
of treating the current table as the whole memory system.

## Authority And Scope

Jarvis memory is local, daemon-owned product state. Provider sessions are not
Jarvis memory, and brain adapters are stateless.

This contract is binding for future memory work:

- No model-originated write may create hidden active memory.
- Active memory must be evidence-backed.
- Memory lifecycle and retrieval must be auditable.
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

Status transitions must be auditable. Future operators should emit events such
as `memory.candidate.created`, `memory.candidate.approved`,
`memory.activated`, `memory.superseded`, `memory.used_in_context`,
`memory.disabled`, and `memory.forgotten`.

## Write Paths

Jarvis supports these intended write paths:

- Manual panel/API/CLI writes: explicit user-managed memory operations.
- Model-originated `memory_save`: a proposal path, not silent activation.
- Explicit user "remember this": creates a candidate or manual memory with
  evidence and policy checks.
- Background consolidator: future only, for dedupe, summaries, topic updates,
  conflict detection, and decay.

A model-originated memory_save cannot silently write active durable memory.
`memory_save` requires approval/execution policy before any candidate becomes
active memory. The current v0 `memory_blocks` surface is manual memory, not a
complete automatic assistant memory system.

In short: memory_save requires approval/execution policy.

Hot-path writes should be narrow: explicit remember requests, clear low-risk
preferences, or explicit project decisions. Heavy summarization, dedupe, topic
consolidation, and conflict detection belong to a future background
consolidator or manual CLI/runbook first.

## Approval Policy

Low-risk explicit preferences may be auto-candidate when the user states them
clearly and they affect product behavior, for example communication style or
workflow preferences.

Sensitive data requires explicit approval before it can become durable memory:
personal data, health, finance, location, relationships, account identifiers,
private identifiers, or other sensitive facts.

Secrets must be rejected or redacted. API keys, passwords, session tokens,
private keys, and credential material must not be stored as memory. Secret
redaction rules still apply before any event or DB write.

The model cannot silently write active durable memory. It may propose a
candidate with rationale and evidence; a human or explicit policy must promote
it.

The system must reject one-off emotion, unsupported inference, and offensive
content as a stored "preference" unless the user explicitly asks for a safe,
bounded project/persona rule.

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

Evidence should explain what was remembered, why it was remembered, where it
came from, when it was observed, and whether it replaced older memory.

Future retrieval should record usage: which memory was included, for which
turn, at what rank, and why. A user must be able to audit whether a response
used a memory item.

## Dedupe, Revision, And Conflict Policy

Memory is not append-only fact spam. Future semantic memory must support:

- create - add a new memory when no suitable existing item exists.
- merge evidence - attach new evidence to the same canonical claim.
- revise - update a claim while preserving evidence history.
- supersede - replace older active memory with a newer memory item.
- reject duplicate - refuse equivalent candidate memories.
- flag conflict - mark contradictions for human or policy review.

Semantic memory should carry a `canonical_key`, `topic_id`, claim hash or
equivalent identity, `supersedes`, `superseded_by`, `evidence_count`, and
`last_confirmed_at`.

When new evidence narrows an older claim instead of simply contradicting it,
split scope instead of deleting useful memory. Example: short step-by-step
instructions during live terminal rescue can coexist with comprehensive reports
when explicitly requested.

## Retrieval Policy

Retrieval must be explainable and scoped. It cannot rely only on nearest-vector
similarity.

Default retrieval rules:

- namespace-scoped.
- topic-routed.
- active only by default.
- default retrieval excludes disabled, superseded, and forgotten memory.
- no secret memory.
- no unsupported sensitive inference.
- explain why included.
- fit a budgeted MemoryCompiler.

The future `MemoryCompiler` should select and organize memory for each
`BrainRequest` under explicit limits, for example procedural rules needed for
the task, active user preferences, current project status, relevant semantic
topic documents, recent episode cards, pending decisions, and user-pinned
memory.

The compiler should also be able to explain exclusions, such as old resolved
bugs, low-confidence candidates, disabled memory, superseded memory, off-topic
summaries, and secrets.

The compiled prompt shape should preserve hierarchy, for example
`user_preferences`, `project_status`, `relevant_lessons`, and `open_risks`,
instead of dumping unrelated memory bullets into one flat section.

## Topic Documents

Topic documents are future consolidation units. They are not implemented in
this task.

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

## Not Implemented Yet

The following are design targets only:

- auto-memory extraction is not implemented yet.
- summarization/consolidator is not implemented yet.
- topic documents are not implemented yet.
- dedupe engine is not implemented yet.
- memory audit UI is not implemented yet.
- episode cards are not implemented yet.
- Memory Inbox candidate review is not implemented yet.
- MemoryCompiler is not implemented yet.

Current `MemoryManager`, `memory_blocks`, `ContextBuilder` memory injection,
manual Memory API/CLI, and `memory.updated` events remain the current behavior.

## Migration Path From memory_blocks

No schema migration is allowed by this task.

Future memory tasks should treat current `memory_blocks` as v0 semantic memory
items and then deliberately introduce:

- observations.
- candidates.
- evidence records.
- active memory item lifecycle.
- topic documents.
- usage audit events.
- review decisions.

Future migration work must preserve the rule that disabled memory is excluded
from default retrieval and that provider sessions are not Jarvis memory.
