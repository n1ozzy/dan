# Memory OS Architecture

Classification: technical architecture.
Scope: Memory OS as implemented and tested on branch `rescue/audt-gpt5.5pro-limit-cdn` at HEAD `802f6e8 test: cover compiled memory rollout precedence matrix`.

Current rollout snapshot: Memory OS compiled-memory rollout safety workstream is complete through compiled-memory policy docs, docs status refresh, session/profile scoped enablement, compiled-memory force-disable, and rollout precedence matrix tests. This document describes the current safety contract; future env/API/panel/production rollout work remains separately scoped.

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

`ContextBuilder` accepts optional memory compiler dependencies, explicit enablement gates, session/profile scoped internal enablement, request-scoped internal override, and a force-disable kill switch.

Key behavior:

- Flag off: no compiler call, no compiled memory context message.
- Flag on: compiler may run and produce a safe compiled memory context message.
- `[memory].enabled=false` and `compiled_memory_force_disabled` prevent compiler calls.
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

Compiled memory remains default-off. Config-based dev/local enablement exists, but it can enable compiled memory only when `[memory].enabled=true`, compiled-memory context is explicitly enabled in config, and the force-disable kill switch is off.

Session/profile scoped enablement exists and is internal-only. An empty session/profile allow-list enables zero sessions and does not globally leak; a `None` allow-list preserves established global config behavior.

Request-scoped internal override support exists for one request at a time. Request override False disables one request. Request override True cannot bypass `[memory].enabled=false` or `compiled_memory_force_disabled`. Overrides do not mutate builder or runtime state.

No env, panel, public API, user-facing, or global production enablement exists yet.

## Compiled memory context policy

This section is the formal rollout and safety contract for compiled memory in prompt context. Future work must preserve it unless a task explicitly scopes a policy change and updates the contract tests.

### Enablement precedence

- The global default is off.
- Config dev/local enablement can enable compiled memory when `[memory].enabled=true`.
- `[memory].enabled=false` is an absolute compiled-memory disable.
- `compiled_memory_force_disabled` disables compiled memory regardless of config, session/profile, or request override.
- Session/profile scoped enablement exists and is internal-only.
- Empty session/profile allow-list enables zero sessions and does not globally leak.
- `None` allow-list preserves established global config behavior.
- Request-scoped override True can enable compiled memory for one request only when `[memory].enabled=true` and the kill switch is off.
- Request-scoped override False disables compiled memory for one request.
- Request-scoped override must not mutate builder/runtime state.
- No env, panel, public API, user-facing, or global production enablement exists yet.

### Prompt-visible output contract

- Compiled memory is represented only as safe compiled_memory context message.
- Metadata remains `kind=compiled_memory` and `untrusted=True`.
- Safe fields are `title`, `claim`, `evidence_count`.
- Existing `memory_blocks` behavior remains separate and unchanged.

### Forbidden prompt-visible data

- Raw IDs, canonical keys, audit metadata, and skipped items must not appear.
- Raw evidence quotes and raw observations must not appear.
- Raw secrets must not appear.
- Exception text and tracebacks must not appear.
- Compiler diagnostics must not be rendered into model-visible context.

### Governance exclusions

- Disabled excluded.
- Superseded excluded.
- Forgotten excluded.
- Conflict excluded.
- Missing provenance/evidence excluded.
- Procedural excluded by default.

### Diagnostics and redaction

- Diagnostics are outside model-visible context.
- Diagnostics are coarse/redacted.
- Diagnostics reflect final post-budget BrainRequest.
- Diagnostics must not contain claim, title, evidence, observation, user input, or secret text.
- Diagnostics must not contain raw IDs, canonical keys, raw skipped reasons, exception text, or traceback.

### Fail-closed and read-only context build

- Compiler failure omits compiled memory.
- Compiler failure does not leak exception details.
- Context build remains read-only.
- No usage ledger, events, or timestamp writes during context build.
- Future work must not change casually: enablement precedence, prompt-visible fields, governance exclusions, diagnostics redaction, fail-closed behavior, or read-only context build behavior.

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

`MEMORY-CONTEXT-OBSERVE-01` is implemented. `ContextBuildResult` includes `CompiledMemoryDiagnostics` and exposes coarse fields only.

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

- Keep env, public API, panel, user-facing, and global production enablement out until scoped future tasks define them.
- Treat optional env enablement, optional internal API enablement, optional panel toggle, production rollout plan, and any observability dashboard as separate tasks.
- Add usage ledger/audit events for memory selection.
- Add production telemetry or dashboards only after a scoped observability task.
- Add panel UX only after a scoped panel task.
