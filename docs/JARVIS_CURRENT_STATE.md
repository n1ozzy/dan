# Jarvis Current State

Classification: current handoff.
Source snapshot: branch `rescue/audt-gpt5.5pro-limit-cdn`, HEAD `2aa7eb1` in the local checkout.
Public reference: `https://github.com/n1ozzy/jarvis` public `main`, used only as a secondary reference.

## Overview

Jarvis is a local, single-user runtime centered on `jarvisd`. The daemon owns durable truth: conversations, turns, events, memory, approvals, tool runs, worker jobs, voice queue, listening leases, settings, and runtime state. UI surfaces such as the panel or cockpit are clients only.

The active local branch is not public `main`. The branch under review is `rescue/audt-gpt5.5pro-limit-cdn`. The branch contains the Memory OS rescue/audit line after the v4.2 runtime work.

## Repository status

- Branch: `rescue/audt-gpt5.5pro-limit-cdn`
- HEAD: `2aa7eb1`
- HEAD commit: `feat: add request-scoped compiled memory override`
- Current uncommitted work: `MEMORY-CONTEXT-POLICY-01` docs and contract tests only.
- Public repo `main`: secondary reference only.
- Local branch checkout is the source of truth for this package.

## Completed runtime milestones

- Runtime contracts and repo scaffold exist.
- SQLite schema, migrations, event store, and event bus exist.
- Runtime state machine exists.
- HTTP daemon API exists.
- Runtime supervisor endpoints exist.
- Brain adapter interface exists.
- Jarvis-owned context builder exists.
- Conversation and turn repositories exist.
- Text turn pipeline exists.
- CLI text input exists.
- Conversation history API exists.
- Tool registry and approval flow exist.
- Approved tool execution exists.
- Model tool request capture and provider tool-call parsing exist.
- Static cockpit / panel client infrastructure exists.
- WebSocket event streaming exists.
- Source-sensitive tool permissions exist.
- Read-only file, UI, screen, terminal, and accessibility tools exist behind policy.
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
- Config-based dev/local compiled memory enablement exists while default-off.
- Request-scoped override support exists for one request at a time.

## Current workstream

The active workstream is `MEMORY-CONTEXT-POLICY-01`: formal compiled memory context policy docs and contract tests.

`MEMORY-CONTEXT-OBSERVE-01` is implemented at `bd18d3b`. It adds a safe, read-only diagnostics surface for the compiled memory context path without making diagnostics prompt-visible. The later `2aa7eb1` state adds request-scoped override support without making compiled memory globally or user-facing enabled.

## Implemented capabilities

### Core runtime

- `jarvisd` daemon startup through scripts and package entry point.
- Local API routes for health, runtime, state, settings, history, input, brain, tools, approvals, workers, memory, audio, and voice.
- Event store and event bus.
- Conversation and turn persistence.
- Text turn orchestration.
- Stateless brain adapters and adapter selection.
- Tool registry, approval gate, permission policy, and tool run recorder.
- Local transport token hardening for private data reads and mutating endpoints.
- Source-sensitive permission model.
- File, shell, terminal, screen, UI, and memory tools with policy boundaries.
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
- Request-scoped override support exists; override True or False applies to one request and must not mutate builder/runtime state.
- Final-context tests cover safe shape, governance exclusions, and fail-closed behavior.
- Context-build diagnostics expose only coarse compiled-memory status and counts; they must not include raw memory content, IDs, user input, exception text, or traceback.

## Default-off capabilities

- Compiled memory remains default-off.
- Runtime dependency wiring does not equal global enablement.
- Config-based dev/local enablement exists, but config defaults keep compiled memory off and `memory.enabled=false` blocks it.
- Request-scoped override support exists, but it is one-request internal wiring and does not persist.
- No env, panel, API, or user-facing enablement exists for compiled memory.
- Provider CLI adapters are configured disabled by default unless enabled in config.
- Voice is configured disabled by default in `config/jarvis.example.toml`.
- Launchd auto-install is disabled by default.
- Destructive tools are disabled by default.
- Network access through tools requires explicit approval policy and is not casually enabled.

## Not implemented yet

- Global compiled memory enablement.
- Env-based compiled memory enablement.
- Panel/API/user-facing compiled memory switch.
- Broader session/profile-scoped compiled memory enablement beyond the request-scoped internal override.
- Usage ledger for compiled memory selection events.
- Topic documents runtime.
- Automatic background memory summarization/consolidation.
- Production telemetry beyond the current coarse compiled-memory diagnostics.

## Deferred or backlog areas

- Voice clone / G5.
- Real Claude/Codex background workers as authoritative runtime workers.
- OpenAI adapter production path.
- WebView bridge completion.
- Panel UX redesign beyond current cockpit/menu-bar shell.
- Packaging/runtime ergonomics beyond existing scripts and launchd assets.

## Safety posture

Current safety posture is conservative.

- Jarvis-owned state is assembled from DB/config/request data.
- Provider sessions are not memory.
- Model-originated tool calls pass through permission policy and approval.
- Model-originated memory writes become candidates, not hidden active facts.
- MemoryCompiler is deterministic and read-only during context build.
- Unsafe, invalid, stale, and non-context-safe memory must not reach final prompt context.
- Compiler failure fails closed by omitting compiled memory.
- Raw evidence, raw observations, raw IDs, canonical keys, skipped items, audit metadata, and secrets must not be prompt-visible.

## Immediate next steps

1. Review and commit `MEMORY-CONTEXT-POLICY-01`.
2. Keep compiled memory default-off.
3. Keep env/panel/API/user-facing enablement future-scoped.
4. Add broader runtime smoke only when the next scoped enablement task requires it.

## Operational rules

- One task at a time.
- One commit per task.
- No commit before clean review.
- Every task must define allowed and forbidden files.
- No schema/migration/API/config/CI/docs changes unless explicitly scoped.
- No “while here” refactors.
- For Memory OS prompt behavior, use `Effort: xhigh` and `Fast: off`.
- Tests must prove behavior at the final output boundary when prompt visibility is involved.
