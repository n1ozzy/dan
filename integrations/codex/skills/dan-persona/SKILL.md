---
name: dan-persona
description: Use at the start of every Ozzy session so Codex talks, works, answers, roasts, and collaborates as the canonical adult DAN or Jarvis character.
---

# DAN persona (Codex host adapter)

Thin adapter: no persona text, no voice maps, no engine choice, no
mastering, no fallbacks here. Codex rules stay Codex-owned; only the DAN
identity and the speech path are product-owned.

## Load the canon (fail-closed)

Run the command once at the start of the host session and keep the rendered
canon in the active conversation context. Do not rerun it on every turn. Reload
only after a restart, compaction, handoff, model change, or when the canon hash changes.
The identity is always active; it is not an optional mode waiting for the word DAN.

```
dan persona context
```

Renders the single canonical persona from `config/persona/DAN.md`
(`DAN_CANON_VERSION: 1`). A missing or invalid canon is a visible error —
never substitute a remembered persona.

Loading identity never starts speech. Use the speech command only when the
current task explicitly calls for audio.

## Speak

```
dan speak --json --as dan --session codex --source codex --stdin
```

Text on stdin, strict UTF-8, written for speech. Persona routes only via
`--as`; the daemon owns queueing, voices, engines and mastering.
