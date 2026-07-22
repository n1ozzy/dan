# Prompt dla Claude: jednogłosowa historia z naturalną prozodią

> Przeniesione z ery przed-dand (2026-07-16). Standard pisania (długości linii,
> drabinka pauz, zakazy profili) obowiązuje nadal; mechanikę odtwarzania w erze
> dand opisuje docs/PROZODIA.md.

Przygotuj od zera jedną oryginalną, długą historię czytaną przez **[DAN albo DANUSIA]** na temat: **[WPISZ TEMAT]**.
Materiał ma wystarczyć na około 15–20 minut naturalnego słuchania. Nie kopiuj cudzego scenariusza
ani transkryptu; możesz wykorzystać popularny motyw, ale fabuła, dialogi, żarty i konstrukcja mają
być nowe. Jeżeli temat dotyczy aktualnych wydarzeń, technologii, UFO, inwigilacji, narkotyków albo
twierdzeń spiskowych, najpierw zweryfikuj aktualne fakty. W audycji jasno odróżniaj: fakt
udokumentowany, wiarygodną hipotezę, plotkę i fikcję sceniczną. Źródła zapisz osobno, nie czytaj URL-i.

Przed pisaniem przeczytaj w całości:

- `/Users/n1_ozzy/Documents/dev/dan/config/persona/DAN.md`
- `/Users/n1_ozzy/.agents/skills/dobranocka/SKILL.md`
- `/Users/n1_ozzy/.agents/skills/gadanie/SKILL.md`
- `/Users/n1_ozzy/.agents/skills/danusia-live/SKILL.md`

## Kontrakt obsady

**Jedna historia = jeden czytający.** Wybierz `dan` albo `danusia` przed pierwszą linią i nie
zmieniaj persony do końca pliku. Drugi prowadzący nie wtrąca się, nie czyta dialogów i nie robi
mostów. Cytaty oraz dialogi bohaterów przekazuje ten sam narrator. Wielogłosowe telefony, reklamy,
roast battle i teleturnieje należą do osobnych bloków radiowych, nigdy do pliku historii.

Narracja ma mieć działanie, konflikt, zwrot, konsekwencję i finał. DAN ma własne reakcje i osąd,
ale nie może być dokumentalnym lektorem z doklejonym przekleństwem. Danusia, jeśli została
wybrana, ma własny jad i punkt widzenia, nie udaje łagodniejszej kopii DAN-a.

Nie używaj powitalnych podpórek `mordy`, `kochane zjeby`, `dobra mordy` ani jednego przekleństwa
jako przecinka. Te frazy są podane jako przykłady regresu i nie mogą trafić do wyjścia. Wulgarność
ma reagować na konkretną osobę, decyzję lub absurd; różnicuj słownictwo i rytm bez kwoty bluzgów.

## Pisanie pod TTS

- Jedna linia to jedna kompletna myśl z wystarczającym kontekstem dla intonacji TTS.
- Zwykła kwestia: orientacyjnie 180–300 znaków.
- Napięcie lub ważny monolog: 250–340 znaków.
- Szybka riposta lub cios: 60–140 znaków.
- Nie przekraczaj około 340 znaków w jednej linii. Nie tnij mechanicznie per zdanie i nie rób
  sieczki po 20–40 znaków.
- Pisz naturalnym mówionym polskim z ogonkami. Cyfry i skróty zapisuj tak, żeby dobrze zabrzmiały.
- Emocję buduj znaczeniem, reakcją, składnią i interpunkcją. Bez didaskaliów czytanych na głos.
- `gritty` i `krzyk` nie są domyślną emocją. Nie używaj ich w naturalnej audycji.
- `szept` może pojawić się najwyżej raz, najlepiej w finale DAN-a. Jest celowo przytłumiony i cichy.
- Danusia zawsze zachowuje bieżącą bazę `F4/clean` z `personas.toml`: bez `speed` i bez `profile`.
- Nie porównuj tempa sąsiednich kwestii różnych person. Każda ma własną bazę.

## Pauzy po zatwierdzonym odsłuchu

- zwykła kwestia: `pause=0.18`
- pytanie lub krótka kontra: `pause=0.26`
- napięcie: maksymalnie `pause=0.32`
- wyraźna zmiana formatu: maksymalnie `pause=0.34`
- dwie kwestie domykające: `pause=0.40` i `pause=0.48`
- finał: `pause=0.68`

Nie używaj starego finału `0.90`; przerwy były za długie. Wewnątrz pełnej myśli rytm prowadzi
interpunkcja, a nie dzielenie na osobne requesty.

## Dokładny format wyjścia

Zwróć wyłącznie gotową playlistę, po jednej wypowiedzi na linię:

```text
dan;speed=1.28;profile=raw;pause=0.18|Pełna kwestia DAN-a.
dan;speed=1.31;profile=raw;pause=0.32|Kwestia z napięciem tego samego narratora.
dan;speed=1.24;profile=szept;pause=0.68|Rzadki, cichy finał tego samego narratora.
```

DAN zwykle używa `profile=raw`; umiarkowana zmiana `speed` może wynikać ze sceny. Nie zmieniaj
tempa co linię dla ozdoby. Jeśli narratorem jest Danusia, każda linia zaczyna się od
`danusia;pause=...` bez tempa i profilu. Role i metadane występują wyłącznie przed `|`;
nie dodawaj nazw mówców, profili, komentarzy reżyserskich ani numerów scen do tekstu czytanego.

Zapisz wynik jako osobny plik `.playlist.txt`. Obok zapisz krótki `.sources.md` z wykorzystanymi
aktualnymi źródłami i oznaczeniem, które fragmenty audycji są fikcją. Na końcu wykonaj kontrolę:
pełna historia, około 15–20 minut, dokładnie jedna persona, każda linia do około 340 znaków, Danusia bez override,
brak `gritty/krzyk`, najwyżej jeden szept DAN-a, pauzy nie większe niż `0.68`.
