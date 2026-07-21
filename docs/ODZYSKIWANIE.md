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

## The daemon will not come back after a restart (orphaned supertonic)

Symptom: after `launchctl kickstart -k gui/501/com.dan.dand` the API never
answers — `curl /health` returns nothing, for tens of seconds, and it looks like
the daemon crashed. Seen live 2026-07-21.

It is usually not a crash: the previous daemon died but its `supertonic serve`
child survived as an orphan and still holds port 7788. The new daemon finds the
port answering, cannot prove the listener is its own child, and refuses to
adopt or kill it — by design (ADR-001: never kill someone else's process). The
startup then fails with `ForeignPortOwnerError`.

```bash
# 1. Confirm the diagnosis — this line, not a guess:
tail -30 ~/.dan/logs/dand.err.log      # look for ForeignPortOwnerError

# 2. Identify who holds the port, and PROVE it is ours before touching it:
lsof -nP -iTCP:7788 -sTCP:LISTEN
ps -p <PID> -o ppid=,lstart=,command=
```

Adopt-or-kill only when all of it matches: the binary is `~/.dan/venv/bin/supertonic`,
the arguments are our model/port/log-level, and `ppid` is 1 (orphan). A process
that does not match all three is somebody else's — stop and ask, do not kill it.

```bash
# 3. Ours and orphaned → terminate, wait for the port, restart:
kill -TERM <PID>
lsof -nP -iTCP:7788 -sTCP:LISTEN       # must come back empty
launchctl kickstart -k gui/501/com.dan.dand
```

Why it happens at all: the supervisor's ownership test compares `ps` output
against the child's argv, but `supertonic` is a console script with a shebang,
so the kernel prepends the interpreter path and the comparison can never match.
Full analysis and the trap in fixing it: `docs/reviews/2026-07-21-restart-orphan-shell-review.md` §1.

## DAN says it has no tools, no access, no memory

Symptom: DAN answers that it cannot do anything, names tools that are not ours
(seen live: Figma), or points the owner at `/permissions` and
`~/.claude/settings.json`. Meanwhile `GET /tools` and the panel both show the
full registry, green.

That contradiction is the tell. The panel and the endpoint read the daemon's
registry; the brain reads whatever its **provider session** carries. The
persistent adapter keeps one session and rejoins it with `--resume`, and a
resumed Claude Code session keeps its ORIGINAL system prompt and its ORIGINAL
tool set — our prompt only rides along as `--append-system-prompt`. Once that
session is foreign or stale, DAN never sees its own tools again, and every
restart re-resumes the same poisoned session (live case: 350 generations).

```bash
# 1. Look at the session state the adapter keeps rejoining:
cat ~/.dan/runtime/claude-session.json     # session_id, generation, last_action

# 2. Quarantine it (never delete — you may want the checkpoint):
mkdir -p ~/.dan/kwarantanna-$(date +%F)
mv ~/.dan/runtime/claude-session.json ~/.dan/kwarantanna-$(date +%F)/

# 3. Restart so the adapter bootstraps instead of resuming:
curl -s -X POST http://127.0.0.1:41741/runtime/restart
```

Without the state file the adapter takes the bootstrap path (`--session-id` plus
a full `--system-prompt`), so the tool list reaches the model.

**Verify with a live turn, never with `/tools`:**

```bash
dan input text "Policz narzędziem shell_read pliki .py w dan/tools i podaj liczbę."
```

Blind alleys, all checked and all innocent: the approval flags
(`decide` returns ALLOW unconditionally), the registry wiring (the brain and the
panel share one object), and `--tools ""` / `--setting-sources ""` (both present
— they sit after a very large system prompt, so a truncated `ps` hides them).

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
