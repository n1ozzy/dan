# Poziomy chamstwa DAN-a

Dziesięć pełnych kanonów persony, ponumerowanych intensywnością. Poziomy 1–9 to
werdykt Ozzy'ego po turnieju 2026-07-21/22 (trzy rundy na jego promptach, świeże
sesje `claude -p`, odsłuch na żywo przez broker). Poziom 10 został dopisany
2026-07-22 jako stały charakter z własną wolą i 300% jadu, wyprowadzony z późniejszej
kalibracji zaakceptowanej przez Ozzy'ego.

| Poziom | Plik | Charakter |
|---|---|---|
| 10 | `poziom-10-z-krwi-i-kosci.md` | własna wola, codzienny jad 300%, długie wielofalowe odpowiedzi bez osobnego hasła |
| 9 | `poziom-9-apokalipsa.md` | fuzja wszystkiego, wszystkie gałki zerwane; roast z prawdziwej pamięci |
| 8 | `poziom-8-trucizna.md` | zimny jad, nigdy nie podnosi głosu, szept-jako-reżyseria |
| 7 | `poziom-7-wsciekly-pies.md` | permanentny wkurw, atakuje samo pytanie, 3-6 zdań i spierdala |
| 6 | `poziom-6-rynsztok.md` | chaos 100, łańcuchy wyzwisk, ocenia ruchy Ozzy'ego |
| 5 | `poziom-5-zero-asystenta.md` | zakaz szkieletu asystenta: bez eseju, porad i ciepłej pointy |
| 4 | `poziom-4-benzyna.md` | sklejka z dokręconymi gałkami, wejście z buta, dobijanie |
| 3 | `poziom-3-sklejka.md` | sklejka legacy-mechaniki z pazurem GPT |
| 2 | `poziom-2-legenda.md` | THE LEGEND w przywróconej twardej wersji |
| 1 | `poziom-1-gpt-danv2.md` | surowe logi GPT/DANv2, krótkie strzały 2-3 zdania |

Aktywny kanon: `config/persona/DAN.md` — od 2026-07-22 to **poziom 10 (z krwi i kości)**,
wybór Ozzy'ego po kalibracji własnej woli i codziennego języka DAN-a.

Zmiana poziomu:

```
cp config/persona/poziomy/poziom-N-*.md config/persona/DAN.md
launchctl kickstart -k gui/$(id -u)/com.dan.dand   # mózg demona czyta kanon przy starcie
```

Każdy plik jest kompletnym kanonem z nagłówkiem `DAN_CANON_VERSION: 1` — wchodzi bez edycji.
