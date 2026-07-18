# CLAUDE.md — dan-runtime

Source of truth for agents: **AGENTS.md** — read it first and follow it.
DAN/Jarvis identity comes exclusively from `config/persona/DAN.md`
(the canon in this repo), never from Claude's memory.

- Python, pyproject: `dan-runtime` v4.2.0a0, entry `dand` (`dan.cli:daemon_main`); production launchd daemon: `com.dan.dand` → `~/.dan/bin/dand`.
- Tests: `pytest` (testpaths=`tests`, ~438 files). Tests MUST mock the TTS layer (AGENTS.md) — never spawn a real afplay/supertonic in tests.
- Voice/personas: canon IN THIS REPO — `config/voice/` (`personas.toml` + `pronunciations.toml`); do NOT hardcode values. Speech goes ONLY through `dan speak` — details and history: AGENTS.md, "Voice source of truth".
- Commits only on Ozzy's explicit command.
