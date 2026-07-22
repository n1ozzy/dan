# Prozodia — reżyseria wypowiedzi w erze dand

Aktywny opis mechanizmu. Kalibracja artystyczna pochodzi z trzech odsłuchów
Ozzy'ego 2026-07-16 (finalne „Teraz jest dobrze :)") — zapis decyzji:
`docs/HANDOFF-PROSODY-2026-07-16.md` (historyczny, stary stack). Research i
ślepe uliczki: `docs/RESEARCH-PROZODIA-TTS.md`. Reżyseria NA ŻYWO, bez
przygotowanych playlist: `docs/PROZODIA-LIVE.md`.

## Zasada naczelna

Emocję buduje TEKST: treść, reakcja, składnia, rytm i interpunkcja.
Supertonic syntezuje CAŁE wypowiedzi (whole-utterance), więc naturalny kontur
intonacji robi model — sztuczny per-zdaniowy bend pitchu przez ffmpeg został
wyłączony 2026-07-08 i odrzucony ponownie 2026-07-22 („modulowane w różnych
kierunkach naraz"). Nie wskrzeszać silnika per-fraza (`dan_voice.py` z backupu).

Reżyseria = trzy pokrętła per wypowiedź, wszystkie opcjonalne:

- `speed` — tempo absolutne supertonica, zakres 0.8–1.6 (bez podania: tempo
  persony z `config/voice/personas.toml`),
- `profile` — profil masteringu barwy (bez podania: mastering persony),
- `pause` — pauza sceniczna PO wypowiedzi, 0.0–2.0 s (bez podania: oddech
  brokera 0.12 s).

## Tor techniczny

> STAN 2026-07-22: tor jest właśnie wdrażany na żywo (sesja równoległa,
> strojona uchem Ozzy'ego): w kodzie jest już `--tempo` (mnożnik tempa
> persony 0.6–1.4 w intencie) oraz oddechy WEWNĄTRZ wypowiedzi (grupy zdań
> ≤140 znaków, pauzy wg interpunkcji `.` 0.40 / `!` 0.30 / `?` 0.45 /
> `…` 0.55, trim brzegów + fade szwów). Aktualne flagi: `dan speak --help`;
> nowe działają po restarcie dand. Opis niżej to warstwa SCENICZNA
> (pauza po wypowiedzi, profil, dynamika) — pokrętła docelowe, częściowo
> jeszcze nie w kodzie; przed użyciem sprawdź --help.

1. Producent (CLI/API/feeder) podaje reżyserię w intencie:
   `dan speak --json --as dan --session s --source claude --speed 1.08 --profile raw --pause 0.32 --stdin`.
   API: pola `speed`, `profile`, `pause` w payloadzie `/voice/speak`.
2. `VoiceResolver.resolve()` merguje reżyserię z trasą persony i zamraża wynik
   w `RenderSnapshot` (`speed`, `mastering_profile`, `pause_after`). Walidacja
   fail-closed: zły zakres/nieznany profil = odmowa przyjęcia, nie cichy default.
3. `SupertonicEngine.synthesize()` po masteringu dokleja wykończenie:
   przycięcie ciszy wejściowej, mikro-fade'y deklikujące, dynamikę zdaniową
   z interpunkcji (`!` = +2 dB z limiterem bezpieczeństwa za boostem —
   lekcja „trzeszczy po rusku" 2026-07-18; `…` = −1.5 dB, po loudnormie)
   i pauzę `apad=pad_dur=<pause_after>`. Pauza siedzi w WAV-ie — player
   i watchdog liczą deadline z realnej długości, nic nie trzeba
   synchronizować.

## Profile masteringu

`raw` (DAN/Jarvis — barwa nietknięta + loudnorm tail), `clean` (Danusia),
`raport`, `gritty` oraz protezy emocji `krzyk` i `szept` (v2 z 2026-07-18,
odkręcone trzaski). `krzyk`/`szept` mają WŁASNY loudnorm (−12.5 / −24 LUFS —
kontrast poziomów robi połowę wrażenia), więc nie dostają wspólnego taila.
`bastard` wycięty 2026-07-10 (przester 2.6/10) — nie przywracać.

## Standard pisania (kanon odsłuchowy 2026-07-16)

- Jedna linia = jedna kompletna myśl; zwykle 180–300 znaków, napięcie do 340,
  riposta 60–140. Rytm WEWNĄTRZ wypowiedzi prowadzi interpunkcja, nie cięcie
  na osobne requesty.
- Drabinka pauz: 0.18 zwykła · 0.26 pytanie · 0.32–0.34 napięcie/zmiana
  formatu · 0.40–0.48 domknięcie · 0.68 finał. Starego finału 0.90 nie używać.
- Danusia: zawsze bazowe F4/clean/tempo z personas.toml — jej linie NIE mają
  override `speed` ani `profile` (co najwyżej `pause`).
- DAN: baza M3/raw; umiarkowane tempo sceniczne, nie zmieniać co linię dla
  ozdoby. `gritty` i `krzyk` nie są domyślną emocją (brzmiały teatralnie).
  `szept` najwyżej raz, najlepiej w finale DANa.
- Nie porównywać tempa różnych person między sobą — każda ma własną bazę.
- Maks. skok tempa między sąsiednimi wypowiedziami tej samej persony: 0.07.

Format playlisty (feeder dobranocki, wstecznie zgodny):

```text
dan;speed=1.12;profile=raw;pause=0.18|Pełna kwestia DAN-a.
danusia;pause=0.18|Kwestia Danusi bazowym głosem.
dan;speed=0.94;profile=szept;pause=0.68|Rzadki, cichy finał.
```

Generator playlist z reżyserią: `scripts/dobranocka_prepare.py`
(źródło `dan|...`/`danusia|...` → `*.playlist.txt` + audyt `*.prosody.json`).
Prompt do pisania audycji: `docs/PROMPT-CLAUDE-HISTORIA-PROZODIA.md`.
