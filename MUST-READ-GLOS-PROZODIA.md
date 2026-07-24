# MUST READ — głos i prozodia DAN-a

Ten plik jest obowiązkowym wejściem przed zmianą głosu, sceny, renderera,
adaptera albo dokumentacji głosowej.

Nie jest bankiem presetów. Nie podaje „magicznych” wartości. Oddziela:

- decyzje właściciela;
- fakty potwierdzone w kodzie;
- problemy potwierdzone odsłuchem;
- założenia czekające na próbkę A/B.

## Twarda granica właściciela

1. Publiczna obsada ma dokładnie dwie postacie: `dan` i `danusia`.
2. Nazwa hosta, sesji lub źródła nie tworzy trzeciej postaci.
3. Techniczne identyfikatory głosów silnika są kandydatami do strojenia tych
   dwóch postaci, a nie osobnymi trasami `--as`.
4. Każdy aktywny producent głosu wysyła wyłącznie `--as dan` albo
   `--as danusia`.
5. Wszystkie dawne postacie, aliasy i personowe pipeline'y są wycofane.
6. Archiwa, stare WAV-y, manifesty i historia Git mogą zostać jako dowód, ale
   nie są źródłem konfiguracji ani instrukcji.

Granica jest wymuszana przez `dan/voice/policy.py` i testy. Kanoniczny casting
jest zapisany wyłącznie w `config/voice/personas.toml`.

## Aktywna ścieżka

- TTS: Supertonic.
- Jeden właściciel kolejki i odtwarzania: daemon `dand`.
- Live: `dan speak` albo voice API.
- Offline/storytelling: `dan prosody render`.
- Casting: `config/voice/personas.toml`.
- Wymowa: `config/voice/pronunciations.toml`.
- Zmierzone korekty głośności: `config/voice/gains.json`.

Nie uruchamiaj dawnych brokerów, feederów ani instalatorów, które podmieniają
`dan/voice/prosody`.

## Decyzje odrzucone

Nie wolno przywrócić tych rzeczy jako presetu, domyślnego profilu, reguły
autorskiej ani „historycznie sprawdzonej” receptury:

- efektów udających skrajną formę mówienia, które nie dały słyszalnej wartości;
- automatycznego ciężkiego przetwarzania barwy;
- stałej prędkości postaci;
- stałej drabinki pauz;
- stałych długości ripost, napięcia, monologu albo zwykłej kwestii;
- automatycznego tempa z samej długości tekstu;
- automatycznej emocji z pojedynczego znaku interpunkcyjnego;
- cichego zastępowania nieobsługiwanej reżyserii wartością `neutral`;
- traktowania jednego seeda jako odsłuchowo reprezentatywnego;
- uznawania exit code, enqueue albo manifestu za dowód naturalnego brzmienia.

Stała może istnieć wyłącznie jako zweryfikowany limit techniczny aktywnego
silnika. Ustawienie artystyczne musi wynikać z kontekstu i odsłuchu.

## Potwierdzone problemy ostatniego renderu

Odsłuch potwierdził:

- syntetyczne czytanie bez emocji;
- brak nacisku wynikającego z kontekstu;
- nierówną głośność postaci;
- zbyt szybkie fragmenty DAN-a;
- sporadycznie uszkodzone końcówki;
- słabą orientację, kto jest w scenie i o co trwa konflikt;
- różnicę naturalności między szybkim one-take a staranniejszym renderem.

To są regresje do naprawy, nie cechy produkcyjnego głosu.

## Potwierdzone fakty kodu

W bieżącym offline:

- obie postacie mają neutralną bazę szybkości `1.0`; zmiana tempa należy do
  konkretnej wypowiedzi, nie do stałego charakteru postaci;
- format sceny przyjmuje jawne `emotion`, `tone`, `tempo`/`tempo_start`,
  `tempo_end`, pauzę, gap i ustawienia deterministycznych take'ów;
- parser nie przyjmuje absolutnego `speed`, profilu masteringu ani głosu;
- brak kontrolki nie uruchamia heurystyki: zostaje neutralna baza, płaski
  kontur i zero automatycznie dodanej pauzy;
- plan zapisuje poprzednią i następną kwestię jako kontekst audytowy;
- Supertonic nadal syntetyzuje tylko tekst bieżącej kwestii; kontekst musi
  zostać przełożony na jawne decyzje przed renderem;
- renderer zachowuje emotion, tone i cały kontur tempa w surowym kandydacie;
- przy technicznym podziale jeden kontur jest rozłożony ciągle na segmenty;
- mastering jest nakładany po wyborze take'a, a kalibrowany gain musi istnieć
  dokładnie dla obu aktywnych tras;
- techniczne metryki wykrywają oczywiste awarie, ale nie potrafią ocenić
  naturalności aktorskiej.

Każda zmiana tych faktów wymaga testu i aktualizacji tego rozdziału.

## Reżyseria, której oczekuje właściciel

1. Każda postać zaczyna od neutralnej bazy technicznej, ale prędkość wypowiedzi
   zmienia się subtelnie z kontekstem.
2. Reżyser analizuje poprzednią kwestię, obecną intencję i następną reakcję.
3. Pauza wynika z myśli, relacji i rytmu sceny, nie z tabelki znaków.
4. Interpunkcja jest zapisem wykonania, więc może łamać zasady drukarskie.
5. Nacisk powstaje przez redakcję tekstu, rytm, mikrotempo i zweryfikowane
   możliwości silnika.
6. Jedna linia wejścia opisuje pełną myśl.
7. Podział wewnętrzny następuje dopiero wtedy, gdy wymaga go aktywny limit
   techniczny, i nie może niszczyć granicy semantycznej.
8. Krótka wypowiedź nie jest automatycznie wolna ani szybka. Decyduje kontekst.
9. Sygnały niewerbalne wymagają osobnych prób A/B. Didaskalia nie mogą zostać
   przypadkiem przeczytane.
10. Wszystkie ważne take'i i ich seedy zostają do odsłuchu.

To są wymagania reżyserskie. Nie wolno udawać, że wszystkie są już
zaimplementowane.

## Kryterium odsłuchowe

Wynik można nazwać dobrym dopiero wtedy, gdy Ozzy rzeczywiście usłyszy:

- dwie wyraźnie różne, stabilne postacie;
- naturalne tempo zmieniające się z sensem;
- emocję i ton zgodne z linią oraz kontekstem;
- nacisk na właściwe słowa;
- zbliżoną odczuwalną odległość i głośność;
- pełne końcówki;
- czytelny konflikt, obecność postaci i rozwój sceny;
- brak teatralnego efektu nałożonego zamiast aktorstwa.

Testy, hash, manifest i replay są koniecznym dowodem technicznym. Ostateczny
werdykt naturalności należy do odsłuchu właściciela.

## Bariera przeciw nawrotom

Przed zakończeniem zmiany uruchom:

```bash
DAN_HOME=/tmp/dan-voice-tests python -m pytest -q \
  tests/test_voice_catalog.py \
  tests/test_voice_policy.py \
  tests/test_voice_persona_api.py \
  tests/test_voice_route_matrix.py \
  tests/test_offline_prosody.py
```

Jeżeli pojawia się trzecia trasa, stary personowy pipeline, sztywny preset
artystyczny albo kopiowalna instrukcja starego toru, wynik jest `FIX FIRST`.
