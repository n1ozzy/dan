# Recovery

## The five basic diagnostics

Exactly five, in this order — each one is read-only:

```bash
# 1. Full product diagnosis (works even when the daemon is down):
dan doctor --json

# 2. Is the daemon alive and answering the API:
dan health

# 3. Runtime state: conversation, brain, queue, workers:
dan state

# 4. What is in the voice queue (and in what status):
dan queue list --json --limit 20

# 5. Which processes the daemon says are its children:
dan runtime processes
```

Interpretation:

- `doctor` clean, `health` failing → the daemon is not running; launchd
  (`KeepAlive`) should bring it back up on its own — if it does not, start
  `~/.dan/bin/dand` manually and watch `~/.dan/logs/`;
- the queue is stuck in `queued` → check the broker pause in the panel
  (`docs/PANEL.md`) and `failed` statuses with the `error` field;
- the panel says "offline" while the daemon is alive → wrong `--url`/port in
  the configuration (`dan config explain`).

## Journaled rollback

The cutover to a new installation is journaled. Going back is done
**exclusively** with the rollback tool — never by moving files around by hand:

```bash
JOURNAL="$(find ~/.dan/migration -name journal.jsonl -type f -print | sort | tail -1)"

# First a dry-run (the default) — shows exactly what will be reverted:
python scripts/dan-rollback apply --journal "$JOURNAL"

# The actual revert:
python scripts/dan-rollback apply --apply --journal "$JOURNAL"
```

The rollback reads the cutover journal (`~/.dan/migration/cutover-*/journal.jsonl`)
and restores the pre-cutover state from backups. Cutover status:
`python scripts/dan-cutover status --journal "$JOURNAL"`.
