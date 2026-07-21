# DAN — Launch & Supervision (FROZEN contract, values refreshed 2026-07-21)

> **Naming — Release 1 cutover (2026-07-18):** this document was written as
> "Jarvis v4.1"; `jarvisd` / `com.ozzy.jarvisd` = today's `dand` /
> `com.dan.dand`. The contract remains in force; the identities, paths and
> script names below were re-verified against the code on 2026-07-21.

> **Status:** FROZEN (Prompt 00A). Defines launch identity, the
> `RuntimeSupervisor`'s observation duties, and the strict "detect, never kill"
> rule. The launchd lifecycle **is implemented and installed** today (§5) — the
> old "this build does not install launchd and does not start anything" line was
> true only before Release 1 and has been removed.

---

## 1. One official identity

There is exactly **one** official launchd label:

```
com.dan.dand
```

([ADR-007](DECISIONS.md#adr-007)) — the same constant lives in code as
`dan/runtime/supervisor.py::OFFICIAL_LABEL`. Anything else that looks like DAN
is **legacy** and is treated as a conflict to *report*, never to adopt or kill.

Paths, logs, PID file, database and API port live in one place —
[CO-JEST-GDZIE.md](CO-JEST-GDZIE.md).

`KeepAlive` in the plist is **required**, not cosmetic: the restart contract
exits the process with code `86` (`dan/daemon/restart.py::RESTART_EXIT_CODE`)
and relies on launchd bringing the daemon back.

---

## 2. Launch modes

`RuntimeLaunchMode` (`dan/runtime/models.py`) defines `cli`, `launchd` and
`unknown`.

**Honest state of the code:** `RuntimeSupervisor.startup_snapshot()`
(`dan/runtime/supervisor.py`) records `RuntimeLaunchMode.CLI` unconditionally —
it does **not** detect launchd. Do not read `launch_mode` in `/runtime/startup`
as evidence of how the daemon was actually started. What the snapshot *does*
report truthfully is `official_label`, `official_plist_installed` and the legacy
observations below (`official_plist_loaded` is always the literal
`"not_checked"`).

---

## 3. Legacy detection (report-only)

The supervisor watches for pre-Release-1 labels, processes and temp artifacts
and raises **warnings** — it never acts on them.

Three families are detected, and the authoritative lists are the constants in
`dan/runtime/supervisor.py`: `LEGACY_LAUNCH_AGENTS` (retired launchd plists),
`LEGACY_PROCESS_PATTERNS` (the retired broker, panel and TTS-server processes
plus their start scripts) and `LEGACY_TEMP_ARTIFACTS` (the old state
directories under the system temp dir). Read the names there, not here: an
enumeration copied into prose drifts without anyone noticing, and this file
already declares the code to be the authority.

If any are present, `/runtime/processes`, `/runtime/startup` and
`/runtime/legacy` report them with a risk level and a warning; nothing is
cleaned up. The official plist, when installed, is reported too — as an
`info`-risk `official_dand_launch_agent` observation, not a conflict.

### 3.1 Diagnostic evidence

The one-off read-only diagnostic that motivated this section is a **dated
snapshot of 2026-06-30**, not a description of the machine today (by now
`com.dan.dand` is installed and loaded, and the legacy voice stack is gone).
Read it as history only: [LEGACY_RUNTIME_FINDINGS.md](LEGACY_RUNTIME_FINDINGS.md).
Its one durable lesson: launchd could not read scripts under `~/Documents`
(`can't open input file`), which is why the wrapper and logs live under `~/.dan`
([ADR-014](DECISIONS.md#adr-014)).

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

## 5. launchd lifecycle scripts (shipped)

The launchd lifecycle exists and is what the machine actually runs:

- `scripts/dand` — daemon entry wrapper (the installer generates a copy pinned
  to this repo at `~/.dan/bin/dand`, outside `~/Documents`).
- `scripts/install-launchd.sh` — **prints exactly what it will do** and exits;
  only `--yes` applies it. The human runs it deliberately. **It is never
  executed by the build.** It refuses to stack a second agent if the label is
  already loaded.
- `scripts/uninstall-launchd.sh` — unloads the agent but **does not delete the
  database**.
- `launchd/com.dan.dand.plist.example` — the only official plist, using the
  official label and `~/.dan/logs`. `__HOME__` is substituted at install time,
  so loading the example file directly fails loudly instead of silently.

### Rules (FROZEN)

- **Do not run `install-launchd.sh`** as part of any automated step.
- **Official label only:** `com.dan.dand`.
- **Logs go to `~/.dan/logs`.**
- **Wrapper + logs live outside `~/Documents`** (under `~/.dan`) to avoid the
  macOS TCC trap that made the legacy `com.dan.voice-broker` agent thrash with
  *"can't open input file"* ([ADR-014](DECISIONS.md#adr-014)).
- **`KeepAlive` stays `true`** — the restart contract depends on it.
- **Uninstall unloads but never deletes the DB.**
- The install script **prints exactly what it will do** before doing anything.

CLI surface (verified against `dan/cli.py`, 2026-07-21): the only daemon
subcommand is `python -m dan.cli daemon run` — there is **no**
`daemon status | stop | restart`. Status is `dan health` / `dan state` /
`dan doctor --json`; a supervised restart is `POST /runtime/restart` (drains,
exits `86`, launchd restarts); stopping is `launchctl bootout` or SIGTERM.

---

## 6. Summary of "do not" for this area

- Do not install or load any launchd agent automatically.
- Do not start the daemon, the broker, the listener or the panel as a side
  effect of a build step.
- Do not kill any process, legacy or otherwise.
- Do not reuse a legacy label for the new daemon.
