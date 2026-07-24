---
name: standup
description: Evening spoken standup; scheduling lives inside the DAN daemon (dan/jobs), this adapter is for manual runs.
---

# standup (shared thin adapter)

Thin adapter: host invocation and context only. No persona text, no voice
maps, no engine choice, no mastering, no fallbacks. The DAN daemon owns the
queue, voices and playback; persona always routes through the explicit
`--as` flag.

## Speak

One utterance, text on stdin (strict UTF-8, written FOR SPEECH):

```
dan speak --json --as dan --session standup --source standup --stdin
```

The standup voice is the canonical DAN persona; source is always `standup`. Queue inspection:
`dan queue list --json`; cancel: `dan queue flush --session standup`.

Scheduled runs are submitted by the daemon itself with `--session standup --source standup`. No material means silence, never filler.
