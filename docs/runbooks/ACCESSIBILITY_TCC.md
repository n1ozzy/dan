# Accessibility (TCC) onboarding for `ui_read` / `ui_act`

FAZA D1 ships the read-only Accessibility tools (`ui_active_app`,
`ui_read_window`); FAZA D2 adds the action tools (`ui_click`, `ui_type`,
`ui_focus_app`) — one and the same **Accessibility** grant covers both.
The real `ax` backend talks to AXUIElement and works only
when the process hosting dand holds the Accessibility grant in TCC.
Without the grant nothing crashes: every read fails cleanly with a pointer to
this runbook, and the daemon keeps running.

## 1. Which process needs the grant

TCC attributes the grant to the *responsible application*, not to the Python
interpreter path:

- **Dev runs from a terminal** (`scripts/dand`, `python -m dan.cli …
  daemon run`): grant Accessibility to the terminal app (iTerm2 / Terminal /
  the IDE hosting the shell).
- **launchd runs** (FAZA F2): grant Accessibility to the binary launchd
  executes — the venv `python3` under the repo's `.venv`. macOS will show the
  prompt for it after the first denied AX call; you can also add it manually.

Per **ADR-014** all runtime artifacts (logs, pid, api-token, DB by default)
live under `~/.dan`, never `~/Documents` — the launchd agent must not
trip the `~/Documents` TCC sandbox on top of the Accessibility grant.

## 2. Granting

1. System Settings → Privacy & Security → **Accessibility**.
2. Add (or enable) the responsible app from §1. Use the `+` button and pick
   the app/binary if it is not listed yet.
3. Restart dand (TCC grants apply to freshly started processes).

## 3. Verifying — the probe

```bash
.venv/bin/python -m dan.macos.accessibility
```

- **exit 0** — trusted; prints a sanitized JSON snapshot of the frontmost
  app and its focused window. This is exactly what `ui_read_window` returns.
- **exit 2, `"trusted": false`** — the grant is missing for *this* process;
  re-check §1 (a grant given to iTerm2 does not cover a launchd-spawned
  python and vice versa).

The probe is read-only and sanitized the same way as the tools: secure text
field values are never printed.

## 4. Revoking / troubleshooting

- Revoke: System Settings → Privacy & Security → Accessibility → toggle off.
  Running daemons lose the capability on their next AX call; reads start
  failing cleanly again.
- Reset for a clean re-prompt: `tccutil reset Accessibility` (all apps) —
  destructive to other grants, prefer the toggle.
- Smoke and tests never need TCC: `scripts/smoke-ui-read.sh` runs on the
  `fake` backend (`[security] ui_read_backend = "fake"`), which serves a
  deterministic fixture and announces itself via `"backend": "fake"` in every
  payload.

## 5. What D1 does *not* grant

The grant is process-wide, which is why the permission matrix stays in
charge: `ui_read` is allow for user sources, approval for the model, blocked
for scheduled/hook sources, and secure text fields are stripped at the tool
layer for every source. UI **actions** (`ui_act`, D2) always cross
ApprovalGate — every click or keystroke needs an explicit approve + execute,
typing into secure text fields is refused outright, and auto sources are
blocked (ADR-018).
