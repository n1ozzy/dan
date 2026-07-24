---
name: trio-live
description: Live voice exchange Ozzy–DAN–Danusia through the DAN runtime.
---

# trio-live (shared thin adapter)

Thin adapter: host invocation and context only. No persona text, no voice
maps, no engine choice, no mastering, no fallbacks. The DAN daemon owns the
queue, voices and playback; persona always routes through the explicit
`--as` flag.

## Speak

One utterance, text on stdin (strict UTF-8, written FOR SPEECH):

```
dan speak --json --as dan --session trio-live --source claude --stdin
```

Replace `--source claude` with the actual calling host (`codex`,
`openclaw`, `gpt-say`, `standup`, `hook`). Queue inspection:
`dan queue list --json`; cancel: `dan queue flush --session trio-live`.

Every spoken turn uses only `--as dan` or `--as danusia` with
`--session trio-live`; host/source metadata never creates a third persona.
Barge-in and lanes are daemon policy, not adapter logic.
