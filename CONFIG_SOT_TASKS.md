# CONFIG SOT — „zmieniam w jednym miejscu, a działa z czwartego"

> Źródło: przegląd 2026-07-09 na `spike/jarvis-local-runtime-check`.
> **Choroba:** ustawienia mają 5 nakładających się warstw źródeł, bez jednego
> widoku „co realnie obowiązuje i skąd". Edytujesz w złej warstwie → cicho no-op.
> **Dowód (filler):** daemon wczytuje 57 fillerów z `DEFAULT_VOICE_FILLERS`
> (`jarvis/config.py:31`), bo produkcyjny `~/.jarvis/jarvis.toml [voice]`
> (linie 73–97) **nie ma klucza `fillers`** — więc cokolwiek zmieniasz w
> `config/jarvis.toml` / `jarvis.example.toml` jest ignorowane.

## Mapa warstw (kto kogo przesłania)

| # | Warstwa | Miejsce | Status |
|---|---------|---------|--------|
| 1 | Hardkod | `jarvis/config.py` dataclass defaults (`DEFAULT_VOICE_FILLERS:31`) | baza |
| 2 | Repo TOML | `config/jarvis.toml`, `config/jarvis.example.toml` | **ignorowane w prod** |
| 3 | Home TOML | `~/.jarvis/jarvis.toml` (`_select_config_path` → `config.py:499`) | **wygrywa** |
| 4 | Shared voices | `~/.config/jarvis-voice/voices.toml` (`jarvis/voice/shared_voice.py:72`) | overlay głos/mastering (nie fillery) |
| 5 | Settings (DB) | tabela `settings`, panel — `_policy_with_settings_overlay` (app.py) | overlay „panel wygrywa" |

Ten sam wzorzec dotyczy: **fillery, głosy, mastering, policy zgód narzędzi, wybór modelu, gate pamięci.**

Status: `- [ ]` do zrobienia · `- [~]` w toku · `- [x]` zrobione.

---

# TIER 1 — natychmiastowy efekt + fundament

## - [ ] CFG-01 · Twoje fillery działają OD RĘKI (unblock + dowód diagnozy) 🟢 LOW

- **Pliki:** `~/.jarvis/jarvis.toml` (sekcja `[voice]`, ~l.73)
- **Problem:** prod-config nie ma klucza `fillers` → leci 57 hardkodów z `config.py:31`. Twoje edycje w repo-TOML są ignorowane.
- **Fix:** dopisać `fillers = [...]` (Twoja lista) do `[voice]` w `~/.jarvis/jarvis.toml`. Opcjonalnie `filler_after_ms`.
- **Testy:** `.venv/bin/python -c "from jarvis.config import load_config; print(load_config().voice.fillers)"` pokazuje Twoją listę, nie 57 defaultów.
- **DoD:** efektywny config zwraca Twoje fillery; nie ruszamy kodu.
- **Estymat:** ~5–10 min · **Zależności:** brak.

## - [ ] CFG-02 · Jeden „efektywny config" z POCHODZENIEM (rdzeń rozwiązania) 🟠 HIGH

- **Pliki:** `jarvis/config.py` (loader, `_select_config_path:491`, `_build_section`, `load_config:466`), nowy `jarvis/config_provenance.py`
- **Problem:** merge warstw (1→5) dzieje się rozproszony i cicho; nigdzie nie ma odpowiedzi „wartość X + z której warstwy + co przesłonięto".
- **Fix:** funkcja `resolve_effective_config()` zwracająca `(config, provenance)`, gdzie `provenance[key] = {value, source_layer, shadowed:[(layer,value)...]}`. Merge idzie przez JEDNĄ jawną listę warstw w ustalonej kolejności; każdy klucz taguje warstwę-zwycięzcę.
- **Testy:** klucz ustawiony w home-TOML raportuje `source=home`; ten sam klucz nadpisany w settings raportuje `source=settings` + `shadowed=[home,...]`; klucz nigdzie nie ustawiony → `source=code-default`.
- **DoD:** istnieje jedno wejście dające wartość + pochodzenie dla dowolnego klucza; żaden konsument nie merge'uje warstw po swojemu.
- **Estymat:** ~0,5 dnia · **Zależności:** brak (fundament pod CFG-03/05).

## - [ ] CFG-03 · `config explain <klucz>` (CLI) + pole w panelu 🟠 HIGH

- **Pliki:** `jarvis/cli.py` (nowa subkomenda `config explain`), `jarvis/api/routes_runtime.py` (endpoint diag), `jarvis/panel/assets/app.js` (pole „config provenance")
- **Problem:** dziś nie da się sprawdzić skąd przyszła wartość bez czytania kodu.
- **Fix:** `python -m jarvis.cli config explain voice.fillers` → drukuje wartość, warstwę-zwycięzcę, listę przesłoniętych warstw z ich wartościami i ścieżkami plików. Panel: analogiczny inspektor. Oparte na `provenance` z CFG-02.
- **Testy:** wywołanie na kluczu z home-TOML pokazuje `home` + ścieżkę; na kluczu z settings pokazuje `settings` + przesłonięty home.
- **DoD:** jedna komenda odpowiada „co obowiązuje i skąd" dla każdego klucza; działa dla fillers, model, tool-approval policy.
- **Estymat:** ~0,5 dnia · **Zależności:** CFG-02.

---

# TIER 2 — spłaszczenie i domknięcie klasy

## - [ ] CFG-04 · Spłaszcz/wyjaśnij warstwy TOML (usuń mylącą warstwę repo) 🟡 MED

- **Pliki:** `jarvis/config.py:491` (`_select_config_path`), `config/jarvis.toml`, `config/jarvis.example.toml`, `README.md`
- **Problem:** repo-`config/jarvis.toml` wygląda jak działający config, a prod czyta home-TOML → fałszywe wrażenie „ustawiłem".
- **Fix (decyzja w tasku):** albo (A) repo-TOML tylko jako `*.example` (nie ładowalny jako prod, jawny błąd gdy ktoś liczy że działa), albo (B) jasny precedens udokumentowany + ostrzeżenie w logu startowym „loaded config from: <ścieżka>". Rekomendacja: **A + linia logu przy starcie z realną ścieżką**.
- **Testy:** start daemona loguje realną ścieżkę configu; próba polegania na repo-TOML w prod jest wykrywalna.
- **DoD:** nie da się „po cichu" edytować nieładowanej warstwy bez sygnału.
- **Estymat:** ~2–3h · **Zależności:** miło po CFG-02.

## - [ ] CFG-05 · Audyt WSZYSTKICH multi-source ustawień → jeden kanoniczny odczyt 🟠 HIGH

- **Pliki:** `jarvis/config.py`, `jarvis/voice/speech.py:213` (fillers fallback), `jarvis/voice/shared_voice.py` (voices/mastering), `jarvis/tools/permissions.py` + app.py `_policy_with_settings_overlay` (tool approval), model selection, `jarvis/brain/context_builder.py:354` (memory gate)
- **Problem:** każdy z tych obszarów czyta warstwy po swojemu (np. `speech.py:213` ma własny fallback do `DEFAULT_FILLERS` — druga ścieżka obok configu).
- **Fix:** dla każdego ustawienia z >1 źródłem: jeden kanoniczny odczyt przez efektywny config z CFG-02; usunąć lokalne fallbacki/merge (np. `speech.py` bierze `config.voice.fillers` bez własnego `or DEFAULT_FILLERS`, bo default siedzi już w warstwie 1).
- **Testy:** dla każdego obszaru — zmiana w home-TOML/settings realnie zmienia zachowanie runtime (nie tylko config).
- **DoD:** zero „drugich ścieżek" odczytu; lista obszarów odhaczona.
- **Estymat:** ~0,5–1 dzień · **Zależności:** CFG-02, CFG-03 (do weryfikacji provenance).

---

# TIER 3 — szerszy przegląd sprzężeń (WYMAGA ZGODY OZZY'EGO na fan-out)

## - [ ] CFG-06 · Pełny przegląd „change-in-one-place → trzeba w czterech" 🟡 MED

- **Zakres:** nie tylko config — także wiring narzędzi (rejestracja toola w N miejscach), event flow, panel↔daemon kontrakty. Cel: znaleźć każde miejsce gdzie dodanie/zmiana czegoś wymaga zsynchronizowanej edycji w ≥3 plikach.
- **Metoda:** przejście kodu pod jednym kątem (sprzężenie/duplikacja źródeł prawdy), z listą „aby dodać X, dziś dotykasz: a, b, c, d → docelowo: jeden punkt".
- **⚠️ FIXME zabrania szerokich fan-outów bez zgody** — ten task rusza dopiero gdy Ozzy da zielone na dokładny przegląd (ew. mały zespół audytorów).
- **DoD:** mapa sprzężeń + rekomendacje „gdzie wprowadzić single-source".
- **Estymat:** ~1 dzień · **Zależności:** brak; niezależne od CFG-01..05.

---

## Kolejność rekomendowana

1. **CFG-01** (5 min, natychmiastowy efekt — Twoje fillery działają)
2. **CFG-02** (fundament: efektywny config + provenance)
3. **CFG-03** (`config explain` — koniec zgadywania „skąd")
4. **CFG-04** (spłaszczenie warstw)
5. **CFG-05** (domknięcie klasy we wszystkich obszarach)
6. **CFG-06** (szerszy przegląd sprzężeń — tylko za zgodą)
