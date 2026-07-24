# Research: naturalna prozodia i mastering TTS (2026-07-10)

> Dopisek 2026-07-11 (info od Ozzy'ego, speechgen.io/pl/node/custom-pausa):
> pauzy SSML `<break time="200ms"/>` — zakres 150 ms–5 s, default 300 ms między
> zdaniami / 400 ms między akapitami. **ElevenLabs wspiera `<break time="1.5s"/>`
> w tekście** — używać przy dobranocce/scenach na eleven (precyzyjne pauzy
> dramatyczne zamiast wielokropków). Supertonic SSML NIE ma — u nas pauzy
> robi interpunkcja + silence_duration + montaż feedera.
>
> Dopisek 2 (2026-07-11, eleven best-practices, od Ozzy'ego): **eleven_v3 ma
> AUDIO TAGI**: `[whispers]` `[laughs]` `[sighs]` `[sarcastic]` `[excited]`
> `[crying]` `[mischievously]` + efekty (`[applause]`, `[gunshot]`) — czyli
> specjalne tryby ekspresji niedostępne w Supertonicu. V3 NIE wspiera
> `<break>` (pauzy: wielokropek,
> myślniki, interpunkcja; WIELKIE LITERY = emfaza); `<break time>` tylko v2
> (max 3 s). V3 stability: Creative (ekspresja, ryzyko halucynacji) / Natural /
> Robust (jak v2). Emocje też przez kontekst narracyjny w zdaniu. IPA w v3 dla
> 70+ języków. Wymowa liczb: nasz pl_norm.py przed wysyłką. Koszt: kredyty
> per znak — tagi liczą się do znaków, używać w krótkich scenkach.

> Raport agenta researchowego na zlecenie Ozzy'ego („tempo dynamiczne, mastering, intonacja").
> Stan wdrożenia na dole. Stack: supertonic (ONNX, brak natywnych emocji) + FFmpeg mastering
> w `tools/jarvis/voice_broker.py` + chatterbox MLX (klon, MA emocje) + eleven (premium, kwota).

## 1. Dynamiczne tempo mowy
- Baseline narracji ~155 słów/min; naturalne wahania **±15% wokół bazy** (więcej = efekt).
- Złość/ekscytacja = szybciej, wyżej, głośniej, krótsze pauzy; smutek/spokój = odwrotnie.
- Azure SSML w praktyce: rate -20%…+25%. ElevenLabs speed 0.7–1.2.
- **Final lengthening**: ludzie wydłużają ostatnią sylabę/słowo frazy (7–18%) — tanie „ludzkie
  domknięcie": ostatnie ~0.5 s zdania przez atempo 0.85–0.92.

## 2. Intonacja / emfaza w post-processingu
- Kontur pytania = wzrost F0 na końcu (PL: 2–4 półtony na ostatnim słowie); twierdzenie = opadanie.
- **asetrate do pitch-bendu ODPADA** (formanty jadą, chipmunk). Drogi sensowne:
  1. **parselmouth/Praat (PSOLA)** — jedyny prawdziwy kontur: pitch tier, podbicie punktów
     w ostatnich 300–500 ms o 1.1–1.25×, resynteza overlap-add. pip-instalowalny, działa na ARM.
  2. **rubberband w ffmpeg** (`formant=preserved`) na potiętym ogonie + `acrossfade=d=0.02` — kompromis.
- Wykrzyknienie: całość +1–2 półtony, ogon lekko w dół, +2–3 dB, mocniejsza kompresja.

## 3. Emocje przez DSP
- Wiarygodna agresja: highpass 120, cut 500–800 Hz, boost 2–3 kHz, kompresja ratio 8–20,
  +3–4 dB głośniej, saturacja tylko śladowo (aexciter). Distortion = tandeta.
- **Ekstremalna bezdźwięczna artykulacja z DSP jest fizycznie niemożliwa.**
  Ściszenie i korekcja pasma tworzą tylko cichy głos, nie wiarygodny osobny sposób mówienia.
- Spokój: pitch −1–2 półtony (rubberband), tempo −10%, łagodna kompresja, +2 dB @ 100–200 Hz.
- Anty-wzorce: crystalizer/aexciter na maksa, ratio 20 na całość, stałe wartości bez wariancji.

## 4. Pauzy (liczby)
- Przecinek 200–350 ms · myślnik 350–500 ms · kropka 600–800 ms · wielokropek 800–1200 ms ·
  zmiana tematu 1–2 s.
- **Dialog dwóch mówców: mediana gapu 110–130 ms, moda ~200 ms.** Powyżej pół sekundy =
  czytanka, nie kłótnia. Cel dla dobranocki: **150–300 ms** między turami; ~700 ms tylko jako
  świadoma dramatyczna pauza. Wariancja ±25% OBOWIĄZKOWA.

## 5. Tekst sterowany (bez SSML) — działa w modelach dyfuzyjnych
- Wielokropek `…` = pauza + wahanie (najpewniejszy trik). Myślnik = urwanie/wtrącenie.
- Cięcie długich zdań na krótkie = najskuteczniejsza kontrola konturu. Krótkie zdania. Właśnie tak.
- CAPS = emfaza, max 1–2 słowa na zdanie (nadużycie = szum).
- `?` honorowany konturem; `!` daje energię słabiej. Powtórzenia liter („noooo") — testować.
- Kontekst narracyjny („wycedził") działa tylko w LLM-TTS (eleven v3), NIE w supertonicu.

## 6. Chatterbox: emocje parą (exaggeration, cfg_weight)
- Default 0.5/0.5. **Silna ekspresja: exaggeration ≥0.7 + cfg 0.3** (wyższa egzageracja
  przyspiesza mowę — niższy cfg to kompensuje; stroić PARĄ). Spokój: 0.2–0.4 / 0.5.
- Multilingual: ref_audio w innym języku niż target → cfg 0 (inaczej przenosi akcent).

## Mastering — wspólny mianownik
- Target mowy: **-16 LUFS integrated, TP -1 dBTP** (Apple Podcasts), highpass 80–120 Hz,
  klarowność 2–4 kHz, lekka kompresja, limiter. Loudnorm per-zdanie z jednym targetem —
  bez wspólnego targetu głośność skacze między zdaniami.

> AKTUALIZACJA (2026-07-10 wieczór): wdrożone dodatkowo — skalibrowany stały gain per głos×profil
> zamiast loudnorm-AGC per klip + mikro-fade'y brzegów + anty-drop guard + normalizacja liczb
> (`tools/jarvis/pl_norm.py`). Szczegóły i mapa parametrów silnika: docs/RESEARCH-SUPERTONIC.md.
> Bateria próbek (głosy/blendy/klony Natera/efekty/tagi) czeka na werdykt Ozzy'ego.
>
> AKTUALIZACJA 2 (2026-07-10 noc): baterie 2-4 w scratchpadzie sesji (gen_battery2/3/4.py,
> gen_clones2/3.py, play_probki3.sh) — WSZYSTKIE próbki przechodzą whisper-gate (transkrypcja
> MLX whisper + SequenceMatcher ≥0.85 vs tekst źródłowy, do 3 prób, zostaje najlepsza; lekcja:
> bateria 1 wypuściła zlewki klonów „klądziała" bez kontroli). Bateria 4 testuje techniki z tego
> raportu POJEDYNCZO na parach A/B (baseline vs technika): pauzy semantyczne, pauza dramatyczna,
> final lengthening, kontur pytania (micro-bend ogona 0.4 s ±3/6%), emfaza słowa (whisper
> word-timestamps + volume envelope) vs CAPS, tempo dynamiczne per zdanie, montaż radiowy
> (panorama ±20%, sox reverb, room-tone brown-noise) vs suchy. Werdykt ucha rozstrzyga wdrożenia.

## Mastering pod DAMSKIE głosy (research 2026-07-10 — do wdrożenia po werdyktach)
Nasze MASTER_PROFILES są strojone pod męski głos (M3). Damski głos (f0 ~165–255 Hz vs męskie
~85–155 Hz) wymaga przesunięcia całego łańcucha w górę:
- **highpass 120–150 Hz** (nie 70–90) — pod 150 Hz u kobiety jest tylko rumble; `highpass=f=120:p=2`.
- **Ciepło/body: +1.5–3 dB @ 200–400 Hz** (u mężczyzny to pasmo się raczej tnie); `equalizer=f=280:t=q:w=1.2:g=2`.
- **Mud: −2…−4 dB @ 400–600 Hz** (męskie muddy to 250–350); `equalizer=f=500:t=q:w=1.4:g=-3`.
- **Presence: +2–3 dB @ 3–5 kHz**; `equalizer=f=3500:t=q:w=1:g=2.5`.
- **De-esser wyżej: kobiece sybilanty 7–8 kHz** (męskie 5–6); ffmpeg `deesser` jest szerokopasmowy —
  dołożyć dynamiczne cięcie ~7.5 kHz. Air shelf +1–3 dB od 10 kHz ZAWSZE po de-esserze.
- Kompresja wspólna: `acompressor=threshold=-18dB:ratio=3.5:attack=5:release=50:makeup=4`.
- Kolejność: cięcia EQ → kompresor → boosty EQ → de-esser → air shelf → gain/limiter.
Pitch-shift (PSOLA/parselmouth): globalnie bezpieczne ±1–2 półtony; kontur lokalny (końcówka
pytania ×1.15–1.25 f0) może więcej, bo trwa krótko.
Plan wdrożenia: zwycięski damski głos z werdyktów → Ozzy stroi w laboratorium (:7800) →
damskie warianty profili w MASTER_PROFILES (`voice_broker.py`) wybierane po prefiksie głosu F.

## Broadcast/miks — research #2 (2026-07-10 wieczór, nowości ponad to co wdrożone)
- **`dialoguenhance`** (FFmpeg ≥6) — natywny filtr klarowności dialogu; przetestować na miksie audycji.
- **resemble-enhance** (pip) — jedyny enhancer, który DODAJE jakość TTS (bandwidth extension),
  ale na M-serii minuty/minutę audio → tylko OFFLINE pre-render (dobranocka), nie live.
  Enhancery ogólnie: trenowane na zdegradowanej mowie ludzkiej, na czystym TTS mały zysk.
- **DeepFilterNet3** (pip, CPU real-time) — bez sensu na TTS, ALE idealny do odszumienia
  przyszłej próbki mikrofonowej Ozzy'ego pod klon głosu.
- Compand „radiowa gęstość": `compand=attacks=0:points=-80/-900|-45/-15|-27/-9|0/-7|20/-7:gain=5`.
- Jingle/bumpery normalizować do TEGO SAMEGO -16 LUFS co mowa; głośniejszy jingiel = amatorka.
- Przejścia mówca→mówca: GAP 150–300 ms, NIE crossfade (crossfade tylko na room-tone);
  room-tone -12…-18 dB pod całością; panorama radiowa ±10–20% (nie hard L/R); ducking muzyki
  `sidechaincompress=threshold=0.03:ratio=8:attack=20:release=300` — pokrywa się z planem
  radio-sznytu z RESEARCH-SUPERTONIC.md pkt 3.
- mcompand: brak sprawdzonych presetów do mowy — strojenie na ślepo to grabie; acompressor
  + nasz stały gain robi 90% efektu.

## Ranking wg (efekt)/(koszt) i stan wdrożenia (2026-07-10)
1. **Pauzy/gap dialogowy 150–300 ms** — ✅ CZĘŚCIOWO: trim brzegów klipów w `_master_phrase`
   (silenceremove -55dB PO loudnorm, keep 0.12 s) + `apad=_PAUSE_AFTER`. UWAGA GRABIE:
   pierwsza wersja (-45dB PRZED masteringiem) zjadała nasady/wybrzmienia słów → „proteza".
2. **Tempo per zdanie** — ✅ w `dobranocka/feeder.sh`: mnożnik od interpunkcji/długości
   (≥2× `!` → +8%, `?` → −4%, >220 zn → +5%, <45 zn → −5%) + wariancja ±3%, baza z personas.toml.
3. **Interpunkcyjny pre-procesor treści** — ✅ ręcznie w pisanych partiach (wielokropki przed
   puentą, krótkie zdania, CAPS 1 słowo); automat TODO.
4. **Chatterbox presety emocji** — TODO (jedyny silnik z prawdziwą emocją; tabelka par w configu).
5. **Parselmouth kontur pytania/wykrzyknienia** — TODO (nowa zależność; asetrate NIE).

Odrzucone po odsłuchu Ozzy'ego: profile DSP skrajnej ekspresji („proteza").
Nie wpinać ich do audycji ani nie traktować jako zaakceptowanego kierunku.

## 2026-07-12 — determinizm supertonica ZBADANY + seed-wrapper
Pytanie kumpla Ozzy'ego: per-zdanie czy całość do TTS? → **Broker pakuje zdania w porcje
≤300 znaków (2-4 zdania na jedno wywołanie)** — `split_sentences` + `_mc=300` w voice_broker.py.

**Test powtarzalności (10× ten sam tekst, M3/1.25/18 steps):** 10/10 RÓŻNYCH hashy przy
IDENTYCZNEJ długości pliku → losowość samplingu (startowy szum dyfuzji:
`np.random.randn` w `supertonic/core.py:sample_noisy_latent`), rytm/czas deterministyczny.
Stąd „raz brzmi jak człowiek, raz średnio" — loteria szumu, nie tekstu.

**Fix:** `tools/jarvis/supertonic_seeded.py` — wrapper CLI seedujący np.random PRZED importem
(`SUPERTONIC_SEED=42 supertonic_seeded.py tts ...`). Zweryfikowane: 3× ten sam seed = 3×
identyczny hash (bit-perfect). Bez zmiennej = oryginalna losowość. NIE podpięte do brokera.

**Odblokowane eksperymenty:** arkusz naturalności (zamrożony szum → mierzysz tylko wpływ
TEKSTU), golden-seed per typ linii, benchmark długości 1/2/4/8 zdań, humanizer rytmu.
**Werdykt A/B zapisu (2026-07-12, ucho Ozzy'ego):** wielokropki-zawieszenia + krótkie oddechy
+ `!` na kulminacji WYGRYWAJĄ miażdżąco („To jest to kurwa... Mamy to"). Reżyseria B = kanon
zapisu audycji (szczegóły: skill dobranocka, sekcja 🎛️).
