# Handoff: prozodia dobranocki DAN + Danusia

> DOKUMENT HISTORYCZNY (stary stack: voice_broker.py + say.py + speak.sh,
> wycofany przy cutoverze na dand 2026-07-18). Kalibracja artystyczna
> („Teraz jest dobrze :)") pozostaje kanonem; aktywny opis mechanizmu w erze
> dand: docs/PROZODIA.md. Ścieżek i komend runtime z tego pliku nie używać.

## Najważniejsza decyzja Ozzy'ego — finalna kalibracja po trzech odsłuchach

Sekwencja ocen była ważna:

1. Pierwsza wersja techniczna: **„Jest kurwa ideolo!!!!”**.
2. Po dłuższym odsłuchu: **„troche bez emocji gadają i nie w stylu DAN'a chyba”**.
3. Demo v2 z mocniejszą reżyserią: **„Troche nienaturalnie brzmi”** oraz uwaga, że Danusia brzmi gorzej niż wczoraj.
4. Demo v3 po powrocie do naturalnych głosów: **„Teraz lepiej”**, następnie finalne **„Teraz jest dobrze :)”**.

Finalna ocena dotyczy `audycja/dobranocka/stories/2026-07-16-do-oceny/demos/demo-dan-style-natural-v3.playlist.txt`. To jest obowiązujący wzorzec do przepisania siedmiu historii.

## Finalnie zatwierdzony mechanizm naturalności

- Danusia używa dokładnie domyślnego `F4/clean/1.25` z `~/.config/voice/personas.toml`; jej linie nie mają override `speed` ani `profile`.
- Nie licz limitu zmiany tempa pomiędzy różnymi postaciami. Każda persona ma własne naturalne tempo.
- DAN może mieć umiarkowane tempo sceniczne, ale podstawą pozostaje naturalny `M3/raw`.
- Emocję buduje tekst, reakcja, interpunkcja i kompletna myśl; filtr nie może udawać aktorstwa.
- Zwykłe kwestie mają być pojedynczymi pełnymi myślami, zwykle około `180–300` znaków; napięcie może dojść do `250–340`; szybka riposta może mieć `60–140`.
- Broker wysyła Supertonicowi maksymalnie `360` znaków przy serwerowym limicie `400`. Nie przekraczaj około `340`, aby uniknąć wtórnego cięcia kontekstu.
- `gritty` i `krzyk` usunięto z zatwierdzonego demo, bo brzmiały teatralnie.
- `szept` pozostaje tylko jako rzadki kontrast, najlepiej finał DAN-a; Danusia w zatwierdzonym demo nie używa szeptu.
- Styl: własna reakcja i stanowisko DAN-a, osobista relacja z Ozzym, realna kontra Danusi, krótkie naturalne spięcia. Nie narrator dokumentalny z doklejonym przekleństwem.

## Materiał

- Teksty źródłowe do oceny: `audycja/dobranocka/stories/2026-07-16-do-oceny/01-*.txt` do `07-*.txt`.
- Gotowe playlisty: `audycja/dobranocka/stories/2026-07-16-do-oceny/ready-prosody/*.playlist.txt`.
- Audyty prozodii: ten sam katalog, pliki `*.prosody.json`.
- Instrukcja operatorska: `ready-prosody/00-JAK-TO-GRAC.txt`.
- Generator: `tools/dobranocka_prepare.py`.

Nie podawaj feederowi tekstów źródłowych, bo mają nagłówki dla człowieka. Do głosu służą wyłącznie pliki `*.playlist.txt`.

## Format linii

```text
dan;speed=1.10;profile=raw;pause=0.18|Treść wypowiedzi.
danusia;pause=0.18|Treść wypowiedzi bazowym F4/clean/1.25.
dan;speed=0.94;profile=szept;pause=0.68|Finał.
```

Feeder usuwa metadane przed wysłaniem tekstu do TTS. Stary format `dan|Tekst` nadal działa.

## Starsza automatyczna reżyseria — zachować technicznie, nie kopiować artystycznie

- Łuk: `warm → wonder → suspense → relief → tender → sleepy`.
- Start zwykle `1.10–1.14`; formaty żywe mogą dojść do około `1.18`.
- Maksymalny skok tempa pomiędzy sąsiednimi wypowiedziami: `0.07`.
- Finał: `0.92–0.94`.
- Korekta po kolejnym odsłuchu: pauzy były odrobinę za długie. Obowiązuje `0.18 s` zwykła,
  `0.26` pytanie, do `0.32–0.34` napięcie/zmiana formatu, `0.40–0.48` domknięcie i `0.68` finał.
- Brokerowy oddech bez jawnego `pause=` wynosi `0.12 s`.
- DAN: bazowo `M3/raw`; Danusia: bazowo `F4/clean`.
- `gritty` i `krzyk` są dostępne technicznie, ale nie należą do zatwierdzonej naturalnej kalibracji v3.
- `szept` ma `lowpass=4200 Hz`, `loudnorm I=-24`, `TP=-6`; jest celowo przytłumiony i około 10 dB cichszy od normalnej mowy.
- Pauza sceniczna jest dokładana dopiero po ostatnim wewnętrznym fragmencie wypowiedzi, nie po każdym chunku.

## Aktywne elementy toru

- `~/.agents/skills/dobranocka/feeder.sh` parsuje `speed`, `profile`, `pause` i ma bezgłośny tryb `--parse-line`.
- `~/.claude/skills/gadanie/speak.sh` przekazuje `--speed`, `--profile` i `DAN_GAP`.
- `dan_core/say.py` zapisuje `pause_after` w request JSON.
- `tools/jarvis/voice_broker.py` przenosi `pause_after` przez prefetch i stosuje ją wyłącznie do ostatniego chunku.
- Jedyny właściciel dźwięku: broker. Nigdy nie odpalaj równoległego `afplay`, `say` ani drugiego brokera.

## Start i diagnostyka

Przed startem zawsze sprawdź aktualny runtime; poniższy stan nie jest trwały:

```bash
pgrep -af 'voice_broker.py|dobranocka/feeder.sh'
cat /tmp/dan-feeder.lock 2>/dev/null
jq '{speaking,queue:(.queue|length),ts}' /tmp/dan-voice/state.json
ls -l /tmp/dan-voice/PAUSE /tmp/dan-voice/STOP /tmp/dan-voice/FLUSH 2>/dev/null
```

W tej sesji broker żył, feeder działał, a mimo tego była cisza, ponieważ istniał `/tmp/dan-voice/PAUSE`. Usunięcie wyłącznie tej flagi wznowiło zdejmowanie requestów. Pełna kolejka nie jest dowodem słyszalnego audio; wymagane jest `speaking != null` oraz realny odsłuch.

Nie ruszaj głośności systemowej. Nie uruchamiaj drugiego feedera. Lock weryfikuj przez `kill -0`, nie samą obecność pliku.

## Weryfikacja bez audio

```bash
cd /Users/n1_ozzy/Documents/dev/dan
PYTHONPATH=. .venv/bin/pytest -q \
  tests/test_dobranocka_prosody.py \
  tests/test_dobranocka_prepare.py \
  tests/test_shared_voices.py
bash -n /Users/n1_ozzy/.agents/skills/dobranocka/feeder.sh
.venv/bin/python -m py_compile \
  tools/dobranocka_prepare.py dan_core/say.py tools/jarvis/voice_broker.py
git diff --check
```

Ostatni wynik zakresowy: `11 passed`. Sprawdzono wszystkie 324 wypowiedzi, zgodność tekstu jeden do jednego, format metadanych, limit skoku `0.07`, finały oraz parser feedera.

Pełny test suite repo miał 206 testów zielonych i 5 wcześniejszych czerwonych niezwiązanych z prozodią: Persona Doctor/Jarvis, dwa stare testy normalizacji `say.py` i dwa testy Voice Lab.

## Stan runtime zapisany 2026-07-16 02:08 CEST

Po korekcie Ozzy'ego utworzono `/tmp/dan-voice/PAUSE`, aby broker nie zdejmował kolejnych kwestii. Feeder PID `72900` i kolejka nadal istniały. To tylko migawka; kolejna sesja ma sprawdzić stan od nowa i nie ufać numerowi PID.

## Co konkretnie nie zadziałało artystycznie

- Długie wypowiedzi brzmiały jak formalny narrator dokumentalny; zmiana profilu audio nie zastąpiła emocji.
- DAN miejscami nie miał własnej reakcji i stanowiska, tylko relacjonował fabułę z doklejonym przekleństwem.
- Danusia zbyt często była drugim narratorem zamiast realną kontrą, prowokatorką lub osobnym punktem widzenia.
- Potrzebne są krótsze, bardziej mówione frazy, gwałtowniejsze zmiany energii w scenach oraz prawdziwe spięcia prowadzących.
- Nie stosować automatycznego postprocessora „stylu DAN”. Źródłowy tekst ma być napisany poprawnie według `config/persona/DAN.md`.
