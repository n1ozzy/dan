# Panel DAN

Panel w pasku menu to **czysty klient HTTP** działającego `dand`. Nie posiada
żadnego runtime'u: nie odtwarza audio, nie startuje procesów, nie dotyka
launchd. Start: `scripts/dan-panel`.

## Stany

| Stan | Znaczenie | Co robić |
|---|---|---|
| online | daemon odpowiada na API, panel streamuje eventy | nic — pracuj |
| `daemon offline` | daemon nie odpowiada na porcie z konfiguracji | patrz „Offline" niżej |
| pauza głosu | broker nie bierze nowych pozycji z kolejki; bieżąca wypowiedź się kończy | „Wznów głos" gdy chcesz kontynuacji |
| restart wymagany | zmiana ustawień czeka na restart daemona | „Bezpieczny restart DANa" |

Sekcja głosu pokazuje: stan brokera, aktualnie mówioną pozycję i zawartość
kolejki (źródło, sesję, status — patrz `docs/GLOS-I-KOLEJKA.md`).

## Przyciski

| Przycisk | Wywołanie | Skutek |
|---|---|---|
| Pauza głosu | `POST /voice/pause` | broker przestaje brać nowe pozycje; kolejka zostaje |
| Wznów głos | `POST /voice/resume` | broker wraca do konsumpcji kolejki |
| Pomiń bieżące | `POST /voice/queue/current/cancel` | ucina tylko aktualną wypowiedź; reszta kolejki gra dalej (skip, nie flush) |
| Bezpieczny restart DANa | `POST /runtime/restart` | daemon domyka bieżącą pracę, wychodzi kodem 86, launchd (`KeepAlive`) wstawia go z powrotem |
| Wyślij / pamięć / eventy | API rozmowy i pamięci | normalna praca tekstowa |

## Co znaczy „offline"

„Offline" = panel nie dostał odpowiedzi od daemona. Panel **nie wskrzesza**
`dand` — celowo. Wstawanie procesu to robota launchd (`KeepAlive`), nie
kontrolki UI. Gdy panel pokazuje offline:

1. `dan doctor --json` — pełna diagnoza (działa też bez daemona);
2. jeśli daemon ma wstać: launchd zrobi to sam po `restart`/padzie, albo
   uruchom ręcznie `~/.dan/bin/dand`;
3. dalsza diagnostyka: `docs/ODZYSKIWANIE.md`.
