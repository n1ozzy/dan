# Odzyskiwanie

## Pięć podstawowych diagnostyk

Dokładnie pięć, w tej kolejności — każda działa read-only:

```bash
# 1. Pełna diagnoza produktu (działa też, gdy daemon leży):
dan doctor --json

# 2. Czy daemon żyje i odpowiada na API:
dan health

# 3. Stan runtime: rozmowa, mózg, kolejka, workery:
dan state

# 4. Co siedzi w kolejce głosu (i w jakim statusie):
dan queue list --json --limit 20

# 5. Jakie procesy według daemona są jego dziećmi:
dan runtime processes
```

Interpretacja:

- `doctor` czyste, `health` pada → daemon nie działa; launchd (`KeepAlive`)
  powinien go wstawić sam — jeśli nie, uruchom ręcznie `~/.dan/bin/dand`
  i patrz w `~/.dan/logs/`;
- kolejka stoi w `queued` → sprawdź pauzę brokera w panelu
  (`docs/PANEL.md`) i statusy `failed` z polem `error`;
- panel „offline" przy żywym daemonie → złe `--url`/port w konfiguracji
  (`dan config explain`).

## Journaled rollback

Cutover na nową instalację jest dziennikowany. Powrót robi się **wyłącznie**
narzędziem rollbacku — nigdy ręcznym przenoszeniem plików:

```bash
JOURNAL="$(find ~/.dan/migration -name journal.jsonl -type f -print | sort | tail -1)"

# Najpierw dry-run (domyślny) — pokazuje dokładnie, co zostanie cofnięte:
python scripts/dan-rollback apply --journal "$JOURNAL"

# Faktyczne cofnięcie:
python scripts/dan-rollback apply --apply --journal "$JOURNAL"
```

Rollback czyta journal cutovera (`~/.dan/migration/cutover-*/journal.jsonl`)
i odtwarza stan sprzed cutovera z backupów. Status cutovera:
`python scripts/dan-cutover status --journal "$JOURNAL"`.
