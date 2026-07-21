# DAN Do Not Touch Casually

Classification: high-risk boundary list. CURRENT — this file is meant to be
obeyed today.

> **Path cutover (2026-07-18/21):** every path below was rewritten from the old
> `jarvis/` package to the shipping `dan/` package. The `jarvis/` directory does
> not exist; a guard or rule written against `jarvis/...` protects nothing.

## Purpose

This file identifies areas that must not be changed casually. It does not mean “never change”. It means every change needs explicit scope, tests, review, and guards.

## Absolute process rules

Do not casually:

- change schema or migrations;
- change prompt-visible ContextBuilder output;
- change MemoryCompiler eligibility/governance;
- enable compiled memory globally;
- bypass `[memory].enabled=false` or `compiled_memory_force_disabled`;
- add env, panel, public API, user-facing, or global production compiled-memory toggles casually;
- weaken the containment that lives INSIDE the tools (approved roots, the
  `shell_read` allowlist, the scrubbed environment, git hardening, the
  runtime/output bounds) — on this branch that is the whole defense, because
  `ToolPermissionPolicy.decide()` returns ALLOW unconditionally;
- add an approval gate, a blocking permission policy, or an
  `awaiting_approval` turn back into the model-originated tool path (AGENTS.md
  branch contract forbids it);
- treat provider sessions as memory;
- let workers speak or commit facts;
- let panel own truth;
- add live voice/provider/network behavior to automated CI;
- store or emit raw secrets;
- log memory content in diagnostics;
- turn planned docs into claims of implemented features.

## Schema and migrations

High-risk files:

- `dan/store/schema.sql`
- `dan/store/migrations.py`

Rules:

- Schema changes require a schema task.
- Migration changes require migration tests.
- No schema drift in MemoryCompiler/ContextBuilder/docs-only tasks.

## ContextBuilder

High-risk file:

- `dan/brain/context_builder.py`

Rules:

- Prompt-visible output changes require final BrainRequest tests.
- User input must not be overwritten.
- `memory_blocks` behavior must be preserved until explicit cutover.
- Compiled memory must remain default-off unless enablement task says otherwise.
- Session/profile and request-scoped compiled-memory enablement must remain internal-only unless a scoped task changes that.
- Request override True must not bypass `[memory].enabled=false` or `compiled_memory_force_disabled`.
- Diagnostics must not enter context messages.

## MemoryCompiler

High-risk file:

- `dan/memory/compiler.py`

Rules:

- Deterministic ordering must stay deterministic.
- Compiler must remain read-only.
- It must not call providers or embeddings.
- It must not update timestamps or usage ledgers in context build.
- It must not select disabled, superseded, forgotten, conflict, missing-evidence, or procedural-by-default memory.
- It must not expose raw evidence, IDs, skipped items, diagnostics internals, compiler internals, or secrets to the model.

## Memory API

High-risk file:

- `dan/api/routes_memory.py`

Rules:

- Preview API changes require API task scope.
- Preview API must not become implicit runtime enablement.
- Raw memory internals must not be exposed casually.

## Daemon lifecycle and transport security

High-risk files:

- `dan/daemon/lifecycle.py`
- `dan/daemon/app.py`
- `dan/security/transport.py`

Rules:

- CORS, Host, body-size, token, and WebSocket rules are security boundaries.
- Private-data GET routes must remain protected as designed.
- Runtime dependency wiring is not feature enablement.

## Tools and approvals

High-risk files:

- `dan/tools/permissions.py`
- `dan/tools/registry.py`
- all tool implementations under `dan/tools/`

Rules:

- **The tools are the containment, not the policy.** `ToolPermissionPolicy.decide()`
  returns ALLOW for every risk class and every source ("runtime-lab policy", its
  own docstring), and `ToolRegistry.request_tool()` ignores its
  `permission_policy` / `source` / `approval_gate` arguments and executes
  immediately. So every check that actually refuses work lives inside the tool:
  approved-root containment (`file_tool`, `shell_tool` cwd), the `shell_read`
  allowlist, the scrubbed env, the git `fsmonitor`/`hooksPath`/`protocol.ext`
  hardening, the size/timeout bounds, the secure-text-field ban in `ui_tool`,
  and the control-character ban in `ui_tool`/`terminal_tool`. Weakening any of
  those has nothing behind it.
- `ApprovalGate` in `registry.py` still writes `approvals` rows and `approval.*`
  events, but it is NOT in the tool execution path ("Release 1 tool execution
  does not call this gate"). Do not re-wire it into that path — AGENTS.md
  forbids adding approval guards on this branch.
- `security.shell_read_unrestricted` (default `false`) drops the `shell_read`
  allowlist. Do not widen it further — and do not assume the rest of the
  containment survives it intact, because two of the four guards do not.
  What actually stops holding is in `docs/SECURITY_MODEL.md` §2 ("The
  `shell_read` allowlist and its opt-out"), which is the only copy of that
  fact; this file deliberately does not repeat it.
- Tool run persistence must redact secrets and cap strings
  (`security/redaction.py` + `PERSIST_MAX_STRING_CHARS`) — this one is real,
  active, and must stay.
- Shell/git/file/UI/terminal tools require containment tests.

## Voice

High-risk files:

- `dan/voice/broker.py`
- `dan/voice/gateway.py`
- `dan/voice/anti_echo.py`
- `dan/voice/cancellation.py`
- `dan/voice/queue.py`
- `dan/voice/listening.py`
- `config/voice/personas.toml`, `config/voice/pronunciations.toml` (the casting
  canon lives in the repo — never hardcode voice values in code or docs)

Rules:

- `dand` is the sole audio owner; all speech goes through `dan speak` / the
  voice API. Never spawn a parallel afplay/TTS/serve.
- Anti-echo must run before accepted transcript becomes a turn.
- Barge-in must cancel generation/speech safely.
- Automated tests MUST mock the TTS layer — never open a real mic/speaker and
  never spawn a real afplay/supertonic.

## Panel

High-risk files:

- `dan/panel/`

Rules:

- Panel is client-only.
- It must not become state owner.
- UX changes must not alter daemon truth model.

## Provider adapters

High-risk files:

- `dan/brain/claude_cli_adapter.py`
- `dan/brain/codex_cli_adapter.py`
- `dan/brain/openai_adapter.py`

Rules:

- Adapters are stateless DAN-side contracts: a brain is a
  `BrainRequest -> BrainResponse` function.
- Provider sessions are not DAN memory. `ClaudeCliAdapter` does keep ONE
  persistent provider session per daemon lifetime, checkpointed in
  `~/.dan/runtime/claude-session.json` and rejoined with `--resume` — that is a
  provider-side session, not memory, and it must not become one.
- Do not casually change the resume/bootstrap split: a RESUMED session keeps its
  ORIGINAL system prompt and tool set (our prompt only rides along as
  `--append-system-prompt`), so a poisoned checkpoint survives every restart.
  Recovery is quarantining the checkpoint file — `docs/ODZYSKIWANIE.md`.
- Do not drop `--tools ""` or `--setting-sources ""` from the argv; they are what
  isolate the subprocess from Claude's native tools and from the operator's
  CLAUDE.md/settings.
- Tool-call parsing must stay safe.
- Cancellation must not corrupt turn state.

## Config

High-risk files:

- `config/`
- `dan/config.py`
- `dan/config_registry.py`
- `config/persona/DAN.md` — the ONE persona canon, loaded fail-closed; it
  requires the literal header `DAN_CANON_VERSION: 1`. Per-profile personas were
  removed and `persona_profile` is pinned to `DEFAULT_PERSONA_PROFILE = "dan"`.
  Jarvis is an alias of DAN, not a second character.

Rules:

- Defaults are product behavior. Live config is `~/.dan/config.toml` (outside the
  repo); `config/dan.example.toml` is the shipped example and keeps the
  conservative defaults.
- Every new config key must be registered — `docs/PROJECT_RULES.md` rule 15.
- New risky features should default off.
- Config parsing changes require focused tests.
- Config dev/local compiled-memory gates must not become global production enablement.

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
