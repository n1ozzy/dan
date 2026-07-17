# Jarvis v4.2 — Master Plan (plan-of-record)

Status: OBOWIĄZUJE (mandat Ozzy'ego 2026-07-02: "chciałbym abyś mnie prowadził
[...] sam ogarniesz co i jak" + "dyscyplina w chuj aż dowieziemy Jarvisa").
Data: 2026-07-02. HEAD w chwili pisania: `28b1611` (19D-A, 615 testów zielonych).
Cel produktu (wizja Ozzy'ego): z DAN-a zrobić Jarvisa jak w Iron Manie —
pełny operator macOS z głosem, pamięcią i osobowością, na dyscyplinowanym runtime.

Ten dokument **zastępuje** jako plan-of-record:

- sekwencję wykonawczą z `JARVIS-V4-1-CODEX-MASTER-PROMPT-SEQUENCE.md` (Desktop, plan GPT 5.5 PRO),
- sekcje planistyczne raportu nadrzędnego `info.txt` (Desktop, handoff GPT 5.5 Thinking).

Nie zastępuje kontraktów: `docs/CONTRACTS.md`, `docs/SECURITY_MODEL.md`,
`docs/MACOS_OPERATOR_CONTRACT.md`, `docs/TURN_PIPELINE.md` pozostają źródłem prawdy
dla swoich domen.

---

## 1. Dlaczego ten dokument istnieje

Historia planowania miała dwa źródła, które nigdy nie zostały rozliczone względem siebie:

1. **Blueprint PRO** (`JARVIS-V4-1-FINAL-MASTER-BLUEPRINT.md` + sequence 00–24):
   produkt = lokalny asystent głosowo-tekstowy; MVP obejmowało voice track
   (audio devices, PTT leases, voice queue, anti-echo), WorkerBroker, MenuBar,
   WebSocket, launchd, realne file/shell toole.
2. **Kontynuacja Thinking** (prompty 14, 15, 15A/B, 19A–D, 20A/20A-FIX):
   pogłębiła approval loop **ponad** blueprint (to było dobre) i przepięła produkt
   na **operatora macOS** (20A) — czego blueprint nie znał — jednocześnie po cichu
   porzucając połowę MVP PRO bez formalnego werdyktu.

Efekt: repo ma szkielety modułów (`NotImplementedError`) po scaffoldzie z PRO,
docs mają operator contract z Thinking, a żaden dokument nie mówił, co z MVP PRO
przeżywa, co jest odroczone i co ubite. Ten plan to rozlicza.

**Decyzja produktowa (Ozzy, potwierdzona):** Jarvis jest operatorem macOS.
Voice jest interfejsem, nie fundamentem — wchodzi po fundamencie operatora.
Pivot 20A stoi. Rozliczamy MVP PRO względem tej decyzji.

---

## 2. Święte zasady (bez zmian)

```text
jarvisd owns truth (SQLite)
panel renders truth
brain thinks statelessly — model NIGDY nie wykonuje, tylko proponuje
wykonuje jarvisd: ToolRegistry -> PermissionPolicy -> ApprovalGate -> EventStore
provider session is not memory
EventStore = append-only audit timeline z centralną redakcją sekretów
approve nie wykonuje; execute-approved jest osobne i jawne
examples != commitments (po 20A-FIX)
/tmp is transport, not memory
jedyny launchd label: com.ozzy.jarvisd
legacy repo dev/dan: read-only muzeum
```

Zasady prowadzenia (dyscyplina do dowiezienia):

```text
jeden etap = jeden scoped prompt = jeden problem = mały commit
po każdym etapie: git status clean, pytest zielony
po każdej zmianie flow: smoke harness (fake CLI brain pattern, nie realny provider)
gate review po etapach oznaczonych GATE — bez przejścia dalej przed review
docs correction oddzielnie od implementation
żadnego "przy okazji" — scope creep = odrzucony diff
nie przytakiwać; fakt / przykład / wizja / decyzja / commitment rozdzielane jawnie
```

**CI status (FIX-12, 2026-07-05):** aktywny workflow GitHub Actions (`.github/workflows/ci.yml`) uruchamia `ruff`, testy jednostkowe oraz `smoke matrix` (`smoke-text-runtime`, `smoke-tools-approvals`, `smoke-file-read`, `smoke-stream`) na `push`/`PR` (`ubuntu-latest`).
To jest minimum z zamkniętym mandatem „zielone po etapie” jako check.

---

## 3. Stan faktyczny repo (zweryfikowany 2026-07-02)

> **AKTUALIZACJA 2026-07-02 wieczór — FAZY A–H ZAMKNIĘTE.** Stan poniżej
> (615 testów, szkielety, znaleziska F1–F5) to obraz sprzed sekwencji v4.2 —
> zostaje jako zapis historyczny. Stan bieżący: **1322 testy, 22/22 smoke**;
> zrealizowane FAZY A–F, voice track G0–G4 (GATE G4 + Gate G zaliczone,
> G5 odroczone dekretem §7.8, model M1 zostaje), H1 menu-bar shell
> (NSStatusItem + NSPopover + WKWebView, cockpit operator-first z widokiem
> podstawowym/zaawansowanym), H2 `scripts/jarvis-dan-report` (diagnose-only,
> snapshot w `docs/reviews/2026-07-02-legacy-dan-leftovers.md`), H3 docs
> handoff. Znaleziska F1–F5 rozliczone w FAZIE A/B (fail-closed roots,
> realpath containment, source-sensitivity, redakcja rozszerzona, token
> transportowy). Orientacja dla reviewerów: `docs/REVIEW_HANDOFF.md`.
> Backlog po-MVP: przeprojektowanie zawartości panelu pod operatora
> (wybór modelu/effort/providera i głębsze ustawienia głosu wymagają nowych
> endpointów daemona; PTT/nasłuch są już na istniejących lease endpoints;
> feedback Ozzy'ego 2026-07-02).

Działa i jest przetestowane (615 testów):

- text turn pipeline (`POST /input/text`, CLI, historia, konwersacje),
- brain adapters: mock + Claude CLI + Codex CLI foundation (fake subprocess w testach),
- EventStore z centralną redakcją (`jarvis/security/redaction.py`),
- Memory API/CLI + ContextBuilder (active-only),
- pełny approval loop: registry → policy → approval → jawny execute → ToolRun
  → one-shot brain continuation → turn finished,
- parser `<jarvis_tool_call>` w adapterach CLI (UWAGA: mock go nie ma — smoke
  z model-originated tool calls wymaga fake CLI, wzorzec `scripts/smoke-tool-continuation.sh`),
- `awaiting_approval` bez deadlocka daemona (celowo brak RuntimeState.WAITING_APPROVAL),
- statyczny HTML cockpit (polling) + ograniczony CORS localhost,
- RuntimeSupervisor report-only, no auto-kill,
- 5 smoke harnessów w `scripts/`.

Szkielety `NotImplementedError` po scaffoldzie 01 (nietknięte od tamtej pory):

- `jarvis/api/websocket.py`, `routes_brain.py`, `routes_voice.py`, `routes_audio.py`
- `jarvis/workers/*` (broker, jobs, codex/claude workers)
- `jarvis/voice/*` (broker, queue, tts, stt, vad, anti_echo, listening)
- `jarvis/audio/*` (devices, models, policy)
- `jarvis/panel/menubar_app.py`, `webview_bridge.py`
- `jarvis/tools/shell_tool.py`, `file_tool.py` (po 38 linii, bez logiki)
- `jarvis/turns/policies.py`

Znaleziska z review kodu (Fable 5, 2026-07-02) — realne defekty w zmergowanym kodzie:

| # | Znalezisko | Miejsce | Waga |
|---|-----------|---------|------|
| F1 | `file_read` fail-OPEN: puste `approved_roots` (default) ⇒ ALLOW dowolnej ścieżki. Łamie SECURITY_MODEL ("allow **within approved roots**") i blueprint PRO §12. | `jarvis/tools/permissions.py:103` | wysoka (latentna do czasu realnego file toola) |
| F2 | Containment bez `realpath` — symlink pod approved rootem wskazujący poza root przechodzi kontrolę. | `jarvis/tools/permissions.py:152` | wysoka (latentna, jw.) |
| F3 | `PermissionPolicy.decide()` nie przyjmuje `source` (`direct_user_command` vs `model_originated` …) — a source-sensitivity to święta zasada z operator contract §5.4. | `jarvis/tools/permissions.py:58` | projektowa — do 20B |
| F4 | Redakcja nie łapie: `gho_/ghs_/ghu_/ghr_`, Slack `xox[bap]-`, AWS `AKIA…`. | `jarvis/security/redaction.py:66` | niska |
| F5 | Zero auth/CSRF na daemon API — tylko bind 127.0.0.1. Blokuje realne toole. | `jarvis/config.py:111`, `jarvis/daemon/app.py` | wysoka przed FAZĄ C |

---

## 4. Rozliczenie MVP PRO — werdykty

Każda pozycja MVP z blueprintu PRO dostaje jawny werdykt. "DEFER" ma warunek wejścia —
nie jest eufemizmem na "nigdy".

| Pozycja MVP PRO (prompt) | Werdykt | Uzasadnienie / warunek wejścia |
|---|---|---|
| Contracts, scaffold, config, schema, events, state machine, API, supervisor, brain, memory, turn pipeline, CLI adapters (00A–11) | **DONE** | zrealizowane, częściowo w innej kolejności |
| ToolRegistry + ApprovalGate (12) | **DONE+** | zrobione lepiej niż PRO: jawny execute-approved zamiast auto-execute po approve; plus policy na model tool calls, continuation, redakcja |
| Realne `shell_tool` / `file_tool` (12) | **KEEP — FAZA C** | operator bez file/shell jest atrapą; wejście po hardeningu (FAZA A) i permission model (FAZA B) |
| WorkerBroker (13) | **DEFER — FAZA E** | operator core ważniejszy; wejście po 21A/21B, gdy będzie co delegować |
| AudioDeviceManager (14) | **DEFER — FAZA G** | voice po fundamencie (decyzja Ozzy'ego); kontrakt AudioDeviceState w CONTRACTS.md zostaje |
| ListeningLease / PTT (15) | **DEFER — FAZA G** | jw.; kontrakt ListeningLease zostaje — nie projektować od nowa |
| VoiceQueue / TTS broker (16) | **DEFER — FAZA G** | jw.; tabela voice_queue już istnieje w schemacie — nie ruszać |
| Anti-echo / STT / barge-in (17) | **DEFER — FAZA G** | jw. |
| MenuBar shell PyObjC (18) | **DEFER — FAZA H** | statyczny cockpit wystarcza do końca fundamentu; native panel po e2e |
| Compact cockpit UI (19) | **DONE inaczej** | jako statyczny HTML cockpit; upgrade do live w FAZIE E (WebSocket) |
| Brain switch API (20) | **KEEP — FAZA E** | `routes_brain.py` to stub; potrzebne zanim będzie >1 realny provider w użyciu |
| Memory UI / settings UI (20) | **CZĘŚCIOWO DONE** | memory API/CLI/cockpit są; settings UI przy FAZIE E |
| WebSocket `/stream` (07) | **KEEP — FAZA E** | polling wystarcza teraz; live stream przed screen-events (21C) i workerami |
| Launchd lifecycle (21) | **KEEP — FAZA F** | po e2e smoke, przed voice; nigdy auto-install |
| E2E MVP smoke (22) | **KEEP — FAZA F** | zaktualizowany scenariusz operatorowy (§6) |
| Docs handoff (23) | **CIĄGŁE** | runbooki utrzymywane per etap |
| Legacy DAN cleanup helpers (24) | **DEFER — FAZA H** | bez zmian: diagnose-only, nigdy destructive |
| Wake word / always-on / MCP / vector memory / multi-persona / cloud (§17 PRO) | **OUT** | bez zmian — nie-MVP |

Dodatki Thinking-ery nieobecne w PRO — werdykt **KEEP, już DONE**: explicit
execute-approved, model tool-call capture, provider tool block parser,
approval decision events, PermissionPolicy na model path, awaiting_approval,
one-shot continuation, centralna redakcja, operator contract + examples≠commitments.

Nowe względem obu planów (pivot operatorowy): FAZY B–D poniżej.

### 4a. Rejestr oczekiwań z legacy DAN (audyt 2026-07-02)

Dekret Ozzy'ego (§7.6): z DAN-a nie przenosimy kodu, logiki ani architektury.
Ten rejestr to wyłącznie: **wymagania** (co ma działać, bo działało i Ozzy
tego oczekuje) oraz **fakty o narzędziach trzecich** (właściwości MLX/sox/
whispera odkryte empirycznie — dotyczą narzędzi, które i tak wybraliśmy,
nie designu DAN-a). Implementacja zawsze clean-room pod kontrakty v4.1.

| Pozycja | Charakter | Werdykt |
|---|---|---|
| First-sound ≤ ~2 s przy odpowiedzi głosowej (streaming zdaniami + fillers) | wymaganie | **KEEP — G0/G3** (projekt kontraktu streamingu w G0) |
| Nasłuch nie ucina użytkownika w pół zdania; echo własnego TTS nie staje się turnem | wymaganie | **KEEP — G4** (mechanizm zaprojektujemy w G0/G4 od zera; stan przez DB, nie /tmp) |
| PTT: przycisk + globalny hotkey; domyślnie cisza, zero always-on | wymaganie | **KEEP — G2** (ListeningLease ma już source `global_hotkey`) |
| sox: gain PRZED silence, inaczej VAD ucina słabe słowa; highpass 80 Hz na buczenie | fakt o narzędziu (sox) | **KEEP — G4** |
| Whisper halucynuje na ciszy/szumie — potrzebne filtry śmieci i próg no-speech | fakt o narzędziu (whisper) | **KEEP — G4** (filtry napiszemy własne) |
| MLX trzyma model+stream per wątek — synteza/inferencja MLX musi żyć w dedykowanym wątku | fakt o narzędziu (MLX) | **KEEP — G5** (dotyczy też STT MLX w G4) |
| TTS chunkowany per-silnik + przygotowywanie następnego chunka podczas grania | wymaganie (płynność) | **KEEP — G3** (własny design w brokerze) |
| Persona jarvis (jedyna, własna, bez kagańca); persona = data, nie stan | wymaganie | **KEEP — ZREALIZOWANE (2026-07-08)** (config/persona/jarvis.md; gangus/mentor skasowane; patrz §7.7) |
| Głos docelowy: voice-clone; do tego czasu dostępne głosy dozwolonych silników | wymaganie | **KEEP — G3/G5** (zestaw silników: decyzja §7.3) |
| Multi-provider brain (groq, qwen, local Bielik, chain) | wymaganie (przyszłe) | **DEFER — po MVP-voice** |
| Work modes normal/auto/plan | zastąpione | source-sensitive policy (FAZA B) + ApprovalGate — lepszy model tego samego |
| `--dangerously-skip-permissions` ("pełne ręce") | grzech | **KILL** — zastąpione registry+policy+approvals |
| Stan w /tmp, direct afplay, panel z własnym stanem, hardcoded paths, kod DAN-a w ogóle | grzech | **KILL** — ADR-y 001/002/005/008 + dekret §7.6 |

Uwaga operacyjna: legacy DAN **nadal działa** na tym Macu (voice_broker.py,
auto_jarvis.py, listen_ozzy.py loop + com.dan.voice-broker.plist w LaunchAgents,
stan na 2026-07-02). Zgodnie z ADR-013 nie ubijamy automatycznie. **Warunek
wejścia w FAZĘ G: Ozzy ręcznie wygasza legacy runtime** (komendy w
`~/Desktop/Jarvis/JARVIS-NEXT-STEPS-FOR-OZZY.md` §5) — inaczej dwa systemy będą się
gryźć o mikrofon i głośnik.

---

## 5. Sekwencja v4.2 — fazy i etapy

Numeracja od nowa (stara była już nieliniowa: 19D po 20A). Stare numery w nawiasach
dla ciągłości z historią commitów.

### FAZA A — Hardening fundamentu (przed jakimkolwiek nowym kodem operatora)

- **A1** — policy fail-closed: `file_read` przy pustych `approved_roots` ⇒ BLOCKED;
  containment przez `os.path.realpath` po obu stronach; testy na symlink escape
  i pusty root. Naprawia F1+F2. Mały commit, sam kod policy + testy.
- **A2** — redaction gaps: wzorce `gho_/ghs_/ghu_/ghr_`, `xox[bap]-`, `AKIA[0-9A-Z]{16}`;
  testy. Naprawia F4. Osobny mały commit.

Gate A: pytest zielony, smoke-tools-approvals PASS, diff review.

### FAZA B — Permission model operatora (docs only) *(dawne 20B)*

- **B1** — `docs/MACOS_CAPABILITIES.md`: inwentarz klas capability
  (Accessibility read / Accessibility act / ScreenCapture+OCR / terminal profile /
  file / shell / network / notifications / …) — każda z: framework macOS,
  risk class, approval default, wymagane uprawnienie TCC, privacy concern,
  przyszłe nazwy tools, implementation status. Klasy, nie commitmenty.
- **B2** — `docs/MACOS_PERMISSION_MODEL.md`: projekt source-sensitive policy —
  sygnatura `decide(risk, source, tool_name, payload)`; macierz
  source × risk → decision; user-presence model; projekt tokenu transportowego
  (F5) jako warunek FAZY C. Projektuje naprawę F3.

Gate B (GATE — review Ozzy): zero kodu runtime w tej fazie; commitment creep check
(§17.6 z info.txt nadal obowiązuje).

### FAZA C — Realne toole fundamentowe *(z PRO promptu 12, nigdy niezrobione)*

- **C1** — transport auth: lokalny token (plik w `~/.jarvis`, 0600), wymagany
  nagłówek dla endpointów mutujących; cockpit dostaje token; testy. Naprawia F5.
- **C2** — `decide()` z parametrem `source` wg B2 + przepięcie obu ścieżek
  (direct i model-originated); testy macierzy. Naprawia F3.
- **C3** — `file_tool` read-only: realny odczyt w fail-closed approved roots,
  limity rozmiaru, ToolRun + eventy + redakcja; smoke.
- **C4** — `file_tool` write + `shell_tool` read-only profile: approval-required
  zawsze; whitelist poleceń dla shell_read; smoke.

Gate C (GATE): pełny smoke tools+approvals+continuation na realnych toolach.

### FAZA D — Operator adapters *(dawne 21A–D)*

- **D1** *(21A)* — Accessibility read-only adapter (AXUIElement przez jarvisd,
  nigdy przez model); TCC onboarding udokumentowany (ADR-014: artefakty poza
  `~/Documents`); smoke z fake danymi.
- **D2** *(21B)* — Accessibility actions (klik, wpisanie) — zawsze approval,
  source-sensitive wg B2.
- **D3** — WebSocket `/stream` + cockpit live (przeniesione z FAZY E —
  decyzja §7.1: screen events w D4 potrzebują strumienia, nie pollingu).
- **D4** *(21C)* — ScreenCaptureKit + Vision OCR bridge (read-only).
- **D5** *(21D)* — Terminal/iTerm operator profile.

Gate D (GATE): każdy etap osobno + review; D2 wymaga działającego C1 (auth).

### FAZA E — Runtime dorasta

- **E1** — brain switch API (`/brain/adapters`, `/brain/current`, `/brain/switch`,
  persist w settings, historia przeżywa switch).
- **E2** — WorkerBroker + pierwszy worker (mock, potem codex/claude);
  worker nie mówi, nie pisze pamięci, wynik = memory candidate.
- **E3** — settings UI w cockpicie.
- **E4** — persona: **ZAKTUALIZOWANO 2026-07-08:** Jarvis — jedyna persona, własna,
  bez kagańca, dla Ozzy'ego. Zwyrolska, wulgarna, bezlitośnie sarkastyczna.
  Implementacja: `config/persona/jarvis.md` (dane, nie kod). Gangus-1/2/3
  i mentor skasowane (plan E4 z 2026-07-02 zakładał 4 profile z granicami;
  dekret 2026-07-08 konsoliduje do JEDNEJ persony). ContextBuilder ładuje
  `config/persona/jarvis.md` bez selektora profilu (fail-closed, daemon
  się nie wywala; przyszła rotacja profili = decyzja Ozzy'ego). Persona
  nie ma stanu, nie decyduje o toolach, nie omija approvals.

### FAZA F — Stabilizacja

- **F1** — e2e MVP smoke (scenariusz operatorowy, §6).
- **F2** — launchd lifecycle (install script jawny, nigdy auto; uninstall nie kasuje DB).

Gate F (GATE): acceptance criteria §6 spełnione.

### FAZA G — Voice track *(cały pakiet PRO 14–17 + lekcje z DAN, §4a)*

Warunek wejścia: legacy DAN wygaszony ręcznie przez Ozzy'ego (§4a, uwaga operacyjna).

- **G0** — projekt streamingu: kontrakt sentence-streaming w brain adapterach
  (on_delta → chunk → VoiceRequest) + fillers policy. Docs-only, bo to zmiana
  kontraktu BrainResponse — bez tego first-sound wraca do 8–10 s i Ozzy
  słusznie powie, że stary DAN był szybszy.
- **G1** — AudioDeviceManager + polityka (pin builtin mic, output follows system,
  BT mic warning) — kontrakty z CONTRACTS.md, bez projektowania od nowa.
- **G2** — ListeningLease + PTT API (flaga + globalny hotkey) + mock recorder.
- **G3** — VoiceQueue + TTS broker: pluggable engines (zestaw z decyzji §7.3:
  Supertonic + Chatterbox; zakaz edgeTTS/piper/XTTS), chunking per-silnik +
  przygotowanie następnego chunka podczas grania (wymaganie §4a); broker =
  jedyny speaker; direct afplay = violation. Pierwszy realny silnik: Supertonic;
  w testach mock engine.
- **G4** — STT: MLX whisper (dekret §7.4) + nagrywanie (fakty o sox z §4a)
  + własne filtry śmieci + anti-echo + barge-in; transcript przez ten sam
  TurnOrchestrator. Implementacja clean-room.
Gate G (GATE): voice safety review dla zakresu G0–G4 (odpowiednik Gate 6
z PRO). Wchodzi PRZED G5 — nie czeka na voice-clone (dekret §7.8).

- **G5** — **ODROCZONE "na kiedyś" (dekret §7.8, 2026-07-02).** Chatterbox MLX
  voice-clone (inferencja w dedykowanym wątku — fakt o MLX z §4a); docelowo
  własny głos Jarvisa. Do odwołania głosem Jarvisa jest supertonic M1;
  chatterbox zostaje w RESERVED_ENGINES. ElevenLabs tylko jeśli Ozzy
  zadekretuje.

### FAZA H — Wykończenie

- **H1** — MenuBar shell (PyObjC) — panel native, nadal thin client.
  **DONE 2026-07-02**: NSStatusItem (wordmark JARVIS jako template icon)
  + NSPopover 480×760 (ciemny chrome/underlay) + WKWebView na tych samych
  assetach cockpita; token seed z `~/.jarvis/runtime/api-token`. Cockpit
  operator-first: widok podstawowy (Rozmowa z Enter-wysyła, Zgody na
  narzędzia, czytelna Historia) + przełącznik „Zaawansowane" (API, Stan
  daemona, Pamięć, Narzędzia, Ustawienia, Zdarzenia, Runtime — z opisami).
  Backlog (feedback Ozzy'ego): wybór modelu/effort/providera i kontrolki
  głosu (silnik/tempo i inne ustawienia poza PTT/nasłuchem) wymagają nowych
  endpointów daemona — osobny etap, nie łatka w panelu. PTT/nasłuch są już
  obsługiwane przez istniejące endpointy lease.
- **H2** — legacy DAN cleanup helpers (diagnose-only). **DONE 2026-07-02**:
  `scripts/jarvis-dan-report` (`jarvis/diagnostics/legacy_dan.py`) —
  inwentaryzacja procesów/LaunchAgentów/repo/tmp/HF cache/TTS z podziałem
  na śmieci DANa (15,6 GiB do decyzji) vs zasoby Jarvisa (M1 — nie kasować);
  strukturalnie niezdolny do kasowania (test kontraktu źródła). Snapshot:
  `docs/reviews/2026-07-02-legacy-dan-leftovers.md`.
- **H3** — docs handoff finalny. **DONE 2026-07-02**: `REVIEW_HANDOFF.md`
  przepisany na stan po FAZACH A–H, adnotacja stanu bieżącego w §3.

---

## 6. Acceptance criteria — MVP-operator (aktualizacja §16 PRO)

MVP-operator zaliczony, gdy:

1. `jarvisd` startuje i raportuje health (launchd lub cli).
2. Jeden input (text/CLI/panel) = dokładnie jeden Turn; historia przeżywa restart.
3. Eventy tłumaczą pełny lifecycle każdego turnu i każdego toola.
4. Cockpit pokazuje tę samą prawdę co daemon, na żywo (stream, nie polling).
5. Model-originated tool call przechodzi: policy(source) → approval → jawny
   execute → ToolRun → continuation; nigdy auto-execute.
6. `file_read` poza approved roots = BLOCKED; symlink escape = BLOCKED (testowane).
7. Endpointy mutujące wymagają lokalnego tokenu.
8. Jarvis czyta realny stan UI (Accessibility read) i wykonuje zatwierdzoną akcję
   UI (Accessibility act) wyłącznie przez jarvisd, z pełnym audit trail.
9. Zrzut+OCR ekranu dostępny jako read-only tool za zgodą.
10. Rejected approval nigdy nie wykonuje; duplicate execute = 409, bez drugiego ToolRun.
11. Brain switch zachowuje historię.
12. Worker job nigdy nie mówi i nie pisze pamięci bezpośrednio.
13. Zero surowych sekretów w events/logach (testy redakcji + manual grep).
14. Launchd install wyłącznie ręczny, jeden label `com.ozzy.jarvisd`.
15. Legacy konflikty widoczne w `/runtime/processes` i cockpicie.
16. `pytest tests -v` zielony; wszystkie smoke harnessy PASS.

Kryteria voice (PRO §16 pkt 8–11) przechodzą do milestone'u MVP-voice po FAZIE G.

---

## 7. Decyzje (podjęte 2026-07-02, mandat Ozzy'ego)

1. **WebSocket przed screen-capture: TAK.** Przeniesiony do FAZY D jako D3
   (przed ScreenCaptureKit/OCR). Screen events na pollingu to proszenie się
   o lagi i gubione klatki stanu.
2. **MenuBar: zostaje na końcu (H1).** Statyczny cockpit robi robotę przez cały
   fundament; native panel to wykończeniówka, nie infrastruktura.
3. **TTS: broker z pluggable engines — zestaw silników zadekretowany przez
   Ozzy'ego (2026-07-02):** Supertonic (szybki/średnia jakość) + Chatterbox
   (voice-clone) + docelowo własny głos. **Zakazane: edgeTTS, piper, XTTS.**
   Do czasu własnego głosu: dostępne głosy z dozwolonych silników. W testach
   wyłącznie mock engine. Silnik natywny macOS nie jest celem — najwyżej
   awaryjny fallback, jeśli Ozzy zdecyduje.
4. **STT: MLX whisper.** Zadekretowane przez Ozzy'ego. Fallbacki nie są celem.
5. **Wybór narzędzi = decyzja Ozzy'ego.** Fable 5 rekomenduje i uzasadnia,
   Ozzy dekretuje. Żadne narzędzie nie wchodzi do projektu bez tej ścieżki.
6. **Legacy DAN: zero kodu, zero logiki, zero architektury** (dekret Ozzy'ego:
   "nie używamy kodu ani działania i logiki z DANA — bo jest zjebana").
   DAN pozostaje wyłącznie: (a) listą wymagań użytkownika (co ma działać),
   (b) muzeum przestróg (czego nie robić), (c) źródłem **faktów empirycznych
   o narzędziach trzecich**, które i tak wybraliśmy (właściwości MLX, sox,
   whispera — patrz §4a, kolumna "charakter"). Wszystko implementujemy
   clean-room pod kontrakty v4.1.
7. **Persona (E4): JEDYNA persona jarvis, bez kagańca.**
   **ZAKTUALIZOWANO 2026-07-08:** Dekret Ozzy'ego (2026-07-02) zakładał
   4 profile (gangus-1..3 + mentor) z selektorem przez settings. Realnie
   wdrożone: JEDNA persona `config/persona/jarvis.md` — zwyrolska, bez
   granic, bez kagańca, dla Ozzy'ego. Gangus-1/2/3 i mentor skasowane.
   Treść świeża w sesji z Ozzym (nie copy-paste z persona.py, dekret §7.6).
   ContextBuilder ładuje `jarvis.md` bez profilu-selektora (fail-closed).
   Przyszła rotacja profili = osobna decyzja Ozzy'ego.
8. **G5 voice-clone: ODROCZONE "na kiedyś".** Dekret Ozzy'ego (2026-07-02,
   po zamknięciu Gate G4): supertonic M1 zostaje głosem Jarvisa do odwołania;
   chatterbox pozostaje w RESERVED_ENGINES (zarezerwowany, nie implementowany).
   Gate G (voice safety review) wchodzi PRZED G5 i obejmuje żywy zakres G0–G4
   — nie czeka na voice-clone. Powrót do G5 wyłącznie nowym dekretem Ozzy'ego.
