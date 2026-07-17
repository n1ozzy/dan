# SQLite Backup i Recovery Runbook

Classification: runbook.

Ten runbook opisuje backup i odtworzenie `dan.db` w środowisku produkcyjnym DANa.

## Dlaczego to robimy

`~/.dan/dan.db` jest source of truth dla:

- historii konwersacji i tur,
- zdarzeń audytowych i narzędzi,
- ustawień runtime i decyzji aprobaty,
- pamięci i jej pipeline.

Awaria DB musi mieć prostą, odtwarzalną ścieżkę powrotu.

## Backup operacyjny (ręczny, zalecany)

1. Zatrzymaj pracę z daemonem (`dand`) i upewnij się, że żaden proces nie modyfikuje DB.
2. Wykonaj `backup` z `sqlite3`:

```bash
DB="${HOME}/.dan/dan.db"
mkdir -p "${HOME}/.dan/backups"
sqlite3 "$DB" ".backup '${HOME}/.dan/backups/dan-$(date +%F-%H%M%S).db'"
sqlite3 "$DB" "PRAGMA quick_check;"
```

3. Zachowaj również skompresowaną kopię pliku jako punkt kontrolny.

## Backup cykliczny (opcjonalnie)

Jeśli potrzebujesz automatyki, uruchom planowane zadanie (np. launchd launchctl start/cron):

```bash
sqlite3 "$HOME/.dan/dan.db" ".backup '$HOME/.dan/backups/dan-auto.db'"
```

Przykład z limitem czasu:

```bash
for i in {1..30}; do
  flock -n "$HOME/.dan/dan.db.lock" \
    sqlite3 "$HOME/.dan/dan.db" ".backup '$HOME/.dan/backups/dan-latest.db'" \
    && break || sleep 5
done
```

## Człon od weryfikacji

Po backupie wykonaj:

- `sqlite3 <backup>.db "PRAGMA integrity_check;"` (powinno zwrócić `ok`)
- `sqlite3 <backup>.db "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;"` (podstawowa zgodność schematu)

## Odtwarzanie z backupu

1. Zatrzymaj daemon i upewnij się, że żaden proces nie trzyma otwartego `dan.db`.
2. Wykonaj kopię obecnego pliku (na wypadek analizy po-incydentowej):

```bash
cp "$HOME/.dan/dan.db" "$HOME/.dan/dan.db.corrupt-$(date +%F-%H%M%S)"
```

3. Podmień bazę:

```bash
cp "<path-do-backupu>/dan-YYYY-MM-DD-HHMMSS.db" "$HOME/.dan/dan.db"
```

4. Zweryfikuj:

```bash
sqlite3 "$HOME/.dan/dan.db" "PRAGMA integrity_check;"
sqlite3 "$HOME/.dan/dan.db" "SELECT name FROM sqlite_master WHERE type='table' AND name='conversations';"
```

5. Uruchom ponownie daemona i sprawdź podstawowe health:

```bash
curl -sS http://127.0.0.1:41741/health
```

## Co robić przy uszkodzeniu DB

Jeśli `PRAGMA integrity_check` daje błąd:

1. zatrzymaj aktywne procesy daemona,
2. przywróć backup,
3. sprawdź integralność (ponownie),
4. jeśli nadal nieok, zgłoś incydent i odtwórz z najstarszego spójnego backupu.

Nie ma automatycznego schematycznego „repair mode” bez ryzyka utraty danych — backup/restore jest zalecanym, kontrolowanym wariantem odzysku.
