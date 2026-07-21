# DAN Project Rules

Classification: authoritative project rules for this branch
(`agent/dan-release1-integration`). Re-verified against the code 2026-07-21.
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
- Tool containment changes must prove the refusal happens: approved-root and
  symlink-escape refusals, the `shell_read` allowlist, the scrubbed env, the git
  hardening, and the size/timeout bounds. Do NOT write tests that assert an
  approval or a policy-level block — `ToolPermissionPolicy.decide()` returns
  ALLOW unconditionally and `ToolRegistry.request_tool()` executes immediately.
  Equally, do NOT write a test asserting that `security.shell_read_unrestricted`
  drops only the allowlist. It does not, and pinning that claim would freeze a
  false guarantee into the suite — see `docs/SECURITY_MODEL.md` §2.
- Security redaction changes must test raw secret markers.
- Live voice/mic/speaker/provider/launchctl behavior belongs in manual runbooks, not default CI. Tests MUST mock the TTS layer — never spawn a real afplay/supertonic.

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

Compiled memory context policy also requires:

- `[memory].enabled=false` blocks compiled memory absolutely;
- `compiled_memory_force_disabled` blocks compiled memory regardless of config, session/profile, or request override;
- config dev/local enablement stays explicit and default-off;
- session/profile scoped enablement stays internal-only;
- empty session/profile allow-list enables zero sessions and does not globally leak;
- `None` allow-list preserves established global config behavior;
- request-scoped overrides do not persist or mutate builder/runtime state;
- request override False disables one request;
- request override True cannot bypass `[memory].enabled=false` or the kill switch;
- env, panel, public API, user-facing, and global production enablement require their own scoped task.

## Fail-closed policy

Safety-sensitive failures must fail closed.

Examples:

- compiler failure omits compiled memory;
- root containment uncertainty blocks access — empty `approved_roots` refuses
  everything, and paths are `realpath`-resolved before the containment test, so
  a symlink out of a root is refused;
- an unrecognised `shell_read` command is refused (unless the operator has
  explicitly set `security.shell_read_unrestricted = true` — what that opt-out
  actually costs is in `docs/SECURITY_MODEL.md` §2, and it is more than the
  allowlist);
- a missing or invalid persona canon (`config/persona/DAN.md`, header
  `DAN_CANON_VERSION: 1`) is a visible error, never a silent fallback;
- secret redaction failure must not expose raw secrets;
- voice anti-echo uncertainty must not create a false user turn.

Fail-closed does **not** mean "ask for approval". There is no approval path in
the tool layer on this branch: uncertainty must make the *tool itself* refuse.

## Documentation rules

- Docs-only tasks must not touch code or tests.
- Code tasks must not opportunistically rewrite docs.
- Docs must distinguish implemented, planned, deferred, and unknown.
- Old handoffs cannot override current `AGENTS.md`, `PROJECT_RULES`, or `STATUS`.
- Examples are not roadmap commitments.

## Safety rules

- No raw secrets in prompt, logs, diagnostics, event payloads, or persisted tool output.
- No raw evidence, observations, IDs, skipped items, diagnostics internals, compiler internals, or secrets in prompt-visible memory.
- No writes during context build.
- No bypassing MemoryCompiler governance exclusions.
- Provider sessions are not DAN memory.
- Workers cannot commit facts or speak.
- Panel cannot own canonical state.
- `/tmp` cannot be source of truth.

## What never changes casually

- `dan/store/schema.sql`
- `dan/store/migrations.py`
- transport security in `dan/daemon/lifecycle.py`
- the tool-internal containment: approved roots and the symlink/realpath check
  (`dan/tools/file_tool.py`), the `shell_read` allowlist plus scrubbed env and
  git hardening (`dan/tools/shell_tool.py`), the secure-text-field and
  control-character bans (`dan/tools/ui_tool.py`, `dan/tools/terminal_tool.py`)
- the direct execution path in `dan/tools/registry.py` — do not reintroduce an
  approval gate into it
- the config key registry `dan/config_registry.py`
- the persona canon `config/persona/DAN.md` and the voice canon
  `config/voice/personas.toml` + `pronunciations.toml`
- ContextBuilder prompt-visible output
- compiled memory enablement precedence
- compiled memory force-disable / kill-switch precedence
- compiled memory diagnostics redaction contract
- compiled memory read-only context build contract
- MemoryCompiler governance logic
- secret redaction behavior
- voice broker sole-speaker contract
- daemon ownership model
- provider statelessness
