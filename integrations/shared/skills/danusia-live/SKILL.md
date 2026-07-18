---
name: danusia-live
description: Use when the agent should speak specifically as Danusia in a live DAN and Danusia voice exchange.
---

# danusia-live (shared thin adapter)

Thin adapter: host invocation and context only. No persona text, no voice
maps, no engine choice, no mastering, no fallbacks. The DAN daemon owns the
queue, voices and playback; persona always routes through the explicit
`--as` flag.

## Speak

One utterance, text on stdin (strict UTF-8, written FOR SPEECH):

```
dan speak --json --as danusia --session danusia-live --source claude --stdin
```

Replace `--source claude` with the actual calling host (`codex`,
`openclaw`, `gpt-say`, `standup`, `hook`). Queue inspection:
`dan queue list --json`; cancel: `dan queue flush --session danusia-live`.

Danusia is a persona of the runtime catalog: `--as danusia`. No voice codes or engine hints here.
