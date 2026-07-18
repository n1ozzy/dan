# CLAUDE.md — dan-runtime

Źródło prawdy dla agentów: **AGENTS.md** — przeczytaj najpierw i stosuj.
Tożsamość DANa/Jarvisa pochodzi wyłącznie z
`config/persona/DAN.md` (kanon w tym repo), nie z pamięci Claude.

- Python, pyproject: `dan-runtime` v4.2.0a0, entry `dand` (`dan.cli:daemon_main`); produkcyjny daemon launchd: `com.dan.dand` → `~/.dan/bin/dand`.
- Testy: `pytest` (testpaths=`tests`, ~438 plików). Testy MUSZĄ mockować warstwę TTS (AGENTS.md) — nigdy nie spawnować prawdziwego afplay/supertonica w testach.
- Głosy/persony: kanon W TYM REPO — `config/voice/personas.toml` + `pronunciations.toml` (stary most `~/.config/voice` i repo `dev/dan` wycofane po cutoverze 2026-07-18) — NIE hardkodować wartości. Jedyny właściciel audio: daemon `dand` (`com.dan.dand`, API :41741); mowa TYLKO przez `dan speak`; supertonic serve (:7788) to superwizowane dziecko danda.
- Worktree: NIE TWORZYĆ nowych (Ozzy 2026-07-13) — praca bezpośrednio na branchach w tym checkoucie. Zastane worktree sprzątnięte 2026-07-13: WIP zachowany commitami na gałęziach `claude/fix-brain-wiring` (persony) i `claude/amazing-hawking-c80907` (codex_cli_adapter/orchestrator/testy) + patche w `~/.claude/archive/cleanup-2026-07-13/worktree-wip-patches/`. Do ewentualnego scalenia z main po przeglądzie.
- Commit tylko na wyraźną komendę Ozzy'ego.
