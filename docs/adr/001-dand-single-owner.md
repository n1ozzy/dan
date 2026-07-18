# ADR 001: `dand` is the sole owner of audio, the hotkey and the voice queue

Status: accepted (Release 1, product consolidation)

## Context

The old layout had multiple owners of the same value: a separate voice broker,
a bash feeder watching the playlist file, a panel calling `launchctl` and
`pkill` directly, a hotkey in a separate process and scripts playing WAVs
outright. The consequences: two players at once, requests lost between
processes, "repairs" by killing processes blindly and a state nobody could
reproduce.

## Decision

One value — one owner. The owner of **audio, the global PTT hotkey and the
durable voice queue** is exclusively the `dand` daemon:

- the voice broker runs inside the `dand` process; synthesis and playback do
  not exist outside it, and the broker takes exactly one item for playback
  at a time;
- every producer of speech (CLI, panel, hooks, skills, other agents) goes
  through the API/CLI (`dan speak`), never directly to the engine or the
  speaker;
- the queue is durable in SQLite in `~/.dan/dan.db`, whose only writer
  is `dand`.

### Restart: exit 86 + launchd `KeepAlive`

A safe restart (`POST /runtime/restart`) closes intake, drains the voice,
stops the children and **exits the process with `RESTART_EXIT_CODE = 86`**
(`dan/daemon/restart.py`). Resurrection is the platform's job: the
`com.dan.dand` plist has `KeepAlive = true`, so launchd puts the daemon
back up. Nobody — neither the daemon nor the panel — calls `launchctl` or
`pkill`. Code 86 distinguishes "a restart was requested" in the logs from a
crash and from a clean stop.

### Hotkey: exclusivity via `flock` on `hotkey.lock`

The global PTT monitor takes an exclusive, non-blocking `flock` on
`~/.dan/runtime/hotkey.lock` (`dan/input/macos_event_tap.py`). The lock is
on the open file description, so even a second monitor in the same process
cannot get around it. A missing lock or missing Accessibility permission is
a visible error, not a silent degradation.

### Ports: `ForeignPortOwnerError`

The child supervisor (`dan/daemon/supervisor.py`) checks the port owner
before starting a service. A port occupied by a process outside the `dand`
family raises `ForeignPortOwnerError` — the daemon **refuses** to start the
service instead of killing someone else's process or silently changing the
port.

## Consequences

- The panel is a pure HTTP client: pause/resume/skip/restart are API calls;
  when the daemon is down, the panel shows "offline" and resurrects nothing.
- No legitimate second speech path exists (a direct player, a separate
  broker, a file feeder). Contract tests enforce a single player, a single
  daemon instance and the absence of `launchctl`/`pkill` in runtime code.
- A daemon failure stops voice entirely — deliberately: one visible missing
  owner is better than two owners at once.
