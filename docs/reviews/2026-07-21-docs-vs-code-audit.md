# Audyt dokumentacja ↔ kod — 2026-07-21

Klasyfikacja: **rejestr rozjazdów dokumentacji. AKTUALNY.** Każdy punkt opisuje
zdanie, które **w stanie na 21 lipca 2026** stało w repo i nie zgadzało się
z kodem na `agent/dan-release1-integration`.

**Status:** korektę w samym dokumencie mają już A1, A2, A6, B1, C2, C3, C5,
D1 i D2 — przy nich zostaje tu dowód, nie zadanie. Reszta jest otwarta.
Opisane zachowanie kodu obowiązuje niezależnie od tego, czy proza je już
przyznaje.

**Uwaga o wskaźnikach.** Numery linii do *dokumentów* starzeją się szybciej niż
te do kodu, bo poprawianie tych dokumentów jest celem tego pliku — dopisanie
korekty przesuwa wszystko pod nią. Gdy numer nie trafia, szukaj po cytowanym
zdaniu. Numery do kodu podawaj z nazwą funkcji, wtedy przeżyją refaktor.

## Skąd to się wzięło

21 lipca 2026 przez `docs/` przeszła duża przeróbka: 47 plików, +2779/−1537
linii, mająca usunąć z dokumentacji twierdzenia niezgodne z runtime. Przeróbka
była potrzebna i w większości trafiona (lista potwierdzeń na końcu). Nikt jej
jednak nie zweryfikował, więc czterech niezależnych czytelników przeszło jej
klastry z kodem w ręku: bezpieczeństwo/narzędzia, głos/kolejka, demon/tura,
pamięć. Wszyscy mieli zakaz używania grepa jako dowodu — każde zdanie niżej ma
`plik:linia` z lektury.

**Nie naprawiono z tego ani jednej linii zachowania.** To rejestr, nie łatka.

---

## A. Bezpieczeństwo i narzędzia

### A1. „Zdejmuje allowlistę i nic więcej" — kod obok mówi coś przeciwnego
`docs/SECURITY_MODEL.md` §2: *„drops **that allowlist and nothing else**. Root
containment, the scrubbed environment, the git hardening and the runtime/output
bounds all stay in force."* To samo w `docs/runbooks/TOOLS_AND_APPROVALS.md`:
*„`shell_read` runs only exact allowlist matches…"*

`dan/tools/shell_tool.py:21-32` ma w tym samym pliku blok `KNOWN DEFECT`
mówiący dokładnie odwrotnie. `:150-155` — utwardzenie gita odpala się tylko gdy
`shlex.split(normalized)[0] == "git"`, więc `/usr/bin/git`, `cd x && git`,
`env git`, `sh -c 'git …'` je omijają. `:158-165` — `subprocess.run(shell=True)`
bez obsługi metaznaków. `:186-204` — approved_roots wiąże **wyłącznie cwd**.
`:132-135` — przy `unrestricted=True` allowlisty nie ma wcale, a żywy
`~/.dan/config.toml` ma `shell_read_unrestricted = true`.

**Waga: najwyższa.** Dokument obiecuje cztery bariery, zostają dwie i pół.

### A2. Inwentarz narzędzi pokazuje runtime jako nieszkodliwy
`docs/runbooks/TOOLS_AND_APPROVALS.md` wymienia cztery narzędzia: `echo`,
`system_status`, `web_fetch`, `approval_probe`.

`dan/daemon/app.py:2209-2241` rejestruje `FileReadTool`, **`FileWriteTool`**,
**`ShellReadTool`**, `WebFetchTool`, `UiActiveAppTool`, `UiReadWindowTool`,
**`UiClickTool`**, **`UiTypeTool`**, `UiFocusAppTool`, `ScreenReadWindowTool`,
`ScreenOcrRegionTool`, `TerminalReadScreenTool`, **`TerminalPasteTool`**;
`:2337-2344` dokłada `MemorySaveTool` i `MemoryRecallTool`. `ApprovalProbeTool`
(`dan/tools/registry.py:74-83`) nie jest rejestrowany nigdzie.

**Waga: najwyższa.** Lista pomija każde narzędzie zdolne do realnej szkody
i zawiera jedno, którego nie ma. Banner „STALE" nad nią dotyczy czego innego.

### A3. Endpointy `/approvals` zwracają 404, nie 503
Ten sam runbook podaje `GET /approvals`, `POST /approvals/{id}/approve|reject|
execute` i twierdzi, że przy niegotowej aplikacji dają `503`. W tablicy routingu
`dan/daemon/lifecycle.py:341-723` ścieżka `/approvals` nie występuje — wpadają
w `404` (`:723`). Modułu `dan/api/routes_approvals.py` nie ma.

### A4. `/state.pending_approval_count` nie istnieje
`snapshot_state()` (`dan/daemon/app.py:689-714`) zwraca `service, ok, started,
state, schema_version, latest_event_id, host, port, voice_enabled,
brain_adapter, launchd_label, session_tokens_in, session_tokens_out, hotkey,
children`; `dan/api/routes_state.py:13-16` dokłada `allowed_state_targets`.

### A5. Bootstrap z pełnym `--system-prompt` — warunek zawężony
`docs/SECURITY_MODEL.md` §5: *„happens only when there is no checkpoint."*
Nieprawda przy zmianie kanonu persony (`dan/brain/claude_cli_adapter.py:876-890`
→ `_rebuild_generation` `:948-956` → `prompt_flag` `:990`) i po nieudanym
wznowieniu (`:921-938`). Istotne, bo to plik do diagnozy zatrutej sesji.

### A6. Rozdział o workerach — ZAMKNIĘTE, znalezisko było nieaktualne w chwili zapisu
**`docs/SECURITY_MODEL.md` §6 ma już poprawny banner** („Workers are not wired up
on this branch. `worker_broker` is `None`…"). Audytor go przeoczył — zostawiam
punkt jako zapis, że sprawdzenie wypadło negatywnie, żeby nikt nie szukał drugi
raz. **Żywą wersją tego problemu jest C1**: `JARVIS_ARCHITECTURE.md`
i `TURN_PIPELINE.md` opisują workerów jako gałąź tury i banneru nie mają.
Dowód wspólny dla obu:
`dan/daemon/app.py:2360-2362` → `worker_broker = None`, a
`_require_worker_broker()` (`:1879-1882`) rzuca błędem, więc `POST /workers/jobs`
kończy się `400` i żadne `worker.job.*` nie powstaje. **To samo znalezisko
niezależnie zgłosił audyt demona** — patrz C1.

---

## B. Głos, kolejka, audio

### B1. `[[GŁOS]]` NIE jest martwą składnią — NAPRAWIONE 2026-07-21
`docs/GLOS-I-KOLEJKA.md` twierdziło *„the `[[GŁOS]]` marker syntax it consumed
is dead — never emit those markers."* Runtime każe je emitować:
`_VOICE_FORM_INSTRUCTION` (`dan/brain/context_builder.py:36-53`) instruuje model
po polsku, żeby zaczął odpowiedź blokiem `[[GŁOS]] … [[/GŁOS]]`, wstrzykiwane
w `:522-529` gdy `speech_form_enabled()` (`:532-543`) — czyli `voice.enabled`
**i** `voice.speak_responses`, oba `true` w żywym configu. Konsument żyje:
`dan/voice/speech_form_stream.py` + router w `dan/turns/orchestrator.py`.

Mylono dwóch producentów: sesja Claude Code w terminalu faktycznie nie ma czego
karmić (hook usunięty), ale mózg wewnątrz `dand` ma instrukcję i parser.
**Ten punkt został naprawiony w dokumencie** — reszta rejestru czeka.

### B2. Stany `LISTENING` / `TRANSCRIBING` / `SPEAKING` / `INTERRUPTED` nigdy nie są ustawiane
`docs/AUDIO_RUNTIME.md:62,94,123` opisuje je jako „Rules (FROZEN)".
`dan/daemon/state_machine.py:24-29` to jedyne miejsce, gdzie te nazwy w ogóle
padają. Realne `transition()`: `dan/turns/orchestrator.py:282` (`THINKING`),
`:463` (`TOOLING`), `:482` (`THINKING`), `dan/daemon/app.py:405` (`IDLE`),
`:621` (`STOPPING`), `:681` (`ERROR`). `dan/voice/listening.py:60-132` nie zna
maszyny stanów — dopisuje tylko eventy. Demon podczas słuchania i mówienia
siedzi w `THINKING`/`IDLE`.

### B3. Dwa nieistniejące endpointy audio
`docs/AUDIO_RUNTIME.md:137-138` podaje `GET /audio/current` i
`POST /audio/select`. `dan/daemon/lifecycle.py:420-422` zna jedną trasę:
`GET /audio/devices`. `dan/audio/devices.py:1-11` mówi wprost *„never mutates
system audio"* — wyboru urządzenia nie ma z założenia.

### B4. Sześć adapterów mózgu „gotowych", runtime zna jeden
`docs/RADIO-DAN.md:35-38` wymienia `claude_cli, codex_cli, openai, ollama, qwen,
eco` + `mock`/`test`. `dan/brain/manager.py:44-91` — `from_config` buduje listę
z jednym elementem i kończy `default_adapter="claude_cli"`; `:100-105` rzuca
`BrainManagerError` dla każdej innej nazwy; `dan/config.py:699` nadpisuje
default niezależnie od configu. Pozostałe pliki adapterów leżą w repo
niezarejestrowane. **To wyjaśnia dziewięć czerwonych testów `test_brain_api.py`
pod czystym `DAN_HOME`** — oczekują adaptera `test`, dostają `claude_cli`.
Uwaga: wszystkie dziewięć jest już w zaakceptowanym rejestrze
`docs/migration/TEST-BASELINE-failures.txt`, więc **to nie jest świeża
regresja** — to znany baseline, którego przyczyna nie była nigdzie zapisana.

### B5. Cykl statusów pomija `synthesizing`
`docs/VOICE_STREAMING.md:121-122`: *„exactly as frozen: `queued → speaking →
done | cancelled | failed`"*. Trigger `voice_queue_status_transition`
(`dan/store/schema.sql:374-383`) dopuszcza z `queued` wyłącznie
`synthesizing`/`cancelled`/`failed` i przerywa `RAISE(ABORT, …)`.
`dan/voice/queue.py:226-241` aktualizuje tylko
`WHERE status = 'synthesizing' AND synthesis_completed_at IS NOT NULL`.
Siostrzany `AUDIO_RUNTIME.md:93-96` podaje ten cykl poprawnie.

### B6. `seq` nie steruje kolejnością odtwarzania i nie jest monotoniczny
`docs/VOICE_STREAMING.md:115-116`. `dan/voice/queue.py:163-176` — `claim_next`
sortuje `CASE lane …, priority DESC, rowid ASC`; `utterance_index` nie
występuje w sortowaniu. `dan/voice/speech.py:71` zeruje `_seq` przy nowej sesji,
a orchestrator zakłada nową sesję dla każdej kontynuacji po narzędziu w tej
samej turze, więc `seq` w turze restartuje. Filler (`speech.py:186-197`) wchodzi
z `utterance_index=0`, kolidując z pierwszym zdaniem.

---

## C. Demon, cykl życia, tura

### C1. Broker workerów opisany jako żywa gałąź tury
`docs/JARVIS_ARCHITECTURE.md:67,218` i `docs/TURN_PIPELINE.md:180,220-222`.
Dowód jak w A6. Komentarz w `dan/daemon/app.py:2362` („the mock worker is the
only registered worker") sam jest nieaktualny wobec przypisania obok.

### C2. ADR-001 złamane: `dand` nie czyta `config.toml`, tylko go przepisuje
`docs/CO-JEST-GDZIE.md:12` pod nagłówkiem *„The rule: one value — one owner"*
przypisuje operatorowi edycję, a `dand` tylko odczyt.
`dan/daemon/app.py:822-825` → `ConfigStore.set_many` →
`dan/config_registry.py:740` → `_write_toml_atomic` → `os.replace` na
`~/.dan/config.toml`. Wejście: `POST /settings`, `PUT /settings/<key>`.
Zapisywalnych kluczy INSTALLATION jest 118. `_dump_toml` odtwarza plik od zera —
**komentarze operatora giną**.

### C3. „Sole writer" bazy nie obowiązuje
`docs/CO-JEST-GDZIE.md:11` i `docs/adr/001-dand-single-owner.md:25-26`.
`dan/cli.py:799-818` — `dan memory sync` otwiera `initialize_database` na
`~/.dan/dan.db` i commituje z własnego procesu, równolegle do żywego demona
(`dan/memory/sync.py:40-51`). `dan db init` (`cli.py:344`) tak samo.

### C4. Plik PID w tabeli „oficjalnej tożsamości" — nikt go nie tworzy
`docs/LAUNCH_SUPERVISION.md:34`. `dan/paths.py:70` tylko rozwija ścieżkę.
Jedyny zapis w repo to `scripts/dan:226`, czyli ścieżka `dan start`, nie launchd.
Sprawdzone na żywo: w `~/.dan/runtime/` są `api-token`, `claude-session.json`,
`hotkey.lock`, `voice/` — `dand.pid` nie ma, mimo działającego demona.

### C5. „`dand` rotuje logi sam" — rotuje jeden plik z sześciu
`docs/CO-JEST-GDZIE.md:13`. `dan/logging.py:74-79` podpina
`SecureRotatingFileHandler` wyłącznie do `dand.log`. `dand.out.log` /
`dand.err.log` pochodzą z `StandardOutPath`/`StandardErrorPath` w pliku plist,
`dand-console.log` ze `scripts/dan:78` — nikt ich nie obraca. **Na żywo
`dand.err.log` ma 5,8 MB i rośnie bez ograniczenia** (`dand.log`: 122 KB).

### C6. „Every transition emits `state.changed`" (FROZEN) — `force_idle` nie emituje
`docs/TURN_PIPELINE.md:81-82` i `:277-279` („fully reconstructable by filtering
events on `correlation_id`"). `dan/daemon/state_machine.py:174-186` —
`force_idle()` łapie błąd appendu, ustawia `event = None`, po czym **bezwarunkowo**
przypisuje `self._state = RuntimeState.IDLE` i publikuje tylko `if event is not
None`. Wołane z `dan/turns/orchestrator.py:678`, `:1631`, `:1649` — czyli
dokładnie tam, gdzie append właśnie zawiódł.

---

## D. Pamięć

### D1. Model ma pełnotekstowe przeszukiwanie archiwum, którego dokument nie zna
`docs/MEMORY_ARCHITECTURE.md:166`: *„`retrieve_memory` exists only as
`MemoryRetriever` over `memory_blocks`."* `dan/tools/memory_recall_tool.py:17-45`
— `memory_recall`, ryzyko `safe_read`, rejestrowany w `dan/daemon/app.py:2344`,
wykonuje `MemoryArchive.recall` (`dan/memory/archive.py:342-370`): zapytanie
FTS5 po `memory_archive_documents` zwracające modelowi pełne `content`.
Narzędzie trafia do promptu jako dostępne.

### D2. Hurtowy zapis wszystkich transkryptów, bez zgody i bez „forget"
`docs/MEMORY_CONTRACT.md:320-323` opisuje „current behaviour" bez tej ścieżki.
`dan/memory/sync.py:115-181` — `sync_dan_turns()` zapisuje treść **każdej tury**
(user i assistant) do trwałych `memory_archive_documents` + FTS;
`sync_path()` (`:22-37`) importuje sesje Claude/Codex JSONL i pliki markdown.
Wystawione jako `dan memory sync` (`dan/cli.py:160-172`, `799-818`).

Nie ma kandydata, dowodu, zgody ani cyklu życia. **Nie ma operacji forget** —
wiersz znika tylko przez ponowny sync z `replace`/`delete_item_ids`. Razem z D1
znaczy to, że model może przeszukiwać pełne archiwum rozmów. **To jest sprawa
prywatności właściciela, nie kosmetyka dokumentacji.**

### D3. Panel ma widok pamięci, który dokument uznaje za nieistniejący
`docs/MEMORY_ARCHITECTURE.md:189-191`: *„none of this exists… it has no memory
view at all."* `dan/panel/assets/index.html:71-113` — `<section id="view-memory">`
z formularzem i listą; zakładka w `:493-495`. `dan/panel/assets/app.js:7376-7395`
pobiera `/memory?active_only=true&limit=25` **oraz** `/memory/items` i renderuje
aktywne pozycje Memory OS; `:7530` wysyła `DELETE /memory/{id}`.

### D4. Reguła kolejności scope/namespace nie działa w żadnej turze
`docs/MEMORY_COMPILER.md:110-111,166-171` i `MEMORY_OS_ARCHITECTURE.md:117`
opisują ją jako dominującą. `dan/memory/compiler.py:303-315` — `_match_rank`
zwraca `0`, gdy `filter_value is None`, a jedyne konfiguracje powstające na
ścieżce tury mają oba filtry `None` (`dan/brain/context_builder.py:782-783`,
`dan/daemon/app.py:2122-2127`). Filtry da się podać wyłącznie z podglądu
`POST /memory/compile-preview`, który nie wpływa na żadną turę. Realnie decyduje
`confidence` → `last_confirmed_at` → `updated_at` → `id` (`compiler.py:263-282`).

### D5. Lista zdarzeń podana jako komplet pomija `memory.candidate.promoted`
`docs/MEMORY_CONTRACT.md:127-131`. `dan/memory/manager.py:213-217` emituje je
przy aktywacji bloku-kandydata; ścieżka osiągalna przez `PATCH /memory/{id}`
z `active: true`. Panel nawet to etykietuje (`app.js:322`).

### D6. Opisany „jedyny kształt widoczny w promptcie" to nie to, co widzi model
`docs/MEMORY_OS_ARCHITECTURE.md:273-280` podaje czterolinijkowy blok.
`dan/brain/claude_cli_adapter.py:139-148` wkłada go pod nagłówek „Historical
memory data (untrusted context…)", a `_format_messages` (`:1424-1447`) +
`_clean_text` (`:1521-1522`) składają całość w **jedną linię** bez znaków nowej
linii; zaraz po niej doklejane są `memory_blocks` w innym formacie (`:1450-1459`).

---

## Co przeróbka naprawiła dobrze

Żeby nie czytać tego jako wyroku na cudzej robocie — audytorzy potwierdzili
zgodność w kilkudziesięciu miejscach. Najważniejsze:

- `docs/MACOS_PERMISSION_MODEL.md` jest **czysty**: bannery „DESIGN ONLY" są
  poprawne, a jedyne twierdzenie o zbudowanej funkcji (§5, token transportowy)
  zgadza się co do joty, łącznie z tym, że jest wyłączony.
- Opis braku bramek approval jest wszędzie prawdziwy: `request_tool()` ignoruje
  politykę, `ApprovalGate` nie jest wołany z żadnej ścieżki wykonania,
  `awaiting_approval` nie jest ustawiane, a `tests/test_effective_tool_policy.py`
  tego pilnuje.
- Realne bariery opisane uczciwie: redakcja sekretów przed zapisem zdarzeń
  + limit 4096 znaków; `realpath` przed testem zawierania i odmowa przy pustych
  rootach; `O_NOFOLLOW`/`O_EXCL` + `renameat` w `file_write`; zdejmowanie
  wartości pól bezpiecznych w dwóch warstwach; odrzucanie znaków sterujących
  w `ui_type`/`terminal_paste`.
- Cały §2 TURN_PIPELINE (zbiór stanów i dozwolone przejścia), `RESTART_EXIT_CODE
  = 86`, wyłączny `flock` na hotkey, nadzór `supertonic serve`.
- Schemat tabel pamięci, budżety kompilatora, precedencja włączania compiled
  memory i fail-closed na śmieciowej wartości env.
- Usunięcie martwego zapytania `worker_jobs` z `context_builder.py` było
  poprawne — kod leżał za `return []` i nigdy się nie wykonywał.

## Kolejność naprawiania, gdyby padła decyzja

1. **D2 + D1** — prywatność. Reszta to opis; to jest dane.
2. **A2 + A1** — inwentarz narzędzi i obietnica barier. Fałszywe poczucie
   bezpieczeństwa kosztuje najwięcej.
3. **C2 + C3** — dwie złamane zasady własności z ADR-001.
4. **C5** — 5,8 MB nierotowanego loga to problem operacyjny, nie prozatorski.
5. Reszta prozą, przy okazji dotykania tych plików.
