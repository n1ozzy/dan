# DAN — Audio Runtime (FROZEN contract, values refreshed 2026-07-21)

> **Naming:** written as "Jarvis v4.1"; every `jarvis*` name below is today's
> `dan*` (Release 1 cutover, 2026-07-18).

> **Status:** FROZEN (Prompt 00A). Defines listening, speaking, device
> ownership, anti-echo and barge-in. Field shapes are in
> [CONTRACTS.md](CONTRACTS.md).
>
> **Voice is LIVE.** The line that used to stand here — "no live voice is
> started by this build" — was true only before the G-gates shipped and has been
> removed. Today `dand` runs the real stack: sox recorder, MLX whisper STT,
> Supertonic TTS (with `supertonic serve` as a supervised child), real playback.
> Mocks remain the *test* doubles, not the runtime (see AGENTS.md: tests must
> never spawn real audio).

---

## 1. Principles

1. **One speaker.** The **voice broker** is the *only* component that plays
   audio. Nothing else — not a worker, not an adapter, not the panel, not a hook
   — ever calls a player ([ADR-005](DECISIONS.md#adr-005)).
2. **One listening contract.** Listening is governed by a `ListeningLease` row
   in the DB, never by the presence of a file ([ADR-006](DECISIONS.md#adr-006)).
3. **One device owner.** The `AudioDeviceManager` owns input/output device
   state; voice and STT code asks it, never the OS directly
   ([ADR-012](DECISIONS.md#adr-012)).
4. **The queue is persisted.** A `VoiceRequest` lives in `voice_queue` and
   survives a restart; pending speech is recovered, not lost.

This is the lesson of the old `dan` broker — "jeden dyrygent głosu" (one
conductor) — made authoritative by moving the queue and state into the daemon DB
instead of `/tmp`.

---

## 2. Listening — `ListeningLease` (Prompt 15)

Push-to-talk and sticky-listen are both **leases**, not flags.

| Endpoint | Effect |
|----------|--------|
| `POST /voice/ptt/down` | create a **hold** lease (button pressed) |
| `POST /voice/ptt/up` | release the **hold** lease (button released) |
| `POST /voice/listen/lock` | create a **locked** lease (sticky listening) |
| `POST /voice/listen/unlock` | release the **locked** lease |
| `GET /voice/listening` | report the current lease state |

### Rules (FROZEN)

- A **hold** lease is created on PTT-down and released on PTT-up.
- A **locked** lease is **not** cleared by a button release — only by an explicit
  unlock (or expiry).
- A **stale** lease **expires** (`expires_at`) instead of listening forever.
- Releasing a **hold** lease **promptly requests the recorder to stop** — no
  lingering capture. (The live recorder backend is `sox`; `mock` is the test
  double. Which one runs is `[voice].recorder` in the runtime config.)
- There is **no raw `/tmp` flag** that means "is listening"
  ([ADR-006](DECISIONS.md#adr-006), [ADR-008](DECISIONS.md#adr-008)).

When a lease is active the daemon is in `LISTENING`; captured audio moves it to
`TRANSCRIBING`.

---

## 3. Speaking — voice queue + TTS broker (Prompt 16)

```
producer (e.g. a finished turn) ──► enqueue VoiceRequest ──► voice_queue (DB)
                                                                  │
                                                                  ▼
                                                          voice broker (sole)
                                                          ┌──────────────────┐
                                                          │ drain queue       │
                                                          │ one item at a time│
                                                          │ synth via TTS     │
                                                          │ play via player    │
                                                          └──────────────────┘
                                                                  │
                                                                  ▼
                                                               speaker
```

### Rules (FROZEN)

- **Only the broker plays speech.**
- The queue is **persisted in the DB**; queued items are **recovered after a
  restart**.
- An **interrupt policy** controls cancellation (see barge-in below).
- TTS is **mocked in tests** — no real synthesis required to validate the queue.
- Lifecycle: `VoiceRequest.status` moves
  `queued → synthesizing → speaking → done | cancelled | failed`; the daemon is
  `SPEAKING` while an item plays. The `synthesizing` step is enforced in SQL —
  a row cannot jump from `queued` to `speaking`
  (`voice_queue_status_transition` trigger, `dan/store/schema.sql`).

---

## 4. Anti-echo, STT, VAD (Prompt 17)

When DAN speaks, his own voice must not be transcribed back into a new turn.

- **Content anti-echo:** a transcript that is similar to recently-spoken TTS is
  **filtered** (not turned into input). This mirrors the old content-based
  anti-echo (compare against recently spoken text) but driven by daemon state,
  not a `/tmp/spoken-recent.txt` flag.
- **Garbage filter:** short/garbage acknowledgements are dropped by policy
  (STT hallucination guard).
- **Accepted transcripts only** become `input.voice.transcribed` and flow into
  the **same** `TurnOrchestrator` ([ADR-011](DECISIONS.md#adr-011)).
- **No direct player calls** outside the player adapter / test fixtures
  (no `afplay` scattered through the code — [ADR-005](DECISIONS.md#adr-005)).

---

## 5. Barge-in (Prompt 17)

```
SPEAKING ──► (real speech detected during playback, under policy)
        ──► cancel current VoiceRequest  (status=cancelled)
        ──► emit voice.speak.cancelled   (frozen)
        ──► state INTERRUPTED ──► resolve
```

The interrupt policy decides what counts as a real barge-in (so the system is
not cancelled by its own echo or a stray noise). Cancellation is explicit and
event-logged; there is never silent duplicate or overlapping playback.

---

## 6. Devices — `AudioDeviceManager` (Prompt 14)

| Endpoint | Effect |
|----------|--------|
| `GET /audio/devices` | list available devices |
| `GET /audio/current` | current input/output selection |
| `POST /audio/select` | select a device (through the manager) |

### Policy (FROZEN)

- **Input policy:** pin the built-in mic. Which device that is comes from
  `[audio].input_policy` / `[audio].preferred_input` in the runtime config —
  not from this document.
- **Output:** follows the system default (`[audio].output_policy`).
- **Bluetooth microphone:** warns, and is disabled by default
  (`[audio].allow_bluetooth_microphone = false`).
- A **mock implementation** backs the tests.
- State is captured as `AudioDeviceState` snapshots (`audio_device_snapshots`);
  voice/STT components must consult the manager rather than choosing devices
  themselves ([ADR-012](DECISIONS.md#adr-012)).

### Why the Bluetooth rule exists

The concrete case that produced it: a Bluetooth speaker was the default
*output* while also exposing a low-rate (16 kHz) Bluetooth *microphone*, so a
naive "follow the default device" would have captured from it. The
`AudioDeviceManager` must not silently do that. The full device listing behind
this is a **dated 2026-06-30 diagnostic** and no longer describes the machine —
see [LEGACY_RUNTIME_FINDINGS.md](LEGACY_RUNTIME_FINDINGS.md) §7 for that
snapshot; for what is connected now, ask the daemon (`GET /audio/devices`).

---

## 7. What is real today (2026-07-21) and what still is not

Real and running:

- **TTS:** Supertonic is the live engine (`dan/voice/tts.py`), plus a warm
  `supertonic serve` HTTP server that `dand` supervises as a child so the model
  is not reloaded per chunk; the CLI path is the fallback. A missing engine,
  voice or asset **fails the request** — there is no silent fallback.
- **STT:** MLX whisper. **Recorder:** sox. Both selected in the runtime config.
- **Autostart:** the launchd agent `com.dan.dand` starts `dand`, and `dand`
  owns the audio devices, the queue and the hotkey. There is no separate
  launchd job for a broker or a TTS server — a second one would be a legacy
  conflict (see LAUNCH_SUPERVISION.md §3).

Still not implemented:

- **Chatterbox as a live engine** — reserved, not wired
  (`RESERVED_ENGINES` in `dan/voice/tts.py`); asking for it is an explicit
  error. The Chatterbox V3 work that does exist is an **offline** render
  pipeline (`dan/voice/pipelines/`), never an automatic live engine.
- **edgeTTS, piper, XTTS** — banned by decree; requesting one raises
  `BannedEngineError`.
- **Streaming VAD (`silero_vad`)** — the config surface exists, the engine in
  use is the energy gate (`[voice].vad_engine`).
