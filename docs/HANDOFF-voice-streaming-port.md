# Voice/streaming port handoff — superseded

The former handoff described a pre-cutover multi-repository voice stack. Its
commands, cast, profiles, fixed speeds, temporary queues, and broker topology
are not part of the current DAN runtime. The detailed text was removed from the
working tree so it cannot be used as an implementation recipe. Git history
retains it for archaeology.

Current sources:

- `AGENTS.md`
- `docs/GLOS-I-KOLEJKA.md`
- `docs/AUDIO_RUNTIME.md`
- `docs/VOICE_STREAMING.md`
- `config/voice/personas.toml`
- `MUST-READ-GLOS-PROZODIA.md`

Do not port behavior from this historical handoff. Verify the live daemon and
current code instead.
