# Jarvis MemoryCompiler Contract

Classification: current design contract.

This document defines the contract for the future MemoryCompiler before runtime
wiring. It is design and contract only. ContextBuilder is not wired in this
task, and this task does not change runtime prompt behavior.

## Purpose

MemoryCompiler is responsible for turning active `memory_items` into a bounded,
deterministic, explainable memory context for future `BrainRequest`
construction.

It selects memory for a conversation turn, records why items were selected or
skipped, preserves provenance, and leaves enough audit metadata for a later
Memory Audit surface. It does not decide that memory is true. It consumes
already-governed memory state and compiles a prompt-ready context under policy
and budget limits.

Memory correctness is trajectory-level, not record-level. A single stored item
is not enough proof that Jarvis "knows" something correctly. The compiler must
respect lifecycle, evidence, conflict state, scope, namespace, revision history,
and prior governance.

## Non-Goals For First Implementation

The first MemoryCompiler implementation must not include:

- no provider calls.
- no vector store.
- no hidden summarization.
- no auto-memory.
- no prompt wiring yet.
- no automatic conflict resolution.
- no automatic confidence boosting from retrieval.
- no automatic overwrite, merge, or revision of memory records.
- no Topic Documents runtime.
- no panel UI.
- no runtime ledger in this task.

In this design task specifically, ContextBuilder is not wired, no runtime
MemoryCompiler service is implemented, and no prompt behavior changes.

## Inputs

The future compiler input must be explicit and serializable:

- `conversation_id`.
- `current_turn_id` or equivalent turn metadata.
- current user text.
- active `memory_items` eligible for retrieval.
- evidence metadata linked to each eligible item.
- budget configuration.
- optional namespace/scope filters.

Budget configuration should include at least max item count, max character
budget, per-item character limit, namespace policy, and safety policy flags.

Optional namespace and scope filters may come from route, project, conversation,
tool mode, or future topic routing. They narrow selection; they must not bypass
lifecycle, evidence, or safety checks.

## Outputs

The compiler returns a `CompiledMemoryContext` contract:

- `selected_items`: prompt-eligible memory entries after lifecycle, safety,
  scope, conflict, and budget checks.
- `skipped_items`: considered entries that were not selected.
- `budget_used`: total budget cost of selected entries.
- `budget_limit`: configured limit applied to this compilation.
- `selection_reasons`: keyed by `memory_id`, explaining why each selected item
  was included.
- `skipped_reasons`: keyed by `memory_id`, explaining why each skipped item was
  excluded.
- `audit_metadata`: deterministic compiler version, input filters, ordering
  policy, timestamp source, and future turn linkage.
- `warnings`: non-fatal conditions such as stale memory, conflicts requiring
  review, missing optional metadata, or budget pressure.

Compiled output must preserve provenance through `memory_id` and evidence
metadata. It must be safe to inspect in logs or an audit view after redaction.
Every selected `memory_item` must have provenance. Every selected `memory_item`
must have `evidence_count >= 1` or an equivalent explicit provenance record.
Legacy, manual, and migrated memories must carry provenance metadata rather
than bypassing provenance. `source_policy` can describe the kind of provenance,
but cannot waive the requirement.

## Selection Rules

Selection is deterministic, policy-first, and explainable:

- Include only `status=active` `memory_items`.
- Exclude inactive, disabled, superseded, forgotten, rejected, and
  candidate-only states.
- Exclude items with sensitivity/secrets policy violations.
- Require provenance for every selected item: `evidence_count >= 1` or an
  equivalent explicit provenance record.
- Prefer current project/thread scope over global scope.
- Prefer exact namespace matches over broad namespace matches.
- Prefer recently confirmed items over stale items only as a tie-breaker, not as
  the sole criterion.
- Skip or mark conflicts for review; do not silently resolve them.
- Treat procedural memory separately from semantic memory.
- Procedural memory must not be mixed blindly with semantic facts.
- Preserve `memory_id` and evidence metadata for every selected item.

Selection must not include disabled, superseded, forgotten, rejected, or
candidate-only memory through aliases, merged records, broad namespace fallback,
or stale caches.

## Conflict And Revision Policy

Do not solve conflicts by overwriting or silently merging records.

Contradictory or revision-related items must be represented explicitly and
require review unless deterministic equivalence is defined:

- exact deterministic equivalence may allow a duplicate to be skipped as
  equivalent.
- non-equivalent conflict must be skipped or marked for review.
- narrowing scope should be represented as separate scoped memory or explicit
  revision metadata.
- supersession must preserve the older `memory_id` lineage.
- conflicting active items should produce a warning and skipped reason unless a
  future governance policy defines deterministic equivalence.

Records must not be silently overwritten, silently merged, or selected as if
they were one clean truth when review is required.

## Budget Rules

Budgeting is part of the contract, not an implementation detail:

- Use deterministic ordering.
- Use stable tie-breakers.
- Enforce max item count.
- Enforce max character budget.
- Apply per-item character truncation policy before final prompt formatting.
- Record a skipped reason when an item is over budget.
- Preserve enough metadata for skipped over-budget items to be auditable.

Suggested ordering inputs are: eligibility pass, kind bucket, exact scope match,
exact namespace match, source policy priority, evidence count, last confirmed
timestamp, and final stable `memory_id` tie-breaker. Recency is allowed as a
tie-breaker, not as a standalone reason to select a memory.

Per-item truncation must not cut through raw secrets or expose unsafe evidence.
If a memory cannot be safely represented under the per-item limit, skip it with
`reason_skipped=unsafe_or_unrepresentable`.

## Explainability

Each selected item must expose:

- `memory_id`.
- `canonical_key`.
- `kind`.
- `scope`.
- `namespace`.
- `title`.
- `reason_selected`.
- `evidence_count`.
- `source_policy`.
- `sensitivity`.

Each skipped item must expose:

- `memory_id`.
- `reason_skipped`.

Selected and skipped explanations must be stable enough for contract tests and
future audit tools. They should be terse product reasons, not model-generated
rationalizations.

## Safety

Compiled memory must be safe by default:

- no raw secrets in compiled output.
- no raw evidence quotes unless explicitly safe/redacted.
- no hidden psychological inference.
- no sensitive auto-inclusion without explicit approval.
- no raw credential material, API keys, private keys, tokens, passwords, or
  session identifiers.
- no candidate-only sensitive memory.
- user control must remain possible through future disable, forget, and
  governance operations.

Secrets policy applies even when an item is otherwise active. If an active item
or evidence record contains unsafe material, the compiler must skip it or emit
only a redacted representation permitted by policy.

## Procedural Versus Semantic Memory

Procedural memory describes how Jarvis must behave: repo rules, approval
boundaries, scope discipline, safety rules, runbook requirements, and tool use
constraints.

Semantic memory describes facts, preferences, project state, decisions, and
stable concepts.

The compiler should keep these as distinct streams or sections in the compiled
context. Procedural memory can be higher priority when it constrains safe
execution, but it must not be mixed blindly with semantic memory. A project fact
must not override a procedural safety rule, and a procedural rule must not be
treated as a factual claim about the user.

## Future Usage Ledger

A future `memory_usage_events` ledger should persist what happened during
compilation. The concept includes:

- `memory_id`.
- `conversation_id`.
- `turn_id`.
- selected/included boolean.
- `reason`.
- `skipped_reason`.
- `budget_cost`.
- `created_at`.

This task does not implement runtime ledger persistence. The ledger is a future
audit feature so a user can ask which memory was used, skipped, or blocked for a
turn and why.

## Failure Modes

The design must explicitly guard against:

- stale memory selected because it is recent enough to look useful.
- conflicting memory selected without review.
- over-selection that floods prompt context.
- secret leakage through memory text or evidence quotes.
- irrelevant global memories beating project/thread memory.
- procedural/semantic mixing that turns rules into fake facts or facts into
  unsafe rules.
- self-reinforcing retrieval loops where repeated selection boosts confidence
  without new evidence.

Warnings and skipped reasons should make these failure modes visible during
manual debugging and future Memory Audit work.

## Future Milestones

- MEMORY-COMPILER-01: deterministic compiler service.
- MEMORY-COMPILER-WIRE-01: ContextBuilder integration.
- MEMORY-USAGE-LEDGER-01: usage event persistence.
- MEMORY-GOVERNANCE-DESIGN-01: conflict/revision/forgetting contract.
- MEMORY-AUDIT-01: explain what memory was used and why.
