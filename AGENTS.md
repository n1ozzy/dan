# Agent instructions for this branch

Use current code as source of truth. Do not follow old handoff/planning docs.

Branch contract:
- Do not add approval guards, disabled-by-policy UI, mock/dev product modes, or new provider mazes.
- Brain: Claude CLI only. Prefer warm Claude CLI if configured/available.
- Conversation: one continuous Jarvis conversation for text and voice.
- Persona: config/persona/jarvis.md is authoritative. Preserve owner style and vulgarity level.
- Voice: Supertonic remains the TTS.
- Workers: disabled for now.
- Panel: render effective runtime state; do not create fake sessions/chats.

Before changing code, identify the active source of truth. Do not add another one.
