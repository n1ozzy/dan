# Jarvis v4.1 — Migration Inventory (SKELETON)

> **Status:** SKELETON (Prompt 00A). This file is *created* here with its
> structure and the already-known risks. The **full classification matrix is
> completed in Prompt 00B** ("old repo inventory and migration matrix"). Line
> numbers below are **preliminary** (from an initial read of the reference repo)
> and are to be **verified** in 00B.
>
> **Reference repo (read-only, never modified):**
> `/Users/n1_ozzy/Documents/dev/dan`
>
> Nothing from the old repo is copied into the new runtime. This is a
> *conceptual* migration: keep ideas, rewrite implementations, drop liabilities.

---

## 1. Classification scheme (filled in by Prompt 00B)

Each old concept/file will be placed in exactly one bucket:

- **KEEP / MIGRATE CONCEPTUALLY** — the *idea* is good; reimplement it cleanly in
  a v4.1 module. No file copied.
- **REWRITE** — the responsibility is needed but the implementation is replaced
  (e.g. moved from `/tmp` to the DB, from a flag to a lease).
- **DELETE / AVOID** — a liability we deliberately do not carry forward.
- **UNKNOWN / NEEDS RUNTIME EVIDENCE** — cannot classify from source alone;
  requires observing the running system.

> The detailed per-file matrix lands here in 00B. Section 3 below seeds it with
> the risks that are already certain.

---

## 2. Conceptual map (old → v4.1 contract)

| Old `dan` concept | v4.1 contract | Disposition (preliminary) |
|-------------------|---------------|---------------------------|
| `voice_broker.py` "jeden dyrygent" (queue + mutex + plugin engines) | `VoiceRequest` + voice queue + broker | KEEP conceptually; REWRITE onto DB queue ([ADR-005](DECISIONS.md#adr-005)) |
| `/tmp/dan-voice/state.json` broker state | `Event` / daemon `/state` | REWRITE (state moves into the daemon) ([ADR-008](DECISIONS.md#adr-008)) |
| `/tmp/dan-voice/req/*.json` synth requests | `VoiceRequest` rows in `voice_queue` | REWRITE |
| `/tmp/dan-listen/PTT` flag | `ListeningLease` (`hold`/`locked`) | REWRITE ([ADR-006](DECISIONS.md#adr-006)) |
| `/tmp/dan-listen/ozzy.log` transcript log | `input.voice.transcribed` events | REWRITE |
| content anti-echo vs `spoken-recent.txt` | daemon-driven anti-echo policy | KEEP conceptually; REWRITE |
| `listen_ozzy.py` STT loop (sox + MLX whisper) | `jarvis/voice` STT/VAD (mocked first) | KEEP conceptually; REWRITE |
| `auto_jarvis.py` orchestration loop | `TurnOrchestrator` | REWRITE ([ADR-011](DECISIONS.md#adr-011)) |
| `cli_brain.run_chat()` (restricted chat) | stateless brain adapter | KEEP conceptually; REWRITE ([ADR-003](DECISIONS.md#adr-003)) |
| `cli_brain.run()` (`--dangerously-skip-permissions`) | tools via registry + approval | DELETE/AVOID the unsafe path ([ADR-010](DECISIONS.md#adr-010)) |
| `dan_core/memory.py` (chat.jsonl + facts.txt) | `MemoryBlock` + policies | KEEP conceptually; REWRITE |
| panel reading `/tmp` as truth | thin client over daemon API | REWRITE ([ADR-002](DECISIONS.md#adr-002)) |
| `dan_core/tools/*` + confirmation model | `ToolRegistry` + `ApprovalGate` | KEEP conceptually; REWRITE |
| role-based "sessions" (DAN-głos / DAN-robot) | `WorkerJob` + speaker boundary | KEEP conceptually; REWRITE ([ADR-009](DECISIONS.md#adr-009)) |
| `com.ozzy.jarvis` / `com.dan.*` launchd agents | one label `com.ozzy.jarvisd` + supervisor | DELETE/AVOID old labels ([ADR-007](DECISIONS.md#adr-007)) |
| XTTS server (`xtts_server.py`) | — (already removed in old repo) | DELETE/AVOID |

---

## 3. Known risks to carry into 00B (seeded)

These are **certain** and must appear in the 00B matrix with verified evidence.
Preliminary pointers from the initial read are noted; **verify line numbers in
00B**.

### 3.1 Hardcoded absolute path `/Users/n1_ozzy/Documents/dev/dan`

- Reported in: `tools/jarvis/voice_broker.py` (`REPO = …`, ~L32),
  `tools/jarvis/auto_jarvis.py` (~L27 and a persona string ~L122),
  `tools/jarvis/listen_ozzy.py` (`sys.path.insert(…)`, ~L30),
  `tools/jarvis/start-voice-broker.sh` (~L3), `*.plist` files, panel helpers.
- **v4.1 rule:** no hardcoded repo path; paths derive from `jarvis/paths.py`
  and config. (Prompt 02.)

### 3.2 `/tmp/dan-*` as legacy transport / state

- Listen side: `/tmp/dan-listen/{PTT, SPEAKING, ozzy.log, spoken-recent.txt,
  level}`.
- Voice side: `/tmp/dan-voice/{state.json, req/*.json, wav/*.wav, backend, lang,
  voice_<engine>, volume, rate, exag, persona, broker.log, broker.pid, ready}`.
- **v4.1 rule:** truth lives in the DB; `/tmp` is compatibility transport only
  ([ADR-008](DECISIONS.md#adr-008)).

### 3.3 Direct `afplay` occurrences

- Reported in: `tools/jarvis/voice_broker.py` (~L541),
  `tools/jarvis/auto_jarvis.py` (filler playback ~L83),
  `tools/jarvis/xtts_server.py` (~L171).
- **v4.1 rule:** only the broker's player adapter calls a player; no `afplay`
  elsewhere ([ADR-005](DECISIONS.md#adr-005)).

### 3.4 Panel depends on `/tmp` as source of truth

- The old panel reads `/tmp/dan-voice/state.json` and toggles
  `/tmp/dan-listen/PTT`, writes `/tmp/dan-voice/volume`. It does not store the
  conversation itself (it accumulates a `chat.jsonl` *log*), but it treats `/tmp`
  as the runtime truth because there is no daemon.
- **v4.1 rule:** panel is a thin client over the daemon API; no `/tmp` canonical
  reads ([ADR-002](DECISIONS.md#adr-002)).

### 3.5 Raw PTT file behavior

- `tools/jarvis/listen_ozzy.py` (~L209): listening iff `/tmp/dan-listen/PTT`
  exists. No expiry, no hold/lock distinction, can linger on crash.
- **v4.1 rule:** `ListeningLease` with mode + expiry
  ([ADR-006](DECISIONS.md#adr-006)).

### 3.6 CLI brain full-access risk

- `dan_core/cli_brain.py`: `run()` uses `--dangerously-skip-permissions`
  (full shell/file access); `run_chat()` is restricted (`--tools ""`).
  `auto_jarvis.py` could route to the full-access path via a command heuristic.
- **v4.1 rule:** brains are stateless and mute; any action goes through the tool
  registry + approval gate ([ADR-003](DECISIONS.md#adr-003),
  [ADR-010](DECISIONS.md#adr-010)).

### 3.7 Old launchd labels

- `com.ozzy.jarvis` (start-jarvis), `com.dan.voice-broker`,
  `com.dan.xtts-server` (deprecated).
- **v4.1 rule:** one official label `com.ozzy.jarvisd`; legacy labels are
  detected and reported, never adopted or killed
  ([ADR-007](DECISIONS.md#adr-007)).

### 3.8 Legacy entry points (for the supervisor to detect)

- `auto_jarvis.py`, `listen_ozzy.py`, `voice_broker.py`, `xtts_server.py`,
  `dan_panel_web.py`; brain entry `cli_brain.run_chat()` / `run()`.
- **v4.1 rule:** these are conflict signals surfaced by the `RuntimeSupervisor`
  (see [LAUNCH_SUPERVISION.md](LAUNCH_SUPERVISION.md)).

---

## 4. Out of scope for this file

- No old source is copied. This inventory references the old repo by path/line
  only.
- The authoritative per-file KEEP/REWRITE/DELETE/UNKNOWN matrix is produced in
  **Prompt 00B** and committed with `docs: inventory old DAN migration risks`.
