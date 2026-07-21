# Memory OS Architecture

Classification: technical architecture — current behaviour.
Scope: re-verified 2026-07-21 against the source on `agent/dan-release1-integration`: `dan/memory/`, `dan/brain/context_builder.py`, `dan/tools/memory_tool.py`, `dan/api/routes_memory.py`, `dan/daemon/app.py`, `dan/config.py`, `dan/store/schema.sql`.

Rollout snapshot: the compiled-memory safety workstream (policy, scoped enablement, force-disable, precedence matrix tests) is complete. Operator env controls and a read-only preview API have landed since. Panel and user-facing enablement remain unbuilt.

## Purpose

Memory OS makes DAN memory explicit, reviewable, evidence-backed, and safe to use in prompt context. It replaces hidden or accidental memory behavior with a lifecycle model.

The design rejects silent model-originated memory activation. **The runtime does not currently honour that design**: the `memory_save` tool creates its own candidate, approves it and activates it in one call. See "Approval and activation".

## Data lifecycle

The flow, with the actor for each arrow:

```text
memory_save call OR POST /memory/candidates
→ memory_candidate            (status needs_review)
→ memory_evidence             (+ memory_observations row)
→ approval or rejection       (memory_save approves its own candidate;
                               the API leaves it to the caller)
→ activation                  (evidence required, canonical_key dedupes)
→ memory_item                 (status active — and it stays active forever)
→ MemoryCompiler              (only when compiled memory is enabled)
→ ContextBuilder
→ BrainRequest/context_messages
```

Nothing feeds this pipeline automatically: there is no observation harvesting from turns and no candidate extraction from conversation text.

Legacy `memory_blocks` remain present and are still injected by ContextBuilder on every turn, independently of compiled memory. Memory OS does not casually replace them.

## Storage model

Tables in `dan/store/schema.sql`, with what actually reads or writes them:

- `memory_blocks` — `MemoryManager`; injected into every turn.
- `memory_observations` — one row per evidence attachment.
- `memory_candidates` — inbox and `memory_save`.
- `memory_items` — written by `activate_candidate`, read by the compiler.
- `memory_evidence` — written by the evidence repository.
- `memory_topics`, `memory_usage_events`, `memory_review_decisions` — declared, never written or read.
- `memory_archive_documents`, `memory_archive_sync_state`, `memory_archive_fts` — separate archive/recall surface (`dan/memory/archive.py`), not part of the compiler path.

Related tables include `events`, `settings`, `conversations`, and `turns`.

## Evidence and provenance

Memory must be evidence-backed before it can be prompt-eligible: the compiler skips any item with `evidence_count < 1`, and `activate_candidate` refuses an approved candidate that has no evidence. `add_evidence` requires at least one locator (`source_id`, `conversation_id`, `turn_id`, `event_id` or `quote`).

Prompt-visible compiled memory does not expose raw evidence quotes — only `title`, `claim` and `evidence_count` are rendered.

## Approval and activation

The candidate → evidence → approval → activation sequence is the only way a `memory_items` row is created, and `activate_candidate` enforces it: approved status required, evidence required, `canonical_key` collision reuses the existing item.

**The approval step is not a human gate.** `MemorySaveTool.run` calls `approve_candidate` on the candidate it just created and then activates it, so a `memory_save` from the model produces active durable memory in one tool call. `DaemonApp.request_tool` discards the request source and calls `ToolRegistry.execute_tool` directly; `ToolRegistry.request_tool` ignores its `approval_gate` argument; the `/approvals` HTTP routes return 404 (`tests/test_no_approval_surface.py`). The `MemorySaveTool.propose` method — candidate and evidence without activation — still exists but is not on any live path.

There is no reverse operation. `MemoryItemRepository` exposes only `activate_candidate`, `get_item`, `list_items` and `list_items_for_compiler`; nothing can disable, supersede or forget an item once it is active.

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
- evidence count through joined evidence (computed at compile time, not stored)

Only active, eligible, evidence-backed items are selectable. In practice
`status` is only ever written as `active`, and `supersedes` / `superseded_by`
are never written at all — the other lifecycle values are recognised by the
compiler but unreachable through any supported operation.

## Compiler architecture

`dan/memory/compiler.py` defines:

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
- warnings (always an empty list; no warning is ever produced)

Those internal fields are not automatically prompt-visible. ContextBuilder renders only safe fields.

## Selected memory model

Selected memory may include internal fields such as `memory_id`, `canonical_key`, `source_policy`, `sensitivity` and `budget_cost`. These fields are useful for audit but forbidden in final prompt text. `memory_id` is itself a projection — `mem_ref_<sha256 of the row id>` — so the database id never leaves the compiler.

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

Procedural memory is excluded by default (`include_procedural=false`). Opting in puts procedural items in the same flat list as semantic ones — there is no separate section.

In practice no procedural item can be created through a supported path: `memory_save` validates `kind` against `MEMORY_KINDS` (identity, user_preference, project, fact, summary, temporary), which does not contain `procedural`. Only the candidate API, which accepts a free-text `candidate_kind`, can produce one.

Safety-relevant procedural behavior should be enforced by code and tests, not by memory.

## ContextBuilder integration

`ContextBuilder` accepts optional memory compiler dependencies, explicit enablement gates, session-scoped internal enablement, request-scoped internal override, and a force-disable kill switch.

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

`create_daemon_app_from_config` builds the compiler and hands it plus every gate to the daemon's `ContextBuilder`. Wiring is not enablement.

**Default-off means the shipped defaults, not necessarily the running daemon.** `MemoryConfig.compiled_context_enabled` defaults to `False` (`dan/config.py`) and `config/dan.example.toml` ships `compiled_context_enabled = false`. But the live config is `~/.dan/config.toml`, which is not in the repo and can set it `true` — and on this machine it does. Read the live file before asserting that compiled memory is off on a given install; `[memory].enabled` must also be true for it to run.

Operator env controls exist and are read at daemon build time (`compiled_memory_operator_env_controls` in `dan/config.py`):

- `DAN_COMPILED_MEMORY_ENABLED` — overrides `compiled_context_enabled`. An unparseable value is treated as false.
- `DAN_COMPILED_MEMORY_FORCE_DISABLED` — sets the kill switch. An unparseable value is treated as TRUE, so a typo fails closed.

Session-scoped enablement exists and is internal-only. The allow-list is typed as `(session_id, persona_profile)` pairs, but per-profile personas were removed: `_build_settings` pins `persona.profile` to `DEFAULT_PERSONA_PROFILE = "dan"` no matter what a request asks for, so an entry naming any other profile can never match and the conversation id is effectively the whole key. An empty allow-list enables zero sessions; a `None` allow-list leaves global config behaviour untouched.

Request-scoped internal override support exists for one request at a time. Request override False disables one request. Request override True cannot bypass `[memory].enabled=false` or `compiled_memory_force_disabled`. Overrides do not mutate builder or runtime state — a compiler created lazily under an override is deliberately not cached.

Not built: panel toggle, public enablement API, and any user-facing switch. There IS a public read-only compiler route, `POST /memory/compile-preview` (`dan/api/routes_memory.py`); it constructs its own compiler and cannot change what any turn sees.

## Compiled memory context policy

This section is the formal rollout and safety contract for compiled memory in prompt context. Future work must preserve it unless a task explicitly scopes a policy change and updates the contract tests.

### Enablement precedence

Ratified policy text, restated here as the contract future work must preserve.
Where the runtime has moved since it was ratified, the correction is inline:

- The global default is off. That is the shipped default (`dan/config.py`, `config/dan.example.toml`); the live `~/.dan/config.toml` may say otherwise — see "Runtime integration".
- Config dev/local enablement can enable compiled memory when `[memory].enabled=true`.
- `[memory].enabled=false` is an absolute compiled-memory disable.
- `compiled_memory_force_disabled` disables compiled memory regardless of config, session/profile, or request override.
- Session/profile scoped enablement exists and is internal-only. The profile half of the key is inert: `_build_settings` pins `persona.profile` to `DEFAULT_PERSONA_PROFILE = "dan"`, so only the conversation id can ever match.
- Empty session/profile allow-list enables zero sessions and does not globally leak.
- `None` allow-list preserves established global config behavior.
- Request-scoped override True can enable compiled memory for one request only when `[memory].enabled=true` and the kill switch is off.
- Request-scoped override False disables compiled memory for one request.
- Request-scoped override must not mutate builder/runtime state. A compiler created lazily under an override is deliberately not cached.
- No env, panel, public API, user-facing, or global production enablement exists yet. **Out of date since the policy was ratified:** the two operator env controls shipped (`DAN_COMPILED_MEMORY_ENABLED`, `DAN_COMPILED_MEMORY_FORCE_DISABLED`, see "Runtime integration"). Panel, public API, user-facing and global production enablement genuinely do not exist.

Evaluated in this order by `ContextBuilder._resolve_compiled_memory_enabled`:

1. `[memory].enabled=false` — absolute disable, nothing overrides it.
2. `compiled_memory_force_disabled` — kill switch; beats config, scope and request override. Settable from config wiring or `DAN_COMPILED_MEMORY_FORCE_DISABLED`.
3. Request-scoped override, if not `None` — True enables, False disables, for one request.
4. Global enablement (`memory.compiled_context_enabled`, or `DAN_COMPILED_MEMORY_ENABLED`).
5. Otherwise the scope gate: enabled only if `(conversation_id, "dan")` is in the allow-list.

Notes:
- The shipped default is off; the live `~/.dan/config.toml` may say otherwise.
- Empty allow-list enables zero sessions and does not globally leak.
- `None` allow-list leaves global config behaviour unchanged.
- Request-scoped override does not mutate builder/runtime state.
- Env enablement exists. Panel, user-facing and global production enablement do not.

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

Fields: `compiled_memory_enabled`, `compiler_available`, `compiled_memory_attempted`, `compiled_memory_section_present`, `selected_count`, `skipped_count`, `fail_closed`, `failure_category` (only ever `"compiler_error"`), and `skipped_categories` — counts bucketed against a fixed allow-list of reasons, with anything else counted as `other`. They are returned on `ContextBuildResult` and not persisted anywhere.

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

- Keep panel, user-facing, and global production enablement out until scoped future tasks define them. (Env enablement is no longer future — it shipped; see "Runtime integration".)
- Give `memory_items` a lifecycle after activation: disable, supersede, forget. Today the compiler honours those statuses and nothing can set them.
- Decide whether `memory_save` should regain a human step, or whether the contract in `docs/MEMORY_CONTRACT.md` should be rewritten to match one-call activation. The two currently disagree.
- Add usage ledger/audit events for memory selection (`memory_usage_events` is an empty table).
- Add production telemetry or dashboards only after a scoped observability task.
- Add panel UX only after a scoped panel task.
