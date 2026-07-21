# CLAUDE.md — dan-runtime

Source of truth for agents: **AGENTS.md** — read it first and follow it. It
carries the branch contract, the test-baseline rule and the voice/persona
canon; nothing is repeated here.

DAN/Jarvis identity comes exclusively from `config/persona/DAN.md` (the canon in
this repo), never from Claude's memory.

Production launchd daemon: `com.dan.dand` → `~/.dan/bin/dand`.
