# Jarvis Agent Rules

These rules apply to every agent working in this repository.

## First Checks

Before editing, run:

```sh
pwd
git rev-parse --show-toplevel
git status --short
```

Confirm the checkout is `/Users/n1_ozzy/Documents/dev/jarvis` when working on
this machine. If unrelated changes already exist, do not revert them and do not
mix them into your task.

## Source Of Truth

- `jarvisd` owns truth: conversation, events, memory, approvals, tool runs,
  worker jobs, voice queue, listening leases, and runtime state.
- The panel is only a client. It renders daemon state and sends intents.
- Brain adapters are stateless. They return text/tool-call requests only.
- Provider sessions are not Jarvis memory.
- The voice broker is the only speaker.

## Scope Discipline

- One task = one scope = one commit = stop for review.
- Change only files explicitly allowed by the current task.
- Do not mix broad cleanup/refactor work into feature, fix, docs, or rescue work.
- Do not touch schema/migrations unless the task explicitly scopes that work.
- Any bugfix must add or update a regression test first when practical.
- Stop before commit unless the user explicitly asks for the commit.

## Forbidden In Automated Checks

Automated CI and guardrail tests must not start live voice, mic, speaker,
launchctl, real provider CLIs, or external network behavior from Jarvis.
Live/manual smokes belong in runbooks, not in default CI.

## Documentation Authority

Docs must identify whether they are authoritative, current, runbook,
historical, or archived. If docs conflict, follow this precedence:

1. `AGENTS.md`
2. `docs/PROJECT_RULES.md`
3. `docs/STATUS.md`
4. Current contract/ADR docs
5. Runbooks
6. Historical handoffs and roadmaps

Old roadmap/handoff files cannot override current `PROJECT_RULES` or `STATUS`.
Examples are not roadmap commitments.

claude code docs: https://code.claude.com/docs