# Radio DAN

**The honest state: Radio Studio is Release 2. It does not exist in this release.**

Release 1 has no radio scheduler, no "Radio DAN" tab in the panel, no radio
sessions with participants and no formats (dobranocka (bedtime-story show),
standup, roast, call-in). No document or skill should pretend otherwise.

Re-verified 2026-07-21: the cockpit has exactly four tabs — Chat, Memory, Logs,
System (`dan/panel/assets/index.html`); the database has no playlist, segment or
schedule table (`dan/store/schema.sql`); `GET /sessions` reports daemon/brain/
queue usage, not radio participants.

**Do not confuse the shipped skills with a runtime feature.** The repo does
ship `dobranocka`, `standup`, `danusia-live`, `trio-live` and friends under
`integrations/shared/skills/`. Those are *agent* skills: an agent writes lines
and pushes them one by one through `dan speak`. There is no scheduler, no
backpressure and no format state inside `dand` behind them.

## What in Release 1 is already compatible with the future Radio

Radio will be a tab of the same product, on the same contracts:

- **the voice queue in `dand`** — persistent, with a render snapshot and lanes
  (`live`, `normal`, `background`); the radio scheduler will be its producer,
  not a separate system (`docs/GLOS-I-KOLEJKA.md`);
- **queue sessions** — `dan speak --session ...` and `dan queue flush --session ...`
  already isolate a stream of utterances today (e.g. a `radio` session);
- **voice personas** — configured in `config/voice/personas.toml` (that file is
  canon, not this document), selected explicitly via `--as`. The public cast is
  exactly `dan` and `danusia`; no other `--as` value is a public voice route;
- **brain adapters** (participant = an explicit `identity + brain + voice`):
  `claude_cli`, `codex_cli`, `openai`, `ollama`, `qwen`, `eco`
  plus `mock`/`test` for testing — all behind the common `BrainAdapter`
  contract (`dan/brain/`);
- **the panel + event stream** — the future "what's playing / what's waiting"
  view will read the same `voice.*` events.

## What does NOT exist (and we do not pretend it does)

- a studio scheduler (participant ordering, backpressure, max 1 pending
  utterance per participant);
- radio session modes/formats and its separate history;
- Ozzy joining a radio session by microphone and a remote "call-in";
- a visualizer.

Radio gets its own specification and plan only after the foundation gates have
passed (the consolidation spec, §7 and §9).
