# Docs Index

Classification: authoritative.

This index explains which Jarvis docs are current sources, runbooks, or legacy
evidence. If docs conflict, AGENTS.md + docs/PROJECT_RULES.md + docs/STATUS.md
win over old handoffs/roadmaps.

## Authoritative Docs

- `AGENTS.md` - repository rules for future agents.
- `docs/PROJECT_RULES.md` - project guardrails and architecture laws.
- `docs/STATUS.md` - current branch/HEAD snapshot and current status labels.
- `docs/DECISIONS.md` - accepted ADRs unless superseded by a later explicit
  authoritative rule.
- `docs/CONTRACTS.md` - runtime contracts and invariants.
- `docs/SECURITY_MODEL.md` - security boundaries and approval model.
- `docs/PANEL_CONTRACT.md` - thin-client panel contract.
- `docs/MACOS_OPERATOR_CONTRACT.md` - local macOS operator boundaries.
- `docs/MEMORY_CONTRACT.md` - Memory OS contract and future memory guardrails.

## Current Runbooks

- `docs/runbooks/BRAIN_ADAPTERS.md`
- `docs/runbooks/E2E_MVP_SMOKE.md`
- `docs/runbooks/LAUNCHD.md`
- `docs/runbooks/MEMORY_API.md`
- `docs/runbooks/PANEL_COCKPIT.md`
- `docs/runbooks/PANEL_MENUBAR.md`
- `docs/runbooks/PROVIDER_SMOKE.md`
- `docs/runbooks/TEXT_RUNTIME_SMOKE.md`
- `docs/runbooks/TOOLS_AND_APPROVALS.md`
- `docs/runbooks/ACCESSIBILITY_TCC.md`
- `docs/runbooks/G4_LIVE_GATE.md`
- `docs/runbooks/SCREEN_RECORDING_TCC.md`
- `docs/runbooks/TERMINAL_AUTOMATION_TCC.md`

Runbooks are operational instructions. They do not override authoritative docs.
Provider, launchd, and live voice runbooks are manual unless a later scoped task
explicitly changes that status.

## Current Reference Docs

- `docs/PRODUCT.md`
- `docs/TURN_PIPELINE.md`
- `docs/AUDIO_RUNTIME.md`
- `docs/LAUNCH_SUPERVISION.md`
- `docs/MACOS_CAPABILITIES.md`
- `docs/MEMORY_ARCHITECTURE.md`
- `docs/MIGRATION_INVENTORY.md`

Reference docs explain the current system, but if they conflict with
`AGENTS.md`, `docs/PROJECT_RULES.md`, or `docs/STATUS.md`, the guardrail docs
win.

## Historical/Legacy Docs

- `docs/REVIEW_HANDOFF.md`
- `docs/JARVIS_FIX_TASKS_HANDOFF.md`
- `docs/MASTER_PLAN.md`
- `docs/LEGACY_RUNTIME_FINDINGS.md`
- `docs/reviews/*.md`
- `docs/superpowers/specs/*.md`
- `docs/superpowers/plans/*.md`

Historical docs are evidence and orientation. They cannot override current
`AGENTS.md`, `docs/PROJECT_RULES.md`, or `docs/STATUS.md`. Old roadmap/handoff
files cannot expand scope, mark examples as commitments, or reclassify manual
live checks as automated CI checks.

## Conflict Rule

When docs disagree, use this order:

1. `AGENTS.md`
2. `docs/PROJECT_RULES.md`
3. `docs/STATUS.md`
4. Current contract, ADR, and security docs
5. Current runbooks
6. Historical/legacy handoffs, reviews, plans, and roadmaps

Examples are not roadmap commitments. Voice claims must say whether they are
mock, smoke, live, or manual.
