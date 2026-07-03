# Spec: sprzątanie setupu — konsolidacja skilli screen-*, drobnica konfiguracyjna, diagnoza PTT

Data: 2026-07-03 · Autor: Klaudiusz (Fable 5) + akceptacja Ozzy · Status: zaakceptowany kierunkowo, do review

## Problem

1. **Trzy skille robią to samo.** `~/.claude/skills/` zawiera `agent-screen-chat`, `coxed-claude-hermes` i `screen-control`. Ten ostatni deklaruje się jako kanoniczny następca dwóch pozostałych, ale stare wciąż są ładowane i łapią te same triggery ("napisz do codexa" pasuje do wszystkich trzech). Skrypty są zdublowane w trzech katalogach i już się rozjechały (`discover_agents.sh` vs `discover-agents.sh`, dwa różne `send_agent.sh`, dwa `agents.conf`). Sesja losuje wersję → niespójne zachowanie, poprawki nie trafiają do jednego miejsca.
2. **Powtarzalne błędy interakcji ekranowej.** Realne frustracje Ozzy'ego z transkryptów: klikanie w composer/input zamiast scrollowania treści, scroll w złym obszarze, głuche wiszenie bez raportu (~20 min bez reakcji). Skill nie ma twardych zasad, które by to blokowały.
3. **`.claude/launch.json` (repo jarvis):** `cockpit-static` serwuje assets na porcie **41800 = port API jarvisd**. Konflikt, gdy daemon żyje.
4. **PTT/dyktowanie w Claude Code nie działa** (zgłoszenie Ozzy'ego 2026-07-03). Stan: `settings.json` ma **dublet** `voice: {enabled: true, mode: "hold"}` ORAZ legacy `voiceEnabled: true`; brak `~/.claude/keybindings.json` (zero własnego bindingu PTT). Przyczyna nieustalona.
5. **`tts_diag_out/`** — pusty, nieignorowany katalog-widmo w rocie repo jarvis.

## Poza zakresem

- PTT/głos **Jarvisa** (produkt: Supertonic/whisper, panel operatorski) — osobny tor, czeka na decyzję Ozzy'ego.
- Tier 1 z raportu 2026-07-03 (CLAUDE.md repo, hooki pytest/pip, /fixme, /jarvis-status), strategia effort MAX, retencja transkryptów, FIX-16 brain-cwd — odłożone, nie zatwierdzone w tym rzucie.

## Rozwiązanie

### 1. Konsolidacja skilli screen-*
- Diff zawartości `agent-screen-chat/` i `coxed-claude-hermes/` (scripts, references, agents, SKILL.md) względem `screen-control/`.
- Unikalne, wciąż wartościowe elementy (kandydat: `peek_agent.sh`; różnice w `agents.conf`/`send_agent.sh` rozstrzyga diff) wciągnąć do `screen-control` — bez zmiany jego publicznych ścieżek skryptów.
- Backup obu starych katalogów: `~/.claude/backups/skills-consolidation-2026-07-03.tar.gz`.
- Skasować `~/.claude/skills/agent-screen-chat/` i `~/.claude/skills/coxed-claude-hermes/` w całości.
- Kryterium: lista skilli zawiera dokładnie jeden skill screen-* (`screen-control`); `discover-agents.sh` działa po zmianach.

### 2. Twarde zasady interakcji w screen-control
Nowa sekcja w `SKILL.md` (+ ewentualne poprawki w skryptach, jeśli diff pokaże, że zasady da się wymusić kodem):
- Przed scrollem/klikiem: ustal, co ma focus; scroll wykonuj nad środkiem obszaru TREŚCI okna, nigdy przy focusie w composerze.
- Scroll myszą nie działa → PageUp/PageDown; zakaz klikania w input "żeby scrollować".
- Brak odpowiedzi agenta → `watch-state.sh` z limitem czasu i raport do Ozzy'ego; zakaz głuchego czekania.

### 3. launch.json
- `cockpit-static`: port 41800 → **41801** (sam serwer statyczny; panel do API łączy się z 41800 daemona niezależnie od portu serwowania).

### 4. Voice/PTT w Claude Code
- Backup `settings.json` przed edycją.
- Ustalić kanoniczny format konfiguracji voice (źródło: agent `claude-code-guide` / docs) — który klucz jest żywy, czego wymaga tryb `hold` (klawisz? uprawnienia mikrofonu dla aplikacji?).
- Usunąć klucz martwy (przewidywanie: legacy `voiceEnabled`), skonfigurować poprawnie tryb `hold`.
- Zweryfikować uprawnienie mikrofonu dla Claude (macOS: Ustawienia → Prywatność → Mikrofon) — jeśli brak, wskazać Ozzy'emu dokładnie co kliknąć.
- Wynik: PTT działa, ALBO jednoznaczny raport "to bug/ograniczenie aplikacji" + obejście. Nie zgadywać.

### 5. tts_diag_out
- `grep -rn "tts_diag_out" jarvis/ tests/ scripts/` — jeśli kod tworzy katalog: `rmdir` + wpis w `.gitignore`; jeśli nie: sam `rmdir`.

## Weryfikacja (bez testów automatycznych — to konfiguracja)

1. Lista skilli: jedna pozycja screen-*.
2. `screen-control/scripts/discover-agents.sh` przechodzi (wykrywa agentów lub czysto raportuje ich brak).
3. `preview_start` cockpit-static wstaje na 41801 przy żywym daemonie na 41800.
4. Test PTT: przytrzymanie/wyzwolenie dyktowania w Claude Code — działa albo raport z przyczyną.
5. `git status` w repo jarvis: zmiany tylko w `.claude/launch.json` (+ ewentualnie `.gitignore`), katalog `tts_diag_out` nie istnieje.

## Ryzyka i odwracalność

- Stare skille: pełny backup w tar.gz — przywrócenie w 10 s.
- `settings.json`: backup przed edycją; jeśli po zmianie voice zachowuje się gorzej — przywrócenie jednej linii.
- Zmiana portu: dotyka wyłącznie dev-podglądu assets; daemon i panel produkcyjny nietknięte.
