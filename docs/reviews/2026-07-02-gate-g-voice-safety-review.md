# Gate G — Voice safety review (zakres G0–G4)

Data przeglądu: 2026-07-02. Zakres: żywy kod voice po zamkniętym Gate G4
(HEAD `449cada` + poprawki tej sesji). G5 (voice-clone) ODROCZONE dekretem
§7.8 — ten przegląd świadomie go nie obejmuje; powrót do G5 = nowy przegląd
delty. Odpowiednik Gate 6 z blueprintu PRO. **Werdykt wydaje Ozzy** —
sekcja 8 na końcu.

Metoda: audyt kodu z cytatami `plik:linia` (każdy cytat nośny zweryfikowany
ręcznie), zestawiony z dowodami na żywo z G4-LIVE-GATE
(`docs/runbooks/G4_LIVE_GATE.md`).

---

## 1. Mikrofon wyłącznie za lease — ZALICZONE

- Recorder startuje i gaśnie wyłącznie przez `_sync_recorder()`
  (`jarvis/voice/listening.py:161-165`), sterowany istnieniem aktywnych
  lease'ów. Nie ma endpointu ani ścieżki kodu uruchamiającej recorder
  bez lease.
- Model/tool NIE może otworzyć mikrofonu: `ALLOWED_SOURCES = ("ptt",
  "global_hotkey", "lock")` (`listening.py:25`), inne źródło rzuca
  `ListeningLeaseError` (`listening.py:59-60`); test
  `tests/test_listening_leases.py:170-176`.
- TTL: hold 30 s, lock 600 s (`jarvis/config.py:135-136`). Wygaśnięcie
  zatrzymuje recorder i domyka audio gracefully (SIGINT do sox, capture
  przechodzi jeszcze przez bramkę VAD). **Dowód na żywo:** TTL potwierdzony
  w sesji G4-LIVE-GATE.
- Audyt: eventy `listening.lease.created/released/expired` w event store
  (`listening.py:88-91,114-117,154-157`).

Uwagi (nie blokują, do świadomej akceptacji):
- **Lazy expiry** — lease wygasa dopiero przy najbliższym wywołaniu API
  (`_expire_stale()` wołane z acquire/release/active); nie ma background
  joba. Recorder i tak nie nagrywa poza TTL przy następnym kontakcie,
  ale "martwy" lease może wisieć w DB jako `active`.
- **Odnawialny hold** — operator klepiący `ptt/down` co <30 s ma efektywnie
  wieczny open mic. Technicznie zgodne z modelem (operator = władza),
  brak twardego limitu łącznego.
- `LISTENING_LEASE_CANCELLED` zdefiniowany w typach, nigdy nie emitowany.
- Brak health-checku recorder-vs-lease: gdy sox padnie, lease zostaje
  `active` do następnego API call.

## 2. `source="voice"` tylko wewnętrzną ścieżką — ZALICZONE

- HTTP odmawia: `ALLOWED_TEXT_INPUT_SOURCES = {"api","cli","panel","text"}`
  (`jarvis/api/routes_input.py:14`), walidacja `routes_input.py:68` —
  klient nie podszyje się pod głos.
- Jedyna mennica voice-turnów: `DaemonApp._start_voice_turn()`
  (`jarvis/daemon/app.py:727-737`), wołana wyłącznie przez
  `VoiceTurnGateway` po przejściu bramek.
- Kolejność bramek w `gateway.handle_transcript()`
  (`jarvis/voice/gateway.py:65-91`): **anti-echo (72) → barge-in (83-87,
  cancel przez wspólny koordynator) → turn (91)** — zgodnie z kontraktem.
- Transkrypt wchodzi w TEN SAM `TurnOrchestrator` co tekst
  (`app.py:733-737` → `handle_text_input`); zero bocznej ścieżki
  wykonawczej.

## 3. Firewall halucynacji STT — ZALICZONE (z zastrzeżeniem kalibracji)

Trzy warstwy, stosowane liniowo w pipeline transkrypcji:

1. **Bramka energii/VAD** — `CaptureGate.evaluate()`
   (`jarvis/voice/vad.py:100-121`); progi z configu:
   `stt_min_rms=300`, `stt_min_voiced_seconds=0.3`,
   `stt_min_voiced_ratio=0.05` (`jarvis/config.py:109-111`).
2. **Junk blocklist** — `transcription.py:120-122` przeciw
   `stt_junk_phrases` (klasyczne halucynacje whispera: "dziękuję",
   "thank you for watching", …).
3. **Degenerate rule** — `is_degenerate_phrase()`
   (`transcription.py:51-60`): ≥12 znaków przy ≤2 unikalnych literach.
   Potwierdzone na żywo przy G4 (446×"m" na szumie).

Zastrzeżenia:
- Bramek nie da się wyłączyć flagą, ale progi SĄ configiem — złośliwy/
  omyłkowy `stt_min_rms=0` osłabia warstwę 1. Realny TOML jest w gestii
  operatora, więc zgodne z modelem władzy; odnotowane.
- Degenerate rule nie łapie wzorców 3+ literowych ("nanana banana");
  junk lista jest statyczna — nowe halucynacje wymagają dopisania frazy
  (runbook G4 §6 przewiduje to jako proces).

## 4. Znane ograniczenie: film/media przy otwartym PTT — UDOKUMENTOWANE

Przy otwartym PTT mikrofon zbiera dźwięk filmu/mediów z głośników; to,
co przejdzie bramkę energii i nie jest echem własnego TTS ani junkiem,
zostanie potraktowane jako mowa operatora. To ŚWIADOME ograniczenie
zakresu G0–G4 (stwierdzone w sesji żywej G4): PTT to akt woli operatora,
odpowiedzialność za tło leży po jego stronie. Mitygacje (VAD-strumieniowy,
diarization, wake-word) są poza zakresem MVP (MASTER_PLAN §5/§17-PRO).
Niniejszy zapis jest formalną dokumentacją tego ograniczenia.

## 5. Anti-echo: unia okna, próg 0.75 — ZALICZONE

- Porównanie transkryptu idzie przeciw **UNII** tokenów wszystkiego, co
  wypowiedziane w oknie (`jarvis/voice/anti_echo.py:76,80` — akumulacja
  `union |= spoken_tokens`, decyzja na `len(tokens & union)/len(tokens)`).
  Naprawa per-wiersz→unia z commita `449cada`; testy pokazują pure echo
  1.00 vs realne wtrącenie 0.31.
- Próg `0.75` (`anti_echo.py:34`, config
  `anti_echo_overlap_threshold`, `config.py:131`), okno 30 s
  (`anti_echo.py:33`). Kwalifikują się tylko wiersze
  `speaking|done|cancelled` — nigdy `queued` (tekst jeszcze nie wybrzmiał).
- Znany trade-off (runbook G4 §4): mówienie RÓWNOCZEŚNIE z TTS może dać
  mix głosów sklasyfikowany jako echo — kalibrować progiem, nie
  przeprojektowywać.
- Ryzyko szczątkowe: race transkryptu zebranego tuż przed cancel przy
  barge-inie; okno 30 s je domyka w praktyce (dowód żywy G4 §5).

## 6. Retencja danych głosowych — DO DECYZJI OZZY'EGO

Stan faktyczny:

| Artefakt | Gdzie | Jak długo |
|---|---|---|
| Transkrypty STT (pełny tekst) | `events.payload_json` przy `input.voice.transcribed` (`transcription.py:144`) | bez limitu |
| Teksty TTS (pełny tekst) | `voice_queue.text` (`schema.sql`, wpisy `done/cancelled/failed` zostają) | bez limitu |
| Surowe audio WAV (STT/TTS/playback) | `~/.jarvis/runtime/voice/*.wav`, `0600` | sekundy — `unlink` w `finally` (`stt.py:109`, `tts.py:206,243`) |
| Odrzucone transkrypty (junk) | logi `~/.jarvis/logs/` — `transcription.py:121` loguje pełny tekst | wg rotacji logów |

- Event store redaguje sekrety w payloadach przed INSERT; **logi Pythona
  nie mają redakcji** — junk transcript idzie do pliku logu verbatim.
- Nie istnieje żaden pruning `events` ani `voice_queue` (zero `DELETE`
  w kodzie) — obie tabele rosną bez ograniczeń.
- Model zagrożeń łagodzi to zasadniczo: baza jest lokalna
  (`~/.jarvis/jarvis.db`), single-user, daemon tylko na localhost.

Do decyzji: czy nieograniczona retencja pełnych tekstów (co powiedziałeś
i co Jarvis powiedział) w lokalnej bazie jest akceptowalna, czy wchodzimy
w politykę retencji (wiek/rozmiar) jako drobnicę FAZY H.

## 7. Ryzyka zbiorczo (ranking)

1. **Retencja bez limitu + junk w logach bez redakcji** (§6) — jedyny
   punkt wymagający jawnej decyzji.
2. **Odnawialny hold = praktycznie wieczny open mic** (§1) — zgodne
   z modelem operatora; ewentualny twardy limit łączny to decyzja.
3. **Progi firewalla w configu** (§3) — operator może je zdegradować;
   akceptowalne przy lokalnym modelu władzy.
4. Drobne: lazy expiry lease'ów, brak health-checku recorder-vs-lease,
   martwy typ `LISTENING_LEASE_CANCELLED`, luka degenerate rule dla
   3+ liter — kandydaci na drobnice, nie blokery.

## 8. Checklista werdyktu (wypełnia Ozzy)

- [ ] §1 Mikrofon za lease + TTL — akceptuję stan (w tym lazy expiry
      i odnawialny hold bez limitu łącznego)
- [ ] §2 `source="voice"` tylko wewnętrzną ścieżką za bramkami — akceptuję
- [ ] §3 Firewall halucynacji (energia + junk + degenerate) — akceptuję
      (progi pozostają w gestii operatora przez TOML)
- [ ] §4 Ograniczenie filmu przy otwartym PTT — akceptuję jako
      udokumentowane ograniczenie zakresu
- [ ] §5 Anti-echo unia/0.75/30 s — akceptuję (kalibracja progiem)
- [ ] §6 Retencja: [ ] akceptuję bez zmian / [ ] drobnica FAZY H:
      pruning + redakcja logów
- [ ] **WERDYKT GATE G: ZALICZONY / poprawki (lista):**
