# Jarvis runtime-lab

Current branch goal: make one local Jarvis runtime actually work.

Runtime decisions:
- One Jarvis conversation. No chat/session picker in the product UI.
- Brain is exactly one persistent `claude_cli` stream-json process. Jarvis owns
  its durable session checkpoint, resumes once after transport failure, and
  keeps provider fallback chains disabled.
- DAN/Jarvis identity comes only from
  `/Users/n1_ozzy/Documents/dev/dan/config/persona/DAN.md`. Conversation history
  and memory are contextual data, never persona instructions.
- TTS is Supertonic. Do not replace it while fixing runtime flow.
- Tools run directly on this local owner-controlled branch. Approval gates are disabled here.
- Workers are disabled for now. Jarvis is the single active brain.

Keep root docs short. Old handoffs/plans caused stale-agent context rot, so they are intentionally removed from root.
