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

## Utterance boundary

Keep one complete spoken thought per submission. If the active runtime rejects
its technical size, split only at a semantic boundary and preserve queue order.
Do not encode a remembered character count, sentence count, tempo, pause,
profile or emotion preset in this adapter.

## Queue & diagnostics

Inspect: `dan queue list --json` · flush a session:
`dan queue flush --session gadanie` · health: `dan doctor --json`.

Load persona context first: `dan persona context` (fail-closed on missing
canon). Loading persona never starts speech.
