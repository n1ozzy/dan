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

One process is exempt: a child of a **previous incarnation of this same
daemon**. A hard restart (`launchctl kickstart -k`) kills `dand` without
draining, and the child keeps listening, reparented to init. To the next
`dand` that looked like a foreign owner, so it refused to start at all and
voice stayed dead until someone killed the orphan by hand — fail-safe had
turned into fail-dead.

The supervisor therefore reclaims a port owner that satisfies **both**
conditions: its argv is identical to the `ChildSpec` argv, and its parent is
init. Together those say "the same program we launch, whose launcher is
gone". It is terminated (`SIGTERM`, then `SIGKILL`) and respawned. Anything
that fails either condition — a different port, a wrapper shell, a live
supervisor — still gets the refusal. The rule stays "never kill someone
else's process"; what changed is that our own orphan is no longer someone
else.

### Failed restart: containment decides whether exiting is safe

`POST /runtime/restart` drains through `DaemonApp.stop()` and exits 86. When
that drain raises, the teardown is already past the point of no return:
`stop()` drops broker, engine and player before either of its failure paths
raises, so the surviving process cannot speak, and intake is closed.

Blocking the exit guards against exactly one thing — launchd starting a
second daemon beside a child that is still alive. When containment is
**proven complete**, that danger is gone and the daemon exits 86 as usual.
Exiting is also what frees the hotkey `flock`, which the kernel releases with
the process; the next daemon needs it for PTT.

Blocking that case too is what this decision corrects. It left a mute daemon
squatting the hotkey lock, unable to speak and unable to be resurrected,
while `dan state` and `dan doctor` still answered ok — so a dead PTT read as
a hotkey fault rather than a dead owner.

Children left alive is the one case that keeps the process here. It then
reports itself failed (`RuntimeState.ERROR`), which drops `ok` in
`snapshot_state()` and therefore in `/health` and the panel.

## Consequences

- The panel is a pure HTTP client: pause/resume/skip/restart are API calls;
  when the daemon is down, the panel shows "offline" and resurrects nothing.
- No legitimate second speech path exists (a direct player, a separate
  broker, a file feeder). Contract tests enforce a single player, a single
  daemon instance and the absence of `launchctl`/`pkill` in runtime code.
- A daemon failure stops voice entirely — deliberately: one visible missing
  owner is better than two owners at once. "Visible" is enforced, not
  assumed: a daemon that survives a failed restart moves to `ERROR` and stops
  reporting `ok`.
