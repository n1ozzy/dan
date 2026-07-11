# Jarvis runtime-lab

Current branch goal: make one local Jarvis runtime actually work.

Runtime decisions:
- One Jarvis conversation. No chat/session picker in the product UI.
- Brain is Claude CLI. Warm Claude CLI is preferred when available; plain Claude CLI is fallback.
- Jarvis owns continuity through its conversation history and Memory OS context.
- TTS is Supertonic. Do not replace it while fixing runtime flow.
- Tools run directly on this local owner-controlled branch. Approval gates are disabled here.
- Workers are disabled for now. Jarvis is the single active brain.

Keep root docs short. Old handoffs/plans caused stale-agent context rot, so they are intentionally removed from root.
