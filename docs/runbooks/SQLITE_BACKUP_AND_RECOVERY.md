# SQLite Backup and Recovery Runbook

Classification: runbook.

This runbook describes backup and restore of `dan.db` in DAN's production environment.

## Why we do this

`~/.dan/dan.db` is the source of truth for:

- conversation and turn history,
- audit and tool events,
- runtime settings and approval decisions,
- memory and its pipeline.

A DB failure must have a simple, repeatable recovery path.

## Operational backup (manual, recommended)

1. Stop working with the daemon (`dand`) and make sure no process is modifying the DB.
2. Run a `backup` with `sqlite3`:

```bash
DB="${HOME}/.dan/dan.db"
mkdir -p "${HOME}/.dan/backups"
sqlite3 "$DB" ".backup '${HOME}/.dan/backups/dan-$(date +%F-%H%M%S).db'"
sqlite3 "$DB" "PRAGMA quick_check;"
```

3. Also keep a compressed copy of the file as a checkpoint.

## Periodic backup (optional)

If you need automation, run a scheduled job (e.g. launchd launchctl start/cron):

```bash
sqlite3 "$HOME/.dan/dan.db" ".backup '$HOME/.dan/backups/dan-auto.db'"
```

Example with a time limit:

```bash
for i in {1..30}; do
  flock -n "$HOME/.dan/dan.db.lock" \
    sqlite3 "$HOME/.dan/dan.db" ".backup '$HOME/.dan/backups/dan-latest.db'" \
    && break || sleep 5
done
```

## Verification step

After the backup, run:

- `sqlite3 <backup>.db "PRAGMA integrity_check;"` (should return `ok`)
- `sqlite3 <backup>.db "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;"` (basic schema conformance)

## Restoring from a backup

1. Stop the daemon and make sure no process is holding `dan.db` open.
2. Make a copy of the current file (for potential post-incident analysis):

```bash
cp "$HOME/.dan/dan.db" "$HOME/.dan/dan.db.corrupt-$(date +%F-%H%M%S)"
```

3. Swap in the database:

```bash
cp "<path-to-backup>/dan-YYYY-MM-DD-HHMMSS.db" "$HOME/.dan/dan.db"
```

4. Verify:

```bash
sqlite3 "$HOME/.dan/dan.db" "PRAGMA integrity_check;"
sqlite3 "$HOME/.dan/dan.db" "SELECT name FROM sqlite_master WHERE type='table' AND name='conversations';"
```

5. Restart the daemon and check basic health:

```bash
curl -sS http://127.0.0.1:41741/health
```

## What to do when the DB is corrupted

If `PRAGMA integrity_check` reports an error:

1. stop the active daemon processes,
2. restore a backup,
3. check integrity (again),
4. if it is still not ok, report an incident and restore from the oldest consistent backup.

There is no automatic, formulaic "repair mode" without the risk of data loss — backup/restore is the recommended, controlled recovery variant.
