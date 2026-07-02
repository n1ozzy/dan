# Screen Recording (TCC) onboarding for `screen_read`

FAZA D4 ships the read-only screen tools (`screen_read_window`,
`screen_ocr_region`). The `native` backend captures with Apple's
`/usr/sbin/screencapture` and OCRs on-device with Vision; both work only
when the process hosting jarvisd holds the **Screen Recording** grant in
TCC. Without the grant nothing crashes: every capture fails cleanly with a
pointer to this runbook, and the daemon keeps running.

## 1. Which process needs the grant

TCC attributes the grant to the *responsible application*, not to the Python
interpreter path:

- **Dev runs from a terminal** (`scripts/jarvisd`, `python -m jarvis.cli …
  daemon run`): grant Screen Recording to the terminal app (iTerm2 /
  Terminal / the IDE hosting the shell).
- **launchd runs** (FAZA F2): grant Screen Recording to the binary launchd
  executes — the venv `python3` under the repo's `.venv`.

This is the same responsibility rule as the Accessibility grant
([ACCESSIBILITY_TCC.md](ACCESSIBILITY_TCC.md)); the two grants are separate
toggles and one never implies the other.

## 2. Granting

1. System Settings → Privacy & Security → **Screen & System Audio
   Recording** (older macOS: **Screen Recording**).
2. Add (or enable) the responsible app from §1.
3. Restart jarvisd (TCC grants apply to freshly started processes).

## 3. Verifying — the probe

```bash
.venv/bin/python -m jarvis.macos.screen
```

- **exit 0** — granted; captures the frontmost window, OCRs it and prints a
  sanitized JSON preview (line count + first lines). This is exactly what
  `screen_read_window` returns.
- **exit 2, `"screen_recording": false`** — the grant is missing for *this*
  process; re-check §1.

The probe's capture is a transient file in a private temp directory,
deleted right after OCR — the same lifecycle the daemon uses (ADR-020).

The OCR bridge alone (no TCC needed) can be exercised on any PNG:

```bash
.venv/bin/python -m jarvis.macos.screen --ocr /path/to/image.png
```

## 4. Revoking / troubleshooting

- Revoke: System Settings → Privacy & Security → Screen Recording → toggle
  off. Running daemons lose the capability on their next capture; reads
  start failing cleanly again.
- Reset for a clean re-prompt: `tccutil reset ScreenCapture` (all apps) —
  destructive to other grants, prefer the toggle.
- Smoke and tests never need TCC: `scripts/smoke-screen-read.sh` runs on
  the `fake` backend (`[security] screen_read_backend = "fake"`), which
  serves a deterministic fixture and announces itself via
  `"backend": "fake"` in every payload.

## 5. What D4 does *not* grant

The grant is process-wide, which is why the permission matrix stays in
charge: `screen_read` (narrow) is allow for user sources, approval for the
model, blocked for scheduled/hook sources. D4 captures only the frontmost
window or an explicitly named region — there is no full-display and no
continuous capture (the broad `screen_read` shape needs a new ADR). Captured
pixels never persist: the PNG is deleted right after OCR, only clipped OCR
text reaches tool_runs/events, where secret redaction applies, and the D3
event stream never carries it (ADR-019).
