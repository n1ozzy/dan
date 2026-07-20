---
name: gadanie
description: Głosowy komentarz, dialog, roast, standup i raport w personie DAN; mechanika i kanon w jednym źródle (DAN runtime).
---

# gadanie (shared thin adapter)

Thin adapter: host invocation and context only. No persona text, no voice
maps, no engine choice, no mastering, no fallbacks. The DAN daemon (dand)
owns the queue, voices and playback; persona always routes through the
explicit `--as` flag. Legacy scripts (speak.sh, ctl.sh, dialog.sh,
pingpong.sh) are retired since the dand cutover (2026-07-18) — they talked
to the old file-driven broker that no longer exists. They live in
`_quarantine-*/` next to this file; never call them.

## Speak

One utterance, text on stdin (strict UTF-8, written FOR SPEECH — numbers as
words, no paths, no markdown):

```
dan speak --json --as dan --session gadanie --source claude --stdin
```

Replace `--source claude` with the actual calling host (`codex`,
`openclaw`, `gpt-say`, `standup`, `hook`).

## Chunking (hard rule)

Keep one utterance to ~2–4 sentences (≤ ~400 characters). For longer
content send consecutive `dan speak` calls — the daemon queue preserves
order. One oversized buffer can fail native playback entirely (the
playback watchdog gives up after its deadline and the listener hears
silence); short chunks also start faster and stay interruptible.

## Queue & diagnostics

Inspect: `dan queue list --json` · flush a session:
`dan queue flush --session gadanie` · health: `dan doctor --json`.

Load persona context first: `dan persona context` (fail-closed on missing
canon). Loading persona never starts speech.
