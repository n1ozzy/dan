# Inwentaryzacja narzędzi voice tracku — żywa weryfikacja (2026-07-02)

Zakres: audyt read-only narzędzi TTS/STT/audio zainstalowanych na tym Macu
pod FAZĘ G (G3-engine, G4-STT, G5-clone). Zero instalacji, zero zmian w
systemie, zero kodu runtime. Testy syntez/transkrypcji wykonane wyłącznie do
plików tymczasowych w scratchpadzie sesji (posprzątane); nic nie grało przez
głośniki. Preflight: HEAD `193157b`, working tree czyste.

Metoda żywej weryfikacji języka polskiego: **zamknięta pętla TTS→STT** —
polskie zdanie zsyntezowane danym silnikiem, przetranskrybowane przez
mlx-whisper (large-v3-turbo), porównane słowo w słowo.

---

## 1. Tabela zbiorcza

| Narzędzie | Jest? | Wersja | Skąd | Polski | Gotowe do G3–G5? |
|---|---|---|---|---|---|
| Supertonic TTS | TAK | 1.3.1 (PyPI `supertonic`) | pip, w `dan/.venv` | **TAK — zweryfikowany żywo, transkrypcja 100 % zgodna** | TAK, po czystej instalacji w venv jarvisa (model już w cache) |
| Chatterbox (MLX) | TAK | mlx-audio 0.4.4 + model MLX fp16 | pip, w `dan/.venv` i `xtts-venv` | **TAK — zweryfikowany żywo** (1 zbitka spółgłoskowa przekręcona w teście, patrz §3) | TAK, po czystej instalacji; klon wymaga oceny jakości uchem |
| mlx-whisper | TAK | 0.4.3 | pip, w `dan/.venv` | **TAK — perfekcyjna transkrypcja PL z diakrytykami** | TAK, po czystej instalacji; G4 MUSI mieć filtry halucynacji (potwierdzone żywo, §4) |
| sox | TAK | 14.4.2_6 | brew | n/d | TAK — coreaudio wkompilowane, `play`/`rec`/`soxi` obecne |
| edgeTTS (ZAKAZANY) | TAK — siedzi | 7.2.8 | pip, w `dan/.venv` | — | NIE DOTYCZY — tylko odnotowane (ADR-013) |
| piper (ZAKAZANY) | **NIE** | — | — | — | brak śladów na maszynie ✓ |
| XTTS (ZAKAZANY) | TAK — siedzi | coqui-tts 0.27.5 | pip, w `xtts-venv` + model 1,7 GB | — | NIE DOTYCZY — tylko odnotowane (ADR-013) |

Wielkie zastrzeżenie wspólne: **wszystkie działające narzędzia pip siedzą w
venvach legacy DAN-a** (`~/Documents/dev/dan/.venv`, Python 3.14.6), nie w
repo jarvis. Dekret §7.6 (zero zależności od DAN-a) ⇒ G3/G5 wymaga czystej
instalacji tych samych pakietów w `jarvis/.venv` (też Python 3.14.6 — zgodność
1:1). Modele w `~/.cache` są per-user i współdzielone — czysta instalacja
**nie pobiera nic ponownie**.

---

## 2. Supertonic TTS — szczegóły

- **Pakiet:** `supertonic` 1.3.1, PyPI, home: github.com/supertone-inc/supertonic.
  Zainstalowany w `dan/.venv` (Python 3.14.6). Binarka: `dan/.venv/bin/supertonic`.
- **Model:** supertonic-3, ONNX, `~/.cache/supertonic3` (385 MB), 44,1 kHz,
  31 języków (w tym `--lang pl`), 10 głosów wbudowanych: F1–F5, M1–M5.
- **CLI:** `tts` (do pliku), `say` (przez głośnik — dla nas zakazane poza
  brokerem), `serve` (**lokalny HTTP `/v1/tts` + OpenAI-compatible
  `/v1/audio/speech`** — istotna opcja dla G3: model trzymany na ciepło
  zamiast zimnego startu procesu na każde zdanie).
- **Żywy test PL:** zdanie 15-wyrazowe z pełnymi diakrytykami („…żółta gęś
  jaśnieje w słońcu") → WAV 9,07 s wygenerowany w **3,44 s** (RTF ≈ 0,38,
  zimny start procesu; 44,1 kHz / 16-bit / mono). Transkrypcja mlx-whisperem:
  **zgodność 100 %, słowo w słowo, z diakrytykami**.
- **Kontekst legacy (fakt, nie wzór):** DAN wołał `supertonic tts … --lang pl
  --steps 14 --speed 1.35`, timeout 120 s; głos M1 był wyborem Ozzy'ego po
  odsłuchu M1–M5. Cudzysłowy drukarskie („ ") wywracały syntezę — sanityzacja
  interpunkcji będzie potrzebna także u nas (clean-room, w chunkerze/engine).

## 3. Chatterbox — szczegóły

- **Port MLX: TAK** — nie PyTorch. Pakiet `mlx-audio` 0.4.4
  (github.com/Blaizzy/mlx-audio), zainstalowany w `dan/.venv` ORAZ w
  `dan/tools/jarvis/xtts-venv`. Inferencja: `python -m mlx_audio.tts.generate`.
- **Modele w `~/.cache/huggingface/hub`:**
  - `litmudoc/Chatterbox-Multilingual-MLX-v2-fp16` — **2,4 GB, używany tor MLX**;
  - `ResembleAI/chatterbox` — 11 GB, oryginał PyTorch (nieużywany przez tor
    MLX; kandydat do decyzji o zwolnieniu 11 GB — decyzja Ozzy'ego);
  - `mlx-community/S3TokenizerV2` — 472 MB (tokenizer audio dla chatterboksa).
- **Polski: TAK** — model multilingual, `--lang_code pl`.
- **Żywy test PL (klon głosu):** zdanie z ref_audio `piotr-nater-grabinski.wav`
  → WAV 5,56 s (24 kHz/16-bit/mono) w ~19 s **zimnego startu** (ładowanie
  2,4 GB modelu wliczone). Legacy raportował ≈ real-time na ciepłym modelu
  (~4 s syntezy na ~4 s mowy) — spójne z tym, co widać po odjęciu ładowania.
  Transkrypcja: 9/10 słów idealnie; „klonowanie" wyszło jako „planowanie"
  (zbitka kl-). Czy to wada wymowy silnika czy słuch whispera — **nie
  ustalono**; do oceny uchem Ozzy'ego w G5.
- **Sklonowane głosy na dysku** (`dan/tools/jarvis/chatterbox/refs/`):
  - `piotr-nater-grabinski.wav` (1,0 MB) — wzór ludzki;
  - `wzor-meski-supertonic-M2.wav` (0,6 MB) — klon syntetyka (wg notatki
    legacy: klon z TTS brzmi „blaszanie" — wzór ludzki >> wzór syntetyczny);
  - `wczorajsza-probka.wav` (0,4 MB).
- Bez `ref_audio` model mówi natywnym głosem (wysokim) — do męskiego Jarvisa
  wzór jest obowiązkowy.

## 4. mlx-whisper — szczegóły

- **Pakiet:** `mlx-whisper` 0.4.3 (ml-explore/mlx-examples), w `dan/.venv`;
  binarka `dan/.venv/bin/mlx_whisper`.
- **Modele pobrane (HF cache):** `mlx-community/whisper-large-v3-turbo`
  (1,5 GB — główny) oraz `openai/whisper-base` (281 MB).
- **Żywy test PL:** transkrypcja 9-sekundowego WAV (44,1 kHz przyjęty wprost)
  w ~5,5 s wallclock z zimnym startem — **wynik idealny, pełne diakrytyki**.
- **Fakt §4a „whisper halucynuje na ciszy" — POTWIERDZONY ŻYWO:** 3 s czystej
  cyfrowej ciszy (sox, 16 kHz mono) → transkrypcja `„Dziękuję."` mimo
  domyślnego `no_speech_threshold=0.6`. Wniosek dla G4: własne filtry śmieci
  (energia/VAD przed whisperem + odrzucanie znanych fraz-halucynacji) są
  obowiązkowe, nie opcjonalne.
- Obok żyje też **whisper-cpp 1.9.1 (brew)** z modelem
  `~/.cache/whisper/ggml-large-v3-turbo-q5_0.bin` (574 MB) — nie jest torem
  dekretowym (§7.4: MLX whisper), odnotowany dla kompletności.

## 5. sox — szczegóły

- **Wersja:** 14.4.2_6 z brew (`~/.homebrew/bin/sox`), bottled.
- **coreaudio:** `AUDIO DEVICE DRIVERS: coreaudio` — wkompilowane; `sox -d`
  ma przez co gadać z urządzeniami (nie nagrywano — zgodnie z zakresem).
- Binarki towarzyszące obecne: `play`, `rec`, `soxi`. Uwaga brew: konflikt
  z formułą `sox_ng` (nieobecną) — bez znaczenia praktycznego.
- **Fakt §4a „gain PRZED silence, highpass 80 Hz":** nie do skonfrontowania
  bez nagrywania z mikrofonu (poza zakresem read-only) — pozostaje jako fakt
  empiryczny legacy do zweryfikowania w G4 przy pierwszych nagraniach.

## 6. Skan ZAKAZANYCH silników (edgeTTS / piper / XTTS)

Zgodnie z ADR-013 wyłącznie odnotowane — **niczego nie odinstalowano**:

| Co | Gdzie | Rozmiar |
|---|---|---|
| edge-tts 7.2.8 + binarki `edge-tts`, `edge-playback` | `dan/.venv` | mały (pip) |
| coqui-tts 0.27.5 (XTTS) + `tts`, `tts-server` | `dan/tools/jarvis/xtts-venv` (1,4 GB venv) | 1,4 GB |
| model XTTS v2 | `~/Library/Application Support/tts/tts_models--multilingual--multi-dataset--xtts_v2` | 1,7 GB |
| serwer XTTS (kod + plist) | `dan/tools/jarvis/xtts_server.py`, `com.dan.xtts-server.plist` (w repo dan, NIE w LaunchAgents) | — |
| kopie powyższego | `dan.backup.20260630-211343`, `dan.backup.20260630-211427` | duplikaty xtts-venv |
| **piper** | **BRAK śladów na maszynie** | — |

Sąsiedztwo (niezakazane, odnotowane): `espeak` 1.48.04_1 (brew),
`whisper-cpp` 1.9.1 (brew, §4 wyżej).

**Stan legacy runtime (warunek wejścia FAZY G):** żaden proces legacy nie
żyje (`voice_broker.py`, `auto_jarvis.py`, `listen_ozzy.py`, `xtts_server.py`
— brak w ps; brak `com.dan.*` w `launchctl list`). Natomiast
`com.dan.voice-broker.plist` **wciąż leży w `~/Library/LaunchAgents`**
(niezaładowany). Warstwa procesów: wygaszona; sprzątnięcie plista — decyzja
Ozzy'ego (komendy w `~/Desktop/JARVIS-NEXT-STEPS-FOR-OZZY.md` §5).

## 7. Braki i komendy instalacyjne (do wykonania PO dekrecie)

Na dysku jest wszystko — web research nie był potrzebny. Jedyny „brak" to
lokalizacja: pakiety żyją w venv legacy DAN-a, a §7.6 zakazuje jakiejkolwiek
zależności od DAN-a. Czysta instalacja w repo jarvis (`.venv`, Python 3.14.6
— identyczny jak u DAN-a, więc wersje przechodzą 1:1):

```bash
# TTS pierwszego silnika (G3): ~10 MB pip; model już w ~/.cache/supertonic3
/Users/n1_ozzy/Documents/dev/jarvis/.venv/bin/pip install supertonic==1.3.1

# STT (G4): model już w HF cache (mlx-community/whisper-large-v3-turbo)
/Users/n1_ozzy/Documents/dev/jarvis/.venv/bin/pip install mlx-whisper==0.4.3

# Voice-clone (G5): model już w HF cache (litmudoc/...-MLX-v2-fp16)
/Users/n1_ozzy/Documents/dev/jarvis/.venv/bin/pip install mlx-audio==0.4.4
```

Zero dodatkowych pobrań modeli (cache per-user). sox już jest z brew — nic
do instalowania. Wzory głosu z `dan/tools/jarvis/chatterbox/refs/` to dane
(WAV), nie kod — ich ewentualne skopiowanie do jarvisa wymaga osobnego
dekretu (formalnie pochodzą z drzewa DAN-a).

## 8. Konfrontacja faktów §4a — podsumowanie

| Fakt §4a | Wynik konfrontacji |
|---|---|
| Whisper halucynuje na ciszy | **POTWIERDZONY ŻYWO** — „Dziękuję." na 3 s czystej ciszy, mimo no_speech_threshold=0.6 |
| MLX trzyma model+stream per wątek | nie skonfrontowano (wymaga testu wielowątkowego z kodem — zakres G5); bez sprzeczności z obserwacjami |
| sox: gain przed silence, highpass 80 Hz | nie skonfrontowano (wymaga nagrywania z mikrofonu — poza zakresem read-only); do weryfikacji w G4 |

## 9. Rekomendacja kolejności: Supertonic-engine PRZED G4-STT

Rekomenduję **najpierw realny SupertonicEngine w brokerze (G3+), potem G4**:

1. **Najmniejszy krok od stanu obecnego.** Broker, kolejka, chunker i fillers
   już istnieją (G3, mock engine). Realny engine to jedna klasa za istniejącym
   interfejsem — wszystkie ryzyka narzędzia zdjęte dzisiejszym żywym testem
   (polski ✓, RTF 0,38 ✓, format 44,1 kHz/16-bit ✓).
2. **G4 potrzebuje mówiącego TTS do testów anty-echa.** Mechanizm „echo
   własnego TTS nie staje się turnem" (wymaganie §4a) da się uczciwie
   przetestować tylko przeciw realnemu głosowi z głośnika — mock nie wytwarza
   echa. Odwrotna kolejność wymusiłaby powrót do G4 po wpięciu silnika.
3. **G4 jest większy niż się wydaje.** Żywy test potwierdził halucynacje
   whispera na ciszy ⇒ G4 = nagrywanie (sox) + transkrypcja + własne filtry
   śmieci + leases + barge-in. Zaczynanie od większego i bardziej ryzykownego
   etapu, gdy mniejszy jest w pełni odblokowany, nie ma uzasadnienia.
4. Decyzja o `supertonic serve` (model na ciepło, HTTP `/v1/tts`) vs proces
   CLI per zdanie (prostszy, 3,4 s na zdanie) — do projektu engine'a w G3+;
   obie ścieżki są dostępne w zainstalowanej wersji.

Do dekretu Ozzy'ego: (a) kolejność jak wyżej, (b) czysta instalacja z §7,
(c) los 11 GB oryginału PyTorch ResembleAI/chatterbox i plista
`com.dan.voice-broker.plist`, (d) ewentualne przeniesienie wzorów głosu z
refs/ do jarvisa.
