# Jarvis Do Not Touch Casually

Classification: high-risk boundary list.

## Purpose

This file identifies areas that must not be changed casually. It does not mean “never change”. It means every change needs explicit scope, tests, review, and guards.

## Absolute process rules

Do not casually:

- change schema or migrations;
- change prompt-visible ContextBuilder output;
- change MemoryCompiler eligibility/governance;
- enable compiled memory globally;
- bypass approval gates;
- bypass tool permission policy;
- treat provider sessions as memory;
- let workers speak or commit facts;
- let panel own truth;
- add live voice/provider/network behavior to automated CI;
- store or emit raw secrets;
- log memory content in diagnostics;
- turn planned docs into claims of implemented features.

## Schema and migrations

High-risk files:

- `jarvis/store/schema.sql`
- `jarvis/store/migrations.py`

Rules:

- Schema changes require a schema task.
- Migration changes require migration tests.
- No schema drift in MemoryCompiler/ContextBuilder/docs-only tasks.

## ContextBuilder

High-risk file:

- `jarvis/brain/context_builder.py`

Rules:

- Prompt-visible output changes require final BrainRequest tests.
- User input must not be overwritten.
- `memory_blocks` behavior must be preserved until explicit cutover.
- Compiled memory must remain default-off unless enablement task says otherwise.
- Diagnostics must not enter context messages.

## MemoryCompiler

High-risk file:

- `jarvis/memory/compiler.py`

Rules:

- Deterministic ordering must stay deterministic.
- Compiler must remain read-only.
- It must not call providers or embeddings.
- It must not update timestamps or usage ledgers in context build.
- It must not select disabled, superseded, forgotten, conflict, missing-evidence, or procedural-by-default memory.

## Memory API

High-risk file:

- `jarvis/api/routes_memory.py`

Rules:

- Preview API changes require API task scope.
- Preview API must not become implicit runtime enablement.
- Raw memory internals must not be exposed casually.

## Daemon lifecycle and transport security

High-risk files:

- `jarvis/daemon/lifecycle.py`
- `jarvis/daemon/app.py`
- `jarvis/security/transport.py`

Rules:

- CORS, Host, body-size, token, and WebSocket rules are security boundaries.
- Private-data GET routes must remain protected as designed.
- Runtime dependency wiring is not feature enablement.

## Tools and approvals

High-risk files:

- `jarvis/tools/permissions.py`
- `jarvis/tools/registry.py`
- all tool implementations under `jarvis/tools/`

Rules:

- Model-originated tools must be source-sensitive.
- Approval-required actions must not become silent actions.
- Tool run persistence must redact secrets and cap strings.
- Shell/git/file/UI/terminal tools require containment and policy tests.

## Voice

High-risk files:

- `jarvis/voice/broker.py`
- `jarvis/voice/gateway.py`
- `jarvis/voice/anti_echo.py`
- `jarvis/voice/cancellation.py`
- `jarvis/voice/queue.py`
- `jarvis/voice/listening.py`

Rules:

- Broker is the sole speaker.
- Anti-echo must run before accepted transcript becomes a turn.
- Barge-in must cancel generation/speech safely.
- Automated tests must not open real mic/speaker.

## Panel

High-risk files:

- `jarvis/panel/`

Rules:

- Panel is client-only.
- It must not become state owner.
- UX changes must not alter daemon truth model.

## Provider adapters

High-risk files:

- `jarvis/brain/claude_cli_adapter.py`
- `jarvis/brain/claude_cli_warm_adapter.py`
- `jarvis/brain/codex_cli_adapter.py`
- `jarvis/brain/openai_adapter.py`

Rules:

- Adapters are stateless Jarvis-side contracts.
- Provider sessions are not Jarvis memory.
- Tool-call parsing must stay safe.
- Cancellation must not corrupt turn state.

## Config

High-risk files:

- `config/`
- `jarvis/config.py`

Rules:

- Defaults are product behavior.
- New risky features should default off.
- Config parsing changes require focused tests.

## Docs

High-risk docs:

- `AGENTS.md`
- `docs/PROJECT_RULES.md`
- `docs/STATUS.md`
- architecture/contract docs

Rules:

- Docs must not claim planned behavior is implemented.
- Historical docs cannot override current authoritative docs.
- Docs-only tasks must not touch code.
