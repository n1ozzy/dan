# Jarvis v4.1 — Architecture Decision Records (FROZEN)

> **Status:** FROZEN (Prompt 00A). These twelve ADRs are the binding
> architectural decisions of Jarvis v4.1. Each is **Accepted**. Changing one
> requires superseding it with a new ADR, not editing it away.
>
> Format per ADR: **Context** (why this comes up) · **Decision** (what is fixed)
> · **Consequences** (what follows). Cross-references point at
> [CONTRACTS.md](CONTRACTS.md), [TURN_PIPELINE.md](TURN_PIPELINE.md),
> [AUDIO_RUNTIME.md](AUDIO_RUNTIME.md),
> [LAUNCH_SUPERVISION.md](LAUNCH_SUPERVISION.md),
> [SECURITY_MODEL.md](SECURITY_MODEL.md),
> [PANEL_CONTRACT.md](PANEL_CONTRACT.md).

---

## ADR-001 — `jarvisd` owns all truth

**Status:** Accepted

**Context.** The old `dan` system spread truth across `/tmp` files, in-memory
process state, and the panel. Restarts lost history; components disagreed.

**Decision.** A single local daemon, **`jarvisd`**, owns all state: conversation,
memory, events, history, voice queue, listening leases, audio snapshots,
approvals, tool runs and worker jobs. Every other component is a client.

**Consequences.** There is exactly one source of truth. Clients hold no
authoritative state. State survives restarts because it lives in the daemon's DB
([ADR-004](#adr-004)). See [PRODUCT.md](PRODUCT.md).

---

## ADR-002 — The panel is a thin client

**Status:** Accepted

**Context.** The old panel read `/tmp/dan-voice/state.json` and toggled
`/tmp/dan-listen/PTT` directly, because there was no daemon to ask.

**Decision.** The macOS panel only renders daemon state and sends intents
(`POST /input/text`, `/voice/ptt/*`, settings, approvals). It owns no canonical
state and, when the daemon is offline, shows an offline state.

**Consequences.** UI changes can never corrupt truth. The panel and any future
client are interchangeable views. See [PANEL_CONTRACT.md](PANEL_CONTRACT.md).

---

## ADR-003 — Brain adapters are stateless

**Status:** Accepted

**Context.** Provider CLIs (Claude, Codex) keep their own server-side sessions.
Treating those as memory makes Jarvis's context non-deterministic and
unportable.

**Decision.** A brain adapter is a stateless function
`BrainRequest → BrainResponse`. Jarvis assembles all context from its own DB +
config. The provider session is **not** Jarvis memory.

**Consequences.** Brains are swappable and testable (mock adapter by default).
The same DB state deterministically produces the same `BrainRequest`. Adapters
cannot speak, write memory, or touch the panel. See
[SECURITY_MODEL.md](SECURITY_MODEL.md) §5 and
[CONTRACTS.md](CONTRACTS.md) §4–§5.

---

## ADR-004 — The SQLite event store is the source of truth

**Status:** Accepted

**Context.** Append-only history is needed to reconstruct any turn and to debug
the system from one place.

**Decision.** State lives in SQLite at `~/.jarvis/jarvis.db`. The `events` table
is append-only and authoritative for history. Migrations are idempotent; an
existing DB is never destroyed.

**Consequences.** Any turn's lifecycle is reconstructable by filtering events on
`correlation_id`. Events are never mutated or deleted. See
[CONTRACTS.md](CONTRACTS.md) §2 and [TURN_PIPELINE.md](TURN_PIPELINE.md) §6.

---

## ADR-005 — The voice broker is the sole speaker

**Status:** Accepted

**Context.** Pre-broker `dan` had multiple components calling `afplay`
independently → overlapping audio, echo, hung queues ("brak jednego dyrygenta").

**Decision.** Exactly one component — the voice broker — plays audio. It drains
the persisted `voice_queue`. No worker, adapter, panel or hook ever calls a
player.

**Consequences.** No overlapping or duplicate speech. There is no direct
`afplay` anywhere outside the player adapter / test fixtures. See
[AUDIO_RUNTIME.md](AUDIO_RUNTIME.md).

---

## ADR-006 — PTT is a `ListeningLease`, not a file

**Status:** Accepted

**Context.** The old listener treated the existence of `/tmp/dan-listen/PTT` as
"is listening". A crashed process could leave the flag in either state, with no
expiry and no distinction between momentary and sticky listening.

**Decision.** Listening is governed by a `ListeningLease` row in the DB, with a
`hold` vs `locked` mode and an expiry. A button release clears a `hold` lease but
not a `locked` one; stale leases expire.

**Consequences.** Listening state is durable, inspectable and self-healing. No
raw `/tmp` flag is the source of truth. See [AUDIO_RUNTIME.md](AUDIO_RUNTIME.md)
§2 and [CONTRACTS.md](CONTRACTS.md) §8.

---

## ADR-007 — launchd has one official Jarvis label

**Status:** Accepted

**Context.** The old setup had several autostart agents (`com.ozzy.jarvis`,
`com.dan.voice-broker`, `com.dan.xtts-server`) that could race for the mic and
speaker.

**Decision.** There is exactly one official label: **`com.ozzy.jarvisd`**. The
`RuntimeSupervisor` detects legacy labels/processes and **reports** them. It
**never kills** anything automatically. Install scripts are never auto-run and
print exactly what they will do.

**Consequences.** Conflicts are surfaced, not silently fought. The human decides
what to stop. See [LAUNCH_SUPERVISION.md](LAUNCH_SUPERVISION.md) and
[CONTRACTS.md](CONTRACTS.md) §13.

---

## ADR-008 — `/tmp` is compatibility transport only

**Status:** Accepted

**Context.** `dan` used `/tmp/dan-*` for the listen log, PTT flag, voice
requests, broker state and control files — i.e. as its de-facto database. `/tmp`
is volatile and non-transactional.

**Decision.** `/tmp` may be used only as a compatibility transport to bridge to
legacy components if ever needed. It is **never** a source of truth. No pipeline
step reads `/tmp` for canonical state.

**Consequences.** Truth survives reboots and races. Bridges to `/tmp`, if any,
are explicitly second-class. See [CONTRACTS.md](CONTRACTS.md) cross-cutting
invariants.

---

## ADR-009 — Workers cannot speak or write memory facts directly

**Status:** Accepted

**Context.** A background worker that can talk or commit memory can act on the
world without a human in the loop and pollute long-term context.

**Decision.** A `WorkerJob` result is a **memory candidate**, never a fact and
never speech. Promotion to a committed `MemoryBlock` requires a human or an
explicit policy. Workers never enqueue a `VoiceRequest`.

**Consequences.** Workers advise; they do not act on the world. Memory stays
curated. See [SECURITY_MODEL.md](SECURITY_MODEL.md) §6 and
[CONTRACTS.md](CONTRACTS.md) §12.

---

## ADR-010 — Tools require a registry plus an approval policy

**Status:** Accepted

**Context.** The old command path ran with `--dangerously-skip-permissions`,
relying on push-to-talk as the only safety brake.

**Decision.** Every tool is registered with a permission class. Reads are
allowed; writes, shell and network require approval; destructive is blocked
unless explicitly enabled. A rejected/blocked `ToolCall` never executes. Secrets
are redacted in event payloads.

**Consequences.** No silent over-reach. Every executed tool leaves an auditable
`tool_run`. See [SECURITY_MODEL.md](SECURITY_MODEL.md) and
[CONTRACTS.md](CONTRACTS.md) §10–§11.

---

## ADR-011 — Panel text and voice transcript use the same `TurnOrchestrator`

**Status:** Accepted

**Context.** The old system had a separate voice loop (`auto_jarvis`) distinct
from any text path, so the two could (and did) drift.

**Decision.** Typed panel input and accepted voice transcripts enter the **same**
`TurnOrchestrator`, differing only in the turn's `source`. There is no separate
"voice brain".

**Consequences.** One pipeline, one event stream, one set of guarantees for both
modalities. Tests for the text turn also protect the voice turn. See
[TURN_PIPELINE.md](TURN_PIPELINE.md) §1, §4.

---

## ADR-012 — `AudioDeviceManager` owns input/output device state

**Status:** Accepted

**Context.** Scattered device handling led to wrong-mic capture and bluetooth
surprises.

**Decision.** A single `AudioDeviceManager` owns device selection and policy
(preferred input `Mikrofon (MacBook Air)`, output follows the system default,
bluetooth mic warns/disabled). Voice and STT code consult the manager; they never
choose devices themselves.

**Consequences.** Predictable capture and playback routing, captured as
`AudioDeviceState` snapshots. See [AUDIO_RUNTIME.md](AUDIO_RUNTIME.md) §6 and
[CONTRACTS.md](CONTRACTS.md) §9.

---

## Decision log

| ADR | Title | Status |
|-----|-------|--------|
| 001 | `jarvisd` owns all truth | Accepted |
| 002 | The panel is a thin client | Accepted |
| 003 | Brain adapters are stateless | Accepted |
| 004 | The SQLite event store is the source of truth | Accepted |
| 005 | The voice broker is the sole speaker | Accepted |
| 006 | PTT is a `ListeningLease`, not a file | Accepted |
| 007 | launchd has one official Jarvis label | Accepted |
| 008 | `/tmp` is compatibility transport only | Accepted |
| 009 | Workers cannot speak or write memory facts directly | Accepted |
| 010 | Tools require a registry plus an approval policy | Accepted |
| 011 | Panel text and voice transcript use the same `TurnOrchestrator` | Accepted |
| 012 | `AudioDeviceManager` owns input/output device state | Accepted |

> Migration-specific decisions discovered during the old-repo inventory
> (Prompt 00B) will be appended below this line as additional ADRs.
