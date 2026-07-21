# DAN Current State

> ## ⚠ HISTORICAL SNAPSHOT — DO NOT READ THIS AS CURRENT BEHAVIOUR
>
> The file name lies. This is a frozen Memory OS handoff, **superseded on
> 2026-07-18 by Release 1**. Nothing below describes how the running daemon
> behaves today.
>
> - Snapshot branch: `rescue/audt-gpt5.5pro-limit-cdn`, HEAD `58cca12 docs: finalize Memory OS rollout handoff`.
> - Live branch today: `agent/dan-release1-integration`.
> - Current truth: `docs/STATUS.md` ("Release 1 cutover"), `AGENTS.md`,
>   `docs/PROJECT_RULES.md`, `docs/SECURITY_MODEL.md`, `docs/CO-JEST-GDZIE.md`.
>
> On 2026-07-21 the false security claims in this file were corrected or deleted
> in place: Release 1 executes tools directly, so there is no approval gate, no
> source-sensitivity and no policy-level blocking. See `dan/tools/permissions.py`
> and `dan/tools/registry.py`.

Public reference: `https://github.com/n1ozzy/jarvis` public `main`, used only as a secondary reference.

## Overview

DAN is a local, single-user runtime centered on `dand`. The daemon owns durable truth: conversations, turns, events, memory, approvals, tool runs, worker jobs, voice queue, listening leases, settings, and runtime state. UI surfaces such as the panel or cockpit are clients only.

At the time of this snapshot the branch under review was `rescue/audt-gpt5.5pro-limit-cdn`, which carried the Memory OS rollout line after the v4.2 runtime work. Work moved to `agent/dan-release1-integration` on 2026-07-18.

## Repository status (snapshot, 2026-07-18 and earlier)

- Branch: `rescue/audt-gpt5.5pro-limit-cdn`
- HEAD: `58cca12`
- HEAD commit: `docs: finalize Memory OS rollout handoff`
- Current committed state: Memory OS compiled-memory rollout safety workstream is complete through policy docs, docs status refresh, session/profile scoped enablement, kill switch, rollout precedence matrix tests, and final handoff docs.
- Public repo `main`: secondary reference only.
- Local branch checkout is the source of truth for this package.

## Completed runtime milestones

- Runtime contracts and repo scaffold exist.
- SQLite schema, migrations, event store, and event bus exist.
- Runtime state machine exists.
- HTTP daemon API exists.
- Runtime supervisor endpoints exist.
- Brain adapter interface exists.
- DAN-owned context builder exists.
- Conversation and turn repositories exist.
- Text turn pipeline exists.
- CLI text input exists.
- Conversation history API exists.
- Tool registry exists. The approval flow described here is NOT in the Release 1
  execution path — `ToolRegistry.request_tool()` executes immediately.
- Model tool request capture and provider tool-call parsing exist.
- Static cockpit / panel client infrastructure exists.
- WebSocket event streaming exists.
- Read-only file, UI, screen, terminal, and accessibility tools exist. Each tool
  enforces its own guards (approved roots, command allowlist, scrubbed env);
  there is no central permission policy gating them.
- Voice queue, listening leases, recorder/STT/TTS plumbing, anti-echo, and broker architecture exist.
- Menu-bar panel and global PTT work exist.
- Security hardening tasks from FIXME are partially or substantially closed.

## Completed Memory OS milestones

- `MEMORY-DESIGN-01`: Memory OS contract.
- `MEMORY-REALITY-00`: current memory behavior characterized.
- `MEMORY-SCHEMA-DESIGN-01`: Memory OS data model design.
- `MEMORY-SCHEMA-01`: additive Memory OS schema foundation.
- `MEMORY-INBOX-01`: memory candidate inbox.
- `MEMORY-EVIDENCE-01`: memory evidence ledger.
- `MEMORY-ACTIVATE-01`: approval/activation into `memory_items`.
- `MEMORY-SAVE-V2-01`: `memory_save` routed through Memory OS candidate flow.
- `MEMORY-COMPILER-DESIGN-01`: MemoryCompiler contract.
- `MEMORY-COMPILER-01`: deterministic MemoryCompiler.
- `MEMORY-COMPILER-EVAL-01`: golden compiler scenarios.
- `MEMORY-COMPILER-PREVIEW-API-HARDENING-01`: hardened compile preview API.
- `MEMORY-COMPILER-WIRE-01`: ContextBuilder compiled-memory wiring behind default-off flag.
- `MEMORY-COMPILER-RUNTIME-01`: runtime dependency wiring, still default-off.
- `MEMORY-CONTEXT-SNAPSHOT-01`: final BrainRequest/context shape tests.
- `MEMORY-CONTEXT-GOVERNANCE-01`: final-context governance tests.
- `MEMORY-CONTEXT-OBSERVE-01`: safe compiled-memory context diagnostics, committed at `bd18d3b`.
- `MEMORY-CONTEXT-POLICY-01`: formal compiled memory context policy docs and contract tests.
- `MEMORY-CONTEXT-ROLLOUT-READINESS-01`: read-only readiness audit completed with focused validation 176 passed, memory/context regression 426 passed, no files changed, and no commit made.
- `MEMORY-CONTEXT-DOCS-STATUS-REFRESH-01`: authoritative docs status refresh after readiness audit, committed at `22c90d6`.
- `MEMORY-CONTEXT-ENABLE-SESSION-01`: session/profile scoped compiled-memory enablement, committed at `6c05474`.
- Compiled-memory force-disable / kill switch, committed at `5e56d1d`.
- Rollout precedence matrix tests for compiled-memory enablement, committed at `802f6e8`.
- Config-based dev/local compiled memory enablement exists while default-off.
- Request-scoped override support exists for one request at a time.

## Current workstream

The active workstream is `MEMORY-OS-FINAL-HANDOFF-01`: docs-only final handoff after the compiled-memory rollout safety workstream.

Runtime/config/ContextBuilder/test work is complete through session/profile scoped enablement, compiled-memory force-disable, and rollout precedence matrix coverage. This handoff is documentation-only and does not change runtime behavior.

## Implemented capabilities

### Core runtime

- `dand` daemon startup through scripts and package entry point.
- Local API routes for health, runtime, state, settings, history, input, brain, tools, approvals, workers, memory, audio, and voice.
- Event store and event bus.
- Conversation and turn persistence.
- Text turn orchestration.
- Stateless brain adapters and adapter selection.
- Tool registry and tool run recorder. `ApprovalGate` and `ToolPermissionPolicy`
  are still importable, but neither gates execution any more.
- Local transport token hardening for private data reads and mutating endpoints
  (header `X-DAN-Token`, token file `~/.dan/runtime/api-token`). This one is real
  and still enforced in `dan/daemon/lifecycle.py`.
- File, shell, terminal, screen, UI, and memory tools; each enforces its own
  boundaries.
- Voice components for queue, broker, chunking, listening leases, recorder, STT, TTS, anti-echo, and cancellation.
- Panel/cockpit assets and menu-bar app client.
- Launchd assets are manual; no automatic install by default.

### Memory

- Legacy `memory_blocks` remain present and preserved.
- Memory OS tables and repositories exist.
- Candidate/evidence/approval/activation flow exists.
- Model-originated `memory_save` creates approval-gated candidates rather than hidden active memory.
- `memory_items` exist as lifecycle-managed memory records.
- MemoryCompiler selects prompt-eligible memory deterministically.
- Preview API exposes compiler results for inspection.
- ContextBuilder can include compiled memory only when explicitly enabled.
- Runtime can wire compiler dependencies, still default-off.
- Config-based dev/local enablement exists and can enable compiled memory only when `memory.enabled=true`.
- Session/profile scoped enablement exists and is internal-only.
- Request-scoped override support exists and is internal-only; override True or False applies to one request and must not mutate builder/runtime state.
- Compiled-memory force-disable / kill switch exists and overrides config, session/profile, and request enablement.
- Final-context tests cover safe shape, governance exclusions, and fail-closed behavior.
- Context-build diagnostics expose only coarse compiled-memory status and counts; they must not include raw memory content, IDs, user input, exception text, or traceback.

## Compiled memory runtime guarantees

- Compiled memory remains default-off.
- `[memory].enabled=false` is an absolute compiled-memory disable.
- `compiled_memory_force_disabled` disables compiled memory regardless of config, session/profile, or request override.
- Config dev/local compiled context gate exists and remains explicit.
- Session/profile scoped enablement exists, is internal-only, and does not add a public toggle.
- Request-scoped override exists, is internal-only, and applies to one request.
- Request override False disables compiled memory for one request.
- Request override True cannot bypass the kill switch or `[memory].enabled=false`.
- Empty session/profile allow-list enables zero sessions and does not globally leak.
- `None` allow-list preserves established global config behavior.
- Final `BrainRequest.context_messages` remain prompt-safe.
- Diagnostics remain outside model-visible context and stay redacted/coarse.
- Compiler failure fails closed by omitting compiled memory.
- Context build remains read-only.

## Default-off capabilities

- Compiled memory remains default-off.
- Runtime dependency wiring does not equal global enablement.
- Config-based dev/local enablement exists, but config defaults keep compiled memory off and `[memory].enabled=false` blocks it.
- Session/profile scoped enablement exists and request-scoped override support exists; both are internal-only wiring and do not persist a public user setting.
- Snapshot claim: No env, panel, public API, user-facing, or global production
  enablement exists for compiled memory. **Corrected 2026-07-21:** operator env
  controls DO exist — `DAN_COMPILED_MEMORY_ENABLED` and
  `DAN_COMPILED_MEMORY_FORCE_DISABLED` are read in
  `create_daemon_app_from_config` (`dan/daemon/app.py`). The rest of the claim
  still holds: no panel, public API, user-facing or global production
  enablement exists.
- Provider CLI adapters default to `enabled = false` in `dan/config.py`, but
  `config/dan.example.toml` ships `[brain.claude_cli] enabled = true`.
- Voice is ENABLED in `config/dan.example.toml` (`[voice] enabled = true`,
  `mastering_profile = "bastard"`); only the `dan/config.py` dataclass default is off.
- Launchd auto-install is disabled by default.
- Destructive tools are ENABLED by default in both `dan/config.py`
  (`destructive_tools_enabled = True`) and `config/dan.example.toml`. Since
  Release 1 the flag is inert anyway — the policy allows every risk class.
- Network access through tools requires no approval:
  `require_approval_for_network = false` everywhere, and the policy never blocks.

## Not implemented yet

- Global compiled memory enablement.
- Panel toggle for compiled memory.
- Public API/user-facing compiled memory toggle.
- Usage ledger for compiled memory selection events.
- Topic documents runtime.
- Automatic background memory summarization/consolidation.
- Global production rollout plan.
- Observability dashboard beyond the current coarse compiled-memory diagnostics.

## Deferred or backlog areas

- Voice clone / G5.
- Real Claude/Codex background workers as authoritative runtime workers.
- OpenAI adapter production path.
- WebView bridge completion.
- Panel UX redesign beyond current cockpit/menu-bar shell.
- Packaging/runtime ergonomics beyond existing scripts and launchd assets.

## Safety posture

Safety posture is permissive by design (Ozzy's decree, owner-controlled localhost
runtime): destructive tools and network enabled, `auto_approve_mode = "all"`, no
approval click for any tool class. What still refuses work lives inside the tools
themselves — approved-root containment, the `shell_read` allowlist, the scrubbed
environment, git hardening and the runtime/output bounds.

- DAN-owned state is assembled from DB/config/request data.
- Provider sessions are not memory.
- Model-originated tool calls execute directly. There is no permission gate and
  no approval step between the model and the tool.
- Model-originated memory writes become candidates, not hidden active facts.
- MemoryCompiler is deterministic and read-only during context build.
- Unsafe, invalid, stale, and non-context-safe memory must not reach final prompt context.
- Compiler failure fails closed by omitting compiled memory.
- Raw evidence, raw observations, raw IDs, canonical keys, skipped items, audit metadata, and secrets must not be prompt-visible.

## Immediate next steps

1. Keep compiled memory default-off.
2. Keep env, public API, panel, user-facing, and global production enablement future-scoped.
3. Treat optional env enablement, optional internal API enablement, optional panel toggle, production rollout plan, and any observability dashboard as separate tasks.
4. Do not bypass governance exclusions or expose raw evidence, IDs, secrets, diagnostics internals, or compiler internals to the model.

## Operational rules

- One task at a time.
- One commit per task.
- No commit before clean review.
- Every task must define allowed and forbidden files.
- No schema/migration/API/config/CI/docs changes unless explicitly scoped.
- No “while here” refactors.
- For Memory OS prompt behavior, use `Effort: xhigh` and `Fast: off`.
- Tests must prove behavior at the final output boundary when prompt visibility is involved.
