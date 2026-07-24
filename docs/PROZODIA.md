# Prozodia — aktywny kontrakt

Najpierw przeczytaj [`MUST-READ-GLOS-PROZODIA.md`](../MUST-READ-GLOS-PROZODIA.md).
Tam znajduje się granica decyzji, lista potwierdzonych problemów i kryterium
odsłuchowe. Ten plik opisuje wyłącznie tor wykonawczy.

## Dwie trasy

Aktywny runtime przyjmuje wyłącznie:

```text
dan
danusia
```

Host, agent, sesja, scena albo identyfikator głosu silnika nie tworzą kolejnej
postaci. Casting jest zapisany w `config/voice/personas.toml` i walidowany
przez `dan/voice/policy.py`.

## Live

Tekst trafia przez stdin:

```bash
printf '%s' 'Treść do powiedzenia.' |
  dan speak --json --as dan --session gadanie --source codex --stdin
```

Dla Danusi zmienia się tylko `--as`:

```bash
printf '%s' 'Treść do powiedzenia.' |
  dan speak --json --as danusia --session danusia-live --source codex --stdin
```

Aktualne flagi są zawsze w `dan speak --help`. Dokument nie utrwala liczbowych
receptur tempa, pauzy ani profilu.

Po wysłaniu sprawdź nie tylko enqueue:

```bash
dan queue list --json
dan doctor --json
```

Dowód odtworzenia wymaga `status=done` i `playback_confirmed=true`.

## Offline

Minimalny format sceny:

```text
dan|Pierwsza pełna myśl.
danusia|Odpowiedź wynikająca z jej kontekstu.
```

Pełny kontrakt jednej linii:

```text
persona;tempo=<początek>;tempo_end=<koniec>;emotion=<emocja>;tone=<ton>;pause=<sekundy>|tekst
```

Parser przyjmuje `emotion`, `tone`, `tempo`/`tempo_start`, `tempo_end`,
`pause`/`pause_after`, `gap`/`gap_before`, `takes` i `seeds`. Nie przyjmuje
absolutnego `speed`, profilu masteringu ani identyfikatora głosu. Te ostatnie
należą wyłącznie do kanonicznego katalogu.

Render:

```bash
dan prosody render scena.scene.txt --out /tmp/dan-render
```

Brak kontrolki oznacza neutralną bazę: tempo `1.0`, płaski kontur, neutralny
emotion/tone i brak automatycznie dodanej pauzy. Parser i planer nie zgadują
reżyserii z długości ani interpunkcji.

Plan zapisuje poprzednią i następną kwestię jako kontekst audytowy. Supertonic
syntetyzuje wyłącznie tekst bieżącej kwestii, więc decyzje reżyserskie muszą
zostać zapisane przed renderem. Renderer przenosi je bez zerowania do każdego
kandydata. Jeśli limit Supertonica wymaga technicznego podziału, jeden kontur
tempa jest rozłożony ciągle na wszystkie segmenty i nie startuje od nowa.

## Odpowiedzialność warstw

1. Autor sceny zapisuje pełną myśl i prawdziwy kontekst wypowiedzi.
2. Reżyser wyprowadza decyzje wykonawcze z sąsiednich kwestii i celu sceny.
3. Parser waliduje jawne kontrolki bez zgadywania.
4. Planer zapisuje sąsiedni kontekst i rozkłada jeden kontur na segmenty.
5. Resolver zamraża rzeczywistą konfigurację głosu oraz jawne kontrolki.
6. Supertonic i etap prozodii tworzą kierowany kandydat.
7. Kontrola jakości odrzuca techniczne awarie, ale nie ogłasza naturalności.
8. Mastering, trim i fade nie mogą niszczyć końcówek.
9. Manifest zachowuje wejście, seedy, metryki i wynik replayu.
10. Ozzy wybiera wynik odsłuchowo.

## Czego ten dokument nie autoryzuje

- stałej prędkości postaci;
- tabeli pauz;
- szablonu długości kwestii;
- automatycznego profilu z emocji;
- dowolnej trzeciej persony;
- surowego kodu głosu jako wartości `--as`;
- dawnego feedera, brokera albo zewnętrznego instalatora silnika.

Testy obowiązkowe są podane w `MUST-READ-GLOS-PROZODIA.md`.
