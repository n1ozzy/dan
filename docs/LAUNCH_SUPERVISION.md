# Jarvis v4.1 — Launch & Supervision (FROZEN)

> **Status:** FROZEN (Prompt 00A). Defines launch identity, the
> `RuntimeSupervisor`'s observation duties, and the strict "detect, never kill"
> rule. **This build does not install launchd and does not start anything.**

---

## 1. One official identity

There is exactly **one** official launchd label for Jarvis:

```
com.ozzy.jarvisd
```

([ADR-007](DECISIONS.md#adr-007)). Anything else that looks like Jarvis is
**legacy** and is treated as a conflict to *report*, never to adopt or kill.

| Concern | Value |
|---------|-------|
| Official label | `com.ozzy.jarvisd` |
| Logs | `~/.jarvis/logs/jarvisd.log` |
| PID file | `~/.jarvis/runtime/jarvisd.pid` |
| Database | `~/.jarvis/jarvis.db` |

---

## 2. Launch modes

The `RuntimeSupervisor` (`jarvis/runtime/supervisor.py`, Prompt 08) detects how
`jarvisd` was started:

| Mode | Meaning |
|------|---------|
| `cli` | started by hand via `python -m jarvis.cli daemon run` |
| `launchd` | started by the official `com.ozzy.jarvisd` agent |
| `unknown` | cannot be determined |

This is recorded as a `RuntimeProcessObservation` (see
[CONTRACTS.md](CONTRACTS.md)) and surfaced in `/state` and `/runtime/processes`.

---

## 3. Legacy detection (report-only)

The supervisor watches for old `dan`-era labels and processes and raises
**warnings** — it never acts on them.

### Legacy launchd labels

- `com.ozzy.jarvis`
- `com.dan.voice-broker`
- `com.dan.xtts-server`

### Legacy processes

- `auto_jarvis.py`
- `listen_ozzy.py` (its listening loop)
- `voice_broker.py`
- `xtts_server.py`
- `dan_panel_web.py`

If any are present, the observation's `legacy_labels` / `legacy_processes` /
`warnings` are populated and shown to the human via the API and panel.

### 3.1 Diagnostic evidence (snapshot 2026-06-30)

A read-only diagnostic confirmed the supervisor's job is real (full detail in
[LEGACY_RUNTIME_FINDINGS.md](LEGACY_RUNTIME_FINDINGS.md)):

- **No DAN/Jarvis label was loaded** — `launchctl print` for `com.ozzy.jarvis`,
  `com.ozzy.jarvisd`, `com.dan.voice-broker`, `com.dan.xtts-server` all returned
  *"Could not find service"*. The future `com.ozzy.jarvisd` **does not exist yet**.
- **A legacy agent is installed but unloaded:** only
  `com.dan.voice-broker.plist` is present in `~/Library/LaunchAgents`.
- **The legacy voice stack was running, started by hand:** `voice_broker.py`
  (pid 89968), `listen_ozzy.py loop` (pid 3238), `auto_jarvis.py` (pid 92804).
- **TCC thrash on the legacy agent:** `/tmp/dan-voice-broker.err` is full of
  `/bin/zsh: can't open input file: …/start-voice-broker.sh` — launchd could not
  read the script under `~/Documents` ([ADR-014](DECISIONS.md#adr-014)).

So at takeover time the conflict is at the **process/device** level (legacy
broker + listener own the speaker and mic), not yet at the label level.

---

## 4. The hard rule: detect, never kill

- The supervisor **never kills a process automatically** — not a legacy one, not
  a conflicting one ([ADR-007](DECISIONS.md#adr-007)).
- Conflicts are **exposed as warnings** in `/state` and `/runtime/processes`.
- The human decides what to stop and when.

This is a deliberate inversion of the old setup, where multiple autostart agents
and processes could race for the speaker and the microphone. v4.1 surfaces the
race instead of fighting it blindly.

---

## 5. launchd lifecycle scripts (Prompt 21 — not run here)

When implemented, the launchd lifecycle ships as:

- `scripts/jarvisd` — daemon entry wrapper.
- `scripts/install-launchd.sh` — **prints exactly what it will do**; the human
  runs it deliberately. **It is never executed by the build.**
- `scripts/uninstall-launchd.sh` — unloads the agent but **does not delete the
  database**.
- `launchd/com.ozzy.jarvisd.plist.example` — the only official plist, using the
  official label and `~/.jarvis/logs`.

### Rules (FROZEN)

- **Do not run `install-launchd.sh`** as part of any automated step.
- **Official label only:** `com.ozzy.jarvisd`.
- **Logs go to `~/.jarvis/logs`.**
- **Script + logs live outside `~/Documents`** (under `~/.jarvis`) to avoid the
  macOS TCC trap that made the legacy `com.dan.voice-broker` agent thrash with
  *"can't open input file"* ([ADR-014](DECISIONS.md#adr-014)).
- **Uninstall unloads but never deletes the DB.**
- The install script **prints exactly what it will do** before doing anything.

CLI surface (Prompt 21): `python -m jarvis.cli daemon status | stop | restart`.

---

## 6. Summary of "do not" for this area

- Do not install or load any launchd agent automatically.
- Do not start the daemon, the broker, the listener or the panel as a side
  effect of a build step.
- Do not kill any process, legacy or otherwise.
- Do not reuse a legacy label for the new daemon.
