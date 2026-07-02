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
