# What is where

One ownership table. The rule: one value — one owner; everything that touches
audio, the hotkey and the voice queue belongs to `dand`
(`docs/adr/001-dand-single-owner.md`).

| Element | Owner | Path |
|---|---|---|
| `dand` daemon (audio, hotkey, queue, brain) | launchd (`KeepAlive`) | `~/.dan/bin/dand` (wrapper around `python -m dan.cli daemon run`) |
| TTS engine (`supertonic serve`, `127.0.0.1:7788`) | `dand` (supervised child — never a separate plist/serve) | binary per `~/.dan/config.toml` |
| Product database (conversation, memory, voice queue, events) | `dand` (sole writer) | `~/.dan/dan.db` |
| Runtime configuration | operator (edits), `dand` (reads) | `~/.dan/config.toml` (template: `config/dan.example.toml` in the repo) |
| Daemon logs | `dand` (rotates them itself) | `~/.dan/logs/` |
| launchd plist | installer (`scripts/install-launchd.sh`) | `~/Library/LaunchAgents/com.dan.dand.plist` (template: `launchd/com.dan.dand.plist.example`) |
| Panel (menu bar) | operator; only an HTTP client of the daemon | start: `scripts/dan-panel`; code: `dan/panel/` |
| `dan` CLI | operator | `~/.dan/bin/dan` (wrapper around `python -m dan.cli`) |
| Product venv | installer (`scripts/install.sh`) | `~/.dan/venv/` |
| DAN persona canon | repo (versioned) | `config/persona/DAN.md` |
| Voice assets (personas, pronunciation, styles) | repo (versioned) | `config/voice/` |
| Runtime directory (pid, locks, e.g. `hotkey.lock`) | `dand` | `~/.dan/runtime/` |
| Installer backups and manifest | installer | `~/.dan/backups/`, `~/.dan/install-manifest.json` |
| Cutover/rollback journal | `scripts/dan-cutover` / `scripts/dan-rollback` | `~/.dan/migration/` |

What is deliberately **not** here: a voice broker separate from `dand`, request
files in temporary directories, a second audio player, a feeder reading playlist
files. The old paths were shut down in Release 1.
