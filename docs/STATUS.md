# Status

Classification: current.

## Git Snapshot

- Branch: `rescue/audt-gpt5.5pro-limit-cdn`
- HEAD: `171fb11 docs: formalize compiled memory context policy`
- Current docs refresh scope: docs/status snapshot metadata only. No runtime,
  code, config, schema, API, panel, provider, voice, or env behavior changes.

## Current Status

- core tests green
- tools/approvals green
- daemon/security/db green
- voice unit/mock tests green
- real live voice still requires manual validation
- `MEMORY-CONTEXT-ROLLOUT-READINESS-01` completed as a read-only audit:
  focused validation: 176 passed; memory/context regression: 426 passed; no
  files changed; no commit made.
- Runtime/tests/policy are ready for the next phase.
- Memory OS compiled-memory policy work is committed at the snapshot above.
- Current `memory_blocks` remain preserved legacy infrastructure.
- Auto-memory extraction is not implemented yet.
- No runtime behavior changed by MEMORY-DESIGN-01.
- The next feature task remains blocked until this docs refresh is committed.
- Next intended task after this docs refresh: `MEMORY-CONTEXT-ENABLE-SESSION-01`.

## Memory OS Guarantees

- Compiled memory remains default-off.
- Config-based dev/local enablement exists.
- Request-scoped override exists and is internal-only.
- No env, panel, API, or user-facing enablement exists.
- `memory.enabled=false` blocks compiled memory.
- Request override True/False are per-request and non-mutating.
- Final BrainRequest output is prompt-safe.
- Diagnostics are redacted and outside model-visible context.
- Compiler failure fails closed.
- Policy docs are protected by contract tests.

These are status labels for the current rescue checkpoint. Fresh test evidence
must come from commands in the current task, not from this file alone.

## Latest Known Rescue Fix

- RESCUE-VOICE-01: ptt_down must not cancel active speech.

This checkout contains a regression-test name for that behavior. The historical
`docs/JARVIS_FIX_TASKS_HANDOFF.md` still describes the older opposite behavior,
so treat that handoff as historical evidence only.

## Guardrail Baseline

- `AGENTS.md`, `docs/PROJECT_RULES.md`, and this file win over old handoffs and
  roadmaps.
- Provider sessions are not Jarvis memory.
- Panel/cockpit clients do not own source-of-truth state.
- Automated CI must stay mock/unit safe and must not run live voice, launchctl,
  provider smoke, or live networked provider behavior.
