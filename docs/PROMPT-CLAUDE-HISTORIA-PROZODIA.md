# Prompt dla reżysera: historia przygotowana do DAN prosody

Najpierw przeczytaj w całości:

- `/Users/n1_ozzy/Documents/dev/dan/MUST-READ-GLOS-PROZODIA.md`
- `/Users/n1_ozzy/Documents/dev/dan/docs/PROZODIA.md`
- `/Users/n1_ozzy/Documents/dev/dan/config/persona/DAN.md`
- `/Users/n1_ozzy/Documents/dev/dan/config/voice/personas.toml`

Przygotuj oryginalną historię na temat **[WPISZ TEMAT]**. Wybierz narratora:
`dan` albo `danusia`. Nie twórz żadnej innej persony głosowej. Jeżeli materiał
ma być dialogiem dwojga prowadzących, używaj wyłącznie tych dwóch nazw.

## Najpierw historia

Zaplanuj działanie, konflikt, zmianę sytuacji, konsekwencję i finał. Każda
reakcja ma wynikać z tego, co właśnie padło i do czego prowadzi następna
kwestia. Nie produkuj rotacji mówców według szablonu. Postać może odpowiedzieć
jednym słowem, wygłosić monolog, przerwać albo milczeć, jeżeli wymaga tego
scena.

Jeżeli temat opiera się na aktualnych faktach, zweryfikuj je. Oddziel fakt,
hipotezę i fikcję sceniczną. Źródła zapisz osobno; nie czytaj adresów URL.

## Potem reżyseria każdej kwestii

Jedna linia sceny zawiera jedną pełną myśl. Jej długość, pauza i tempo wynikają
z sensu, relacji oraz miejsca w scenie. Nie używaj tabel długości, drabinki
pauz, stałej prędkości postaci ani reguły opartej na interpunkcji.

Dla każdej linii:

1. Przeczytaj poprzednią kwestię, bieżący cel i następną reakcję.
2. Zredaguj tekst do naturalnego mówienia; nacisk zapisz rytmem i doborem słów.
3. Dobierz osobno tempo początku i końca, emocję, ton oraz pauzę.
4. Użyj małej zmiany tylko wtedy, gdy ma czytelny powód w scenie.
5. Nie wpisuj didaskaliów, które syntezator mógłby przeczytać.

Neutralna baza obu postaci wynosi `1.0`. Nie wpisuj absolutnego `speed`,
profilu masteringu, DSP ani kodu głosu. Te pola należą do katalogu, nie do
autora sceny.

## Format

Każda wypowiedź ma postać:

```text
persona;tempo=<początek>;tempo_end=<koniec>;emotion=<emocja>;tone=<ton>;pause=<sekundy>|tekst mówiony
```

Dozwolone nazwy i zakresy odczytaj z bieżącego parsera oraz:

```bash
dan prosody render --help
```

Nie kopiuj wartości z wcześniejszych scen jako presetów. Używaj tylko
`dan`/`danusia`. Podział techniczny zostaw rendererowi; pobiera aktywny limit
Supertonica i szuka granicy semantycznej. Jeżeli jedna myśl nie ma bezpiecznej
granicy przed limitem, przeredaguj ją zamiast ciąć słowo.

Zapisz gotowy tekst jako `.scene.txt`. Obok zapisz `.sources.md` tylko wtedy,
gdy historia korzysta z zewnętrznych faktów.

## Kontrola przed renderem

Uruchom:

```bash
dan prosody render HISTORIA.scene.txt --plan-only --out /tmp/dan-plan
```

Otwórz `plan.json` i sprawdź:

- obsadę wyłącznie `dan`/`danusia`;
- pełne myśli bez mechanicznego szatkowania;
- jawny powód każdej zmiany tempa i pauzy;
- ciągłość konturu po podziale technicznym;
- poprzedni i następny kontekst przy każdej środkowej kwestii;
- brak `speed`, `profile`, automatycznych presetów i czytanych didaskaliów.

Plan techniczny nie dowodzi naturalności. Nie nazywaj materiału finalnym przed
rzeczywistym odsłuchem.
