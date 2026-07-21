# Jarvis MemoryCompiler Contract

Classification: current contract. Implementation status verified 2026-07-21.

The compiler is BUILT (`dan/memory/compiler.py`) and WIRED: `ContextBuilder`
calls it and renders a `compiled_memory` context message when compiled memory is
enabled (`dan/brain/context_builder.py`). Enablement rules live in
`docs/MEMORY_OS_ARCHITECTURE.md`.

This document is the contract. Where the shipped implementation is narrower than
the contract, the gap is called out inline as "Not implemented" — those are the
lines you must not trust as descriptions of runtime behaviour.

## Purpose

MemoryCompiler turns active `memory_items` into a bounded, deterministic,
explainable memory context that `ContextBuilder` renders into the
`BrainRequest`.

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

Non-goals set for the first MemoryCompiler implementation, each marked with
whether the shipped compiler still honours it:

- no provider calls. — still honoured.
- no vector store. — still honoured.
- no hidden summarization. — still honoured.
- no auto-memory. — still honoured.
- no prompt wiring yet. — **this one no longer holds.** MEMORY-COMPILER-WIRE-01
  shipped the `ContextBuilder` wiring, and compiled output does reach the prompt
  when compiled memory is enabled.
- no automatic conflict resolution. — still honoured.
- no automatic confidence boosting from retrieval. — still honoured.
- no automatic overwrite, merge, or revision of memory records. — still
  honoured.
- no Topic Documents runtime. — still honoured.
- no panel UI. — still honoured.
- no runtime ledger in this task. — still honoured, and still true afterwards:
  the compiler performs no database writes of any kind.

Scope of the original design task (MEMORY-COMPILER-DESIGN-01, 2026-07-04) —
historical record, superseded 2026-07-21. As written then:

> ContextBuilder is not wired in this task, and this task
> does not change runtime prompt behavior.

That is the scope of that task, not today's runtime; see the header for what
shipped since.

## Inputs

`MemoryCompilerRequest` carries `conversation_id`, `current_turn_id`,
`current_user_text` and a `MemoryCompilerConfig`. The items themselves are not
passed in: the compiler pulls them through
`MemoryItemRepository.list_items_for_compiler()`, which joins the evidence count
onto every `memory_items` row.

`MemoryCompilerConfig` fields are exactly: `max_items` (default 3), `max_chars`
(default 1200), `include_procedural` (default false), `scope_filter`,
`namespace_filter`, `include_debug_metadata` (default true).

Not implemented: a per-item character limit and any safety policy flags —
neither exists as a config field. `current_user_text` is accepted and echoed
into nothing; the compiler does not use it for relevance.

Namespace and scope filters narrow selection. They do not bypass lifecycle or
evidence checks; both of those run before the filter.

## Outputs

The compiler returns a `CompiledMemoryContext` contract:

- `selected_items`: prompt-eligible memory entries after lifecycle, provenance,
  procedural, scope/namespace and budget checks. There is no separate safety
  check — secrets are redacted, not filtered.
- `skipped_items`: considered entries that were not selected.
- `budget_used`: total budget cost of selected entries.
- `budget_limit`: configured limit applied to this compilation.
- `selection_reasons`: keyed by `memory_id`, explaining why each selected item
  was included.
- `skipped_reasons`: keyed by `memory_id`, explaining why each skipped item was
  excluded.
- `audit_metadata`: policy tag `memory_compiler_v1`, redacted conversation/turn
  ids, source/selected/skipped counts and the filter settings. Empty when
  `include_debug_metadata=false`.
- `warnings`: **Not implemented — always an empty list.** No stale-memory,
  conflict or budget-pressure warning is ever emitted.

Compiled output preserves provenance through `memory_id` and evidence metadata.
`memory_id` is NOT the database id: it is `mem_ref_<sha256 of the row id>`, so
raw ids never leave the compiler. It must be safe to inspect in logs or an audit
view after redaction.

Every selected `memory_item` must have provenance: `evidence_count >= 1` or an
equivalent explicit provenance record. Enforced as `evidence_count >= 1`, with
`reason_skipped="missing_provenance"` otherwise; the "equivalent explicit
provenance record" alternative has no implementation, because no second form of
provenance exists to accept. Legacy, manual, and migrated memories must carry
provenance metadata rather than bypassing provenance. `source_policy` can
describe the kind of provenance, but cannot waive the requirement — the shipped
compiler does not read it for eligibility at all.

## Selection Rules

Selection is deterministic, policy-first, and explainable:

- Include only `status=active` `memory_items`. An `active` row that carries
  `superseded_by` is treated as superseded and skipped.
- Exclude inactive, disabled, superseded, forgotten, rejected, and
  candidate-only states.
- Require provenance for every selected item: `evidence_count >= 1`.
- Secrets are handled by redaction, not exclusion: `redact_secret_text` runs
  over title, claim, canonical key, kind, scope, namespace and sensitivity, and
  the item is still selected. **Not implemented: there is no
  sensitivity-policy skip and no `sensitivity` threshold** — an item marked
  sensitive is compiled like any other.
- Prefer current project/thread scope over global scope.
- Prefer exact namespace matches over broad namespace matches.
- Prefer recently confirmed items over stale items only as a tie-breaker, not as
  the sole criterion.
- Skip items already marked `conflict` or `merge_candidate`; do not resolve
  them. **Not implemented: the compiler does not detect conflicts between two
  active items** — it only honours a conflict status someone else has set.
- Skip procedural memory unless `include_procedural=true`.
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

Records must not be silently overwritten, silently merged, or selected as if
they were one clean truth when review is required.

What the shipped compiler actually does: it reads `status` and `superseded_by`
and skips accordingly. Two contradictory `active` items are both selected, with
no warning. Deduplication happens earlier, at write time —
`MemoryItemRepository.activate_candidate` reuses an existing row when the
`canonical_key` matches instead of inserting a second one.

## Budget Rules

Budgeting is part of the contract, not an implementation detail:

- Use deterministic ordering.
- Use stable tie-breakers.
- Enforce max item count.
- Enforce max character budget.
- Apply per-item character truncation policy before final prompt formatting —
  **Not implemented.** There is no per-item limit and no
  `unsafe_or_unrepresentable` reason. The upside is that nothing is ever cut
  mid-value, so nothing can be cut through a secret.
- Record a skipped reason when an item is over budget.
- Preserve enough metadata for skipped over-budget items to be auditable.

Per-item cost is `len(title) + len(claim)`. An item that would push the running
total past `max_chars`, or that arrives after `max_items` are already selected,
is skipped whole with `reason_skipped="over_budget"`; the loop continues, so a
later smaller item can still fit.

Ordering applied by `_sort_eligible_items`, from weakest to strongest key:
`memory_id`, then `updated_at` descending, then `last_confirmed_at` descending,
then confidence rank (high/medium/low/unknown), then the scope-match and
namespace-match ranks — the last sort wins, so scope/namespace match dominates
and `memory_id` is the final stable tie-breaker. Kind bucket and source-policy
priority are not used.

## Explainability

`SelectedMemoryItem` exposes:

- `memory_id` (projected `mem_ref_<sha256>`, never the raw row id).
- `canonical_key`.
- `kind`.
- `scope`.
- `namespace`.
- `title`.
- `claim`.
- `reason_selected` — a single constant, `"eligible"`. There is no per-item
  explanation of WHY this item beat another.
- `evidence_count`.
- `source_policy`.
- `sensitivity`.
- `budget_cost`.

`SkippedMemoryItem` exposes:

- `memory_id` (projected the same way).
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

Secrets policy applies even when an item is otherwise active. The shipped
compiler satisfies it by redaction only: every string it returns has passed
through `redact_secret_text`, and `content` is never returned at all. It never
skips an item for being unsafe.

Reality check on user control: there is no disable, forget or supersede
operation anywhere — `MemoryItemRepository` has no such method and no API route
exists. The compiler honours those statuses, but today only a direct database
edit can set one.

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

Shipped behaviour: separation is achieved by exclusion, not by sectioning. With
the default `include_procedural=false` every item whose `kind` is `procedural`
is skipped. If a caller sets it true, procedural items land in the same flat
list as semantic ones — **the separate section does not exist.** Note also that
`memory_save` cannot create procedural items: `MEMORY_KINDS` is
identity / user_preference / project / fact / summary / temporary.

## Governance addendum for first compiler implementation

This addendum closes first-compiler governance rules without creating a separate
governance source of truth. Every rule in it is implemented as written, except
the procedural sectioning noted below.

### Compiler eligibility statuses

- selectable: active only, and only when `superseded_by` is empty.
- never selectable: candidate, needs_review, approved-but-not-activated,
  rejected, disabled, superseded, forgotten, conflict, merge_candidate.
- any other status value falls through to `reason_skipped="inactive"`.

### Status precedence

Governance status beats relevance, recency, namespace match, and confidence. If
a memory item is disabled/superseded/forgotten/conflict, the compiler must skip
it even if it looks highly relevant.

### Conflict handling for first compiler

The compiler must not resolve conflicts. The compiler must skip conflict-marked
items or surface `reason_skipped="conflict"`. The compiler must not merge
memories and must not silently pick one conflicting memory as truth.

Implemented as written: it skips items whose status is `conflict` or
`merge_candidate` with `reason_skipped="conflict"`. It also does not detect
conflicts — nothing in the codebase sets a conflict status, so the rule is
enforced only over statuses somebody else would have to write.

### Supersession handling for first compiler

The compiler must skip superseded items, and a skipped superseded item must
receive `reason_skipped="superseded"`. If a superseding active item exists and is
eligible, the compiler may select the superseding item.

Implemented as written, both when `status="superseded"` and when an `active` row
carries `superseded_by`. Nothing in the codebase sets `supersedes` or
`superseded_by`.

### Forget/disable handling for first compiler

Disabled memory is skipped and remains auditable. Forgotten memory is skipped and
must not be surfaced in compiled output. Future APIs may distinguish
tombstone/audit behavior, but compiler output must not expose forgotten content.

Implemented as written. Nothing in the codebase can set either status.

### Merge policy for first compiler

There is no runtime merge in the first compiler. Same title, same namespace, or
similar text is not enough to merge. Deterministic equivalence/merge is future
governance runtime, not compiler runtime. The only deduplication anywhere is
`activate_candidate` reusing a `memory_items` row with an identical
`canonical_key`, and that happens at write time, not in the compiler.

### Procedural memory handling for first compiler

The first compiler must skip procedural memories by default unless explicitly
requested by caller config — implemented. Procedural memory must not be mixed
into semantic memory output without a separate section or reason — **NOT
implemented**: with `include_procedural=true` both kinds share one flat list.

### Compiler output reasons

The addendum defines canonical reason values only; it does not define a new
output field. Per-item skip reasons must use the existing `reason_skipped`
field, and aggregate skipped reasons must use the existing `skipped_reasons`
collection.

Skipped reasons the compiler actually emits: `inactive`, `disabled`,
`superseded`, `forgotten`, `conflict`, `candidate_only`, `rejected`,
`over_budget`, `missing_provenance`, `procedural_not_requested`,
`namespace_mismatch`. `merge_candidate` status is reported as `conflict`.

`sensitivity_policy` is reserved and never emitted — no sensitivity skip exists.
`ContextBuilder` buckets any reason outside the list above as `other` in its
diagnostics.

## Future Usage Ledger

Still future, still unbuilt. A future `memory_usage_events` ledger should
persist what happened during compilation; today `memory_usage_events` exists as
a table in `dan/store/schema.sql` and no code writes to or reads from it. The
compiler does not implement runtime ledger persistence — it performs no database
writes at all. If it is ever built, the fields should be:

- `memory_id`.
- `conversation_id`.
- `turn_id`.
- selected/included boolean.
- `reason`.
- `skipped_reason`.
- `budget_cost`.
- `created_at`.

The point of the ledger would be to let a user ask which memory was used,
skipped, or blocked for a turn and why. Today that question has no answer beyond
the coarse per-build `CompiledMemoryDiagnostics`, which is not persisted.

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

Skipped reasons make some of these visible during manual debugging. Warnings do
not — the list is always empty, so "conflicting memory selected without review"
and "self-reinforcing retrieval" would pass unannounced.

## Future Milestones

Original milestone list, with what has landed since:

- MEMORY-COMPILER-01: deterministic compiler service — DONE.
- MEMORY-COMPILER-WIRE-01: ContextBuilder integration — DONE.
- MEMORY-USAGE-LEDGER-01: usage event persistence — not started.
- MEMORY-GOVERNANCE-DESIGN-01: conflict/revision/forgetting contract — not
  started; no operation can move an item out of `active`.
- MEMORY-AUDIT-01: explain what memory was used and why — not started.
