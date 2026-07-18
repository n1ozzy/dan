# Agent instructions for this branch

Use current code as source of truth. Do not follow old handoff/planning docs.

Branch contract:
- Do not add approval guards, disabled-by-policy UI, mock/dev product modes, or new provider mazes.
- Brain: cold Claude CLI only; no warm/session reuse and no provider chain.
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

## Voice source of truth (2026-07-18, post-cutover Release 1)
- Personas/voices/tempo/mastering: `config/voice/personas.toml` + `pronunciations.toml`
  IN THIS REPO (the old `~/.config/voice` bridge and the old `dev/dan` repo are retired).
  Canon after the 2026-07-18 casting: dan=M3/raw/1.28, danusia=F4/clean/1.28,
  jarvis=M1/clean/1.35, zaneta=F2/raw/1.15+DSP; bare codes M1-M5/F1-F5 have explicit entries.
- Single audio owner: the `dand` daemon (launchd `com.dan.dand`, API 127.0.0.1:41741).
  Speech goes ONLY through `dan speak` / the voice API; supertonic serve (:7788) is dand's
  supervised child — never spawn parallel afplay/TTS/serve; tests MUST mock the TTS layer.
- Stack map: `docs/GLOS-I-KOLEJKA.md` + `docs/CO-JEST-GDZIE.md` (in this repo).
  The old `dev/dan` checkout is parked in `~/Documents/DAN-migration-backups/` — historical only.
