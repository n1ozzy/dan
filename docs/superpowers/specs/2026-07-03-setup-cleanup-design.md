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

### 2. screen-control "porządnie" — pilnowanie, czytanie całości, zero udawania (wymaganie Ozzy'ego 2026-07-03)

Cel: agent ma NA PEWNO wiedzieć, co partner (Codex/Claude/inny) napisał — nie zgadywać z jednego zrzutu — i sensownie reagować.

**a) Transkrypty z logów jako pierwsze źródło prawdy.** Dla agentów CLI okno to tylko podgląd — pełna treść leży na dysku (Claude Code: `~/.claude/projects/<projekt>/*.jsonl`; Codex CLI: katalog sesji `~/.codex/`; per-agent ścieżka konfigurowalna w `agents.conf`). Watch/odczyt najpierw czyta transkrypt (tail od ostatniej znanej pozycji), OCR ekranu służy do potwierdzenia "gdzie jest UI" i do agentów bez logów. Koniec z regresem "przeczytał pół okna i udaje".

**b) Doczytywanie całości scrollem (gdy logów brak).** Import działających wzorców z wos-bota (`$HOME/Desktop/dana rzeczy /wos-bot-runs-codex/codexBOT.py`, `HermesBOT.py` — Ozzy potwierdza: działają):
- `screencap_when_stable` — czytaj dopiero, gdy ekran przestał się zmieniać;
- `perceptual_hash` — wykrywanie zmiany ekranu i KOŃCA scrolla (hash bez zmian = koniec treści);
- kalibrowany scroll (`_measure_actual_scroll_px` + adaptacyjny krok) — bez gubienia/dublowania linii;
- scroll-and-stitch z deduplikacją nakładek (wzorzec `capture_members` + `same_scrolled_tile`/fuzzy-match) → jeden pełny odczyt okna;
- `find_text` z normalizacją/fuzzy — szukanie fraz w obserwacjach OCR;
- OCR: Apple Vision przez gotową binarkę `bin/wos_ocr` (Mach-O arm64, pl/en/es, ~50–200 ms/klatkę) — skopiować do `screen-control/bin/`;
- dowody: znacznik miejsca kliknięcia (wzorzec `annotate_tap`) + zapis klatek do katalogu roboczego — Ozzy widzi, co zrobiono;
- weryfikacja stanu PO każdej akcji (wzorzec `classify_screen`/`navigate_*`: akcja → zrzut → sprawdzenie oczekiwanego stanu → dopiero dalej).
Adaptacja: tap/swipe ADB → macOS (`osascript`/scroll eventy/PageUp/PageDown); reszta logiki przenosi się wprost.

**c) Watch-loop, który pilnuje i reaguje.** `watch-state.sh`/`coop-loop.sh` rozbudowane: pętla obserwuje transkrypt/ekran, wykrywa NOWĄ treść od partnera (pozycja w logu albo perceptual hash), czyta CAŁOŚĆ nowej treści (a nie ostatni ekran), reaguje wg protokołu współpracy; limit czasu → raport do Ozzy'ego zamiast głuchego wiszenia.

**d) Twarde zasady interakcji** (do `SKILL.md` + wymuszone w skryptach, gdzie się da):
- przed scrollem/klikiem ustal focus; scroll nad środkiem obszaru TREŚCI, nigdy przy focusie w composerze;
- scroll myszą nie działa → PageUp/PageDown; zakaz klikania w input "żeby scrollować";
- zero deklaracji "przeczytałem" bez dowodu (transkrypt/stitch/hash stabilny).

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
2a. Odczyt "całości": test na realnym oknie z treścią dłuższą niż ekran — wynik zawiera początek I koniec treści (stitch/transkrypt), nie tylko ostatni ekran.
2b. Watch: nowa wiadomość od agenta wykryta i przeczytana w całości z logu (Claude/Codex) bez OCR; `wos_ocr` odpala się i zwraca JSON na testowym zrzucie.
3. `preview_start` cockpit-static wstaje na 41801 przy żywym daemonie na 41800.
4. Test PTT: przytrzymanie/wyzwolenie dyktowania w Claude Code — działa albo raport z przyczyną.
5. `git status` w repo jarvis: zmiany tylko w `.claude/launch.json` (+ ewentualnie `.gitignore`), katalog `tts_diag_out` nie istnieje.

## Status wykonania (2026-07-03)

Zrealizowane wg planu `docs/superpowers/plans/2026-07-03-setup-cleanup.md` (wykonanie inline).
Weryfikacja: skille = jedna pozycja screen-* (`screen-control` + zarchiwizowany loud-thinking);
`discover-agents.sh` exit 0; `read-agent.sh claude-cli` czyta transkrypt z dysku (pozycja --since
działa, exit 1 przy braku nowego); `read-agent.sh codex-cli` znajduje sesje w archived_sessions;
`ocr-window.py` czyta PNG i żywe okno Terminala; `--stitch` na oknie Terminala złożył 114 linii
(więcej niż jeden ekran — początek i koniec treści); `wait-for-agent.sh` timeout → exit 75;
`coop-loop.sh` (1 min) loguje `new-content` z pełną treścią partnera — na żywo wyłapał treść
równoległej sesji FIX-06. `launch.json` = 41801 (2 wystąpienia; plik lokalny, `.claude/` w
.gitignore repo — bez commitu); `tts_diag_out` skasowany (kod go nie tworzył — bez wpisu w
.gitignore). Backup klonów: `~/.claude/backups/skills-consolidation-2026-07-03.tar.gz`.

PTT: `voiceEnabled` (legacy, martwy wg claude-code-guide) usunięty z `~/.claude/settings.json`
(backup: `settings.json.bak-przed-ptt-fix`); kanoniczny `voice: {enabled: true, mode: "hold"}`
zostaje. Wg dokumentacji voice input = funkcja CLI w terminalu (hold SPACJA przy pustym
composerze), desktop-GUI jej nie ma; mikrofon w macOS musi mieć aplikacja TERMINALA. Test ręczny
po stronie Ozzy'ego (instrukcja w raporcie sesji); jeśli hold nie łapie — `/voice tap`.

## Ryzyka i odwracalność

- Stare skille: pełny backup w tar.gz — przywrócenie w 10 s.
- `settings.json`: backup przed edycją; jeśli po zmianie voice zachowuje się gorzej — przywrócenie jednej linii.
- Zmiana portu: dotyka wyłącznie dev-podglądu assets; daemon i panel produkcyjny nietknięte.
