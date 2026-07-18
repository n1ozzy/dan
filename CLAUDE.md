# CLAUDE.md — dan-runtime

Źródło prawdy dla agentów: **AGENTS.md** — przeczytaj najpierw i stosuj.
Tożsamość DANa/Jarvisa pochodzi wyłącznie z
`config/persona/DAN.md` (kanon w tym repo), nie z pamięci Claude.

- Python, pyproject: `dan-runtime` v4.2.0a0, entry `dand` (`dan.cli:daemon_main`); produkcyjny daemon launchd: `com.dan.dand` → `~/.dan/bin/dand`.
- Testy: `pytest` (testpaths=`tests`, ~438 plików). Testy MUSZĄ mockować warstwę TTS (AGENTS.md) — nigdy nie spawnować prawdziwego afplay/supertonica w testach.
- Głosy/persony: wspólny most `~/.config/voice/personas.toml` + `pronunciations.toml` — NIE hardkodować wartości. Jedyny właściciel audio: broker `~/Documents/dev/dan/tools/jarvis/voice_broker.py` (port 7788).
- Worktree: NIE TWORZYĆ nowych (Ozzy 2026-07-13) — praca bezpośrednio na branchach w tym checkoucie. Zastane worktree sprzątnięte 2026-07-13: WIP zachowany commitami na gałęziach `claude/fix-brain-wiring` (persony) i `claude/amazing-hawking-c80907` (codex_cli_adapter/orchestrator/testy) + patche w `~/.claude/archive/cleanup-2026-07-13/worktree-wip-patches/`. Do ewentualnego scalenia z main po przeglądzie.
- Commit tylko na wyraźną komendę Ozzy'ego.
