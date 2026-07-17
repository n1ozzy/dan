# G4-LIVE-GATE — pierwszy realny mikrofon (gate Ozzy'ego)

Status: DO PRZEJŚCIA. Ten gate przechodzi **Ozzy osobiście** — asystent go
nie odpala (dekret sesji 2026-07-02: głos wyłączony do decyzji Ozzy'ego).
To jest odpowiednik Gate 6 z PRO (voice safety review) dla zakresu G4a–G4d.

Cel: skonfrontować z rzeczywistością wartości, które dotąd były tylko
testowane na mockach — progi bramki energii (`stt_min_*`), filtr śmieci,
łańcuch sox (`gain`/`highpass`), próg anti-echo i barge-in na żywo.

---

## 0. Warunki wstępne (bez nich nie startować)

- [ ] **Legacy DAN wygaszony ręcznie** (MASTER_PLAN §4a, uwaga operacyjna;
      komendy w `~/Desktop/Jarvis/JARVIS-NEXT-STEPS-FOR-OZZY.md` §5). Dwa systemy
      będą się gryźć o mikrofon i głośnik — sprawdź
      `launchctl list | grep dan` i `GET /runtime/processes`.
- [ ] `pytest` zielony i **22/22 smoke** PASS na HEAD, z którego startujesz.
- [ ] W configu realnym (nie smoke): `[voice] enabled=true`,
      `speak_responses=true`, `broker_enabled=true`, `default_tts="supertonic"`,
      `default_stt="mlx_whisper"`, `recorder="sox"`.
- [ ] Wejście audio = `Mikrofon (MacBook Air)` (polityka pin_builtin_mic;
      BT mic ma być odrzucany/ostrzegany — sprawdź `GET /audio/current`).
- [ ] Model whispera w cache (`mlx-community/whisper-large-v3-turbo`),
      `supertonic` i `sox`/`play` w venv/PATH — daemon i tak zabije się na
      starcie, jeśli czegoś brakuje (fail-closed, to jest oczekiwane).

## 1. Realna transkrypcja (ja mówię — DAN transkrybuje)

- [ ] PTT down (`POST /voice/ptt/down`) → powiedz po polsku jedno pełne
      zdanie → PTT up. Sprawdź w eventach dokładnie **jeden**
      `input.voice.transcribed` z sensownym `text`, `rms`, `voiced_seconds`.
- [ ] Transkrypt stał się **jednym** turnem `source="voice"` i DAN
      odpowiedział głosem (rows w `voice_queue` → `done`).
- [ ] Odpowiedź zaczyna grać szybko (cel: pierwszy dźwięk ≤ ~2 s od
      `brain.requested`; streaming zdaniami z G4d + filler ma to dawać).
- [ ] Szept / cicha mowa: jeśli bramka utnie (`too_quiet` w logu debug),
      zanotuj `rms` z logów — to wejście do kalibracji w §3.

## 2. Test ciszy na filtry (firewall halucynacji)

- [ ] PTT down → **nic nie mów** 3–5 s → PTT up. Ma NIE być żadnego
      `input.voice.transcribed` (bramka energii tnie przed whisperem).
- [ ] PTT down → szum tła (wentylator, klawiatura) → PTT up. Jeśli coś
      przejdzie bramkę, whisper zwykle halucynuje „Dziękuję."/warianty —
      ma to zdechnąć na `stt_junk_phrases` (log `junk transcript dropped`).
      Nowe halucynacje **dopisz do listy w TOML** (lista = data, nie kod).
- [ ] Żaden z powyższych nie stworzył turnu ani wpisu w `voice_queue`.

## 3. Kalibracja progów (stt_min_* + sox gain/highpass)

Kręć jednym pokrętłem naraz; po każdej zmianie config → restart daemona
→ powtórz §1 i §2. Wartości startowe (dzisiejsze defaulty):

| Klucz | Default | Kiedy ruszyć |
|---|---|---|
| `stt_min_rms` | 300 | cisza przechodzi → w górę; szept ucinany → w dół |
| `stt_min_voiced_seconds` | 0.3 | krótkie „tak/nie" ucinane → w dół (ostrożnie) |
| `stt_min_voiced_ratio` | 0.05 | długa cisza z jednym stuknięciem przechodzi → w górę |
| `recorder_gain_db` | 0.0 | słabe słowa gubione → +6..+10 dB (fakt §4a: gain PRZED przyszłym silence) |
| `recorder_highpass_hz` | 80 | buczenie/hum w nagraniu → zostaw/podnieś; głos męski zbyt cienki → obniż |

- [ ] Po kalibracji: 5/5 zdań mówionych normalnym głosem → 5 transkryptów;
      3/3 próby ciszy/szumu → 0 transkryptów.
- [ ] Wpisz finalne wartości do realnego TOML i **zanotuj je w handoffie**.

## 4. Anti-echo na żywo (echo własnego TTS ≠ turn)

- [ ] Zadaj pytanie, na które DAN odpowie długo. W trakcie jego mowy
      trzymaj PTT przy głośniku tak, by mikrofon zbierał **jego własny głos**.
      Ma NIE powstać żaden nowy turn (event `input.voice.transcribed` może
      być — bramka anti-echo tnie dalej; w logu `transcript rejected as echo`).
- [ ] Jeśli echo przechodzi: podnieś `anti_echo_window_seconds` (default 30)
      lub obniż `anti_echo_overlap_threshold` (default 0.75).
- [ ] Jeśli TWOJE prawdziwe wtrącenia są zbijane jako echo (false positive,
      bo mówisz podczas gry TTS i transkrypt zbiera mix głosów): podnieś
      próg w górę. To jest znany trade-off content anti-echo — kalibrować,
      nie przeprojektowywać.

## 5. Barge-in na żywo (3 nogi z VOICE_STREAMING §7)

- [ ] DAN mówi długą odpowiedź → PTT i powiedz **coś innego** niż on.
      Oczekiwane, w tej kolejności: generacja ubita (jeśli jeszcze trwała),
      wiersze kolejki `cancelled` (+ eventy `voice.speak.cancelled`),
      playback ucichł natychmiast (kill procesu `play`), po czym DAN
      podejmuje TWÓJ nowy temat jako nowy turn `source="voice"`.
- [ ] Idempotencja w praktyce: dwa szybkie barge-iny pod rząd nie wywalają
      daemona i nie zostawiają wiszącego audio.
- [ ] Po barge-inie `GET /health` ok; brak procesów-sierot `play`/`sox`
      (`pgrep -fl "play|sox"`).

## 6. Wyjście z gate'a

- [ ] Finalne wartości configu zapisane (TOML + notatka w handoffie).
- [ ] Nowe frazy-śmieci dopisane do `stt_junk_phrases`.
- [ ] Werdykt Ozzy'ego: G4 ZAMKNIĘTE / poprawki (lista) — dopiero po tym
      wolno wchodzić w G5 (Chatterbox voice-clone, MLX w dedykowanym wątku).

Uwagi diagnostyczne: logi daemona (kategorie `voice.stt`, `voice.gateway`,
`voice.cancellation`, `voice.broker`), eventy w DB (`events`), stan kolejki
(`voice_queue`). Smoke'i `smoke-voice-*.sh` pozostają mockowe — nie są
częścią tego gate'a i niczego nie mówią ani nie nagrywają.
