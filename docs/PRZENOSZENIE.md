# Moving DAN to another computer

## What is in Git (travels with the repo)

- all of the code (`dan/`, `scripts/`, `launchd/`, `integrations/`);
- the persona canon: `config/persona/DAN.md`;
- voice configuration: `config/voice/` — `personas.toml`, `pronunciations.toml`,
  `gains.json`, the pipelines and 20 versioned Supertonic custom styles
  with deterministic recipes and SHA-256;
- example configuration: `config/dan.example.toml`;
- documentation and tests.

## What is local/private (NEVER send it anywhere)

- `~/.dan/dan.db` — conversation, memory, queue, events; this is the owner's
  private history;
- `~/.dan/config.toml` — local configuration (ports, paths, devices);
- `~/.dan/logs/`, `~/.dan/backups/`, `~/.dan/migration/` — logs and backups;
- the base TTS/STT models — the installer fetches them at a pinned revision;
  they do not live in the repo;
- Lily's reference WAV and Żaneta's generated WAVs — **local-only**: not
  enough provenance for redistribution; the repo holds only the expected
  SHA-256 as metadata;
- API keys and secrets (audit: `scripts/dan-release-audit`).

## Asset licenses

The custom voice styles (`config/voice/custom_styles/`) are derivatives of the
Supertonic model (revision `724fb5ab…`) and ship under the **OpenRAIL-M**
license (`LICENSE-OpenRAIL-M.txt` + `NOTICE.txt` next to the files). The
Chatterbox V3 offline pipeline verifies the pinned source commit and the model
snapshot, with the network fallback disabled. Details:
`docs/migration/VOICE-DECISIONS.md`.

## Clean installation (e.g. a new Mac with an M5)

```bash
git clone <repo> DAN && cd DAN
bash scripts/install.sh --no-launchd
dan doctor --json
```

1. `install.sh` creates `~/.dan/venv` and the `~/.dan/bin/{dan,dand}` wrappers,
   runs the preflight (every local-only asset is explicitly explained,
   not hidden behind a fallback) and writes the installation manifest;
2. copy `config/dan.example.toml` → `~/.dan/config.toml`, review it;
3. deliberate autostart: `bash scripts/install-launchd.sh --yes`;
4. verification: `dan doctor --json` must come out clean on an empty `$HOME` —
   the product must not depend on caches or repositories from the old computer.

Uninstall is manifest-scoped: `scripts/uninstall.sh` (leaves `dan.db` and the
backups in place).
