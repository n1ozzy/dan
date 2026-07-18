# Docs Index

Classification: authoritative.

This index explains which docs are current sources, runbooks, or legacy
evidence. If docs conflict, AGENTS.md + docs/PROJECT_RULES.md + docs/STATUS.md
win over old handoffs/roadmaps.

> **Naming note (Release 1, 2026-07-18):** the daemon formerly named `jarvisd`
> is now **`dand`** (launchd label `com.dan.dand`); the product/repo renamed
> Jarvis → DAN. Architecture docs below (CONTRACTS, DECISIONS, PRODUCT,
> PROJECT_RULES, PANEL_CONTRACT, SECURITY_MODEL, MACOS_*, LAUNCH_SUPERVISION)
> still say `jarvisd`/`com.ozzy.jarvisd` — read those names as `dand`/
> `com.dan.dand`; the contracts themselves remain in force. `JARVIS_*` and
> `*HANDOFF*` files are historical evidence, not active instructions.
> Operator docs written post-rename: CO-JEST-GDZIE, GLOS-I-KOLEJKA, ODZYSKIWANIE.

## Authoritative Docs

- `AGENTS.md` - repository rules for future agents.
- `docs/PROJECT_RULES.md` - project guardrails and architecture laws.
- `docs/STATUS.md` - current branch/HEAD snapshot and current status labels.
- `docs/DECISIONS.md` - accepted ADRs unless superseded by a later explicit
  authoritative rule.
- `docs/adr/ADR-001-memory-os-data-model.md` - authoritative/current Memory OS
  data model and migration direction.
- `docs/CONTRACTS.md` - legacy schema/API compatibility plus runtime contracts;
  its approval lifecycle does not govern model-originated tools on this branch.
- `docs/SECURITY_MODEL.md` - legacy restrictive permission design retained as
  historical/API reference, superseded by direct execution for model tools.
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
- `docs/runbooks/TOOLS_AND_APPROVALS.md` - legacy approval API reference; do not
  use it to restore approval capture to model-originated calls.
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
