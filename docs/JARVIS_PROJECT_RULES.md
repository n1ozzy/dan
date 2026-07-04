# Jarvis Project Rules

Classification: authoritative project rules proposed for this branch.
This document is stricter than a roadmap. It defines how work must be done.

## Core workflow

- One task at a time.
- One scope per task.
- One commit per task.
- Prompt first.
- Implementation second.
- Review third.
- Commit only after clean review.
- Stop before commit unless explicitly told to commit.

## Task format

Every implementation task must include:

- Task ID.
- Effort.
- Fast setting.
- Reason for effort/Fast.
- Current state.
- Goal.
- Hard rules.
- Allowed files.
- Forbidden files.
- Implementation requirements.
- Required tests.
- Verification commands.
- Report requirements.

Every task must also have:

- Review prompt.
- Commit command.
- Guard commands.

## Scope control

Never mix unrelated work.

Examples of forbidden mixing:

- Memory task plus voice cleanup.
- Runtime task plus docs rewrite.
- Tests-only task plus production refactor.
- API task plus schema changes not explicitly scoped.
- Panel task plus provider changes.
- Config task plus behavior changes not explicitly scoped.

“No broad refactors” means no broad refactors. Human creativity is adorable, but not here.

## Commit rules

- Do not commit before clean review.
- Commit only files changed for the task.
- Do not `git add` unrelated allowed files just because they were listed.
- Commit messages should identify type and scope.
- After commit, run:

```sh
git status --short --branch
git rev-parse --short HEAD
```

## Review rules

Review must check the current uncommitted diff only.

Review must report:

- Verdict: CLEAN or FINDINGS.
- Changed files reviewed.
- Behavior proven.
- Tests reviewed.
- Guards reviewed.
- Exact findings with severity and file/line references.

Review must not request unrelated refactors or future work.

## Testing rules

- Bugfixes should add or update regression tests when practical.
- Prompt-visible behavior must be tested at final output boundary.
- ContextBuilder changes must protect final `BrainRequest` shape.
- MemoryCompiler changes must have deterministic tests.
- Tool permission changes must prove allow/approval/block behavior.
- Security redaction changes must test raw secret markers.
- Live voice/mic/speaker/provider/launchctl behavior belongs in manual runbooks, not default CI.

## Fast mode rules

Use `Fast: off` for:

- ContextBuilder production behavior.
- API/security/transport policy.
- schema/migrations.
- MemoryCompiler selection/governance.
- runtime/daemon behavior.
- approval/tool execution policy.
- provider adapters.
- voice live/runtime behavior.
- P0/P1 safety fixes.

`Fast: on` is acceptable only for:

- docs-only work.
- tests-only work.
- read-only analysis.
- trivial non-behavioral cleanup explicitly scoped.

## Default-off policy

New behavior that can influence model output, tools, voice, memory, runtime state, or user data must start default-off unless the task explicitly says otherwise.

Default-off means:

- no global enablement;
- no implicit config flip;
- no user-facing switch added casually;
- no hidden runtime behavior change.

## Fail-closed policy

Safety-sensitive failures must fail closed.

Examples:

- compiler failure omits compiled memory;
- tool policy uncertainty blocks or requests approval;
- secret redaction failure must not expose raw secrets;
- root containment uncertainty blocks access;
- voice anti-echo uncertainty must not create a false user turn.

## Documentation rules

- Docs-only tasks must not touch code or tests.
- Code tasks must not opportunistically rewrite docs.
- Docs must distinguish implemented, planned, deferred, and unknown.
- Old handoffs cannot override current `AGENTS.md`, `PROJECT_RULES`, or `STATUS`.
- Examples are not roadmap commitments.

## Safety rules

- No raw secrets in prompt, logs, diagnostics, event payloads, or persisted tool output.
- No raw evidence/observation in prompt-visible memory.
- No writes during context build.
- Provider sessions are not Jarvis memory.
- Workers cannot commit facts or speak.
- Panel cannot own canonical state.
- `/tmp` cannot be source of truth.

## What never changes casually

- `jarvis/store/schema.sql`
- `jarvis/store/migrations.py`
- transport security in `jarvis/daemon/lifecycle.py`
- permission policy in `jarvis/tools/permissions.py`
- approval execution path in `jarvis/tools/registry.py`
- ContextBuilder prompt-visible output
- MemoryCompiler governance logic
- secret redaction behavior
- voice broker sole-speaker contract
- daemon ownership model
- provider statelessness
