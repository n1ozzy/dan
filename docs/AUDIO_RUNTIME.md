# Jarvis v4.1 — Audio Runtime (FROZEN)

> **Status:** FROZEN (Prompt 00A). Defines listening, speaking, device
> ownership, anti-echo and barge-in. Field shapes are in
> [CONTRACTS.md](CONTRACTS.md). **No live voice is started by this build** —
> every component below is implemented and tested against mocks first.

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
- Releasing a **hold** lease **promptly requests the (mock) recorder to stop** —
  no lingering capture.
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
- Lifecycle: `VoiceRequest.status` moves `queued → speaking → done | cancelled |
  failed`; the daemon is `SPEAKING` while an item plays.

---

## 4. Anti-echo, STT, VAD (Prompt 17)

When Jarvis speaks, its own voice must not be transcribed back into a new turn.

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

- **Preferred input:** `Mikrofon (MacBook Air)`.
- **Output:** follows the system default.
- **Bluetooth microphone:** warns, or is disabled by default.
- A **mock implementation** backs the tests.
- State is captured as `AudioDeviceState` snapshots (`audio_device_snapshots`);
  voice/STT components must consult the manager rather than choosing devices
  themselves ([ADR-012](DECISIONS.md#adr-012)).

---

## 7. What is explicitly NOT done in this build

- No microphone is opened, no recorder is run, no TTS engine is loaded for real.
- No launchd autostart of any audio component.
- Real STT/TTS engine selection (supertonic / chatterbox / eleven equivalents)
  is deferred; the contracts here are engine-agnostic and validated with mocks.

Turning on real audio is a separate, deliberate step taken only after the queue,
leases, anti-echo and device policy pass with mocks.
