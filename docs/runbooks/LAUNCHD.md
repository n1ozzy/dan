# Launchd Lifecycle (F2)

Manual-only lifecycle for the single official Jarvis agent. Nothing here is
ever run automatically — no build step, test, or smoke installs or loads
launchd (LAUNCH_SUPERVISION.md §5, FROZEN).

| Concern | Value |
|---------|-------|
| Official label (the only one) | `com.ozzy.jarvisd` |
| Wrapper installed to | `~/.jarvis/bin/jarvisd` |
| Plist installed to | `~/Library/LaunchAgents/com.ozzy.jarvisd.plist` |
| Logs | `~/.jarvis/logs/jarvisd.{out,err}.log` |
| Database (never touched by these scripts) | `~/.jarvis/jarvis.db` |

## Install (human runs it, deliberately)

```bash
scripts/install-launchd.sh          # prints the exact plan, changes nothing
scripts/install-launchd.sh --yes    # applies the plan
```

The script always prints exactly what it will do before doing anything.
It refuses to run when the agent is already loaded (uninstall first — it
never stacks agents), and it never installs automatically from any other
script. `RunAtLoad` is true: after this deliberate install the daemon comes
up at login. `KeepAlive` is false: launchd never fights the human over
restarts (same spirit as the report-only RuntimeSupervisor).

Verify:

```bash
launchctl print gui/$(id -u)/com.ozzy.jarvisd | head -20
curl -s http://127.0.0.1:41741/health
```

## Uninstall

```bash
scripts/uninstall-launchd.sh        # prints the exact plan, changes nothing
scripts/uninstall-launchd.sh --yes  # boots the agent out, removes plist + wrapper
```

Uninstall **never deletes** the database, the logs, or the API token —
removing the agent must not cost history (MASTER_PLAN F2).

## Config resolution

The wrapper prefers `~/.jarvis/jarvis.toml` when it exists (user-owned
config outside the repo), then falls back to the repo default. An explicit
`JARVIS_CONFIG` environment variable wins over both.

## Log rotation (FIX-11)

Three log files exist, rotated by two different mechanisms:

| File | Written by | Rotation |
|------|-----------|----------|
| `~/.jarvis/logs/jarvisd.log` | the Python `jarvis` logger (all app diagnostics) | **in-process**, automatic |
| `~/.jarvis/logs/jarvisd.out.log` | launchd `StandardOutPath` (stray stdout) | newsyslog (optional) |
| `~/.jarvis/logs/jarvisd.err.log` | launchd `StandardErrorPath` (crash tracebacks) | newsyslog (optional) |

**`jarvisd.log`** carries essentially all volume and rotates itself: a
`SecureRotatingFileHandler` (a `RotatingFileHandler` that re-applies the 0600
perms across rollover) caps it at `daemon.log_max_bytes` per file, keeping
`daemon.log_backup_count` rotated files (`jarvisd.log.1` … `.N`). Defaults:
10 MiB × (1 active + 5 backups) ≈ 60 MiB ceiling. Set `log_max_bytes = 0` in
`[daemon]` to disable rotation. Because the daemon is `RunAtLoad` and never
self-restarts, this in-process cap is what actually bounds disk growth.

**`jarvisd.{out,err}.log`** only receive whatever escapes the logger — stray
`print`/stderr and pre-logging crash tracebacks — so they grow slowly. They are
plain files launchd holds open, so process-side rotation can't touch them. If
you want them bounded too, add an optional newsyslog rule (macOS-native):

```
# /etc/newsyslog.d/jarvisd.conf  — substitute your real home path
# logfilename                              owner:group  mode count size  when flags
/Users/<you>/.jarvis/logs/jarvisd.out.log  <you>:staff  600  5    10240 *    N
/Users/<you>/.jarvis/logs/jarvisd.err.log  <you>:staff  600  5    10240 *    N
```

`size` is in KB (10240 = 10 MiB); `N` skips the SIGHUP (there is no PID to
signal — launchd owns the fd). Caveat: launchd keeps the redirect fd open, so
after a rename launchd keeps writing to the rotated inode until the agent is
reloaded (`launchctl kickstart -k gui/$(id -u)/com.ozzy.jarvisd`). Given how
little lands there, the newsyslog rule is hygiene, not a hard cap — the real
retention control is the in-process rotation of `jarvisd.log` above.

## The ADR-014 TCC trap (read before debugging a silent agent)

launchd agents cannot read scripts under `~/Documents` — that is what made
the legacy `com.dan.voice-broker` agent thrash with *"can't open input
file"*. That is why the wrapper and logs live under `~/.jarvis`. The
installed wrapper is a thin shim that pins `JARVIS_REPO` and delegates to
the repo wrapper, so the *start logic* has a single source of truth in git.

One residual TCC risk remains: the venv python itself lives inside the repo
under `~/Documents`. If the agent bootstraps but the daemon never comes up:

1. Check `~/.jarvis/logs/jarvisd.err.log`. A permission / *operation not
   permitted* / *can't open input file* error means TCC blocked the read.
2. Grant access: System Settings → Privacy & Security → Files and Folders
   (or Full Disk Access) for the blocked binary, then
   `launchctl kickstart -k gui/$(id -u)/com.ozzy.jarvisd`.
3. The disclaimed-client trap from the D4 live gate applies here too: a
   grant given to a parent app does not automatically cover a launchd
   child (see docs/runbooks/SCREEN_RECORDING_TCC.md §4 for the pattern).

## Rules (FROZEN, LAUNCH_SUPERVISION.md)

- Manual install only — never automatic, never from a build or smoke step.
- One label: `com.ozzy.jarvisd`. Legacy labels are conflicts to report,
  never to adopt.
- Uninstall unloads but never deletes data.
- The supervisor detects and reports; it never kills (ADR-007).
