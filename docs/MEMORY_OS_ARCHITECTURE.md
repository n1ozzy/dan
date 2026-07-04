# Memory OS Architecture

Classification: technical architecture.
Scope: Memory OS as implemented and tested on branch `rescue/audt-gpt5.5pro-limit-cdn` at HEAD `bd18d3b`.

## Purpose

Memory OS makes Jarvis memory explicit, reviewable, evidence-backed, and safe to use in prompt context. It replaces hidden or accidental memory behavior with a lifecycle model.

The design rejects silent model-originated memory activation. A model may propose memory through a controlled path, but active memory requires approval and evidence.

## Data lifecycle

The intended flow is:

```text
observation / memory_save proposal
→ memory_candidate
→ memory_evidence
→ approval or rejection
→ activation
→ memory_item
→ MemoryCompiler
→ ContextBuilder
→ BrainRequest/context_messages
```

Legacy `memory_blocks` remain present and are still used by ContextBuilder. Memory OS does not casually replace them.

## Storage model

Primary tables in `jarvis/store/schema.sql` include:

- `memory_blocks`
- `memory_observations`
- `memory_candidates`
- `memory_items`
- `memory_evidence`

Related tables include `events`, `settings`, `conversations`, and `turns`.

## Evidence and provenance

Memory must be evidence-backed before it can be prompt-eligible. Evidence can link to candidate, observation, conversation, turn, event, and quote fields.

Prompt-visible compiled memory must not expose raw evidence quotes.

## Approval and activation

Candidates are created before activation. Approval makes a candidate eligible for activation; activation creates a durable `memory_item`.

Model-originated `memory_save` is a proposal path. It must not create hidden active memory.

## Memory item states

`memory_items` include lifecycle-relevant fields:

- `status`
- `kind`
- `scope`
- `namespace`
- `canonical_key`
- `title`
- `claim`
- `content`
- `confidence`
- `sensitivity`
- `source_policy`
- `supersedes`
- `superseded_by`
- evidence count through joined evidence

Only active, eligible, evidence-backed items should be selectable.

## Compiler architecture

`jarvis/memory/compiler.py` defines:

- `MemoryCompilerConfig`
- `MemoryCompilerRequest`
- `SelectedMemoryItem`
- `SkippedMemoryItem`
- `CompiledMemoryContext`
- `MemoryCompiler`

The compiler is deterministic. It does not call providers, use embeddings, write storage, or update timestamps.

## Compiler output contract

The compiler output may contain internal metadata:

- selected items
- skipped items
- budget usage
- selection reasons
- skipped reasons
- audit metadata
- warnings

Those internal fields are not automatically prompt-visible. ContextBuilder must render only safe fields.

## Selected memory model

Selected memory may include internal fields such as `memory_id`, `canonical_key`, `source_policy`, and `sensitivity`. These fields are useful for audit but forbidden in final prompt text.

Prompt-rendered compiled memory uses only:

- `title`
- `claim`
- `evidence_count`

Both title and claim are normalized and secret-redacted before rendering.

## Skipped memory model

Skipped memory is not prompt-visible. Skipped items exist for audit/diagnostics only.

Unsafe or ineligible reasons include:

- candidate-only
- inactive
- rejected
- disabled
- forgotten
- superseded
- conflict
- missing provenance/evidence
- namespace/scope mismatch
- over budget
- procedural memory not requested

## Governance exclusions

Final BrainRequest/context output must exclude:

- disabled memory
- superseded memory
- forgotten memory
- conflict memory
- missing provenance/evidence memory
- procedural memory by default
- raw evidence quotes
- raw observation text
- raw secrets
- audit metadata
- skipped items
- raw IDs
- canonical keys
- debug reason maps
- traceback text
- exception text

## Procedural memory

Procedural memory is excluded by default. It requires explicit opt-in through compiler configuration.

Procedural rules must not be mixed blindly with semantic facts. Safety-relevant procedural behavior should also be enforced by code and tests, not only by memory.

## ContextBuilder integration

`ContextBuilder` accepts optional memory compiler dependencies and a default-off flag.

Key behavior:

- Flag off: no compiler call, no compiled memory context message.
- Flag on: compiler may run and produce a safe compiled memory context message.
- Compiler failure: fail closed by omitting compiled memory.
- Existing `memory_blocks` behavior is preserved.
- User input must survive unchanged except for existing budget-capping behavior.

The compiled memory message uses metadata:

```text
kind = compiled_memory
untrusted = True
```

## Runtime integration

Runtime dependency wiring exists so daemon-created ContextBuilder instances can receive compiler dependencies. That is not global enablement.

Compiled memory remains default-off until a specific enablement task adds explicit dev/local or scoped activation.

No env/config/panel/API enablement exists for compiled memory in this branch. Current enablement is explicit constructor/test wiring only.

## Prompt-visible output contract

The only prompt-visible compiled memory shape is:

```text
Compiled memory:
- title: <safe title>
  claim: <safe claim>
  evidence_count: <number>
```

No raw compiler object, ID, canonical key, evidence quote, observation, skipped item, or audit metadata may be rendered.

## Fail-closed behavior

If the compiler raises, ContextBuilder still builds the BrainRequest and omits compiled memory.

A safe diagnostics object records coarse failure state. It must not include exception message or traceback.

## Observability

At HEAD `bd18d3b`, `MEMORY-CONTEXT-OBSERVE-01` is implemented. `ContextBuildResult` includes `CompiledMemoryDiagnostics` and exposes coarse fields only.

Diagnostics are not prompt-visible and must not include raw IDs, canonical keys, evidence, observations, secrets, user input, exception text, or traceback.

## Test coverage

Relevant tests:

- `tests/test_memory_contract.py`
- `tests/test_memory_manager.py`
- `tests/test_memory_inbox.py`
- `tests/test_memory_evidence.py`
- `tests/test_memory_items.py`
- `tests/test_memory_save_tool.py`
- `tests/test_memory_compiler.py`
- `tests/test_memory_compiler_contract.py`
- `tests/test_memory_compiler_eval.py`
- `tests/test_memory_compiler_preview_api.py`
- `tests/test_memory_compiler_wire.py`
- `tests/test_context_builder.py`
- `tests/test_secret_redaction.py`

Test focus has moved from compiler internals to final BrainRequest/context output. That is the correct boundary for prompt-safety claims.

## Future work

- Add dev/local enablement.
- Add runtime smoke around explicit enablement.
- Add scoped enablement.
- Add usage ledger/audit events for memory selection.
- Add production telemetry beyond the current coarse diagnostics.
- Add formal policy document after behavior stabilizes.
- Add panel UX only after safe enablement is proven.
