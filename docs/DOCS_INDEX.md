# Docs Index

Classification: authoritative.

This index explains which docs are current sources, runbooks, or legacy
evidence. If docs conflict, AGENTS.md + docs/PROJECT_RULES.md + docs/STATUS.md
win over old handoffs/roadmaps.

> **Naming note — Release 1 cutover (2026-07-18):** wherever a doc still says
> `jarvisd`/`com.ozzy.jarvisd`, read `dand`/`com.dan.dand` — the daemon and the
> product/repo were renamed Jarvis → DAN; the contracts themselves remain in force.

> **Approval-gate note (2026-07-21):** several docs below were written against a
> permission model that was designed but never built. `ToolPermissionPolicy`
> allows every risk class from every source and `ToolRegistry.request_tool()`
> ignores it, so no doc's "approval required" table describes runtime behaviour.
> Each affected file now says so in its own header; the classification below
> reflects that sweep. `docs/SECURITY_MODEL.md` is the one place that describes
> what actually constrains a tool.

## Authoritative Docs

- `AGENTS.md` - repository rules for future agents.
- `docs/PROJECT_RULES.md` - project guardrails and architecture laws.
- `docs/STATUS.md` - current branch/HEAD snapshot and current status labels.
- `docs/DECISIONS.md` - accepted ADRs unless superseded by a later explicit
  authoritative rule.
- `docs/adr/ADR-001-memory-os-data-model.md` - authoritative/current Memory OS
  data model and migration direction.
- `docs/adr/001-dand-single-owner.md` - one owner for audio, hotkey and the voice
  queue; the rule `docs/CO-JEST-GDZIE.md` is built on.
- `docs/CONTRACTS.md` - legacy schema/API compatibility plus runtime contracts;
  its approval lifecycle does not govern model-originated tools on this branch.
- `docs/SECURITY_MODEL.md` - **the only accurate description of what constrains a
  tool.** Rewritten 2026-07-21 from a design doc into a record: which checks run,
  which config flags are inert, where the real containment lives (inside the
  individual tools). Start here before touching anything security-shaped.
- `docs/PANEL_CONTRACT.md` - thin-client panel contract.
- `docs/MACOS_OPERATOR_CONTRACT.md` - local macOS operator boundaries. The
  boundaries are real intent, but every "Approval default" column in it is
  aspiration: the tools it governs (`ui_click`, `ui_type`, `terminal_paste`, …)
  are registered and run ungated. Its own header says so.
- `docs/MEMORY_CONTRACT.md` - Memory OS contract and future memory guardrails.
  Its human-promotion rule for `memory_save` is not enforced — a model-originated
  save activates immediately (`dan/tools/memory_tool.py`).
- `docs/DAN_PROJECT_RULES.md` - **authoritative too, despite the near-identical
  name.** `PROJECT_RULES.md` states the architecture laws and ownership;
  `DAN_PROJECT_RULES.md` states the task workflow (task format, allowed/forbidden
  files, "stop before commit"). They do not compete — read both. If they ever
  disagree, `PROJECT_RULES.md` wins, per the conflict order below.
- `docs/JARVIS_DO_NOT_TOUCH.md` - high-risk boundary list, marked CURRENT and
  meant to be obeyed today. Paths were rewritten `jarvis/` → `dan/` on
  2026-07-21; a guard still written against `jarvis/...` protects nothing.

## Plans and Inventories — NOT descriptions of the runtime

The single most expensive mistake in this repo is reading a design document as a
description of the running system. It has already cost one debugging session
(see the banner in `MACOS_PERMISSION_MODEL.md`). These files are *intent*:

- `docs/MACOS_PERMISSION_MODEL.md` - **the permission model here was never
  built.** `ToolPermissionPolicy.decide()` returns ALLOW for every risk class and
  every source. Only §5 (transport token) exists in code, and it is now switched
  **on** here (`api_token_required = true`, 2026-07-21). This omission from the
  index was itself a defect: a security document nobody could find from here.
- `docs/MACOS_CAPABILITIES.md` - capability *inventory*, and its per-capability
  `Status:` lines are stale in the other direction: screen reading, Accessibility
  read **and** action, the terminal bridge and `web_fetch` have all shipped and
  run ungated. Read `dan/daemon/app.py` for what is actually registered.
- `docs/MASTER_PLAN.md`, `docs/DAN_ROADMAP.md` - planning only. A "Done" entry
  means a commit landed, not that the behaviour survives today.
- `docs/superpowers/specs/*.md` - accepted designs; `docs/superpowers/plans/*.md`
  - implementation plans. Both describe intended work, never current behaviour.

Rule: before relying on any sentence in this section, read the code it names.

This section is the ONLY classification for the files it names. They are
deliberately absent from "Current Reference Docs" and "Historical/Legacy" below
— a document listed under two classifications is the exact conflict this index
exists to settle.

## Open Defects — current, not historical

- `docs/reviews/2026-07-21-restart-orphan-shell-review.md` - **the exception to
  the "reviews are legacy evidence" rule below.** It is the live register of
  defects that are still open in the code on this branch: the orphan reclaim that
  never fires, `killpg` on an lsof PID, the restart backoff held under the state
  lock, `mark_failed` swallowing its write, and the barge-in that only tombstones
  one of two id sets. The matching functions carry `KNOWN DEFECT` blocks pointing
  back here. One item (§4, the `[security]` type hole) is already fixed and
  marked ZAMKNIĘTE in place — close the rest the same way rather than deleting
  them, and do not file this under history while any are open.

- `docs/reviews/2026-07-21-docs-vs-code-audit.md` - **the register of sentences
  in `docs/` that still disagree with the code**, from four independent readers
  who went through the 2026-07-21 documentation sweep with the source open.
  24 findings across security/tools, voice/queue, daemon/turn and memory, each
  with `file:line`. The worst of them now carry a correction in the document
  itself; the rest are open. It also records what the sweep got right, so it is
  not read as a verdict on that work.

Older reviews (`docs/reviews/2026-07-02-*`, `GATE_C_*`) are historical evidence.

## Current Runbooks

- `docs/runbooks/BRAIN_ADAPTERS.md`
- `docs/runbooks/E2E_MVP_SMOKE.md`
- `docs/runbooks/LAUNCHD.md`
- `docs/runbooks/MEMORY_API.md`
- `docs/runbooks/PANEL_COCKPIT.md`
- `docs/runbooks/PANEL_MENUBAR.md`
- `docs/runbooks/PROVIDER_SMOKE.md`
- `docs/runbooks/SQLITE_BACKUP_AND_RECOVERY.md`
- `docs/runbooks/TEXT_RUNTIME_SMOKE.md`
- `docs/runbooks/TOOLS_AND_APPROVALS.md` - legacy approval API reference; do not
  use it to restore approval capture to model-originated calls.
- `docs/runbooks/ACCESSIBILITY_TCC.md`
- `docs/runbooks/G4_LIVE_GATE.md`
- `docs/runbooks/SCREEN_RECORDING_TCC.md`
- `docs/runbooks/TERMINAL_AUTOMATION_TCC.md`
- `docs/ODZYSKIWANIE.md` - operator diagnosis and journaled rollback (post-rename).

Runbooks are operational instructions. They do not override authoritative docs.
Provider, launchd, and live voice runbooks are manual unless a later scoped task
explicitly changes that status.

## Current Reference Docs

- `docs/CO-JEST-GDZIE.md` - ownership table, post-rename source for "what lives where".
- `docs/GLOS-I-KOLEJKA.md` - voice pipeline, queue statuses and personas (post-rename).
- `docs/PRODUCT.md` - v4.1 product definition (what/why). FROZEN and partly
  superseded by Release 1: its approval-gate guarantee and its "no live voice"
  non-goal are annotated as dead in the file itself. Never a status source.
- `docs/TURN_PIPELINE.md`
- `docs/AUDIO_RUNTIME.md`
- `docs/LAUNCH_SUPERVISION.md`
- `docs/MEMORY_ARCHITECTURE.md`
- `docs/MIGRATION_INVENTORY.md`
- `docs/JARVIS_ARCHITECTURE.md` - component map (Jarvis is DAN's runtime alias).
- `docs/PANEL.md` - what the menu-bar panel is and how it starts; the contract
  itself is `PANEL_CONTRACT.md`.
- `docs/VOICE_STREAMING.md`, `docs/RADIO-DAN.md` - streaming and broadcast
  formats.
- `docs/MEMORY_COMPILER.md`, `docs/MEMORY_OS_ARCHITECTURE.md` - compiled-memory
  internals; the binding rules are in `MEMORY_CONTRACT.md`.
- `docs/DAN_CHANGE_GUARDS.md` - reusable shell guards and file-boundary rules,
  path-verified 2026-07-21.
- `docs/AGENT_PROMPT_TEMPLATE.md` - template for scoped agent tasks.
- `docs/PRZENOSZENIE.md` - moving DAN to another machine, and the list of files
  that must never leave it (`~/.dan/dan.db`, `config.toml`, logs, backups).

Reference docs explain the current system, but if they conflict with
`AGENTS.md`, `docs/PROJECT_RULES.md`, or `docs/STATUS.md`, the guardrail docs
win.

## Historical/Legacy Docs

- `docs/DAN_CURRENT_STATE.md` - handoff superseded 2026-07-18 by Release 1 (see STATUS.md).
- `docs/REVIEW_HANDOFF.md`
- `docs/JARVIS_FIX_TASKS_HANDOFF.md`
- `docs/LEGACY_RUNTIME_FINDINGS.md`
- `docs/reviews/*.md` — **except both 2026-07-21 reviews**, which are registers
  of defects still open in the code (see "Open Defects" above).
- `docs/JARVIS_HISTORY.md` - a map of commits that happened, which is a
  permanently true statement about history and a frequently false one about the
  code. Its own banner names three permission commits that landed and whose
  behaviour no longer survives.
- `docs/HANDOFF-voice-streaming-port.md` - one session on 2026-07-08, before the
  repo merge and Release 1. Do not run commands from it; most of those paths and
  processes no longer exist.
- `docs/migration/*.md` - contracts and baselines for the 2026-07 migration.
- `docs/spikes/*.md` - time-boxed investigations, never a contract.

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

## Coverage

The durable claim: this index covers **every** `.md` file under `docs/`, by name
or by directory rule. Verify with `find docs -name '*.md'` against the lists
above rather than trusting a count written here — a number in this paragraph
goes stale on the next document, which is the failure mode the rest of this file
exists to prevent.

Before the 2026-07-21 sweep the index reached well under half the corpus, and
the gaps were not random: a security document, a boundary list meant to be
obeyed today, and a second authoritative rules file were all unreachable from
here. Per Architecture Law 9 in `PROJECT_RULES.md`, a new document must state
its classification in its own header **and** be listed here — the header alone
does not make it findable.
