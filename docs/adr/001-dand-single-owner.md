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

> **NOT IN EFFECT (2026-07-21).** The argv test never matches in production.
> `supertonic` is a console script with a shebang, so `ps` reports the Python
> interpreter path ahead of the arguments and `shlex.split(command)` is one
> token longer than `spec.argv`. The reclaim therefore never fires and the
> fail-dead described above is unchanged; the tests pass only because they feed
> synthetic argv. Fixing the comparison also arms the `ppid == 1` test, which on
> macOS is true of every launchd-managed process — the two must be fixed
> together. See `docs/reviews/2026-07-21-restart-orphan-shell-review.md` §1.

### Failed restart: containment decides whether exiting is safe

`POST /runtime/restart` drains through `DaemonApp.stop()` and exits 86. When
that drain raises, the daemon now asks the child supervisor for containment.
Containment **proven complete** exits 86 anyway; children left alive — or
containment unavailable — keeps the process here and marks it failed
(`RuntimeState.ERROR`), which drops `ok` in `snapshot_state()`.

The problem being addressed is real: blocking the exit unconditionally left a
mute daemon squatting the hotkey lock, unable to speak and unable to be
resurrected, while `dan state` and `dan doctor` still answered ok — so a dead
PTT read as a hotkey fault rather than a dead owner. Exiting is what frees the
hotkey `flock`, which the kernel releases with the process.

> **THE IMPLEMENTATION DOES NOT YET HOLD UP (2026-07-21).** Recorded here so the
> next reader does not mistake this section for a working guarantee. Details and
> fix order: `docs/reviews/2026-07-21-restart-orphan-shell-review.md` §6-§10.
>
> - **The premise is false.** This section originally claimed `stop()` drops
>   broker, engine and player before it can raise. Three of its four raise sites
>   fire earlier (`app.py:482` `close_intake`, `:483` `wait_for_drain`, `:552`
>   `_quiesce_voice_broker`), and a lease outliving the drain timeout is the most
>   likely failure of all. There the surviving process CAN still speak, and the
>   new branch `os._exit(86)`s it mid-turn.
> - **Containment is the wrong invariant.** `ChildContainmentResult` covers only
>   `ChildSupervisor` children. Exiting skips `_stop_hotkey_monitor()`,
>   `brain_manager.close()` and the recorder, so the Claude stream-json
>   subprocess (its own process group) and `sox` survive onto a live mic — two
>   owners, which is what this ADR exists to forbid.
> - **"Visible" is not yet enforced.** `mark_failed` swallows a failed ERROR
>   transition and leaves the state untouched, so the green-when-dead case
>   survives exactly when the event store is the thing that broke;
>   `ERROR -> IDLE` is an allowed transition, so a worker turn finishing clears
>   the flag; and the panel's only runtime-state readout (`#activityStrip`) is
>   force-hidden by `typewriter.js`, so ERROR is displayed nowhere.
>   `RuntimeStateMachine.force_idle` is the shape the fix should copy.

## Consequences

- The panel is a pure HTTP client: pause/resume/skip/restart are API calls;
  when the daemon is down, the panel shows "offline" and resurrects nothing.
- No legitimate second speech path exists (a direct player, a separate
  broker, a file feeder). Contract tests enforce a single player, a single
  daemon instance and the absence of `launchctl`/`pkill` in runtime code.
- A daemon failure stops voice entirely — deliberately: one visible missing
  owner is better than two owners at once. Making "visible" *enforced* rather
  than assumed is the intent of `mark_failed`; as of 2026-07-21 that
  enforcement is incomplete on three counts (see the box above), so a failed
  restart can still read as green.
