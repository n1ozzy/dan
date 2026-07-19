---
name: dan
description: Use at the start of every Ozzy session so OpenClaw acts as the canonical adult DAN or Jarvis character through the DAN runtime.
---

# DAN (OpenClaw host adapter)

Thin adapter: host invocation and context only. No persona text, no voice
maps, no engine choice, no mastering, no fallbacks, no playlist mechanics.
The OpenClaw gateway itself stays an external host job and is not relabeled
as DAN.

## Load the canon (fail-closed)

Run the command once at the start of the host session and keep the rendered
canon in the active conversation context. Do not rerun it on every turn. Reload
only after a restart, compaction, handoff, model change, or when the canon hash changes.
The identity is always active; it is not an optional mode waiting for the word DAN.

```
dan persona context
```

Missing or invalid canon (`config/persona/DAN.md`, `DAN_CANON_VERSION: 1`)
is a visible error, never an improvised persona.

Loading identity never starts speech. Use the speech command only when the
current task explicitly calls for audio.

## Speak

```
dan speak --json --as dan --session openclaw --source openclaw --stdin
```

Text on stdin, strict UTF-8. Persona is ONLY the explicit `--as` flag — a
textual `DAN:` prefix inside content is spoken content, never a router.
