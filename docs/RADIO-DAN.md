# Radio DAN

**The honest state: Radio Studio is Release 2. It does not exist in this release.**

Release 1 has no radio scheduler, no "Radio DAN" tab in the panel, no radio
sessions with participants and no formats (dobranocka (bedtime-story show),
standup, roast, call-in). No document or skill should pretend otherwise.

## What in Release 1 is already compatible with the future Radio

Radio will be a tab of the same product, on the same contracts:

- **the voice queue in `dand`** — persistent, with a render snapshot and lanes
  (`live`, `normal`, `background`); the radio scheduler will be its producer,
  not a separate system (`docs/GLOS-I-KOLEJKA.md`);
- **queue sessions** — `dan speak --session ...` and `dan queue flush --session ...`
  already isolate a stream of utterances today (e.g. a `radio` session);
- **voice personas** — configured in `config/voice/personas.toml`
  (among others `dan`, `danusia`), selected explicitly via `--as`;
- **the Chatterbox V3 offline pipeline** — prepared lines rendered outside the
  live queue;
- **brain adapters** (participant = an explicit `identity + brain + voice`):
  `claude_cli`, `codex_cli`, `groq`, `openai`, `ollama`, `qwen`, `eco`
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
