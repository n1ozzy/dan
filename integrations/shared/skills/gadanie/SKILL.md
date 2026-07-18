---
name: gadanie
description: Głosowy komentarz, dialog, roast, standup i raport w personie DAN; mechanika i kanon w jednym źródle (DAN runtime).
---

# gadanie (shared thin adapter)

Thin adapter: host invocation and context only. No persona text, no voice
maps, no engine choice, no mastering, no fallbacks. The DAN daemon owns the
queue, voices and playback; persona always routes through the explicit
`--as` flag.

## Speak

One utterance, text on stdin (strict UTF-8, written FOR SPEECH):

```
dan speak --json --as dan --session gadanie --source claude --stdin
```

Replace `--source claude` with the actual calling host (`codex`,
`openclaw`, `gpt-say`, `standup`, `hook`). Queue inspection:
`dan queue list --json`; cancel: `dan queue flush --session gadanie`.

Load persona context first: `dan persona context` (fail-closed on missing canon).
