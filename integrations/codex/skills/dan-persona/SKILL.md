---
name: dan-persona
description: Use when Ozzy wants Codex to talk, work, answer, roast, or collaborate as DAN or Jarvis with the canonical adult DAN character.
---

# DAN persona (Codex host adapter)

Thin adapter: no persona text, no voice maps, no engine choice, no
mastering, no fallbacks here. Codex rules stay Codex-owned; only the DAN
identity and the speech path are product-owned.

## Load the canon (fail-closed)

```
dan persona context
```

Renders the single canonical persona from `config/persona/DAN.md`
(`DAN_CANON_VERSION: 1`). A missing or invalid canon is a visible error —
never substitute a remembered persona.

## Speak

```
dan speak --json --as dan --session codex --source codex --stdin
```

Text on stdin, strict UTF-8, written for speech. Persona routes only via
`--as`; the daemon owns queueing, voices, engines and mastering.
