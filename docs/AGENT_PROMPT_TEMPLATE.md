# Agent Prompt Template

Classification: current.

Use this template for future scoped Jarvis tasks.

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
- No broad cleanup/refactor mixed with feature/fix work.
- No schema/migrations unless explicitly allowed.
- No live voice/mic/speaker/launchctl/provider/network in automated CI.
- Examples are not roadmap commitments.
- Voice claims must identify mock/smoke/live/manual evidence.

