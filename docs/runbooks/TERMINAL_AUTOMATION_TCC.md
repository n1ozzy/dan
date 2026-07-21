# Automation (TCC) onboarding for the terminal profile

FAZA D5 ships the terminal operator tools (`terminal_read_screen`,
`terminal_paste`). The `osascript` backend talks to Terminal.app / iTerm2
with fixed AppleScript, which works only when TCC grants the process
hosting dand the **Automation (Apple Events)** permission *for each
target app*. Without the grant nothing crashes: every call fails cleanly
(osascript error `-1743`) with a pointer to this runbook, and the daemon
keeps running.

## 1. Which process needs the grant

Automation grants are per **(host app → target app)** pair. The host is
the responsible application of the process running dand — same
responsibility rule as Accessibility ([ACCESSIBILITY_TCC.md](ACCESSIBILITY_TCC.md))
and Screen Recording ([SCREEN_RECORDING_TCC.md](SCREEN_RECORDING_TCC.md)),
and none of the three grants implies another:

- **Dev runs from a terminal** (`scripts/dand`, `python -m dan.cli …
  daemon run`): the host is the terminal app / IDE hosting the shell.
- **launchd runs** (FAZA F2): the host is the binary launchd executes —
  the venv `python3` under the repo's `.venv`.

You need one grant per target you use: host → Terminal, host → iTerm2.

## 2. Granting

Unlike Accessibility, Automation has no "add app" list you can pre-fill:
macOS shows a consent dialog on the **first Apple Event** sent to a given
target app. So:

1. Start the target terminal app (the bridge never launches it for you).
2. Run the probe from §3 — expect the system dialog
   "… wants access to control Terminal/iTerm2 …" → **Allow**.
3. Review or revoke later under System Settings → Privacy & Security →
   **Automation** (host app → toggles per target).

Restart dand after changing grants (TCC applies to freshly started
processes).

**Known trap (D4 lesson, runbook §4 of SCREEN_RECORDING_TCC.md):** when
the host spawns subprocesses through a TCC-disclaimed helper, each child
can be its own TCC client — the dialog may name an unexpected app, or a
host-level grant may not preflight in a child. If the probe keeps failing
with `-1743` although the Automation toggle looks enabled, re-run the
probe from a plain terminal session and compare.

A second symptom of an undecided grant: the first Apple Event can **hang
on the pending consent dialog** instead of failing fast with `-1743`
(observed live during D5 development in a TCC-disclaimed session). The
bridge cuts this off cleanly after 15 s ("osascript did not run: …
timed out"); answer the dialog — or find who swallowed it — and re-run.

## 3. Verifying — the probe

```bash
.venv/bin/python -m dan.macos.terminal            # first running supported app
.venv/bin/python -m dan.macos.terminal iTerm2     # explicit target
```

- **exit 0** — granted; reads the target's front session and prints a
  sanitized JSON preview (line count + last lines). This is exactly what
  `terminal_read_screen` returns.
- **exit 2** — no supported terminal app running, or the Automation grant
  is missing/denied; the JSON `error`/`hint` says which.

## 4. What the grant does NOT change

- Paste never submits: `terminal_paste` writes the line without a newline
  and rejects control characters — pressing Enter stays with you. The check runs
  twice (in `dan/tools/terminal_tool.py` and again in the bridge), so the
  invariant never depends on the backend.
- Read output is clipped by `sanitize_terminal_snapshot` before it leaves the
  tool, and the pasted text is not echoed back in the tool output.
- Terminal output is treated as secret-bearing: redacted before it
  persists, never carried by the `/stream` websocket (ADR-019).

Nothing gates the grant once it exists: a model-originated
`terminal_read_screen` / `terminal_paste` simply runs against the target app
(`docs/SECURITY_MODEL.md` §2). Paste-never-submits is the guarantee that
actually holds. Grant deliberately.
