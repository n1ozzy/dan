# Agent Prompt Template

Classification: current. Re-verified 2026-07-21.

Use this template for future scoped DAN tasks. (The product was renamed
Jarvis → DAN in the Release 1 cutover, 2026-07-18; the package is `dan/`, the
daemon is `dand`.)

```text
Task
<One concrete task name and objective.>

Scope
<What this task is allowed to change and what success means.>

Allowed files
- <exact file or directory>

Forbidden files
- <exact file, directory, subsystem, or behavior>

Required failing test first
<Name the regression or guardrail test to add/update before implementation.
If test-first is not practical, explain why in the prompt.>

Verification
- <command 1>
- <command 2>

Stop condition
Stop before commit. Report files changed, tests run, runtime behavior touched
or untouched, and any stale/conflicting docs left behind.
```

## Scope Checklist

- One task = one scope = one commit = stop for review.
- Commit only on Ozzy's explicit command.
- No broad cleanup/refactor mixed with feature/fix work.
- No schema/migrations unless explicitly allowed.
- No live voice/mic/speaker/launchctl/provider/network in automated CI; tests
  MUST mock the TTS layer.
- No new approval gates, blocking permission policy, or disabled-by-policy UI —
  AGENTS.md forbids them on this branch.
- New config keys must be registered — `docs/PROJECT_RULES.md` rule 15.
- Examples are not roadmap commitments.
- Voice claims must identify mock/smoke/live/manual evidence.

