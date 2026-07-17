# DAN runtime-lab

Current branch goal: make one local DAN runtime actually work.

Runtime decisions:
- One DAN conversation. No chat/session picker in the product UI.
- Brain is exactly one persistent `claude_cli` stream-json process. DAN owns
  its durable session checkpoint, resumes once after transport failure, and
  keeps provider fallback chains disabled.
- DAN identity comes only from
  `/Users/n1_ozzy/Documents/dev/dan/config/persona/DAN.md`. Conversation history
  and memory are contextual data, never persona instructions.
- TTS is Supertonic. Do not replace it while fixing runtime flow.
- Tools run directly on this local owner-controlled branch. Approval gates are disabled here.
- Workers are disabled for now. DAN is the single active brain.

Keep root docs short. Old handoffs/plans caused stale-agent context rot, so they are intentionally removed from root.
