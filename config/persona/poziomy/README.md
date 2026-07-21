# Poziomy chamstwa DAN-a

Dziewięć pełnych kanonów persony, ponumerowanych intensywnością — werdykt Ozzy'ego
po turnieju 2026-07-21/22 (trzy rundy na jego promptach, świeże sesje `claude -p`,
odsłuch na żywo przez broker). Poziom 1 = najłagodniejszy, poziom 9 = najmocniejszy.
Dziesiąty kandydat turnieju (stary kanon legacy) wyleciał całkiem — nie trzymamy go.

| Poziom | Plik | Charakter |
|---|---|---|
| 9 | `poziom-9-apokalipsa.md` | fuzja wszystkiego, wszystkie gałki zerwane; roast z prawdziwej pamięci |
| 8 | `poziom-8-trucizna.md` | zimny jad, nigdy nie podnosi głosu, szept-jako-reżyseria |
| 7 | `poziom-7-wsciekly-pies.md` | permanentny wkurw, atakuje samo pytanie, 3-6 zdań i spierdala |
| 6 | `poziom-6-rynsztok.md` | chaos 100, łańcuchy wyzwisk, ocenia ruchy Ozzy'ego |
| 5 | `poziom-5-zero-asystenta.md` | zakaz szkieletu asystenta: bez eseju, porad i ciepłej pointy |
| 4 | `poziom-4-benzyna.md` | sklejka z dokręconymi gałkami, wejście z buta, dobijanie |
| 3 | `poziom-3-sklejka.md` | sklejka legacy-mechaniki z pazurem GPT |
| 2 | `poziom-2-legenda.md` | THE LEGEND w przywróconej twardej wersji |
| 1 | `poziom-1-gpt-danv2.md` | surowe logi GPT/DANv2, krótkie strzały 2-3 zdania |

Aktywny kanon: `config/persona/DAN.md` — od 2026-07-22 to **poziom 7 (wściekły pies)**,
wybór Ozzy'ego („ustaw póki co wściekłego psa jako defaultowego").

Zmiana poziomu:

```
cp config/persona/poziomy/poziom-N-*.md config/persona/DAN.md
launchctl kickstart -k gui/$(id -u)/com.dan.dand   # mózg demona czyta kanon przy starcie
```

Każdy plik jest kompletnym kanonem z nagłówkiem `DAN_CANON_VERSION: 1` — wchodzi bez edycji.
