---
name: dobranocka
description: Overnight DAN and DANUSIA show; content lines are spoken text submitted one by one.
---

# dobranocka (shared thin adapter)

Thin adapter: host invocation and context only. No persona text, no voice
maps, no engine choice, no mastering, no fallbacks. The DAN daemon owns the
queue, voices and playback; persona always routes through the explicit
`--as` flag.

## Speak

One utterance, text on stdin (strict UTF-8, written FOR SPEECH):

```
dan speak --json --as dan --session dobranocka --source claude --stdin
```

Replace `--source claude` with the actual calling host (`codex`,
`openclaw`, `gpt-say`, `standup`, `hook`). Queue inspection:
`dan queue list --json`; cancel: `dan queue flush --session dobranocka`.

The only valid `--as` values are `dan` and `danusia`. A textual "DAN:" or
"Danusia:" prefix inside a line is spoken content, not a router. This adapter
does not accept raw voice codes or remembered speed, profile, pause, tone or
emotion presets.
