# HANDOFF — Voice/Streaming Port (DAN → Jarvis) + Streaming Fixes

> # ⛔ DOKUMENT HISTORYCZNY — 2026-07-08. NIE OPISUJE DZISIEJSZEGO RUNTIME'U.
>
> Ten plik jest zapisem jednej sesji z **8 lipca 2026**, sprzed scalenia repo
> i sprzed Release 1. **Nie czytaj go jako opisu tego, jak system działa dziś**
> i nie wykonuj z niego żadnych komend — ścieżki, procesy i skrypty poniżej
> w większości już nie istnieją.
>
> Co się zmieniło (stan zweryfikowany 2026-07-21):
> - **Nie ma dwóch repo ani dwóch brokerów.** Jest jedno repo (`~/Documents/dev/dan`)
>   i jeden broker — **w środku demona `dand`** (`dan/voice/broker.py`).
>   `tools/jarvis/voice_broker.py`, `dan_core/say.py`, `start-voice-broker.sh`
>   i `~/Documents/dev/jarvis` to nieżywe byty; supervisor traktuje je jako
>   **legacy do zgłoszenia** (`docs/LAUNCH_SUPERVISION.md` §3).
> - **Żadnego stanu w `/tmp`.** Kolejka, stan i pliki gotowości żyją w bazie
>   `~/.dan/dan.db` i w `~/.dan/runtime/` — `/tmp/dan-voice/*` to legacy artefakt.
> - **Mowa idzie wyłącznie przez** `dan speak --json --as <persona> --session <s>
>   --source claude --stdin`. Hook MessageDisplay **hosta Claude Code** został
>   usunięty i zakwarantannowany 2026-07-21 — nie wskrzeszać.
>   **To nie dotyczy markerów `[[GŁOS]]` w kodzie `dand`** — tam są żywym
>   produktem (`dan/brain/context_builder.py`), a ich wycięcie psuje mowę na
>   żywo. Szczegóły i granica: `docs/GLOS-I-KOLEJKA.md`.
> - **Głosy i persony** to `config/voice/personas.toml` (kanon w repo). Opisany
>   niżej podział „DAN=M3 / DANusia=F4 / Jarvis=M5 bastard" jest NIEAKTUALNY —
>   `jarvis` jest dziś **aliasem DAN-a**, nie osobną postacią, a profile
>   masteringu person czytaj z pliku, nie stąd.
> - **Warm serve** został przeportowany i żyje w `dan/voice/tts.py`
>   (`supertonic serve` jako dziecko nadzorowane przez `dand`), a nie w bashu.
> - Skille nie leżą w `~/.claude/skills/` tylko w `integrations/` w tym repo.
> - **Klucz ElevenLabs** z §7: potraktuj jako spalony i zrotowany; ElevenLabs
>   nie jest dziś silnikiem DAN-a (silnik = Supertonic).
>
> Gdzie jest dzisiejsza prawda: `AGENTS.md`, `docs/CO-JEST-GDZIE.md`,
> `docs/GLOS-I-KOLEJKA.md`, `docs/AUDIO_RUNTIME.md`, `docs/VOICE_STREAMING.md`.
>
> Po co ten plik zostaje: to zapis pomiarów i decyzji (dlaczego warm serve,
> dlaczego whole-utterance NIE dla live, gdzie ucieka latencja). Wartość ma
> **rozumowanie**, nie ścieżki.

**Data:** 2026-07-08 · **Autor sesji:** Claude Opus 4.8 (1M) · **Dla:** nowej sesji Ozzy'ego

> Cel tego pliku (w 2026-07-08): nowa sesja z ZEROWYM kontekstem ma móc wejść i kontynuować bez zgadywania.
> Ozzy słyszy większość rzeczy głosem — nie zasypuj czatu.

---

## 0. TL;DR — gdzie jesteśmy TERAZ
- **Dwa osobne repo, dwa brokery głosu.** Nie mylić.
  - **DAN** (`~/Documents/dev/dan`, branch `main`) — standup/beka. Broker: `tools/jarvis/voice_broker.py` (single-file).
    Tu ZROBILIŚMY dziś cały upgrade głosu. **Commit `644bc03` wbity** (3 pliki, tylko moje — reszta working tree nietknięta).
  - **JARVIS** (`~/Documents/dev/jarvis`, branch `spike/jarvis-local-runtime-check`) — REALNY asystent (Claude CLI brain,
    daemon, panel, turns, VoiceQueue). Broker: `jarvis/voice/broker.py`. **Tu portujemy** dzisiejsze wygrane + naprawiamy streaming.
- **Następny krok:** zacząć port w Jarvisie. Rekomendacja: **#5 latency trace najpierw** (mierz→tnij), potem warm-serve.
- **Zero commitów w jarvisie bez słowa Ozzy'ego.** DAN commit był na jego wyraźną komendę.

---

## 1. Co zrobiliśmy dziś w DANie (commit 644bc03) — to jest materiał do portu
Broker DANa (`dan/tools/jarvis/voice_broker.py`) + `dan_core/say.py` + `state/overrides.json`:

1. **Warm serve** — broker podnosi `supertonic serve` (port 7788, model `supertonic-3`) RAZ na starcie; synteza przez
   `POST /v1/tts`. Wcześniej forkował `supertonic tts` per kawałek = **0.64s reloadu modelu za każdym razem**.
   Warm = zero reloadu (~2× szybciej). Fallback na cold CLI gdy serwer padnie. Serwer ubijany przy zamknięciu brokera.
   - **API (dokładnie):** pola `text, voice, lang` (NIE `language`!), `speed` (0.7-2.0), `steps` (1-100),
     `max_chunk_length`, `silence_duration`, `response_format`. Health: `GET /v1/health`. Zwraca WAV bajty.
2. **Whole-utterance = naturalna prozodia** — broker pakuje zdania do **300 znaków** (`_mc`) + serve `max_chunk_length=400`
   → całe wypowiedzi w JEDNYM wywołaniu → supertonic sam intonuje między zdaniami. Cięcie per-zdanie ZABIJAŁO prozodię.
   ⚠️ **TO NIE PORTUJE SIĘ 1:1 DO JARVISA** — patrz §3.
3. **Mastering per-persona** — `MASTER_PROFILES` (bastard/gritty/clean) + `_master_phrase()`, łańcuch ffmpeg:
   pitch↓ (asetrate+atempo, tempo bez zmian), EQ bas/presence, aexciter, crystalizer, deesser, kompresor, limiter, loudnorm.
   ~0.2s/kawałek, fail-safe (błąd→gra surowy). Profil z persony przez pole `profile` w REQ.
   - **Intonacja ffmpeg (head/tail) WYŁĄCZONA** (`_intonation_for`→0) — whole-utterance ją zastępuje; była droga (~1s) i psuła.
4. **Cross-request prefetch** — głowa następnej wypowiedzi syntezuje się gdy ogon bieżącej gra → zero ciszy między mówcami.
5. **Steps** konfigurowalne (`/tmp/dan-voice/steps`, default 18, ustawione 8 — Ozzy nie słyszy różnicy 6-18).
6. **Słownik anglicyzmów** `_PL_PHONETIC` rozszerzony (runtime→rantajm, request→rikłest, streaming→striming, cache→kesz...).
   Regex `\b(stem)(\w*)` — słowo + polska końcówka, dłuższe klucze pierwsze.
7. **Persony w say.py:** `PERSONA_VOICE`/`PERSONA_VOICE_KEY` + `PERSONA_PROFILE`. Głosy: **DAN=M3 surowy** (Ozzy woli bez
   masteringu), **DANusia=F4 clean**, **Jarvis=M5 bastard (mniej basu: asetrate 0.91→0.93, bas +4.5→+3dB), tempo 1.4**.
   Jarvis = ZIOMEK nie kamerdyner.

**Czasy (zmierzone):** synteza warm ~1.3s/6s-audio (główny koszt, schowany pod streaming), mastering 0.2s, intonacja 0 (off).

---

## 2. STAN STREAMINGU JARVISA — zweryfikowane (agent przeczytał realny kod check brancha)
Ozzy dostał 2 zewnętrzne analizy streamingu. **DUŻA CZĘŚĆ ICH P0-ów JEST JUŻ NAPRAWIONA** — patrzyły na starszy snapshot.
NIE marnuj czasu na te „bugi":

**JUŻ ZROBIONE (nie ruszać):**
- `supports_streaming` jest JAWNE (class attr), NIE zgadywane z sygnatury. `manager.py:216` gejtuje `getattr(adapter,"supports_streaming",False)`.
- Codex `supports_streaming=False` — poprawnie nie-streamujący (`codex_cli_adapter.py:23`).
- Args streamingu OK: `--output-format stream-json --verbose --include-partial-messages` (`claude_cli_contract.py:54`).
  **Empirycznie potwierdzone:** `claude -p` z tymi flagami REALNIE streamuje delty co ~0.5s (ttft_stream ~1.9s).
- **Golden-path test ISTNIEJE:** `tests/test_streaming_turn_speech.py` (delta→zdanie→VoiceQueue), `tests/test_brain_cli_streaming.py`.
- Broker poll = **0.05** (`jarvis/voice/broker.py:26`), nie 0.25 jak twierdziły analizy.

**REALNE LUKI (zweryfikowane, po ważności):**
- **P0 — brak hop-by-hop latency trace.** Są tylko statusy `BRAIN_REQUESTED`/`BRAIN_RESPONDED`. Brak `first_stdout/first_delta/
  first_speech_chunk/tts_synth/playback`. → nie zmierzysz gdzie ucieka czas. **Zrób NAJPIERW** (additive, zero ryzyka).
- **P1 — brak early-chunk policy.** `SpeechStreamSession.feed` (`voice/speech.py:95`) enqueue'uje tylko to co zwróci
  `SentenceChunker.feed` (`voice/chunker.py:43`, min 12 znaków, tylko terminator/newline). Brak flush po timeout/min-chars/przecinku.
  → pierwsze zdanie czeka na kropkę. Największa dźwignia odczuwalnej latencji.
- **P1 — delty tylko do speech.** `orchestrator.py:310` `streaming_enabled = self._speech is not None and supports_streaming()`,
  `:329` `on_delta=speech_session.feed`. Delty NIE docierają do panelu/event-bus/trace.
- **P1 — degraded mode bez jawnego statusu** (`live_delta`/`final_only_degraded`/`no_speech`). Stan tylko wewnętrzny (`speech.py:107`).
- **P2:** auto_detect bez proweniencji (`source: configured/detected/probed/assumed`); `bypassPermissions` nieoflagowany
  (`claude_cli_contract.py:35`); cichy default provider (PATH/env może zmienić, brak `auto_selected`); dup capability ProviderInfo vs adapter attr.
- **P3:** monolit `build_claude_cli_command` (`claude_cli_contract.py:125-282`); docs drift (`docs/runbooks/BRAIN_ADAPTERS.md` opisuje tylko blocking).

---

## 3. ⚠️ KLUCZOWA KOREKTA — whole-utterance NIE dla Jarvisa
DAN standup = linie pisane Z GÓRY → cała naraz = prozodia OK. **Jarvis = LIVE**: Claude streamuje token po tokenie, chcesz
mówić pierwsze zdanie JAK NAJSZYBCIEJ. Whole-utterance by ZABIŁO tę latencję. Więc w jarvisie:
**zostaje sentence-streaming + dodajemy early-chunk (#4).** Whole-utterance zostawiamy DANowi. NIE portuj go.

---

## 4. MAPA PORTU DAN → JARVIS (czysto, w design jarvisa)
| Wygrana DANa | Cel w Jarvisie | Uwaga |
|---|---|---|
| **Warm serve** | `jarvis/voice/tts.py` `SupertonicEngine.synthesize` (~:184, forkuje CLI `subprocess.run`) → POST /v1/tts, fallback CLI | Największy win. **Zachowaj kill/cancel (barge-in)** — jarvis liczy na „one subprocess per chunk = kill = cancel". Serve potrzebuje ścieżki anulowania (np. nie czekać na response przy barge-in). |
| **Anglicyzmy** | `config/jarvis.example.toml` sekcja `[voice].tts_pronunciations` | Mechanizm `apply_pronunciations` (`voice/tts.py:120`) JUŻ jest (dłuższy klucz pierwszy, IGNORECASE). Tylko dołóż dane z `_PL_PHONETIC`. |
| **Mastering per-persona** | `jarvis/voice/tts.py` (ffmpeg po syntezie) | Net-new (jarvis nie ma ffmpeg). Adaptuj do person jarvisa. Szlif — na końcu. |
| **Prefetch** | jarvis broker JUŻ ma slot prefetch (`voice/broker.py:45,98`) | Nic do portu. |
| Whole-utterance | — | NIE portować (§3). |

---

## 5. KOLEJNOŚĆ (mierz → tnij) — plan uzgodniony z Ozzym
1. **#5 latency trace** — design gotowy (patrz §6). Additive, chroni golden-path test.
2. **Warm serve** w `tts.py` (zmierzony win — zabija reload CLI).
3. **Anglicyzmy** → config (trywialne).
4. **Early-chunk** (#4) — jarvisowy gap.
5. **Delta fan-out** (#3) → panel/trace.
6. **Mastering** — szlif barwy, ostatni.
- **Skille (standup)** przeniesiemy osobno (Ozzy: „skille przeniesiemy jeszcze też"). Skill: `~/.claude/skills/standup/`.

---

## 6. DESIGN #5 (latency trace) — gotowy do implementacji
Punkty hop znalezione:
- adapter `for line in proc.stdout` (`claude_cli_adapter.py:315`) = `first_stdout_line`; `self._on_delta(text)` (`:236`) = delta.
- orchestrator generate (`turns/orchestrator.py:326`), `on_delta=speech_session.feed` (`:329`).
- `SpeechStreamSession._enqueue` (`voice/speech.py:128`) = pierwszy chunk → VoiceQueue. Filler `kind="filler",seq=-1` (`:226`), sentence `kind="sentence"` (`:135`).
- Wzór emisji eventu: `_append_event(EventType.BRAIN_RESPONDED, {...})` (`orchestrator.py:369`).

**Plan:** klasa `LatencyTrace` per tura — `.mark(name)` zapisuje PIERWSZY monotonic ts. Orchestrator owija `on_delta`
(→`first_delta`), `SpeechStreamSession` zapisuje własny `first_enqueue_at` + `first_audio_kind` (filler|sentence), orchestrator
zbiera po generacji i emituje JEDEN event `turn.latency` na końcu tury (bez N nowych typów eventów). Reuse istniejących
`context_built/brain_requested/brain_responded`. To od razu rozdzieli winnych: `request→first_delta` (brajn/CLI reload),
`first_delta→first_chunk` (chunker = #4), `chunk→responded`.

---

## 7. GRABIE / gotchas (nie powtarzać wtop z tej sesji)
- **DAN broker restart** po zmianie kodu: `pkill -f "supertonic serve"; kill -TERM $(pgrep -f voice_broker.py)`; potem
  `zsh tools/jarvis/start-voice-broker.sh`; czekaj na `/tmp/dan-voice/ready`. Broker sam podnosi serve.
- **Głos z Basha:** ZAWSZE `dangerouslyDisableSandbox:true` ORAZ `run_in_background:true` — inaczej afplay ubity (exit 144), cisza.
- **Serve API:** klucz `lang` NIE `language` (pydantic po cichu ignoruje nieznane pola → język by defaultował).
- **ElevenLabs API key** wisi JAWNIE w `dan/.env` (widoczny w tej sesji) — Ozzy: rozważ rotację. Nie wklejać kluczy do czatu.
- **Jarvis warm-serve port:** zachowaj cancel/barge-in. Obecny `subprocess.run` daje kill=cancel za darmo; przez HTTP musisz
  dać własny cancel (timeout/abort), inaczej barge-in nie przerwie syntezy.
- **jarvis working tree** ma niezacommitowane zmiany (spike). Nie clobber. Czytaj przez git gdy trzeba porównać branche.
- **Analizy streamingu Ozzy'ego są częściowo NIEAKTUALNE** (§2) — weryfikuj każdy punkt na realnym kodzie, nie implementuj na wiarę.

---

## 8. Pliki/pamięć referencyjne
- Pamięć: `~/.claude/projects/-Users-n1-ozzy-Documents-dev/memory/` — `broker-mastering.md`, `standup-jarvis-voice.md`,
  `standup-max-vulgar.md`. Indeks: `MEMORY.md`.
- DAN broker: `dan/tools/jarvis/voice_broker.py`. Głos say: `dan/dan_core/say.py`. Naturalny DAN (mastering źródłowy):
  `~/.claude/skills/voice-report/dan_voice.py` (pełen silnik prozodii/masteringu — DAN broker wziął tylko rdzeń).
- Jarvis voice: `jarvis/voice/{broker,tts,speech,chunker}.py`. Brain: `jarvis/brain/{manager,claude_cli_adapter,codex_cli_adapter,auto_detect}.py`.
  Kontrakt: `jarvis/brain/claude_cli_contract.py`. Orchestrator: `jarvis/turns/orchestrator.py`.
- **Persona standupu = MAX wulgarna** (Ozzy decree): patrz `~/.claude/skills/standup/SKILL.md` sekcja „POZIOM WULGARNOŚCI = MAX".
