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

## Voice source of truth (2026-07-12)
- Personas/voices/tempo/mastering: `~/.config/voice/personas.toml` + `pronunciations.toml`
  (shared bridge; DAN repo = voice factory, Jarvis = runtime). Canon: dan=M3/raw/1.25,
  danusia=F4/clean/1.25, jarvis=M3/clean/1.35; bare codes M1-M5/F1-F5 have explicit entries.
- Single audio owner: broker `~/Documents/dev/dan/tools/jarvis/voice_broker.py` (:7788).
  Never spawn parallel afplay/TTS; tests MUST mock the TTS layer.
- Full stack map: `~/Documents/dev/dan/docs/GLOSY-STACK-2026-07-12.md`.
