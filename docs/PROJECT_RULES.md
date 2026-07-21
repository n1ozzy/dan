# Project Rules

Classification: authoritative. Naming updated for the Release 1 cutover
(2026-07-18) and re-verified against the code on 2026-07-21.

> **Naming:** the daemon is `dand` (launchd label `com.dan.dand`, wrapper
> `~/.dan/bin/dand`, API `127.0.0.1:41741`), the package is `dan/`, runtime state
> lives in `~/.dan/`. Older docs that still say `jarvisd` / `com.ozzy.jarvisd` /
> `~/.jarvis` mean exactly these. Jarvis is DAN's runtime alias, not a second
> product.

This file is the repo-level guardrail contract for DAN maintenance. It is
not a feature roadmap and not a smoke log.

## Architecture Laws

1. `dand` owns truth.
2. Panel is only a client.
3. Brain adapters are stateless.
4. Provider sessions are not DAN memory.
5. One task = one scope = one commit = stop for review.
6. No schema/migrations changes without explicit task scope.
7. No live voice/mic/speaker/launchctl/provider/network in automated CI; tests
   MUST mock the TTS layer.
8. Any bugfix must add or update a regression test first when practical.
9. Docs must identify whether they are authoritative, current, runbook,
   historical, or archived.
10. Old roadmap/handoff files cannot override current PROJECT_RULES/STATUS.
11. Examples are not roadmap commitments.
12. Voice claims must say whether they are mock/smoke/live/manual.
13. No broad cleanup/refactor mixed with feature/fix work.
14. Do not add approval gates, blocking permission policy, disabled-by-policy UI,
    or mock/dev product modes. On this branch model-originated tools execute
    directly (AGENTS.md branch contract); containment lives inside the tools.
15. Any new config key must be registered in
    `dan/config_registry.py::_RUNTIME_CONFIG_KEYS`. `validate_registry_complete()`
    compares dataclass fields against that registry and raises on EVERY config
    load if one is missing. This rule is stated here only; other docs link to it.

## Ownership

- `dand` is the system of record for conversation, events, memory,
  tool runs, worker jobs, voice queue, listening leases, and runtime state. It is
  also the single audio owner — all speech goes through `dan speak` / the voice
  API, never a parallel player.
- The `approvals` table and `ApprovalGate` still exist as a legacy record
  surface, but they are NOT in the tool execution path. Do not describe them as
  a live control.
- UI clients, including the static cockpit and macOS panel, render daemon state
  and submit intents. They do not own canonical data.
- Brain adapters are stateless request/response adapters. They cannot preserve
  hidden provider session memory as DAN memory.
- Provider CLI sessions may exist, but DAN context must be assembled from
  DAN config, DB, and explicit request data.
- Persona comes only from `config/persona/DAN.md` (header
  `DAN_CANON_VERSION: 1`), loaded fail-closed. Voice casting comes only from
  `config/voice/personas.toml` + `pronunciations.toml`. Never hardcode either.

## Change Discipline

- Start each task by checking cwd, git root, and git status.
- Keep the current prompt as the scope boundary.
- Touch only files allowed by the prompt.
- Do not refactor unrelated modules while doing docs, rescue, feature, or fix
  work.
- Do not change schema or migrations unless the task explicitly says schema or
  migration work is allowed.
- Do not start daemons, panels, voice loops, launchctl jobs, providers, or
  networked runtime behavior unless the task explicitly asks for a manual live
  validation.
- Stop for review after the scoped change and verification.

## Test Discipline

- Bugfixes require a failing regression test first when practical.
- Guardrail/docs changes should add or update contract tests.
- CI must stay mock/unit safe. Live voice, mic/speaker, launchctl, provider
  smoke, and networked provider checks are manual only.

## Documentation Discipline

- Every new durable doc must state one of these classifications:
  authoritative, current, runbook, historical, or archived.
- `AGENTS.md`, this file, and `docs/STATUS.md` win over old handoffs and
  roadmaps when there is a conflict.
- Historical roadmap and handoff docs remain useful evidence, but they are not
  allowed to resurrect old scope.
- Capability examples are examples. They become roadmap commitments only after
  a later scoped prompt, contract, tests, and permission model promote them.
- Any voice claim must say whether it is mock, smoke, live, or manual evidence.

