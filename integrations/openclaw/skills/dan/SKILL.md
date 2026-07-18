---
name: dan
description: Use when the OpenClaw agent should act or speak as DAN (radio-dan successor slot included) through the DAN runtime.
---

# DAN (OpenClaw host adapter)

Thin adapter: host invocation and context only. No persona text, no voice
maps, no engine choice, no mastering, no fallbacks, no playlist mechanics.
The OpenClaw gateway itself stays an external host job and is not relabeled
as DAN.

## Load the canon (fail-closed)

```
dan persona context
```

Missing or invalid canon (`config/persona/DAN.md`, `DAN_CANON_VERSION: 1`)
is a visible error, never an improvised persona.

## Speak

```
dan speak --json --as dan --session openclaw --source openclaw --stdin
```

Text on stdin, strict UTF-8. Persona is ONLY the explicit `--as` flag — a
textual `DAN:` prefix inside content is spoken content, never a router.
