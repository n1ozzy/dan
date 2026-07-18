# DAN Release 1 Audit Remediation — Execution Index

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remediate every confirmed finding from the professional 15-step audit, prove each fix through RED/GREEN testing and independent review, and only then create a new Release 1 candidate and begin a new seven-day observation period.

**Architecture:** The work is divided into six dependent batches. Each batch has its own plan, small TDD boundaries, and review gates. The integration branch `agent/dan-release1-integration` remains the Release 1 line; `main` is not touched. The existing `dan-v1-foundation-candidate` tag is immutable historical evidence and must not be moved.

**Tech Stack:** Python 3.11+, SQLite/WAL, local HTTP API, persistent Claude CLI transport, Supertonic serve as a child of `dand`, CoreAudio/PyObjC, `launchd`, pytest, ruff, sdist/wheel, offline wheelhouse, JSON/JSONL/TOML.

## Global Constraints

- This execution index plus the six batch plans are the executable requirements, interpreted against current code and `AGENTS.md`. The [approved remediation specification](../specs/2026-07-18-release1-audit-remediation-design.md) is frozen design rationale and an entry-state snapshot; do not follow its now-superseded Fable dirty-patch ownership steps when they conflict with committed HEAD.
- The active line is `agent/dan-release1-integration`. Before each task, record `git rev-parse HEAD`, `git status --short`, and the list of files assigned to the owner.
- Fable's implementation patch is already frozen in commit `5c931d7563ace2056c1ca215458ea8fe230a4b36` and is part of the remediation baseline. Do not revert, restage, or replace it wholesale. A remediation task may modify an overlapping file only inside its recorded scope, with a task-scope diff against the current HEAD and both required reviews; any unrelated overlap is a `STOP` gate.
- The original `docs/superpowers/plans/2026-07-16-dan-foundation-release-1.md` is historical and must not be executed. Its Task 14 worktree, old-`jarvis` merge, dirty-tree, cutover, tag, and deployment commands are superseded in full by Batch 5 below; its inline branch correction does not make the remaining commands current.
- Do not use `git add -A`, `git stash`, `git reset --hard`, force-move a tag, or use destructive globs.
- Production brain semantics are settled: one **persistent** Claude CLI session. A persona hash change recycles the transport before the next input; cold-per-turn mode must not be restored.
- `config/persona/DAN.md` is the sole canon. Voice configuration comes from the repository's `config/voice/`. `dand` is the sole owner of runtime and audio; Supertonic serve is its supervised child.
- Automated tests always use a fresh `mktemp -d` or pytest `tmp_path` for `HOME` and `DAN_RELEASE_EVIDENCE_ROOT`, set `DAN_DISABLE_AUDIO=1`, `DAN_DISABLE_MIC=1`, `PYTHONNOUSERSITE=1`, and load `-p tests.audio_guard_plugin` after Task 0.3 creates it. Fixed shared `/private/tmp/dan-batch*-home` directories are forbidden. No plan step starts audible audio without a separate, explicit live gate.
- A commit is permitted only when Ozzy has explicitly authorized it in the current session. Stage exactly the files for one task, and always inspect `git diff --cached --name-only` and `git diff --cached`.
- Push, deployment to `~/.dan`, modification of the active HOME, production restart, tagging, and merge are not implied by commit authorization. Each operation requires a separate explicit command.
- After every task, the implementer shows RED and GREEN; a fresh agent performs a specification-compliance review; a second fresh agent performs a quality review. The next task must not start until both reviews are `APPROVED`.
- Every reviewer starts with the task-scope diff, not the implementer's description. Findings are reported as `BLOCKER`, `MAJOR`, or `MINOR`, with the file, line, evidence, and required test.
- All evidence uses the versioned `ReleaseEvidenceEnvelope`, a producer ID imported from the sole `dan/release/producer_ids.py` authority, the exact subject SHA, canonical report hash, input hashes, derived status, finding codes, and fail-closed unknown evidence. A CLI caller cannot self-declare `green`.
- `DAN_RELEASE_EVIDENCE_ROOT` must be absolute and outside the repository, active `~/.dan`, `~/.claude`, voice config, and active database roots. Reports derived from the private HOME remain beneath that external root; only schemas, producer code, and anonymized fixtures may be stored in the repository.
- Production code, tests, APIs, and error contracts use clear English names. Do not add comments or docstrings that merely narrate obvious code; reserve them for non-obvious invariants, races, compatibility constraints, and platform workarounds.

## Plans and Dependency Order

| Order | Plan | Audit Scope | Exit Gate |
|---|---|---|---|
| 0 | [Batch 0 — worktree and baseline](2026-07-18-release1-audit-remediation-00-worktree-baseline.md) | Task 1, 2, 4 | frozen ownership, new checkpoint, clean import surface, baseline v2 |
| 1 | [Batch 1 — data and cutover](2026-07-18-release1-audit-remediation-01-data-cutover.md) | Task 3, 12 | family-safe backup/rollback, real intake, durable journal, complete migrator |
| 2 | [Batch 2 — runtime and host](2026-07-18-release1-audit-remediation-02-runtime-host.md) | Task 9, 11 | exact TTS identity, watchdog, quiescent broker ownership, scheduler, atomic installer, TCC truth |
| 3 | [Batch 3 — persona, config, and voice](2026-07-18-release1-audit-remediation-03-persona-config-voice.md) | Task 5, 6, 7, 8 | persona recycling, local runtime truth, pinned gate, atomic cancel/event/tombstone boundary |
| 4 | [Batch 4 — panel, testing, and release](2026-07-18-release1-audit-remediation-04-panel-test-release.md) | Task 2, 4, 10, 13 | daemon-truth panel, one provider, audio containment, offline clean-clone gate |
| 5 | [Batch 5 — candidate and observation](2026-07-18-release1-audit-remediation-05-observation-candidate.md) | Task 14, 15 | candidate.2 readiness, new deployment receipt, 7 monotonic dates, 2 SHA-bound cold starts/cycles, sign-off |

Execute Batch 2 before Batch 3 by default because their configuration and voice-route files overlap. Individual tasks from those batches may run concurrently only after Batch 1 is GREEN and an explicit ownership map proves their exact file lists are disjoint. Batch 4 must additionally wait for formal handover of the panel files from Fable. Batch 5 is strictly last.

## Review Protocol for Every Task

- [ ] **Implementer:** record the baseline SHA and the exact file scope.
- [ ] **Implementer:** add one or more contract tests and show the expected RED, with a cause matching the finding.
- [ ] **Implementer:** make the smallest production change that satisfies the test.
- [ ] **Implementer:** show focused GREEN, the batch regression, `ruff`, and `git diff --check`.
- [ ] **Spec reviewer:** compare the diff against the task requirements and the specification; return `APPROVED` or findings.
- [ ] **Quality reviewer:** check races, failure modes, compatibility, privacy, the absence of a second source of truth, and test credibility.
- [ ] **Implementer:** address the findings and repeat both reviews.
- [ ] **Owner:** if the commit is authorized, stage only the task scope, show the cached diff, and create one narrow commit.

Task 0.1 freezes one canonical repository review-scope registry containing every expanded Batch 0–5 task ID and its allowed paths. Caller-supplied review evidence cannot change that set. After every task review is approved, two independent reviewers must also approve `release1-final-integration` at the exact final HEAD against the complete checkpoint-to-HEAD binary diff. A later fix, omitted task, extra path, or HEAD advance invalidates the review summary.

## Global Verification Gates

After each batch, run its full regression command in an isolated HOME. After Batch 4, also run the full baseline v2 and the clean-clone/offline build gate. Implement and review Batch 5's evidence, candidate, receipt, recorder, and observation-gate tools before producing final readiness evidence. Reports must bind to the exact final candidate SHA; any code change after a report invalidates that report and forces every required producer to rerun.

Before creating the candidate, all of the following are required simultaneously:

1. all implementation tasks in Batches 0–5 are GREEN on the final candidate HEAD;
2. both task-scope reviews are `APPROVED` for every canonical task, and both final-integration reviews approve the exact current HEAD and complete checkpoint-to-HEAD delta, with no outstanding debt;
3. a clean worktree for the release scope;
4. a fixture rollback rehearsal plus a separately authorized isolated manual drill, both reconciled by the rollback producer;
5. every pre-deployment versioned evidence envelope regenerated after the final Batch 5 implementation commit and bound to that exact HEAD;
6. a candidate gate that emits the intent `dan-v1-foundation-candidate.2` but does not create the tag.

After separately authorized candidate creation and deployment, observation cannot begin until all of the following agree:

1. a deployment receipt binds candidate tag, installed identity, artifact, manifest, runtime SHA, and deployment ID;
2. a separately authorized read-only audit of the active HOME is green, has no unknown evidence, and is bound to that receipt;
3. live voice acceptance on real Apple Silicon M5 hardware is green, uses canonical JSON stdin with no acceptance text in argv, and is bound to that receipt;
4. the observation recorder stores the canonical hashes of the receipt, active-HOME audit, and voice-acceptance envelope in every daily record.

After the seventh observation, the final gate must still prove one immutable target: candidate-tag target, deployment receipt candidate SHA, every observation/sign-off binding, and current `agent/dan-release1-integration` HEAD are identical. It emits that exact `observed_sha`. Any branch/ref/worktree/deployment drift invalidates readiness; the final tag must be created at `observed_sha`, and any eventual merge must merge that exact commit rather than a moving branch name.

## Explicitly Manual Operations

The tools planned for the repository **do not** automatically perform:

- deployment to `~/.dan` or modification of the active HOME;
- production restart or an actual cutover;
- listening review and acceptance of Żaneta/M5;
- tag creation or push;
- seven calendar days or two actual login cycles;
- Ozzy's sign-off, the final `dan-v1-foundation` tag, or merge to `main`.

---

## Execution Handoff

The recommended mode is `superpowers:subagent-driven-development`: a fresh implementer for each task, followed by a separate specification reviewer and quality reviewer. For data, migration, runtime-ownership, persona, and release-gate tasks, the quality reviewer works at `max` or `ultra` after the first failed review.

Alternatively, the plans may be executed sequentially through `superpowers:executing-plans`, while preserving exactly the same RED/GREEN gates and two independent reviews.
