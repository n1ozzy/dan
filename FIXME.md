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

- **TDD (celowany):** przy fixie napisz test odtwarzający konkretny bug i odpalaj **tylko TEN test** — nie całą matrycę.
- **NIE podbijaj paczek.** Wszystkie zależności są już najnowsze (supertonic 1.3.1, mlx-whisper 0.4.3, mlx-audio 0.4.4, pyobjc 12.2.1, onnxruntime 1.27.0, torch 2.12.1, numpy 2.4.6, httpx/sounddevice/soundfile/pytest — wszystkie latest). `pip install -U` to NIE jest fix. Szczegóły w tasku **FIX-15**.
- **Preflight sesji = tanio:** `git log -1` + `git status --short` + health daemona. **NIGDY** nie odpalaj testów na starcie sesji.
- **🔬 TESTY — NIE co task (twarde żądanie Ozzy'ego 2026-07-03):** pełny `pytest` + smoke odpalaj **wyłącznie po DUŻYCH taskach** (FIX-03, FIX-04, FIX-05, FIX-07, FIX-09 — fundament / współbieżność / głos / migracje), **po ich wykonaniu**. Przy WSZYSTKICH pozostałych: co najwyżej **celowany test danego fixa** (ten jeden plik/case), **nigdy pełna matryca 1322 testów**. Zero rutynowego pełnego pytest na koniec każdego taska.
- **NIE odpalaj multi-agentowych workflow/fan-outów** (Workflow, deep-research, 7+ subagentów) **bez wyraźnej zgody Ozzy'ego** — tokeny ograniczone.
- **Linie mogły się przesunąć** — po wcześniejszych fixach zweryfikuj `plik:linia` grepem/Read zanim edytujesz.
- **Na koniec taska:** commit z rzeczowym opisem + odhacz status w tym pliku. Pełne testy TYLKO jeśli to duży task (patrz reguła 🔬 wyżej).

---

# TIER 1 — MUSI (≈ 1,5 dnia) — po tym wszystko *niebezpieczne* jest zamknięte

## - [x] FIX-01 · CORS `null` origin czyta prywatne dane 🟠 HIGH — DONE `884d500`

- **Pliki:** `jarvis/daemon/lifecycle.py:91`, test `tests/test_api_cors.py`
- **Problem:** `"null"` jest w `ALLOWED_CORS_ORIGINS`, a token-gate obejmuje tylko `MUTATING_METHODS` (POST/PATCH/DELETE, l.94) → GET-y nietokenowane. Lokalna złośliwa strona (`file://`, origin `null`) robi `fetch('http://127.0.0.1:41800/conversations'|'/memory'|'/settings')` i eksfiltruje dane. `test_api_cors.py:21` wręcz utrwala `null` jako dozwolony.
- **Fix:** usuń `"null"` z `ALLOWED_CORS_ORIGINS`; popraw test tak, by asertował że `null` jest ODRZUCany. Rozważ (opcjonalnie, zapytaj) token na endpointach GET.
- **Testy:** test że żądanie z `Origin: null` nie dostaje `Access-Control-Allow-Origin: null`.
- **DoD:** `null` niedozwolony, test zielony, reszta CORS bez regresji.
- **Estymat:** ~20–30 min · **Zależności:** brak.

## - [x] FIX-02 · git-config RCE mimo approval-gate 🟠 HIGH — DONE `78a58b4`

- **Pliki:** `jarvis/tools/shell_tool.py:46` (`_SCRUBBED_ENV`) i wywołanie `subprocess.run` (~l.95)
- **Problem:** `_SCRUBBED_ENV` ustawia tylko PATH/LANG/LC_ALL — brak `GIT_CONFIG_NOSYSTEM`/`GIT_CONFIG_GLOBAL`. Whitelistowane `git status/log/diff` lecą w atakowalnym `cwd`; repo ze złośliwym `.git/config [core] fsmonitor = /tmp/evil.sh` wykona ten skrypt przy „niewinnym" `git status`. Operator zatwierdza tekst „git status --short", nie widząc exec sterowanego configiem.
- **Fix:** przy wywołaniu git wymuś hardening: env `GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null` + flagi `-c core.fsmonitor= -c core.hooksPath=/dev/null -c protocol.ext.allow=never`. Alternatywa do rozważenia: usunąć git z domyślnej whitelisty lub przypiąć `cwd` do zaufanego katalogu.
- **Testy:** test z tymczasowym repo mającym `fsmonitor`/hook wskazujący na plik-sentinel; asercja że sentinel NIE został wykonany.
- **DoD:** git odporny na repo-local config, test zielony, whitelist git nadal działa dla legit repo.
- **Estymat:** ~30–45 min · **Zależności:** brak.

## - [x] FIX-03 · CRITICAL: współdzielone połączenie SQLite przez wątki 🔴 — DONE `b61f537` (opcja A: ThreadLocalConnection)

- **Pliki:** `jarvis/daemon/app.py` (~`:130`/`:1130` — `sqlite3.connect(check_same_thread=False)`), konsumenci: `repository`, `event_store`, `approval_gate`, `tool_run_recorder`; powiązane: `app.py:596` (wątki workerów), `jarvis/memory/manager.py:315` (transakcja + event osobno)
- **Problem:** jedno `self.conn` obsługuje **zapisy** z wielu wątków HTTP + wątków workerów, chronione dwiema rozłącznymi blokadami które się nie pokrywają. Bo to jedno connection, bloki `with conn:` dzielą jedną transakcję → rollback jednego wątku wyrzuca niezacommitowany append-only event drugiego (ciche gubienie), albo `sqlite3.ProgrammingError`. **WAL tego nie naprawia** (to przeplot na jednym connection, nie kontencja blokad).
- **Fix (DECYZJA ARCHITEKTONICZNA — do podjęcia w tasku):**
  - **Opcja A:** connection-per-wątek (WAL już wspiera wielu writerów) — factory dająca każdemu wątkowi/turze krótkotrwałe połączenie.
  - **Opcja B:** jeden proces-wide write-lock serializujący WSZYSTKIE zapisy `self.conn`.
  - Rekomendacja: **A** (mniej kontencji, zgodne z WAL), ale wymaga przejścia po wszystkich konsumentach `self.conn`.
- **Testy:** test współbieżności — N wątków równolegle append-uje eventy + zapisuje tury; asercja że żaden event nie zniknął i brak `ProgrammingError`.
- **DoD:** brak współdzielenia jednego Connection do zapisów między wątkami; test współbieżności zielony; przy okazji domknij `app.py:596` (join/drain workerów na shutdown) i `memory/manager.py:315` (zmiana+event w jednej transakcji).
- **Estymat:** ~0,5–1 dzień · **Zależności:** wykonać PRZED pełnym domknięciem workerów (FIX-07 część) i store (FIX-10).

## - [x] FIX-04 · Voice: „gorący mikrofon" + przeżywalność brokera 🟠 HIGH (×4) — DONE `00a42be` (4/4 potwierdzone testami deterministycznymi, bez sprzętu)

- **Pliki:** `jarvis/daemon/app.py:225` (stop bez `voice_recorder.stop()`), `jarvis/voice/listening.py:139` (brak timera lease'u), `jarvis/voice/broker.py:63` (broker umiera na nie-TTS wyjątku), `jarvis/voice/broker.py:57` (`stop()` nie zatrzymuje brokera)
- **Problem:** (a) `stop()` nie woła `voice_recorder.stop()` → po restarcie in-process osierocony `sox` nagrywa dalej (hot mic + puchnący dysk). (b) TTL lease'u egzekwowany tylko przy wywołaniu API; crash panelu przed puszczeniem PTT = nagrywanie w nieskończoność. (c) broker propaguje nie-`TTSEngineError` (np. „database is locked") → trwale niemy. (d) `stop()` brokera nie przerywa drain-loopa ani nie ubija executora.
- **⚠️ Te findingi były NIEZWERYFIKOWANE** (legi weryfikacji ubite) — **najpierw potwierdź na żywo** (mikrofon/sox), potem napraw.
- **Fix:** `stop()` woła `voice_recorder.stop()` przed `voice_stt.stop()` (żeby ostatni capture dotarł do STT), `voice_recorder=None`; daemon-side sweeper (`threading.Timer` lub pętla brokera) wołający `active()/_expire_stale` cyklicznie; try/except (Exception, nie tylko TTSEngineError) wokół drain + backoff w `_run`; `stop()` sprawdza `_stop.is_set()`, woła `stop_playback()` i `_executor.shutdown(cancel_futures=True)`.
- **Testy:** stop() zatrzymuje recorder; wygasły lease bez klienta zatrzymuje recorder przez sweeper; wyjątek DB w brokerze nie ubija wątku; stop() brokera realnie kończy pętlę.
- **DoD:** brak osieroconego sox po stop/restart; lease samoegzekwuje się bez klienta; broker przeżywa wyjątek DB i daje się zatrzymać. Potwierdzenie na żywo udokumentowane.
- **Estymat:** ~0,5–1 dzień (w tym potwierdzenie na sprzęcie) · **Zależności:** brak; refactor anti-echo/cancel to osobny FIX-09.

---

# TIER 2 — POWINNO (≈ 2–3 dni) — rób sukcesywnie

## - [x] FIX-05 · Stany tury / orchestrator: udany turn jako FAILED + utknięcia 🟠 HIGH + 🟡 MED×3 — DONE `8cc2ebd` (5/5 przypadków, TDD; STOPPING tolerowany zamiast locków w stop(); continuation → FAILED; lock+force_idle w state_machine; 1343 testy)

- **Pliki:** `jarvis/daemon/app.py:254` (stop race — HIGH, POTWIERDZONY), `jarvis/turns/orchestrator.py:427` (FINISHED→FAILED), `:516` (stuck AWAITING_APPROVAL), `:1150` (wedge non-IDLE), `jarvis/daemon/state_machine.py:100` (brak locka)
- **Problem:** wspólny root — przejścia stanu nie tolerują terminalnych/błędnych ścieżek. `stop()`→STOPPING bez `text_turn_lock` → udany, wypowiedziany turn zapisany jako FAILED. Wyjątek po `_turns.finish()` przepisuje skończony turn na FAILED. Nieudana kontynuacja zostawia turn na zawsze w AWAITING_APPROVAL. Recovery potrafi zablokować runtime w nie-IDLE. State machine bez locka.
- **Fix:** failure-handler ograniczony do fazy generacji (guard na status tury przed `fail()`); `stop()` bierze `text_turn_lock`/`tool_execution_lock` przed STOPPING (lub terminalne IDLE toleruje STOPPING); nieudana kontynuacja → FAILED/re-runnable zamiast dyndać; recovery resetuje `_state` in-memory jako ostatnia deska; lock na `transition()`.
- **Testy:** turn skończony podczas shutdown pozostaje FINISHED; wyjątek po finish() nie przepisuje na FAILED; nieudana kontynuacja daje status terminalny; równoległe `transition()` bez wyścigu.
- **DoD:** żadne przejście nie przeklasyfikowuje skończonej tury; brak stanów-pułapek; state machine atomowa.
- **Estymat:** ~0,5 dnia · **Zależności:** miło po FIX-03 (spójność locków), ale niezależne.

## - [x] FIX-06 · API hardening: DNS rebinding, slowloris, WS cap 🟡 MED×3 + LOW — DONE `9c79ea6` (4 obrony: Host-walidacja 403+close / socket timeout 10s / cap 8 sesji WS 503 / 401 Connection:close; TDD raw-socket) + FOLLOW-UP token na GET DONE `5406421` (GET /conversations,/turns,/memory,/settings wymagają X-Jarvis-Token; panel+CLI ślą token na każdym żądaniu; status/mechanizm GET zostają otwarte)

- **Pliki:** `jarvis/daemon/lifecycle.py:209` (brak walidacji Host-header), `:622` (brak socket timeout), `:508` (brak capa na sesje WS), `:218` (401 nie drenuje body)
- **Problem:** brak walidacji Host → localhost binding pokonywalny DNS rebindingiem dla nietokenowanych GET-ów. Brak socket timeout + blocking `rfile.read(Content-Length)` → slowloris trzyma wątek. Brak limitu równoległych sesji `/stream` (każda = osobne SQLite + wątek). 401 nie drenuje body → desync keep-alive.
- **Fix:** odrzucaj Host spoza `{127.0.0.1, localhost, ::1}:port`; `handler.timeout` (np. 10s) + deadline na read; cap sesji WS z odrzuceniem ponad limit; drain body lub `Connection: close` przy 401.
- **Follow-up z FIX-01:** rozważ token (`X-Jarvis-Token`) także na endpointach GET (`/conversations`, `/memory`, `/settings`) — po usunięciu `null` z CORS jedyną obroną GET-ów pozostaje localhost binding + walidacja Host z tego taska; token domyka wektor „dowolny lokalny proces czyta prywatne dane".
- **Testy:** obcy Host odrzucony; wolne body nie blokuje w nieskończoność; N+1 sesja WS odrzucona; 401 nie desynca.
- **DoD:** cztery obrony na miejscu, testy zielone.
- **Estymat:** ~2–3h · **Zależności:** brak.

## - [x] FIX-07 · Brain/workers: stdin deadlock, atomic claim, cap kontekstu 🟠 HIGH + MED×3 + LOW×3 — DONE `82cde12` (7/8; H odroczony)

**DONE `82cde12` (TDD, deterministyczne mocki bez sprzętu; suite 1398 zielone):**
- **HIGH stdin deadlock:** `default_stream_process_factory` NIE pisze już stdin; `stream_cli_response` karmi stdin z osobnego wątku (`_write_stdin`) współbieżnie z drenażem stdout, pod już-uzbrojonym watchdogiem → wielki prompt nie zablokuje pipe'a.
- **MED atomic claim:** `WorkerBroker.execute` używa `_claim_job` = jeden warunkowy `UPDATE ... WHERE status='queued'`, działa tylko przy `rowcount==1`; przegrany dostaje conflict → job odpalony co najwyżej raz (double-run odtworzony barierą w teście przed fixem).
- **MED cap input_text:** `_cap_input_text` przycina input do budżetu z widocznym markerem (był nieograniczony mimo `context_budget_chars` — karmił deadlock).
- **MED parser risk:** `tool_call_parser` fail-safe `destructive`, NIE czyta `risk` od modelu; autorytatywny risk derywowany downstream z `tool.risk` zarejestrowanego speca (`registry.evaluate_permission`).
- **LOW worker-job prompt:** rola `user` + framing „untrusted" + cytowanie (był `system` = prompt-injection surface).
- **LOW settings DoS:** zły JSON w jednym wierszu settings → skip+log zamiast abortu całej budowy tury; ważne wiersze nadal ładowane.
- **LOW allowlist flag CLI:** `_reject_unsafe_args` = allowlist znanych bezpiecznych flag (mija bypassy typu flaga-z-wartością/aliasy), non-flag tokeny nietykane.
- **H ODROCZONY (LOW):** cancel handle dla BLOCKING generate. Zerowy trigger praktyczny — voice ZAWSZE streamuje (`on_delta`), więc blocking+barge-in nigdy nie współwystępują; dodanie kolidowałoby z jawną decyzją `test_blocking_path_never_touches_the_registry`. Do rozważenia jeśli kiedyś powstanie blocking-barge-in use case.
- **DoD FIX-07 (stdin deadlock / atomic claim / kontekst ograniczony / risk niezależny) SPEŁNIONY w całości.**

- **Pliki:** `jarvis/brain/claude_cli_adapter.py:121` (stdin deadlock — HIGH), `jarvis/workers/broker.py:174` (double-run job), `jarvis/brain/context_builder.py:419` (input_text nieograniczony), `:351` (prompt-injection labeling), `:213` (zły settings row = DoS tury), `jarvis/brain/tool_call_parser.py:90` (ufa `risk` od modelu), `jarvis/brain/claude_cli_adapter.py:521` (denylist flag), `:351` (blocking bez cancel)
- **Problem:** streaming zapisuje cały prompt na stdin ZANIM uzbroi watchdog i zacznie drenować stdout/stderr → duży prompt (a `_fit_budget` nie tnie `input_text`) deadlockuje bez timeoutu. Job QUEUED→RUNNING nieatomowo → odpalany dwa razy. Parser ufa polu `risk` od modelu. Denylist flag mija równoważne bypassy. Blocking-generate bez barge-in.
- **Fix:** uzbrój watchdog + drainy PRZED zapisem stdin, stdin z osobnego wątku; atomic claim (`UPDATE ... WHERE status='queued'`, działaj przy rowcount==1); utnij `input_text` wg budżetu; risk z `BrainToolSpec` nie od modelu; allowlist flag; worker-job prompt jako oznaczone dane untrusted; zły settings row skip+default zamiast abort; blocking-generate dostaje uchwyt cancel.
- **Testy:** duży prompt nie deadlockuje; job nie odpalony dwa razy; risk brany ze speca; nadmiarowy input przycięty.
- **DoD:** brak deadlocka stdin; claim atomowy; kontekst ograniczony; risk niezależny od modelu.
- **Estymat:** ~3–5h · **Zależności:** cap `input_text` łagodzi deadlock — zrób razem. Atomic claim spójny z FIX-03. **[PROMPT wykonany — przycięty przy DONE.]**

## - [x] FIX-08 · Redakcja sekretów i containment plików 🟡 MED×2 + LOW×2 — DONE `9fa6840`

**DONE `9fa6840` (TDD, deterministyczne testy bez sprzętu; pełny pytest 1422 zielone):**
- MED `registry._redact`: deleguje do wspólnych `redact_secrets()`/`is_sensitive_key()` z `security/redaction.py` (normalizacja separatorów — `api-key`/`API.KEY` maskowane jak `api_key`; stary substring je gubił); + `credential(s)` do shared `SENSITIVE_KEYS` żeby nie zregresować pokrycia.
- MED `file_read` pełna treść — **DECYZJA: NIE ciąć treści dla modelu, ciąć dla durable store.** Model dostaje treść przez ulotny `ToolResult.output` (`redact_secrets`, bez capa → `file_read` zostaje użyteczny); persystencyjny `_redact` size-cap `PERSIST_MAX_STRING_CHARS=4096` (256 KB body nie ląduje w całości w tool_runs/events) + high-recall detektory w `redaction.py`: bloki PEM/PRIVATE KEY, poświadczenia w connection-stringach (`scheme://user:pass@host`), przypisania `sensitive-key=wartość` (.env/config). Entropia świadomie pominięta (deterministyczność; cap = backstop na nowy kształt sekretu).
- LOW `ui_type`: guard control-char/newline na warstwie toola (mirror `validate_paste_text`) — „Enter zostaje przy człowieku" nie zależy od backendu.
- LOW `file_write` TOCTOU: temp przez `dir_fd` + `O_NOFOLLOW|O_EXCL` (openat/renameat), symlink-swap rodzica po checku nie przekieruje zapisu poza root.
- **[PROMPT wykonany — przycięty przy DONE.]**

- **Pliki:** `jarvis/tools/file_tool.py:76` (file_read persystuje pełną treść, redakcja przecenia ochronę), `jarvis/tools/registry.py:837` (słabsza reguła redakcji niż `redaction.py`), `jarvis/tools/ui_tool.py:139` (brak bana control-char), `jarvis/tools/file_tool.py:120` (TOCTOU symlink na write)
- **Problem:** `file_read` zapisuje pełną treść pliku do tool_runs/events, a redakcja łapie krótką listę kształtów tokenów → docstring „secret redaction applies" przecenia ochronę. `registry._redact` używa słabszego substring niż wspólne `is_sensitive_key` (bez normalizacji separatorów). `UiTypeTool` bez guardu newline (inwariant „Enter przy człowieku" zależy od backendu). `file_write` TOCTOU na symlinku rodzica.
- **Fix:** nie persystuj pełnej treści (hash/preview) lub dodaj high-recall detektory (PEM, connection stringi, entropia) + size-cap; `registry._redact` woła wspólne `is_sensitive_key()/redact_secrets()`; guard control-char w `UiTypeTool` (mirror `validate_paste_text`); `O_NOFOLLOW`/`openat` lub re-walidacja tuż przed `os.replace`.
- **DECYZJA:** czy w ogóle persystować treść `file_read` — to model danych/prywatności (rozstrzygnij w tasku).
- **Testy:** sekret w pliku nie ląduje w evencie; klucz z separatorem zamaskowany; newline w UiType odrzucony; symlink-swap nie wychodzi poza root.
- **Estymat:** ~3–4h · **Zależności:** brak.

## - [x] FIX-09 · Voice: refactor toru anulowania + anti-echo (z migracją DB) 🟡 MED×5 + LOW×2 — DONE `b1711da`

**DONE `b1711da` (TDD, deterministyczne mocki bez sprzętu; suite 1389 zielone; migracja v2 idempotentna z version guard, schema_version = append-log [1,2]):**
- **Priorytet operatora #0:** rc=143-po-naszym-cancelu → `BrainGenerationCancelled` (osobna podklasa `BrainAdapterError`; flaga w handle cancela rozróżnia „my ubiliśmy" od crasha) → orchestrator znaczy turę **CANCELLED** nie FAILED, runtime wraca do IDLE (nie przez ERROR), gateway loguje anulowanie zamiast „voice turn failed". To samo na ścieżce `continue_after_tool_result`. Nowe eventy `TURN_CANCELLED`/`BRAIN_CANCELLED`.
- **#1 TOCTOU (`broker`/`tts`):** silnik re-sprawdza predykat `should_play` pod `_player_lock` tuż przed `Popen` → cancel w luce check→spawn nie odpala playera (`PlaybackCancelled`, broker pomija czysto).
- **#2 anti-echo korpus (`anti_echo`+`queue`, migracja):** członkostwo po `voice_queue.spoken_at` (broker stempluje przy realnym play), nie po statusie → wiersz `queued`→`cancelled` (nigdy nie zabrzmiał) wykluczony; `failed` po częściowym audio włączony.
- **#3 tombstone (`cancellation`+`queue`, migracja):** `cancel_active_speech` tombstonuje unię tur (generujące PIERWSZE, potem kolejkowe) w tabeli `cancelled_turns`; `VoiceQueue.enqueue` odmawia im nowych wierszy → późna delta/FillerTimer nie przecieka przez sweep.
- **#4 renewal (`listening`):** `acquire` na istniejącym lease woła `_sync_recorder()` → martwy sox restartowany (start() idempotentny).
- **#5 locked-mode segmentacja (`recorder`):** `recorder_segment_seconds` (domyślnie 8s) rotuje capture (capture-first, dostawa poza lockiem) → transkrypty płyną w trakcie lease; hold niezmieniony; wątek rotacji jak sweeper.
- **#6 stt timeout (`stt`):** `future.result(timeout)` skalowany do długości audio + recykling executora na timeout → zawieszony MLX nie blokuje workera na zawsze.
- **#7 kolejność (`queue`):** `claim_next` grupuje po pierwszym rowid tury → per-turn `seq` nie przeplata dwóch tur; filler (seq=-1) nadal pierwszy.
- **Nowe knoby configu:** `voice.recorder_segment_seconds=8.0`, `voice.stt_timeout_seconds=30.0`, `voice.stt_timeout_per_audio_second=10.0` (do strojenia na żywym gate).
- **Głośniki (uczciwie):** FIX-09 zmniejsza fałszywe barge-iny (mniej przecieku echa) i odkłamuje panel+logi, ale **echa sprzętowego nie usunie bez AEC** (osobny feature) — testować na słuchawkach.

- **🔴 ŻYWA DIAGNOZA (sesja Ozzy'ego 2026-07-03, na GŁOŚNIKACH):** objawy operatora — Jarvis „wchodzi w zdanie"/ucina się, panel nie pokazuje aktywności, nienaturalne przerwy między zdaniami. Root z `~/.jarvis/logs/jarvisd.log`: powtarzalny `barge_in generation_cancelled=1 → claude_cli exited 143 (SIGTERM) → TURN FAILED`. Mechanizm: `gateway.handle_transcript:83` robi barge-in gdy `_is_speech_active()` a transkrypt przeszedł anti-echo → `cancel_active_speech` ubija generację → `stream_cli_response` traktuje rc=143 jako `BrainAdapterError` → tura **FAILED** (nie CANCELLED). Skutki: (A) panel nie widzi cyklu bo tury FAILED (panel technicznie OK — menubar sam seeduje token, zero 401); (B) urwane wypowiedzi. **Główny sprawca na głośnikach = brak AEC** — mikrofon łapie głos Jarvisa, anti-echo łapie część, reszta przecieka jako fałszywy barge-in. Workaround natychmiastowy: **słuchawki** (echo znika u źródła; potwierdzić na nich co jest bugiem kodu a co echem sprzętowym). **PRIORYTET dla operatora (częściowo poza listą findingów niżej):** (1) generacja anulowana barge-inem → status **CANCELLED** nie FAILED — odkłamuje panel+logi; dotyka claude_cli_adapter (rozpoznać rc=143-po-cancel) + orchestrator, pogranicze z FIX-07; (2) anti-echo korpus `spoken_at` (poniżej) = mniej przecieku. Ozzy zdecydował: zrobić całe FIX-09.
- **Pliki:** `jarvis/voice/broker.py:103` (TOCTOU cancel→nowy player), `jarvis/voice/cancellation.py:104` (snapshot pomija późne wiersze), `jarvis/voice/anti_echo.py:38` (cancelled/failed queued text w korpusie echo), `jarvis/voice/listening.py:66` (renewal nie restartuje martwego sox), `jarvis/voice/recorder.py:168` (locked-mode = jeden rosnący capture), `jarvis/voice/stt.py:90` (whisper future bez timeoutu), `jarvis/voice/queue.py:88` (global seq interleaving — z FIX-04? nie, tu)
- **Problem:** rodzina bugów toru anulowania i korpusu echo. TOCTOU między checkiem a `engine.play`. Snapshot anulowania pomija wiersze dołożone po sweepie. Tekst nigdy niewypowiedziany (cancelled/failed z „queued") wchodzi do korpusu echo — sprzecznie z kontraktem modułu. Renewal lease'u nie restartuje martwego sox. Locked-mode nie segmentuje. Whisper future bez timeoutu blokuje workera. Kolejka sortuje po globalnym seq (per-turn) → przeplot zdań.
- **Fix:** tombstone anulowanych turn_id + `enqueue` odmawia dla nich; re-check statusu pod `_player_lock` tuż przed `Popen`; kolumna `spoken_at` (**migracja DB**) → tylko realnie wypowiedziane wiersze w korpusie echo; `_sync_recorder()` na renewal; segmentacja locked-mode (rolling interval / split na ciszy); timeout na `future.result()` + recykling executora; order-by rowid ASC albo (first-rowid-of-turn, seq).
- **⚠️ Wymaga migracji DB** (`jarvis/store/migrations.py` — idempotentna, version guard). Wymaga potwierdzenia na żywo.
- **Estymat:** ~0,5–1 dzień · **Zależności:** PO FIX-04 (hot-mic). Anti-echo to fundament PRZED tuningiem VAD. **[PROMPT wykonany — przycięty przy DONE.]**

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

## - [x] FIX-16 · Panel file:// odcięty od daemona przez CORS (regresja FIX-01) 🟠 HIGH — DONE `79fa80a`

- **Pliki:** `jarvis/panel/menubar_app.py` (`_build_popover`), przyczyna w `jarvis/daemon/lifecycle.py:92` (`ALLOWED_CORS_ORIGINS`)
- **Problem (znalezione 2026-07-03, live):** natywny panel to WKWebView ładowany z `file://` → `Origin: null`. FIX-01 (`884d500`) usunął `null` z `ALLOWED_CORS_ORIGINS` (słusznie — złośliwa strona `file://` nie ma czytać jarvisd), ale **panel też jest `file://`** → wszystkie jego fetche do 41741 CORS-blocked → panel ładuje się, ale ślepy na dane („panel jest, ale nie działa"). Dowód: `curl -H "Origin: null" .../state` = brak `Access-Control-Allow-Origin`; origin `41800` (dev-preview) = dostaje. **Panel nie działał dla nikogo od czasu FIX-01.**
- **Fix (zrobiony):** `allowUniversalAccessFromFileURLs` + `allowFileAccessFromFileURLs` na `WKWebViewConfiguration` (KVC, guarded) — ten JEDEN zaufany WebView omija CORS lokalnie; daemon `ALLOWED_CORS_ORIGINS` **zostaje szczelny** (prawdziwe przeglądarki dalej blokowane). NIE dotyka lifecycle.py.
- **DoD:** panel z `file://` dostaje dane z daemona; CORS daemona bez zmian; testy panelu zielone (33). Weryfikacja finalna = wizualna (WKWebView runtime, nie z bash).

## - [ ] FIX-17 · Zły `source`/`mode` w /voice/* → 500 zamiast 400 🟡 MED

- **Pliki:** `jarvis/daemon/lifecycle.py` (blok `except`, ~l.484-509), `jarvis/voice/listening.py` (`ListeningLeaseError`)
- **Problem (znalezione 2026-07-03):** `POST /voice/ptt/down` z nieznanym `source` (np. `"panel-test"`) albo `mode` rzuca `ListeningLeaseError` z `ListeningLeaseManager.acquire`. Ten wyjątek **nie jest** w liście `except` w `handle_request` (lifecycle.py) → leci jako niezłapany → **500 Internal Server Error** zamiast **400 Bad Request**. Zły input klienta nie powinien wyglądać jak awaria serwera. Panel wysyła poprawny `source="ptt"`, więc go nie dotyka — ale to defekt kontraktu API.
- **Fix:** dodać `except ListeningLeaseError as exc: _write_json(handler, 400, {"error": str(exc), "status": 400})` (import z `jarvis.voice.listening`). Uwaga: `lifecycle.py` bywa gorącym plikiem (FIX-06/07) — zrób na czystym drzewie.
- **Testy:** POST /voice/ptt/down z `source="nope"` → 400 (nie 500); poprawny `source="ptt"` → 200 bez regresji.
- **DoD:** zły source/mode → 400 z czytelnym błędem; poprawny → 200; test celowany zielony.
- **Estymat:** ~15–20 min · **Zależności:** brak (czyste drzewo lifecycle.py).

```text
Repo Jarvis v4.1 (/Users/n1_ozzy/Documents/dev/jarvis), branch main. Realizujesz FIX-17 z FIXME.md.
ZASADY: preflight tanio; TDD (test przed fixem); NIE podbijaj paczek; NIE fan-outów bez zgody; po skończeniu celowany test + commit + odhacz FIX-17.
PROBLEM (kontrakt API, MED): POST /voice/ptt/{down,up} i /voice/listen/{lock,unlock} z nieznanym source (nie w ALLOWED_SOURCES = ptt/global_hotkey/lock) albo mode rzuca ListeningLeaseError (jarvis/voice/listening.py) w app.acquire_listening_lease -> ListeningLeaseManager.acquire. Ten wyjątek NIE jest łapany w handle_request w jarvis/daemon/lifecycle.py (blok except ~l.484-509) -> niezłapany -> 500 zamiast 400.
ZADANIE: Zweryfikuj aktualną linię (mogła się przesunąć). Napisz test: POST /voice/ptt/down z source="nope" -> 400 (obecnie 500). Potem dodaj `except ListeningLeaseError` mapujący na 400 z {"error": str(exc), "status": 400} (import ListeningLeaseError z jarvis.voice.listening), w spójnej kolejności z innymi except. Potwierdź że poprawny source="ptt" -> 200 bez regresji. Celowany test API/voice, bez pełnej matrycy.
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
