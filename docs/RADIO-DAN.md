# Radio DAN

**Stan uczciwie: Radio Studio to Wydanie 2. Nie istnieje w tym wydaniu.**

W Wydaniu 1 nie ma schedulera radia, zakładki „Radio DAN" w panelu, sesji
radiowych z uczestnikami ani formatów (dobranocka, standup, roast, telefon).
Żaden dokument ani skill nie powinien udawać, że jest inaczej.

## Co z Wydania 1 jest już kompatybilne z przyszłym Radiem

Radio będzie zakładką tego samego produktu, na tych samych kontraktach:

- **kolejka głosu w `dand`** — trwała, ze snapshotem renderu i pasmami
  (`live`, `normal`, `background`); scheduler radia będzie jej producentem,
  nie osobnym systemem (`docs/GLOS-I-KOLEJKA.md`);
- **sesje kolejki** — `dan speak --session ...` i `dan queue flush --session ...`
  już dziś izolują strumień wypowiedzi (np. sesja `radio`);
- **persony głosowe** — konfiguracja w `config/voice/personas.toml`
  (m.in. `dan`, `danusia`), wybierane jawnie przez `--as`;
- **pipeline offline Chatterbox V3** — przygotowane kwestie renderowane poza
  żywą kolejką;
- **adaptery mózgów** (uczestnik = jawne `identity + brain + voice`):
  `claude_cli`, `codex_cli`, `groq`, `openai`, `ollama`, `qwen`, `eco`
  oraz `mock`/`test` do testów — wszystkie za wspólnym kontraktem
  `BrainAdapter` (`dan/brain/`);
- **panel + strumień eventów** — przyszły widok „co gra / co czeka" będzie
  czytał te same eventy `voice.*`.

## Czego NIE ma (i nie udajemy, że jest)

- schedulera studia (kolejność uczestników, backpressure, max 1 oczekująca
  wypowiedź uczestnika);
- trybów/formatów sesji radiowej i jej osobnej historii;
- dołączania Ozzy'ego mikrofonem do sesji radiowej i zdalnego „telefonu";
- wizualizera.

Radio dostanie osobną specyfikację i plan dopiero po przejściu bramek
fundamentu (spec konsolidacji, §7 i §9).
