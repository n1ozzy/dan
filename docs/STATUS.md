# Status

Classification: current.

## Git Snapshot

- Branch: `rescue/audit-8a5a0f0`
- HEAD: `1411a16`
- Final guardrail status for this task: only docs, guardrail tests, `AGENTS.md`,
  and optional CI files are expected to change.

## Current Status

- core tests green
- tools/approvals green
- daemon/security/db green
- voice unit/mock tests green
- real live voice still requires manual validation
- Memory OS is design-only: contract/architecture docs define future memory
  layers, evidence, approvals, retrieval audit, and topic documents, but
  auto-memory extraction, topic documents, dedupe, MemoryCompiler, and memory
  audit UI are not implemented.

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
