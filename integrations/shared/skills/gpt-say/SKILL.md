---
name: gpt-say
description: Use when Codex/ChatGPT/GPT should speak aloud in the Trio Live third slot.
---

# gpt-say (shared thin adapter)

Thin adapter: host invocation and context only. No persona text, no voice
maps, no engine choice, no mastering, no fallbacks. The DAN daemon owns the
queue, voices and playback; persona always routes through the explicit
`--as` flag.

## Speak

One utterance, text on stdin (strict UTF-8, written FOR SPEECH):

```
dan speak --json --as gpt --session gpt-say --source gpt-say --stdin
```

The third slot always reports itself as `--source gpt-say`. Queue inspection:
`dan queue list --json`; cancel: `dan queue flush --session gpt-say`.

Identity drift guard: the third slot ALWAYS submits `--as gpt` with `--source gpt-say` — never DAN's persona.
