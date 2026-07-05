# Status

Classification: current.

## Git Snapshot

- Branch: `rescue/audt-gpt5.5pro-limit-cdn`
- HEAD: `58cca12 docs: finalize Memory OS rollout handoff`
- Current final handoff scope: full docs-only Memory OS rollout handoff across
  current state, status, roadmap, architecture, guardrails, and docs package
  metadata. No runtime, code, config, schema, API, panel, provider, voice, or
  env behavior changes.

## Current Status

- core tests green
- tools/approvals green
- daemon/security/db green
- voice unit/mock tests green
- real live voice still requires manual validation
- `MEMORY-CONTEXT-ROLLOUT-READINESS-01` completed as a read-only audit:
  focused validation: 176 passed; memory/context regression: 426 passed; no
  files changed; no commit made.
- Memory OS compiled-memory policy docs, docs status refresh, session/profile
  scoped enablement, kill switch, rollout precedence matrix tests, and final
  handoff docs are committed at the snapshot above.
- Current `memory_blocks` remain preserved legacy infrastructure.
- Auto-memory extraction is not implemented yet.
- No runtime behavior changed by MEMORY-DESIGN-01.
- This checkpoint is the docs-only final handoff after the compiled-memory
  runtime rollout safety workstream.

## Memory OS Guarantees

- Compiled memory remains default-off.
- Config-based dev/local enablement exists.
- Session/profile scoped enablement exists and is internal-only.
- Request-scoped override exists and is internal-only.
- No env, panel, public API, user-facing, or global production enablement exists.
- `[memory].enabled=false` is an absolute compiled-memory disable.
- `compiled_memory_force_disabled` disables compiled memory regardless of config,
  session/profile, or request override.
- Request override False disables one request.
- Request override True cannot bypass the kill switch or `[memory].enabled=false`.
- Empty session/profile allow-list enables zero sessions and does not globally leak.
- `None` allow-list preserves established global config behavior.
- Final BrainRequest output is prompt-safe.
- Diagnostics are redacted and outside model-visible context.
- Compiler failure fails closed.
- Context build remains read-only.
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
