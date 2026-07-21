# What is where

One ownership table. The rule: one value — one owner; everything that touches
audio, the hotkey and the voice queue belongs to `dand`
(`docs/adr/001-dand-single-owner.md`).

| Element | Owner | Path |
|---|---|---|
| `dand` daemon (audio, hotkey, queue, brain) | launchd (`KeepAlive`), label `com.dan.dand`, API `127.0.0.1:41741` | `~/.dan/bin/dand` (wrapper around `python -m dan.cli daemon run`) |
| TTS engine (`supertonic serve`, `127.0.0.1:7788`) | `dand` (supervised child — never a separate plist/serve) | binary per `~/.dan/config.toml` |
| Product database (conversation, memory, voice queue, events) | `dand` — **but not the sole writer, see ¹** | `~/.dan/dan.db` |
| Runtime configuration | operator (edits) **and `dand` (rewrites it, see ²)** | `~/.dan/config.toml` (template: `config/dan.example.toml` in the repo) |
| Daemon logs | `dand` rotates **one of them, see ³** | `~/.dan/logs/` |
| launchd plist | installer (`scripts/install-launchd.sh`) | `~/Library/LaunchAgents/com.dan.dand.plist` (template: `launchd/com.dan.dand.plist.example`) |
| Panel (menu bar) | launchd (`KeepAlive`), label `com.dan.panel`; only an HTTP client of the daemon | plist: `~/Library/LaunchAgents/com.dan.panel.plist` → `~/.dan/bin/dan-panel`; code: `dan/panel/` (legacy rumps widget + `com.dan.panels` plist quarantined 2026-07-21 → `~/.dan/quarantine-2026-07-21-menubar-cutover/`) |
| `dan` CLI | operator | `~/.dan/bin/dan` (wrapper around `python -m dan.cli`) |
| Product venv | installer (`scripts/install.sh`) | `~/.dan/venv/` |
| DAN persona canon | repo (versioned) | `config/persona/DAN.md` |
| Voice assets (personas, pronunciation, styles) | repo (versioned) | `config/voice/` |
| Runtime directory (`dand.pid`, `api-token`, `supervised-children.json`, locks e.g. `hotkey.lock`) | `dand` | `~/.dan/runtime/` |
| Installer backups and manifest | installer | `~/.dan/backups/`, `~/.dan/install-manifest.json` |
| Cutover/rollback journal | `scripts/dan-cutover` / `scripts/dan-rollback` | `~/.dan/migration/` |

### Three rows where "one value — one owner" is currently broken

Measured 2026-07-21; details in `docs/reviews/2026-07-21-docs-vs-code-audit.md`
C2–C5. These are defects against the rule at the top of this file, not
exceptions to it.

**¹** The database has a second writer that ships with the product: `dan memory
sync` and `dan db init` commit to `~/.dan/dan.db` from their own process,
concurrently with the live daemon.

**²** `dand` does not merely read that config file — a write through
`POST /settings` regenerates it from parsed data, so **the operator's comments
and formatting are lost**. Two owners, one file.

**³** Only `dand.log` is rotated. `dand.out.log`, `dand.err.log` and
`dand-console.log` have no owner and grow without bound — and the error log is
by far the largest of them.

What is deliberately **not** here: a voice broker separate from `dand`, request
files in temporary directories, a second audio player, a feeder reading playlist
files. The old paths were shut down in Release 1.
