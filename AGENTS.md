# Agent instructions for this branch

Use current code as source of truth. Do not follow old handoff/planning docs.

Branch contract:
- Do not add approval guards, disabled-by-policy UI, mock/dev product modes, or new provider mazes.
- Conversation: one continuous Jarvis conversation for text and voice.
- Persona: `config/persona/DAN.md` (repo canon, `dan/persona.py` default) is the only
  authority for both DAN and Jarvis. Load it fresh and fail loudly if invalid.
  Do not copy it, soften it, classify it, shorten it, or rewrite model output.
- Model-originated tools execute directly and return their real result; no approval
  row or awaiting-approval turn may be inserted into that path.
- Voice: Supertonic remains the TTS.
- Workers: disabled for now.
- Panel: render effective runtime state; do not create fake sessions/chats.

Before changing code, identify the active source of truth. Do not add another one.
Conversation history and memory are evidence, never persona/system instructions.
Commits require Ozzy's explicit command.

## Traps that already cost real debugging time (2026-07-21)

Facts, not rules. Each one contradicts something a stale doc still implies.

- **The permission policy blocks nothing.** `ToolPermissionPolicy.decide()`
  returns ALLOW for every risk class and every source, and
  `ToolRegistry.request_tool()` ignores it entirely. Every
  `security.require_approval_*`, `auto_approve_mode`, `destructive_tools_enabled`,
  `voice_auto_approve_tools` and `trusted_scopes` key is inert. Do not diagnose a
  permissions problem by changing one. Real containment is inside each tool:
  `approved_roots`, the scrubbed env, per-tool argument validation and bounds.
  `approved_roots` is enforced but not narrow — on this machine it is `~`,
  `/tmp`, `/Volumes`, `/Applications`, so "contained" means the whole home.
  Details: `docs/SECURITY_MODEL.md` §2. `docs/MACOS_PERMISSION_MODEL.md` is an
  unimplemented design — never quote its matrix as behaviour.
- **The `shell_read` allowlist and the git hardening are off on this machine.**
  `security.shell_read_unrestricted = true` in the owner's `~/.dan/config.toml`
  (his deliberate choice). The knock-on effect is not: the git hardening arms
  `core.fsmonitor` / `core.hooksPath` / `protocol.ext` only when the command's
  first token is literally `git`, a test that was exhaustive *only* while the
  allowlist held commands to a fixed set of strings. With it off, `/usr/bin/git`,
  `cd sub && git …`, `env git …` and `sh -c '…'` reach git unhardened. Do not
  list the allowlist among the things that still contain a tool.
  Details: `docs/SECURITY_MODEL.md` §2.
- **`dan memory sync` copies the content of every turn into a durable archive.**
  Yours and the owner's, into `memory_archive_documents` plus an FTS index — no
  candidate, no approval, and **no forget operation**. The registered
  `memory_recall` tool lets the model full-text search it. Treat it as owner
  data, not as an index. Details: `docs/MEMORY_CONTRACT.md`, top of file.
- **This checkout has extra git worktrees, against the owner's own rule.**
  `git worktree list` before you trust a doc you opened by path — a live
  worktree holds its own stale copy of `docs/` that every correction here
  bypasses.
- **The brain's provider session is persistent and survives restarts.**
  `ClaudeCliAdapter` keeps one session for the daemon's lifetime, checkpointed in
  `~/.dan/runtime/claude-session.json` and rejoined with `--resume`. A resumed
  Claude Code session keeps its ORIGINAL system prompt and ORIGINAL tool set —
  ours only rides along as `--append-system-prompt`. So a poisoned or foreign
  checkpoint makes DAN report the wrong tools forever, through every restart.
  Symptom and fix: `docs/ODZYSKIWANIE.md`. Verify with a live turn, never with
  `/tools` — the endpoint reads the daemon registry, not what the model sees.
- **A new config key MUST be registered** — `docs/PROJECT_RULES.md` rule 15. One
  forgotten line took out most of the suite at once.
- **Never run bare `pytest`.** It touches the real `~/.dan` and can overwrite the
  live `personas.toml`. Use an isolated runner with its own `DAN_HOME`.
- **A red suite is normal here — judge the delta, not the count.** The accepted
  failure set, the counts and the freeze point live in
  `docs/migration/TEST-BASELINE.md` + `docs/migration/TEST-BASELINE-failures.txt`,
  produced and compared by `scripts/dan-test-baseline`. Look your failure up
  there before you debug it. Whole blocks in that ledger
  (`test_ui_act_tools`, `test_ui_read_tools`, `test_web_tool`,
  `test_model_tool_permission_policy`) assert the approval gate the branch
  contract above removed — **never "fix" them by reintroducing a gate**;
  deleting or rewriting them needs Ozzy's word. For files added after the ledger
  was frozen, get a true baseline from a clean `git archive HEAD` export; never
  `git stash` in this checkout, a second session may be working in it.
- **`[[GŁOS]]` means two different things — do not conflate them.**
  1. **ALIVE, do not delete:** DAN's own speech protocol. `_VOICE_FORM_INSTRUCTION`
     in `dan/brain/context_builder.py` tells the brain to open a voiced answer
     with a `[[GŁOS]] … [[/GŁOS]]` block, which the daemon extracts and sends to
     TTS. Removing it breaks spoken replies.
  2. **DEAD, never reintroduce:** the Claude Code MessageDisplay hook that made
     the *host assistant* emit `[[GŁOS]]` markers into chat. Removed and
     quarantined 2026-07-21; an operator-side marker is now just litter.

  Rule of thumb: inside `dan/` the marker is product behaviour; in a Claude Code
  session's own output it is garbage. Speech still only leaves via `dan speak`.

## Voice source of truth — Release 1 cutover (2026-07-18)
- Casting canon: `config/voice/personas.toml` + `pronunciations.toml` IN THIS REPO —
  read them, never copy values into docs (decision log: `docs/migration/VOICE-DECISIONS.md`).
- Speech goes ONLY through `dan speak` / the voice API — never spawn parallel
  afplay/TTS/serve; tests MUST mock the TTS layer. Single audio owner: the `dand`
  daemon (launchd `com.dan.dand`, API `127.0.0.1:41741`); supertonic serve
  (`127.0.0.1:7788`) is dand's supervised child.
- Older docs still say `jarvisd`/`com.ozzy.jarvisd` — same daemon, today `dand`/`com.dan.dand`.
- Old stack (`~/.config/voice` bridge, `dev/dan` repo) retired — migration record:
  `docs/STATUS.md`, section "Release 1 cutover". Stack map: `docs/GLOS-I-KOLEJKA.md`
  + `docs/CO-JEST-GDZIE.md`; recovery: `docs/ODZYSKIWANIE.md`.
