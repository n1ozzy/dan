# DAN

One local voice-and-text runtime: the `dand` daemon (sole owner of audio,
the hotkey and the voice queue), the `dan` CLI and a menu bar panel.

Runtime rules:

- One DAN conversation. The brain is a single persistent `claude_cli` process (stream-json).
- DAN's identity comes exclusively from `config/persona/DAN.md` (the canon in
  this repo), loaded fresh and fail-loud. Conversation history and memory are
  context data, never persona instructions.
- TTS is Supertonic; tests always mock the TTS layer.
- Ownership details: `docs/adr/001-dand-single-owner.md` and
  `docs/CO-JEST-GDZIE.md`.

## Installation

```bash
git clone <repo> DAN && cd DAN
bash scripts/install.sh --no-launchd
```

The installer is backup-first: it creates `~/.dan/venv` and the `~/.dan/bin/dan`
and `~/.dan/bin/dand` wrappers, and stashes every path it replaces into
`~/.dan/backups/`. It does not touch `~/.dan/dan.db` or the archives. Autostart
via launchd is a separate, deliberate step:

```bash
bash scripts/install-launchd.sh --yes
```

## First start

1. Copy `config/dan.example.toml` to `~/.dan/config.toml` and review it
   (paths, port, brain, voice).
2. Start the daemon: via launchd (after `install-launchd.sh`) or manually
   with `~/.dan/bin/dand`.
3. Check health:

```bash
dan doctor --json
```

## Panel

The menu bar panel shows the state of the daemon, the voice broker, the queue
and the current utterance; it provides pause, resume, skip and a safe restart.
The panel resurrects nothing — when `dand` is down, the panel shows "offline"
and waits. Start: `scripts/dan-panel`. Details: `docs/PANEL.md`.

## Your first three commands

```bash
dan config explain
dan speak --as dan "Cześć, żyję i mówię po polsku."
dan queue list --json
```

## Operator documentation

- `docs/CO-JEST-GDZIE.md` — what lives where and who owns it;
- `docs/GLOS-I-KOLEJKA.md` — voice, queue, statuses and CLI examples;
- `docs/PANEL.md` — panel states and buttons;
- `docs/RADIO-DAN.md` — Radio status (Release 2);
- `docs/PRZENOSZENIE.md` — moving to another computer, Git vs private;
- `docs/ODZYSKIWANIE.md` — diagnostics and rollback.

Smoke runbooks (for developers):
`docs/runbooks/TEXT_RUNTIME_SMOKE.md`, `docs/runbooks/PROVIDER_SMOKE.md`,
`docs/runbooks/TOOLS_AND_APPROVALS.md`, `docs/runbooks/MEMORY_API.md`.
