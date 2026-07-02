# FIXME — Jarvis v4.1

> Źródło: deep-review kodu (8 wymiarów) + research paczek, sesja **2026-07-03**, na HEAD `d95f304`.
> 47 findingów (2 CRITICAL, 9 HIGH, 20 MEDIUM, 16 LOW) zgrupowanych w 14 tasków naprawczych + 1 task modelowy.
> Pełny kontekst wniosków: `memory/jarvis-review-2026-07-03.md`.

---

## Jak używać tego pliku

1. Bierzesz jeden task (zalecana kolejność: **Tier 1 → 2 → 3**, wewnątrz tieru wg numeru).
2. Kopiujesz blok **PROMPT** do nowej sesji Claude Code odpalonej w tym repo.
3. Po zamknięciu taska: odhaczasz `- [ ]` → `- [x]` i dopisujesz commit SHA.

Status: `- [ ]` do zrobienia · `- [~]` w toku · `- [x]` zrobione.

---

## ⚠️ ZASADY PROJEKTU (obowiązują w KAŻDYM tasku)

- **TDD:** najpierw test odtwarzający bug (czerwony), potem minimalny fix (zielony). Projekt ma ~1322 testy i dyscyplinę „zielone co krok".
- **NIE podbijaj paczek.** Wszystkie zależności są już najnowsze (supertonic 1.3.1, mlx-whisper 0.4.3, mlx-audio 0.4.4, pyobjc 12.2.1, onnxruntime 1.27.0, torch 2.12.1, numpy 2.4.6, httpx/sounddevice/soundfile/pytest — wszystkie latest). `pip install -U` to NIE jest fix. Szczegóły w tasku **FIX-15**.
- **Preflight sesji = tanio:** `git log -1` (zgodność z handoffem) + `git status --short` + health daemona. **NIE** odpalaj pełnych smoke'ów/pytest na starcie, jeśli tree czysty na tym samym HEAD — szkoda tokenów. Testy odpalasz po pierwszej własnej zmianie i na końcu.
- **NIE odpalaj multi-agentowych workflow/fan-outów** (Workflow, deep-research, 7+ subagentów) **bez wyraźnej zgody Ozzy'ego** — tokeny ograniczone.
- **Linie mogły się przesunąć** — po wcześniejszych fixach zweryfikuj `plik:linia` grepem/Read zanim edytujesz.
- **Na koniec:** pełny `pytest` + relevantny smoke, commit z rzeczowym opisem, aktualizacja statusu w tym pliku. Handoff jeśli zamykasz większy blok.
- Głos jest **wyłączony na życzenie** (hook usunięty), ale gate głosowy jest w toku za zgodą — kod głosowy nadal ma być poprawny.

---

# TIER 1 — MUSI (≈ 1,5 dnia) — po tym wszystko *niebezpieczne* jest zamknięte

## - [ ] FIX-01 · CORS `null` origin czyta prywatne dane 🟠 HIGH

- **Pliki:** `jarvis/daemon/lifecycle.py:91`, test `tests/test_api_cors.py`
- **Problem:** `"null"` jest w `ALLOWED_CORS_ORIGINS`, a token-gate obejmuje tylko `MUTATING_METHODS` (POST/PATCH/DELETE, l.94) → GET-y nietokenowane. Lokalna złośliwa strona (`file://`, origin `null`) robi `fetch('http://127.0.0.1:41800/conversations'|'/memory'|'/settings')` i eksfiltruje dane. `test_api_cors.py:21` wręcz utrwala `null` jako dozwolony.
- **Fix:** usuń `"null"` z `ALLOWED_CORS_ORIGINS`; popraw test tak, by asertował że `null` jest ODRZUCany. Rozważ (opcjonalnie, zapytaj) token na endpointach GET.
- **Testy:** test że żądanie z `Origin: null` nie dostaje `Access-Control-Allow-Origin: null`.
- **DoD:** `null` niedozwolony, test zielony, reszta CORS bez regresji.
- **Estymat:** ~20–30 min · **Zależności:** brak.

```text
Repo Jarvis v4.1 (/Users/n1_ozzy/Documents/dev/jarvis), branch main. Realizujesz task FIX-01 z FIXME.md.
ZASADY: preflight tanio (git log -1 + git status, bez pełnych smoke'ów); TDD (test przed fixem); NIE podbijaj paczek; NIE odpalaj workflow/fan-outów bez zgody; po skończeniu pełny pytest + commit + odhacz FIX-01 w FIXME.md.
PROBLEM (bezpieczeństwo, HIGH): w jarvis/daemon/lifecycle.py stała ALLOWED_CORS_ORIGINS zawiera "null". Endpointy GET nie są tokenowane (token-gate tylko dla POST/PATCH/DELETE). Skutek: lokalna strona file:// (origin "null") może przez CORS odczytać /conversations, /memory, /settings i wyeksfiltrować dane. Test tests/test_api_cors.py obecnie UTRWALA "null" jako dozwolony.
ZADANIE: Zweryfikuj aktualną linię (mogła się przesunąć). Napisz najpierw test, że żądanie z Origin: null NIE dostaje nagłówka Access-Control-Allow-Origin: null. Potem usuń "null" z allowlisty i popraw istniejący test tak, by asertował odrzucenie. Nie ruszaj dozwolonych originów 127.0.0.1/localhost. Uruchom pytest dla API/CORS. Zaproponuj (ale nie wprowadzaj bez pytania) dołożenie tokenu na GET-ach jako osobny task.
```

## - [ ] FIX-02 · git-config RCE mimo approval-gate 🟠 HIGH

- **Pliki:** `jarvis/tools/shell_tool.py:46` (`_SCRUBBED_ENV`) i wywołanie `subprocess.run` (~l.95)
- **Problem:** `_SCRUBBED_ENV` ustawia tylko PATH/LANG/LC_ALL — brak `GIT_CONFIG_NOSYSTEM`/`GIT_CONFIG_GLOBAL`. Whitelistowane `git status/log/diff` lecą w atakowalnym `cwd`; repo ze złośliwym `.git/config [core] fsmonitor = /tmp/evil.sh` wykona ten skrypt przy „niewinnym" `git status`. Operator zatwierdza tekst „git status --short", nie widząc exec sterowanego configiem.
- **Fix:** przy wywołaniu git wymuś hardening: env `GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null` + flagi `-c core.fsmonitor= -c core.hooksPath=/dev/null -c protocol.ext.allow=never`. Alternatywa do rozważenia: usunąć git z domyślnej whitelisty lub przypiąć `cwd` do zaufanego katalogu.
- **Testy:** test z tymczasowym repo mającym `fsmonitor`/hook wskazujący na plik-sentinel; asercja że sentinel NIE został wykonany.
- **DoD:** git odporny na repo-local config, test zielony, whitelist git nadal działa dla legit repo.
- **Estymat:** ~30–45 min · **Zależności:** brak.

```text
Repo Jarvis v4.1 (/Users/n1_ozzy/Documents/dev/jarvis), branch main. Realizujesz task FIX-02 z FIXME.md.
ZASADY: preflight tanio; TDD; NIE podbijaj paczek; NIE odpalaj workflow/fan-outów bez zgody; po skończeniu pytest + commit + odhacz FIX-02.
PROBLEM (bezpieczeństwo, HIGH — RCE): jarvis/tools/shell_tool.py uruchamia whitelistowane komendy git (git status/log/diff) przez subprocess.run w cwd sterowanym przez model, ze scrubowanym env (_SCRUBBED_ENV ~linia 46), które NIE ustawia GIT_CONFIG_NOSYSTEM ani GIT_CONFIG_GLOBAL. git honoruje repo-local .git/config, więc repo z [core] fsmonitor=/ścieżka/do/skryptu wykona ten skrypt przy git status — omijając approval-gate (operator widzi tylko "git status --short").
ZADANIE: Zweryfikuj linie. Napisz test: utwórz tymczasowe repo git z .git/config ustawiającym core.fsmonitor (lub core.hooksPath) na skrypt tworzący plik-sentinel; wywołaj ShellReadTool na "git status" w tym cwd; asertuj, że sentinel NIE powstał. Potem wprowadź hardening: przy komendach git dołóż do env GIT_CONFIG_NOSYSTEM=1 i GIT_CONFIG_GLOBAL=/dev/null oraz flagi -c core.fsmonitor= -c core.hooksPath=/dev/null -c protocol.ext.allow=never. Upewnij się że legalne `git status --short` w normalnym repo nadal działa. pytest.
```

## - [ ] FIX-03 · CRITICAL: współdzielone połączenie SQLite przez wątki 🔴

- **Pliki:** `jarvis/daemon/app.py` (~`:130`/`:1130` — `sqlite3.connect(check_same_thread=False)`), konsumenci: `repository`, `event_store`, `approval_gate`, `tool_run_recorder`; powiązane: `app.py:596` (wątki workerów), `jarvis/memory/manager.py:315` (transakcja + event osobno)
- **Problem:** jedno `self.conn` obsługuje **zapisy** z wielu wątków HTTP + wątków workerów, chronione dwiema rozłącznymi blokadami które się nie pokrywają. Bo to jedno connection, bloki `with conn:` dzielą jedną transakcję → rollback jednego wątku wyrzuca niezacommitowany append-only event drugiego (ciche gubienie), albo `sqlite3.ProgrammingError`. **WAL tego nie naprawia** (to przeplot na jednym connection, nie kontencja blokad).
- **Fix (DECYZJA ARCHITEKTONICZNA — do podjęcia w tasku):**
  - **Opcja A:** connection-per-wątek (WAL już wspiera wielu writerów) — factory dająca każdemu wątkowi/turze krótkotrwałe połączenie.
  - **Opcja B:** jeden proces-wide write-lock serializujący WSZYSTKIE zapisy `self.conn`.
  - Rekomendacja: **A** (mniej kontencji, zgodne z WAL), ale wymaga przejścia po wszystkich konsumentach `self.conn`.
- **Testy:** test współbieżności — N wątków równolegle append-uje eventy + zapisuje tury; asercja że żaden event nie zniknął i brak `ProgrammingError`.
- **DoD:** brak współdzielenia jednego Connection do zapisów między wątkami; test współbieżności zielony; przy okazji domknij `app.py:596` (join/drain workerów na shutdown) i `memory/manager.py:315` (zmiana+event w jednej transakcji).
- **Estymat:** ~0,5–1 dzień · **Zależności:** wykonać PRZED pełnym domknięciem workerów (FIX-07 część) i store (FIX-10).

```text
Repo Jarvis v4.1 (/Users/n1_ozzy/Documents/dev/jarvis), branch main. Realizujesz task FIX-03 z FIXME.md (CRITICAL).
ZASADY: preflight tanio; TDD; NIE podbijaj paczek; NIE odpalaj workflow/fan-outów bez zgody; testy współbieżności są upierdliwe — pisz je deterministycznie (bariery/eventy, nie sleepy). Po skończeniu pełny pytest + smoke + commit + odhacz FIX-03. To zmiana fundamentu — rozważ osobny branch.
PROBLEM (CRITICAL, integralność danych): jarvis/daemon/app.py otwiera jedno sqlite3 connection z check_same_thread=False (~linia 130/1130) i przekazuje je do TurnRepository, EventStore, ApprovalGate, ToolRunRecorder. ThreadingHTTPServer serwuje żądania równolegle; są też wątki workerów (app.py ~596). Wszystkie robią zapisy przez `with self._conn:` na TYM SAMYM connection, chronione dwiema rozłącznymi blokadami (text_turn_lock, tool_execution_lock), które się nie pokrywają. Efekt: bloki `with conn` różnych wątków dzielą jedną transakcję — rollback jednego wyrzuca niezacommitowany append-only event drugiego (ciche gubienie zdarzeń) lub rzuca sqlite3.ProgrammingError/OperationalError. WAL tego NIE naprawia (to single-connection interleaving, nie lock contention między connection).
ZADANIE: 1) Napisz deterministyczny test współbieżności odtwarzający zgubiony event / błąd transakcji (kilka wątków równolegle: append eventów + zapis tur). 2) Wybierz i uzasadnij strategię: A) connection-per-wątek/tura (rekomendowana, WAL wspiera multi-writer) — factory krótkotrwałych połączeń; albo B) jeden proces-wide write-lock na wszystkie zapisy self.conn. 3) Przejdź po WSZYSTKICH konsumentach self.conn i zastosuj strategię. 4) Przy okazji: dołóż tracking + join/drain (z timeoutem) wątków workerów w stop() przed appendem daemon.stopped, oraz opakuj w memory/manager.py zmianę bloku i jej memory-event w JEDNĄ transakcję. Uruchom pełny pytest + smoke. Jeśli decyzja A vs B jest niejednoznaczna — zarekomenduj i wykonaj lepszą jakościowo, nie odbijaj do usera.
```

## - [ ] FIX-04 · Voice: „gorący mikrofon" + przeżywalność brokera 🟠 HIGH (×4)

- **Pliki:** `jarvis/daemon/app.py:225` (stop bez `voice_recorder.stop()`), `jarvis/voice/listening.py:139` (brak timera lease'u), `jarvis/voice/broker.py:63` (broker umiera na nie-TTS wyjątku), `jarvis/voice/broker.py:57` (`stop()` nie zatrzymuje brokera)
- **Problem:** (a) `stop()` nie woła `voice_recorder.stop()` → po restarcie in-process osierocony `sox` nagrywa dalej (hot mic + puchnący dysk). (b) TTL lease'u egzekwowany tylko przy wywołaniu API; crash panelu przed puszczeniem PTT = nagrywanie w nieskończoność. (c) broker propaguje nie-`TTSEngineError` (np. „database is locked") → trwale niemy. (d) `stop()` brokera nie przerywa drain-loopa ani nie ubija executora.
- **⚠️ Te findingi były NIEZWERYFIKOWANE** (legi weryfikacji ubite) — **najpierw potwierdź na żywo** (mikrofon/sox), potem napraw.
- **Fix:** `stop()` woła `voice_recorder.stop()` przed `voice_stt.stop()` (żeby ostatni capture dotarł do STT), `voice_recorder=None`; daemon-side sweeper (`threading.Timer` lub pętla brokera) wołający `active()/_expire_stale` cyklicznie; try/except (Exception, nie tylko TTSEngineError) wokół drain + backoff w `_run`; `stop()` sprawdza `_stop.is_set()`, woła `stop_playback()` i `_executor.shutdown(cancel_futures=True)`.
- **Testy:** stop() zatrzymuje recorder; wygasły lease bez klienta zatrzymuje recorder przez sweeper; wyjątek DB w brokerze nie ubija wątku; stop() brokera realnie kończy pętlę.
- **DoD:** brak osieroconego sox po stop/restart; lease samoegzekwuje się bez klienta; broker przeżywa wyjątek DB i daje się zatrzymać. Potwierdzenie na żywo udokumentowane.
- **Estymat:** ~0,5–1 dzień (w tym potwierdzenie na sprzęcie) · **Zależności:** brak; refactor anti-echo/cancel to osobny FIX-09.

```text
Repo Jarvis v4.1 (/Users/n1_ozzy/Documents/dev/jarvis), branch main. Realizujesz task FIX-04 z FIXME.md (4× HIGH, prywatność/„gorący mikrofon").
ZASADY: preflight tanio; TDD; NIE podbijaj paczek; NIE odpalaj workflow/fan-outów bez zgody; głos jest wyłączony na co dzień, ale kod ma być poprawny. Po skończeniu pytest + smoke głosowy + commit + odhacz FIX-04.
UWAGA: te 4 findingi były NIEZWERYFIKOWANE (adwersaryjna weryfikacja została ubita). NAJPIERW potwierdź każdy na żywo (mikrofon, sox) albo deterministycznym testem z mockiem, ZANIM naprawisz. Jeśli któryś okaże się fałszywy — udokumentuj i pomiń.
PROBLEMY:
(a) jarvis/daemon/app.py ~225: stop() nie woła self.voice_recorder.stop() (tylko broker/stt/gateway) → po restarcie in-process stary sox nagrywa dalej bez właściciela (hot mic + rosnący WAV).
(b) jarvis/voice/listening.py ~139: TTL lease'u sprawdzany tylko wewnątrz acquire/release/active — brak timera po stronie daemona. Crash panelu przed button-up → sox nagrywa godzinami po TTL.
(c) jarvis/voice/broker.py ~63: _run nie ma catch-all — nie-TTSEngineError (np. sqlite "database is locked", OSError zniknionego binarnego supertonic) zabija wątek brokera → Jarvis trwale niemy, kolejka rośnie bez sygnału.
(d) jarvis/voice/broker.py ~57: stop() nie działa — drain loop ignoruje _stop, join(timeout=5) cicho się poddaje, _executor nigdy nie ubity.
ZADANIE: Dla każdego: test odtwarzający → fix. (a) stop() woła voice_recorder.stop() PRZED voice_stt.stop(), potem voice_recorder=None. (b) mały sweeper daemon-side (threading.Timer lub pętla brokera) cyklicznie wołający active()/_expire_stale. (c) try/except Exception wokół drain w _run z logowaniem + backoff; catch Exception (nie tylko TTSEngineError) wokół syntezy. (d) stop() sprawdza _stop.is_set() w pętli, woła engine.stop_playback() i _executor.shutdown(cancel_futures=True), start() odmawia gdy stary wątek żyje. pytest + smoke.
```

---

# TIER 2 — POWINNO (≈ 2–3 dni) — rób sukcesywnie

## - [ ] FIX-05 · Stany tury / orchestrator: udany turn jako FAILED + utknięcia 🟠 HIGH + 🟡 MED×3

- **Pliki:** `jarvis/daemon/app.py:254` (stop race — HIGH, POTWIERDZONY), `jarvis/turns/orchestrator.py:427` (FINISHED→FAILED), `:516` (stuck AWAITING_APPROVAL), `:1150` (wedge non-IDLE), `jarvis/daemon/state_machine.py:100` (brak locka)
- **Problem:** wspólny root — przejścia stanu nie tolerują terminalnych/błędnych ścieżek. `stop()`→STOPPING bez `text_turn_lock` → udany, wypowiedziany turn zapisany jako FAILED. Wyjątek po `_turns.finish()` przepisuje skończony turn na FAILED. Nieudana kontynuacja zostawia turn na zawsze w AWAITING_APPROVAL. Recovery potrafi zablokować runtime w nie-IDLE. State machine bez locka.
- **Fix:** failure-handler ograniczony do fazy generacji (guard na status tury przed `fail()`); `stop()` bierze `text_turn_lock`/`tool_execution_lock` przed STOPPING (lub terminalne IDLE toleruje STOPPING); nieudana kontynuacja → FAILED/re-runnable zamiast dyndać; recovery resetuje `_state` in-memory jako ostatnia deska; lock na `transition()`.
- **Testy:** turn skończony podczas shutdown pozostaje FINISHED; wyjątek po finish() nie przepisuje na FAILED; nieudana kontynuacja daje status terminalny; równoległe `transition()` bez wyścigu.
- **DoD:** żadne przejście nie przeklasyfikowuje skończonej tury; brak stanów-pułapek; state machine atomowa.
- **Estymat:** ~0,5 dnia · **Zależności:** miło po FIX-03 (spójność locków), ale niezależne.

```text
Repo Jarvis v4.1 (/Users/n1_ozzy/Documents/dev/jarvis), branch main. Realizujesz task FIX-05 z FIXME.md (1× HIGH potwierdzony + 3× MED + 1 LOW).
ZASADY: preflight tanio; TDD; NIE podbijaj paczek; NIE fan-outów bez zgody; po skończeniu pytest + commit + odhacz FIX-05.
PROBLEM (spójny root: przejścia stanu nie tolerują ścieżek terminalnych/błędnych):
- app.py ~254 (HIGH, potwierdzony): stop() przechodzi w STOPPING bez text_turn_lock; turn w locie kończy się, wypowiada odpowiedź, potem transition(IDLE) rzuca StateTransitionError (STOPPING terminalny) → recovery przepisuje SKOŃCZONY turn na FAILED i emituje TURN_FAILED.
- orchestrator.py ~427: dowolny wyjątek po _turns.finish() przeklasyfikowuje FINISHED na FAILED.
- orchestrator.py ~516: nieudana kontynuacja tool-result zostawia turn na zawsze w AWAITING_APPROVAL.
- orchestrator.py ~1150: _recover_runtime_after_failure potrafi trwale zablokować runtime w nie-IDLE, gdy append eventu recovery padnie.
- state_machine.py ~100: transition() robi check-then-append-then-set na współdzielonym _state bez locka.
ZADANIE: Zweryfikuj linie. Napisz testy odtwarzające każdy przypadek. Fixy: ogranicz failure-handler do fazy generacji (sprawdzaj status tury przed fail() — nie ruszaj FINISHED/AWAITING_APPROVAL); stop() bierze text_turn_lock+tool_execution_lock przed STOPPING LUB terminalne IDLE toleruje STOPPING bez failowania tury (wybierz czystszą opcję); nieudana kontynuacja → FAILED lub re-runnable; recovery resetuje _state=IDLE in-memory nawet gdy persist eventu padnie; dodaj lock na transition() (validate+append+assign atomowo). pytest.
```

## - [ ] FIX-06 · API hardening: DNS rebinding, slowloris, WS cap 🟡 MED×3 + LOW

- **Pliki:** `jarvis/daemon/lifecycle.py:209` (brak walidacji Host-header), `:622` (brak socket timeout), `:508` (brak capa na sesje WS), `:218` (401 nie drenuje body)
- **Problem:** brak walidacji Host → localhost binding pokonywalny DNS rebindingiem dla nietokenowanych GET-ów. Brak socket timeout + blocking `rfile.read(Content-Length)` → slowloris trzyma wątek. Brak limitu równoległych sesji `/stream` (każda = osobne SQLite + wątek). 401 nie drenuje body → desync keep-alive.
- **Fix:** odrzucaj Host spoza `{127.0.0.1, localhost, ::1}:port`; `handler.timeout` (np. 10s) + deadline na read; cap sesji WS z odrzuceniem ponad limit; drain body lub `Connection: close` przy 401.
- **Testy:** obcy Host odrzucony; wolne body nie blokuje w nieskończoność; N+1 sesja WS odrzucona; 401 nie desynca.
- **DoD:** cztery obrony na miejscu, testy zielone.
- **Estymat:** ~2–3h · **Zależności:** brak.

```text
Repo Jarvis v4.1 (/Users/n1_ozzy/Documents/dev/jarvis), branch main. Realizujesz task FIX-06 z FIXME.md (API hardening: 3× MED + 1 LOW).
ZASADY: preflight tanio; TDD; NIE podbijaj paczek; NIE fan-outów bez zgody; po skończeniu pytest + commit + odhacz FIX-06.
PROBLEM (jarvis/daemon/lifecycle.py):
- ~209: brak walidacji nagłówka Host — localhost binding to jedyna obrona nietokenowanych GET-ów, którą DNS rebinding pokonuje.
- ~622: ThreadingHTTPServer handler bez socket timeout + _read_json_body robi blocking rfile.read(Content-Length) → klient trzyma wątek w nieskończoność (slowloris).
- ~508: brak capa na równoległe sesje WS /stream; każda otwiera własne SQLite i trzyma wątek na czas życia.
- ~218: żądanie mutujące odrzucone 401 (zły/brak token) nie drenuje body → desync keep-alive.
ZADANIE: Zweryfikuj linie. Testy + fixy: odrzucaj żądania z Host spoza {127.0.0.1, localhost, ::1}:<port> przed dispatch; ustaw handler.timeout (np. 10s) i/lub deadline na read, zamykaj wolne/częściowe body; ogranicz liczbę jednoczesnych sesji /stream i odrzucaj ponad cap (close/503); przy 401 drenuj body lub ustaw Connection: close. pytest.
```

## - [ ] FIX-07 · Brain/workers: stdin deadlock, atomic claim, cap kontekstu 🟠 HIGH + MED×3 + LOW×3

- **Pliki:** `jarvis/brain/claude_cli_adapter.py:121` (stdin deadlock — HIGH), `jarvis/workers/broker.py:174` (double-run job), `jarvis/brain/context_builder.py:419` (input_text nieograniczony), `:351` (prompt-injection labeling), `:213` (zły settings row = DoS tury), `jarvis/brain/tool_call_parser.py:90` (ufa `risk` od modelu), `jarvis/brain/claude_cli_adapter.py:521` (denylist flag), `:351` (blocking bez cancel)
- **Problem:** streaming zapisuje cały prompt na stdin ZANIM uzbroi watchdog i zacznie drenować stdout/stderr → duży prompt (a `_fit_budget` nie tnie `input_text`) deadlockuje bez timeoutu. Job QUEUED→RUNNING nieatomowo → odpalany dwa razy. Parser ufa polu `risk` od modelu. Denylist flag mija równoważne bypassy. Blocking-generate bez barge-in.
- **Fix:** uzbrój watchdog + drainy PRZED zapisem stdin, stdin z osobnego wątku; atomic claim (`UPDATE ... WHERE status='queued'`, działaj przy rowcount==1); utnij `input_text` wg budżetu; risk z `BrainToolSpec` nie od modelu; allowlist flag; worker-job prompt jako oznaczone dane untrusted; zły settings row skip+default zamiast abort; blocking-generate dostaje uchwyt cancel.
- **Testy:** duży prompt nie deadlockuje; job nie odpalony dwa razy; risk brany ze speca; nadmiarowy input przycięty.
- **DoD:** brak deadlocka stdin; claim atomowy; kontekst ograniczony; risk niezależny od modelu.
- **Estymat:** ~3–5h · **Zależności:** cap `input_text` łagodzi deadlock — zrób razem. Atomic claim spójny z FIX-03.

```text
Repo Jarvis v4.1 (/Users/n1_ozzy/Documents/dev/jarvis), branch main. Realizujesz task FIX-07 z FIXME.md (1× HIGH + 3× MED + 3× LOW, brain/workers).
ZASADY: preflight tanio; TDD; NIE podbijaj paczek (adaptery muszą umieć konsumować stream-json — zachowaj to); NIE fan-outów bez zgody; po skończeniu pytest + commit + odhacz FIX-07.
PROBLEMY:
- claude_cli_adapter.py ~121 (HIGH): streaming zapisuje CAŁY prompt na stdin dziecka PRZED uzbrojeniem watchdoga i PRZED drenażem stdout/stderr. Duży prompt (osiągalny, bo context_builder._fit_budget nie tnie input_text) → parent blokuje na stdin.write gdy pipe się zapełni, dziecko blokuje na niedrenowanym stdout/stderr → trwały deadlock bez timeoutu, wyciek dziecka.
- workers/broker.py ~174 (MED): nieatomowy read-check-then-update QUEUED→RUNNING → ten sam job odpalony dwa razy.
- context_builder.py ~419 (MED): _fit_budget nie tnie input_text → prompt/stdin nieograniczony mimo context_budget_chars.
- tool_call_parser.py ~90 (MED): parser ufa polu 'risk' (i nazwie narzędzia) od modelu zamiast brać z zarejestrowanego speca.
- context_builder.py ~351 (LOW): surowy user-text worker-joba wstrzyknięty jako system-role = prompt-injection surface.
- context_builder.py ~213 (LOW): jeden zły wiersz settings / zły config int wywala całą budowę tury.
- claude_cli_adapter.py ~521 (LOW): _reject_unsafe_args to denylist na jeden token, mija równoważne flagi bypass.
- claude_cli_adapter.py ~351 (LOW): blocking generate bez uchwytu cancel — nie da się barge-inować.
ZADANIE: Zweryfikuj linie. Testy + fixy: uzbrój watchdog i uruchom drenaż stdout/stderr PRZED zapisem stdin, pisz stdin z osobnego wątku (żaden etap nie jest nietimeoutowany/niedrenowany); atomic claim UPDATE worker_jobs SET status='running' WHERE id=? AND status='queued', działaj tylko przy rowcount==1; utnij input_text wg budżetu (z markerem) lub odrzuć powyżej twardego limitu; bierz risk z pasującego BrainToolSpec, odrzucaj nazwy spoza oferty; podawaj worker-job prompt jako oznaczone dane untrusted (rola user/tool, cytowane); zły settings row skip+log+default zamiast abort; allowlist bezpiecznych flag zamiast denylisty; przekaż generation_registry do blocking-generate. Zachowaj obsługę stream-json. pytest.
```

## - [ ] FIX-08 · Redakcja sekretów i containment plików 🟡 MED×2 + LOW×2

- **Pliki:** `jarvis/tools/file_tool.py:76` (file_read persystuje pełną treść, redakcja przecenia ochronę), `jarvis/tools/registry.py:837` (słabsza reguła redakcji niż `redaction.py`), `jarvis/tools/ui_tool.py:139` (brak bana control-char), `jarvis/tools/file_tool.py:120` (TOCTOU symlink na write)
- **Problem:** `file_read` zapisuje pełną treść pliku do tool_runs/events, a redakcja łapie krótką listę kształtów tokenów → docstring „secret redaction applies" przecenia ochronę. `registry._redact` używa słabszego substring niż wspólne `is_sensitive_key` (bez normalizacji separatorów). `UiTypeTool` bez guardu newline (inwariant „Enter przy człowieku" zależy od backendu). `file_write` TOCTOU na symlinku rodzica.
- **Fix:** nie persystuj pełnej treści (hash/preview) lub dodaj high-recall detektory (PEM, connection stringi, entropia) + size-cap; `registry._redact` woła wspólne `is_sensitive_key()/redact_secrets()`; guard control-char w `UiTypeTool` (mirror `validate_paste_text`); `O_NOFOLLOW`/`openat` lub re-walidacja tuż przed `os.replace`.
- **DECYZJA:** czy w ogóle persystować treść `file_read` — to model danych/prywatności (rozstrzygnij w tasku).
- **Testy:** sekret w pliku nie ląduje w evencie; klucz z separatorem zamaskowany; newline w UiType odrzucony; symlink-swap nie wychodzi poza root.
- **Estymat:** ~3–4h · **Zależności:** brak.

```text
Repo Jarvis v4.1 (/Users/n1_ozzy/Documents/dev/jarvis), branch main. Realizujesz task FIX-08 z FIXME.md (2× MED + 2× LOW, redakcja/containment).
ZASADY: preflight tanio; TDD; NIE podbijaj paczek; NIE fan-outów bez zgody; po skończeniu pytest + commit + odhacz FIX-08.
PROBLEMY:
- file_tool.py ~76 (MED): file_read zwraca pełną treść pliku, która jest persystowana do tool_runs/events; jedyna redakcja (redaction.py SECRET_VALUE_PATTERNS) łapie krótką listę kształtów tokenów i mija większość sekretów — docstring "secret redaction applies" przecenia ochronę.
- registry.py ~837 (MED): registry._redact używa any(part in lowered ...) na nienormalizowanym kluczu, więc klucze z separatorami nie są maskowane — rozjazd z redaction.py:is_sensitive_key, które normalizuje separatory.
- ui_tool.py ~139 (LOW): UiTypeTool nie banuje control-char/newline (w przeciwieństwie do TerminalPasteTool), więc inwariant "Enter zostaje przy człowieku" zależy tylko od backendu.
- file_tool.py ~120 (LOW): file_write realpath-then-write to TOCTOU — containment sprawdzany na ścieżce z czasu checku, ale open()/os.replace re-resolvuje symlink rodzica przy zapisie.
ZADANIE: Zweryfikuj linie. DECYZJA do podjęcia i uzasadnienia: czy file_read w ogóle ma persystować pełną treść — rekomendacja: NIE (zapisuj hash/preview), albo dodaj high-recall detektory (bloki PEM, connection stringi, wysoka entropia) + niezależny size-cap na to co persystowane. Ujednolić: registry._redact ma wołać wspólne is_sensitive_key()/redact_secrets() z security/redaction.py. Dodaj guard control-char do UiTypeTool (mirror validate_paste_text — odrzuć/normalizuj newline). file_write: otwieraj rodzica z O_NOFOLLOW/openat i pisz relatywnie do zwalidowanego fd, albo re-waliduj finalną ścieżkę tuż przed os.replace. Testy dla każdego. pytest.
```

## - [ ] FIX-09 · Voice: refactor toru anulowania + anti-echo (z migracją DB) 🟡 MED×5 + LOW×2

- **Pliki:** `jarvis/voice/broker.py:103` (TOCTOU cancel→nowy player), `jarvis/voice/cancellation.py:104` (snapshot pomija późne wiersze), `jarvis/voice/anti_echo.py:38` (cancelled/failed queued text w korpusie echo), `jarvis/voice/listening.py:66` (renewal nie restartuje martwego sox), `jarvis/voice/recorder.py:168` (locked-mode = jeden rosnący capture), `jarvis/voice/stt.py:90` (whisper future bez timeoutu), `jarvis/voice/queue.py:88` (global seq interleaving — z FIX-04? nie, tu)
- **Problem:** rodzina bugów toru anulowania i korpusu echo. TOCTOU między checkiem a `engine.play`. Snapshot anulowania pomija wiersze dołożone po sweepie. Tekst nigdy niewypowiedziany (cancelled/failed z „queued") wchodzi do korpusu echo — sprzecznie z kontraktem modułu. Renewal lease'u nie restartuje martwego sox. Locked-mode nie segmentuje. Whisper future bez timeoutu blokuje workera. Kolejka sortuje po globalnym seq (per-turn) → przeplot zdań.
- **Fix:** tombstone anulowanych turn_id + `enqueue` odmawia dla nich; re-check statusu pod `_player_lock` tuż przed `Popen`; kolumna `spoken_at` (**migracja DB**) → tylko realnie wypowiedziane wiersze w korpusie echo; `_sync_recorder()` na renewal; segmentacja locked-mode (rolling interval / split na ciszy); timeout na `future.result()` + recykling executora; order-by rowid ASC albo (first-rowid-of-turn, seq).
- **⚠️ Wymaga migracji DB** (`jarvis/store/migrations.py` — idempotentna, version guard). Wymaga potwierdzenia na żywo.
- **Estymat:** ~0,5–1 dzień · **Zależności:** PO FIX-04 (hot-mic). Anti-echo to fundament PRZED tuningiem VAD.

```text
Repo Jarvis v4.1 (/Users/n1_ozzy/Documents/dev/jarvis), branch main. Realizujesz task FIX-09 z FIXME.md (5× MED + 2× LOW, refactor toru anulowania głosu + anti-echo). Zrób PO FIX-04.
ZASADY: preflight tanio; TDD; NIE podbijaj paczek; NIE fan-outów bez zgody; głos wyłączony na co dzień ale kod ma być poprawny; migracje w store/migrations.py muszą zostać idempotentne z version guard. Potwierdź kluczowe bugi na żywo (sox/whisper/TTS) lub deterministycznym mockiem przed fixem. Po skończeniu pytest + smoke głosowy + commit + odhacz FIX-09.
PROBLEMY (rodzina toru anulowania i korpusu echo):
- broker.py ~103 (MED): TOCTOU między checkiem _still_speaking a engine.play — pełny cancel (queue flip + stop_playback) w luce nic nie ubija, broker odpala nowy player dla anulowanego chunka.
- cancellation.py ~104 (MED): _cancel_queued snapshotuje turn_ids/rows raz; wiersze dołożone po sweepie a przed faktycznym raise ubitej generacji (in-flight delty, FillerTimer) są pominięte; re-cancel orchestratora jest DB-only (nie zatrzymuje playera).
- anti_echo.py ~38 (MED+LOW): _SPOKEN_STATUSES ma 'cancelled', ale cancel_turn flipuje też wiersze 'queued' → tekst który nigdy nie zabrzmiał wchodzi do korpusu echo (sprzeczne z kontraktem l.5-8); dodatkowo 'failed' wykluczone nawet gdy audio częściowo poszło.
- listening.py ~66 (MED): acquire() na istniejącym lease tylko przedłuża TTL i wraca — nie woła _sync_recorder, więc martwy sox nie jest restartowany przy odnowieniu.
- recorder.py ~168 (MED): locked-mode = jeden wiecznie rosnący capture; renewals przedłużają jedną sesję sox, cały WAV czytany do RAM, jeden przebieg whisper, zero transkryptu do końca lease.
- stt.py ~90 (LOW): future.result() bez timeoutu — zawieszony MLX/Metal blokuje jedynego workera pipeline'u na zawsze.
- queue.py ~88 (jeśli nie zrobione w FIX-04): claim_next sortuje po globalnym seq, a seq jest per-turn → zdania dwóch tur przeplatają się.
ZADANIE: Zweryfikuj linie. Testy + fixy: cancel_active_speech zapisuje tombstone anulowanych turn_id, VoiceQueue.enqueue odmawia wierszy dla tombstonowanych tur; re-check statusu pod _player_lock tuż przed Popen (lub flaga 'cancelled' sprawdzana przed spawn); dodaj kolumnę spoken_at przez NOWĄ idempotentną migrację i włączaj do korpusu echo tylko wiersze które realnie były 'speaking' (obejmij też 'failed' po częściowym audio); wołaj _sync_recorder() na renewal; segmentuj locked-mode capture (rolling interval / split na ciszy) by transkrypty płynęły; timeout na future.result() skalowany do długości nagrania + recykling executora na timeout; order-by rowid ASC (lub (first-rowid-of-turn, seq)). pytest + smoke.
```

## - [ ] FIX-10 · Store/memory/paths: uprawnienia, LIMIT, transakcje 🟡 MED×2 + LOW×2

- **Pliki:** `jarvis/paths.py:48` (DB/logi world-readable), `jarvis/memory/manager.py:318` (brak SQL LIMIT), `:315` (mutacja+event osobno), `jarvis/config.py:45` (martwe flagi)
- **Problem:** `ensure_runtime_dirs()` robi goły `mkdir` → plik DB i logi world-readable (0644), choć reszta sekretów jest 0600/0700. `_read_blocks` bez `LIMIT` ładuje całą tabelę i tnie w Pythonie. Mutacja pamięci i audit-event w osobnych transakcjach. `destroy_existing`/`migrations` parsowane, nieużywane.
- **Fix:** chmod home 0700, DB/logi 0600 po utworzeniu; wepchnij filtr+ORDER BY+LIMIT do SQL; jedna transakcja na zmianę+event (spójne z FIX-03); usuń lub zaimplementuj martwe flagi.
- **Estymat:** ~2–3h · **Zależności:** transakcja spójna z decyzją FIX-03.

```text
Repo Jarvis v4.1 (/Users/n1_ozzy/Documents/dev/jarvis), branch main. Realizujesz task FIX-10 z FIXME.md (2× MED + 2× LOW, store/memory/paths).
ZASADY: preflight tanio; TDD; NIE podbijaj paczek; NIE fan-outów bez zgody; po skończeniu pytest + commit + odhacz FIX-10.
PROBLEMY:
- paths.py ~48 (MED): ensure_runtime_dirs() tworzy ~/.jarvis, logs, runtime gołym mkdir (bez mode/chmod); plik SQLite (sqlite3.connect) i log FileHandler zostają world-readable (0755/0644), choć kod chmoduje inne sekrety 0600/0700 (security/transport.py, voice/*, macos/screen.py).
- memory/manager.py ~318 (MED): _read_blocks robi SELECT ... ORDER BY updated_at DESC BEZ SQL LIMIT; list_blocks() ładuje całą pasującą tabelę do Pythona i dopiero tnie blocks[:limit] — argument limit nie ogranicza zapytania.
- memory/manager.py ~315 (LOW): mutacja bloku i jej audit-event to dwie osobne transakcje (_update_block commit w swoim `with self._conn:`, potem _append_memory_event w innej; promote dokłada trzecią). Brak transakcji spinającej zmianę stanu + event.
- config.py ~45 (LOW): DatabaseConfig.destroy_existing i .migrations są parsowane, ale nigdzie w jarvis/ nieużywane (martwe flagi).
ZADANIE: Zweryfikuj linie. Testy + fixy: chmod runtime home 0700 i plik DB/log 0600 po utworzeniu (mirror hardeningu tokenu transportu); wepchnij active-filter + ORDER BY + LIMIT (bind budżetu) do SQL w _read_blocks; opakuj zapis wiersza i insert eventu w jedną transakcję (spójnie z decyzją FIX-03); usuń martwe flagi config albo je zaimplementuj/udokumentuj jako no-op. pytest.
```

---

# TIER 3 — MIŁO MIEĆ (≈ 1–2 dni) — dług operacyjny, rób przy okazji

## - [ ] FIX-11 · Rotacja logów 🟡 MED

- **Pliki:** `jarvis/logging.py:46`
- **Problem:** brak rotacji dla always-on (`RunAtLoad`) daemona; gate review powołuje się na rotację, której nie ma.
- **Fix:** `RotatingFileHandler`/`TimedRotatingFileHandler` (maxBytes+backupCount) dla `jarvisd.log`; udokumentuj rotację stdout/stderr launchd (newsyslog); popraw referencję w Gate G review.
- **Estymat:** ~1h.

```text
Repo Jarvis v4.1 (/Users/n1_ozzy/Documents/dev/jarvis), branch main. Realizujesz task FIX-11 z FIXME.md (MED, rotacja logów).
ZASADY: preflight tanio; TDD; NIE podbijaj paczek; NIE fan-outów bez zgody; po skończeniu pytest + commit + odhacz FIX-11.
PROBLEM: jarvis/logging.py ~46 — brak jakiejkolwiek rotacji logów dla always-on daemona (launchd RunAtLoad); dodatkowo gate review powołuje się na rotację, która nie istnieje → log rośnie bez ograniczeń.
ZADANIE: Zweryfikuj linię. Test + fix: dodaj RotatingFileHandler lub TimedRotatingFileHandler (maxBytes + backupCount) dla jarvisd.log; udokumentuj rotację stdout/stderr launchd (newsyslog lub krok rotate); popraw/uściślij referencję do rotacji w Gate G review (docs/). pytest.
```

## - [ ] FIX-12 · Minimalne CI 🟡 (dług, brak egzekucji „zielone co krok")

- **Problem:** `docs/MASTER_PLAN.md:63` manduje zielone testy co krok, ale nic tego nie egzekwuje. Repo ma `origin/main` (GitHub) → Actions możliwe.
- **Fix:** minimalny workflow: `pytest` + `ruff` + smoke matrix. Albo świadomie zapisz w planie, że egzekucja jest manualna-by-decree i dlaczego.
- **Estymat:** ~2–4h.

```text
Repo Jarvis v4.1 (/Users/n1_ozzy/Documents/dev/jarvis), branch main. Realizujesz task FIX-12 z FIXME.md (dług: brak CI).
ZASADY: preflight tanio; NIE podbijaj paczek; NIE fan-outów bez zgody; po skończeniu odpal lokalnie to co ma robić CI, commit + odhacz FIX-12.
PROBLEM: docs/MASTER_PLAN.md ~63 manduje zielone testy na każdym kroku, ale nie ma żadnego CI, które to egzekwuje. Repo ma remote origin/main na GitHubie.
ZADANIE: Dodaj minimalny GitHub Actions workflow (.github/workflows/) odpalający pytest + ruff (target py311, config z pyproject) + smoke matrix projektu na push/PR do main. Użyj Pythona >=3.11. Uwaga: część testów głosowych/macos może wymagać skipów na CI bez sprzętu — oznacz je markerami i udokumentuj co CI pokrywa a co nie. Jeśli CI dla części macos jest niewykonalne — zapisz w planie że egzekucja jest częściowo manualna-by-decree i dlaczego. Zweryfikuj że workflow jest poprawny składniowo.
```

## - [ ] FIX-13 · Backup/restore SQLite 🟡 LOW (single-file source of truth bez recovery)

- **Pliki:** `jarvis/paths.py:44`, `docs/DECISIONS.md`
- **Fix:** runbook backup/restore (`sqlite .backup` na timerze lub udokumentowana procedura kopii) + nota o recovery przy korupcji; decyzja do `DECISIONS.md`.
- **Estymat:** ~1–2h.

```text
Repo Jarvis v4.1 (/Users/n1_ozzy/Documents/dev/jarvis), branch main. Realizujesz task FIX-13 z FIXME.md (LOW, backup SQLite).
ZASADY: preflight tanio; NIE podbijaj paczek; NIE fan-outów bez zgody; po skończeniu commit + odhacz FIX-13.
PROBLEM: jednoplikowa baza SQLite jest source of truth (jarvis/paths.py ~44), ale ani plan, ani runbooki nie mają procedury backup/restore ani recovery przy korupcji.
ZADANIE: Napisz runbook backup/restore (np. sqlite3 .backup na timerze albo udokumentowana procedura kopii przy zatrzymanym daemonie) + notę o recovery przy korupcji (integrity_check, odtworzenie z backupu). Zapisz decyzję w docs/DECISIONS.md jako nowy ADR. Bez zmian w kodzie jeśli niepotrzebne — to głównie dokumentacja/runbook.
```

## - [ ] FIX-14 · Sync dokumentacji z rzeczywistością 🟡 MED + LOW×2

- **Pliki:** `README.md:16` (mówi że daemon nie startuje), `pyproject.toml` (label „4.1 scaffold"), `docs/REVIEW_HANDOFF.md:74` (PTT jako backlog choć w HEAD), `docs/MASTER_PLAN.md:63` (nota CI)
- **Fix:** przepisz README na stan post-A-H (daemon/panel/voice żywe); zbij wersję/opis pyproject z etykiety „scaffold"; zaktualizuj REVIEW_HANDOFF (PTT/listening dostarczone); dopisz notę o CI.
- **Estymat:** ~1–2h · **Zależności:** rób po FIX-12 (żeby nota CI była prawdziwa).

```text
Repo Jarvis v4.1 (/Users/n1_ozzy/Documents/dev/jarvis), branch main. Realizujesz task FIX-14 z FIXME.md (doc drift). Najlepiej po FIX-12.
ZASADY: preflight tanio; NIE podbijaj paczek; NIE fan-outów bez zgody; po skończeniu commit + odhacz FIX-14. Zweryfikuj każdą tezę w docsach z realnym stanem kodu przed przepisaniem.
PROBLEM (doc drift — front-door docs kłamią):
- README.md ~16: mówi nowemu czytelnikowi, że daemon nie startuje — nieprawda po fazach A-H.
- pyproject.toml: wersja/opis wciąż z etykietą "4.1 scaffold".
- docs/REVIEW_HANDOFF.md ~74: listuje panel PTT/listening jako niezaczęty backlog, choć HEAD (d95f304) już to dostarczył.
- docs/MASTER_PLAN.md ~63: mandat zielonych testów bez wzmianki o (nie)istnieniu CI.
ZADANIE: Zweryfikuj stan kodu, potem: przepisz README na stan post-A-H (daemon/panel/voice żywe, jak uruchomić); zaktualizuj wersję/opis w pyproject; popraw REVIEW_HANDOFF „Known open items" (PTT/listening dostarczone — zaznacz czy używa istniejących endpointów lease czy potrzebuje obiecanych nowych); dopisz w MASTER_PLAN notę o statusie CI (zgodnie z FIX-12). Bez zmian w kodzie produkcyjnym. Sprawdź czy testy dot. docs (jeśli są) przechodzą.
```

---

# PACZKI / MODEL — osobne (NIE bugfix)

## - [ ] FIX-15 · Supertonic v3 + audyt paczek (odpowiedź na „stare wersje")

- **Ustalenie z researchu (2026-07-03):** **żadna paczka nie jest stara — wszystkie latest.** „Supertonic 3" to **generacja modelu** (29.04.2026, 31 języków, polski explicit), NIE wersja pakietu 3.x. Pakiet `supertonic 1.3.1` (Twój pin) już celuje w model v3.
- **Realna akcja (NIE `pip install -U`):**
  1. Ustal, czy lokalne assety Supertonica to **v3 czy stary v2** (gdzie cache modeli; czy CLI 1.3.1 pobrał v3).
  2. **KRYTYCZNE:** sprawdź, czy głos **`M1`** (`jarvis/config.py:143` `supertonic_voice="M1"`) istnieje w v3 (10 wbudowanych głosów) **zanim** cokolwiek odświeżysz — inaczej migracja zabije auditowany głos Ozzy'ego (§7.3). Uwaga: pamięć wiąże „M1" z assetem MLX 2,4 GiB — rozstrzygnij, czy M1 to głos Supertonica czy osobny model.
  3. Jeśli v3 bezpieczne dla M1 — odśwież asset, przesłuchaj polski (v3: mniej repeat/skip, może zbędny workaround `supertonic_short_sentence_speed` z config.py:152), rozważ `supertonic serve` (HTTP OpenAI-compatible) zamiast shell-out CLI.
- **Bonus (panel):** macOS 26 Tahoe ma regresję AppKit `NSStatusItem`+`NSPopover` (pusty popover) — jeśli wystąpi, to bug OS, nie pyobjc; reprodukuj zanim obwinisz pin.
- **Estymat:** audyt+decyzja ~2–3h; ew. odświeżenie v3 + audycja ~0,5 dnia · **Zależności:** decyzja Ozzy'ego (głos to rzecz zdekretowana §7.3).

```text
Repo Jarvis v4.1 (/Users/n1_ozzy/Documents/dev/jarvis), branch main. Realizujesz task FIX-15 z FIXME.md (Supertonic v3 / audyt paczek).
ZASADY: preflight tanio; NIE podbijaj paczek pip (research potwierdził: WSZYSTKIE są już latest — supertonic 1.3.1, mlx-whisper 0.4.3, mlx-audio 0.4.4, pyobjc 12.2.1, onnxruntime 1.27.0, torch 2.12.1, numpy 2.4.6, httpx/sounddevice/soundfile/pytest latest; torch już załatany na CVE-2026-24747). NIE fan-outów bez zgody. Głos to rzecz ZDEKRETOWANA (MASTER_PLAN §7.3) — decyzje o zmianie modelu/głosu wymagają zgody Ozzy'ego, NIE zmieniaj samowolnie.
KONTEKST: "Supertonic 3" to generacja MODELU (nie wersja pakietu). Pakiet supertonic 1.3.1 już implementuje model v3. v3: 31 języków (polski explicit), mniej repeat/skip na krótkich/długich zdaniach, tagi ekspresji, 10 wbudowanych głosów.
ZADANIE (audyt + rekomendacja, minimum zmian): 1) Ustal, czy lokalna instalacja Supertonica używa assetów v3 czy starego v2 (znajdź cache modeli HF/lokalny, sprawdź co CLI `supertonic tts` faktycznie ładuje). 2) KRYTYCZNE: sprawdź czy głos "M1" z jarvis/config.py:143 (supertonic_voice="M1") istnieje w modelu v3 — rozstrzygnij czy M1 to wbudowany głos Supertonica czy osobny asset MLX (pamięć projektu wiąże M1 z ~2,4 GiB MLX). Jeśli M1 nie jest w v3 — NIE migruj, zgłoś Ozzy'emu. 3) Jeśli v3 bezpieczne dla M1: zaproponuj plan odświeżenia assetu + audycji polskiego (sprawdź czy workaround supertonic_short_sentence_speed z config.py:152 nadal potrzebny), oraz oceń `supertonic serve` (HTTP OpenAI-compatible) jako alternatywę dla shell-out CLI. Przedstaw ustalenia i rekomendację; wprowadzaj zmiany dopiero po akceptacji. Nie odpalaj pełnych smoke'ów bez potrzeby.
```

---

## Podsumowanie pokrycia

| Tier | Taski | Findingi | Estymat |
|---|---|---|---|
| 1 MUSI | FIX-01…04 | 2 CRIT + 6 HIGH (+ powiązane) | ~1,5 dnia |
| 2 POWINNO | FIX-05…10 | reszta HIGH + większość MED | ~2–3 dni |
| 3 MIŁO MIEĆ | FIX-11…14 | dług operacyjny + doc drift | ~1–2 dni |
| Model/paczki | FIX-15 | odpowiedź na „stare wersje" | ~2–3h + ew. 0,5 dnia |

**Rekomendacja:** nie ścigaj 47/47. Realna wartość w Tier 1 (~1,5 dnia) — po nim wszystko *niebezpieczne* zamknięte. Reszta sukcesywnie.
Architektura z researchu (wake word, model-based turn detection, AEC) to **feature'y, nie fixy** — poza tym plikiem, ~1–2 tygodnie każdy.
