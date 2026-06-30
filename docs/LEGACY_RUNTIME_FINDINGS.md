# Jarvis v4.1 — Legacy Runtime Findings (DIAGNOSTIC-GROUNDED)

> **Status:** AUTHORITATIVE (Prompt 00B). Runtime picture of the old DAN/Jarvis,
> reconstructed **read-only** from
> `jarvis-diagnostic-20260630-194208.tar.gz` (snapshot **2026-06-30 19:42 CEST**,
> macOS 26.5.1 / Darwin 25.5.0 arm64, user uid 501) plus a read-only inspection
> of `/Users/n1_ozzy/Documents/dev/dan`.
>
> **Nothing was started, loaded, killed, or installed.** The archive was
> extracted only to a temporary scratch location; the source archive and the old
> repo were not modified.
>
> **Secret scan:** no API keys/tokens found. The only pattern hits were
> false positives from the vendored `xtts-venv` (package `RECORD` `sha256=`
> manifests, `cacert.pem`/`roots.pem`, the class name `OpenAIGPTTokenizer`) and a
> log line naming a TTS model. Nothing was redacted because nothing sensitive was
> present; no secret is reproduced in these docs.

---

## 1. How old DAN/Jarvis starts

Two start paths exist; both reference the old repo under `~/Documents`:

- **`start-jarvis.sh`** (label `com.ozzy.jarvis`): `cd` into the repo, remove
  `/tmp/dan-listen/SPEAKING`, launch `listen_ozzy.py loop` in the background and
  `auto_jarvis.py` in the foreground, with a `trap` to kill the listener on exit.
- **`start-voice-broker.sh`** (label `com.dan.voice-broker`): idempotent launcher
  (guards via `/tmp/dan-voice/broker.pid` + `kill -0`, and `pgrep -f
  'listen_ozzy.py loop'`) that starts the **broker** (from `xtts-venv`) and the
  **listener** (from `.venv`), creating `/tmp/dan-voice/{req,wav}` and
  `/tmp/dan-listen`.

At the diagnostic snapshot the live processes were started **manually**, not by
launchd (see §3, §2).

---

## 2. launchd labels / plists

| Label | Plist (in repo) | Installed in `~/Library/LaunchAgents`? | Loaded at snapshot? |
|-------|-----------------|----------------------------------------|---------------------|
| `com.ozzy.jarvis` | `tools/jarvis/com.ozzy.jarvis.plist` | No | No |
| `com.dan.voice-broker` | `tools/jarvis/com.dan.voice-broker.plist` | **Yes** (2705 B, 29 Jun 16:51) | No |
| `com.dan.xtts-server` | `tools/jarvis/com.dan.xtts-server.plist` | No | No |
| `com.ozzy.jarvisd` (future official) | — | No | No (**does not exist yet**) |

Evidence: `launchagents.txt` lists only `com.dan.voice-broker.plist`;
`launchctl list | egrep …` shows **no** DAN/Jarvis service (only Apple's
`com.apple.voicebankingd` / `com.apple.voicememod`); `launchctl print
gui/501/<label>` returns *"Could not find service"* for all four labels including
`com.ozzy.jarvisd`.

**TCC thrash evidence:** `/tmp/dan-voice-broker.err` is filled with hundreds of
`/bin/zsh: can't open input file: …/dan/tools/jarvis/start-voice-broker.sh` — i.e.
when the `com.dan.voice-broker` agent *was* loaded, launchd (KeepAlive) could not
read the script under `~/Documents` (macOS TCC sandbox), so it thrashed. This is
the concrete reason v4.1 keeps its launchd script/logs out of `~/Documents`.

The legacy `com.dan.voice-broker.plist` itself documents a **manual** conflict
rule: "enabling this, disable the old `com.dan.xtts-server.plist`, else two XTTS
servers fight over audio." v4.1 replaces manual conflict management with the
`RuntimeSupervisor` (detect + report).

---

## 3. Old processes found in diagnostics

`processes.txt` (snapshot) showed these **running**:

| PID | Process | Role |
|-----|---------|------|
| 89968 | `…/dan/tools/jarvis/voice_broker.py` | **broker** (matches `state.json.ready`) |
| 3238 | `…/dan/tools/jarvis/listen_ozzy.py loop` | **listener** (mic) |
| 92804 | `tools/jarvis/auto_jarvis.py` | **orchestration loop** |
| 67961 | `claude` | interactive Claude CLI |
| 51936, 39009 | `codex` | Codex CLI |

Not present: `dan_panel_web.py` / `dan_panel.py` (panel not running),
`xtts_server.py` (XTTS removed), `afplay`/`supertonic`/`chatterbox` (broker idle
at that instant). **Conclusion:** the legacy voice stack (broker + listener +
auto_jarvis) was alive and manually started — exactly the conflict set the
supervisor must detect before `jarvisd` takes over the mic and speaker.

---

## 4. `/tmp/dan-voice` and `/tmp/dan-listen`

### `/tmp/dan-listen/` (listening side)
- `PTT` — **the listening gate** (present ⇒ listening). **Absent at snapshot**,
  consistent with `state.json.ptt=false`.
- `SPEAKING` — legacy anti-echo flag.
- `ozzy.log` (29 KB) — appended STT transcripts (personal content; **not
  reproduced here**).
- `spoken-recent.txt` — anti-echo ring buffer (recently spoken text).
- `listen.out`, `panel.out`, `threshold` — logs / mic threshold.

### `/tmp/dan-voice/` (speaking side)
- `state.json` — **runtime truth** `{speaking, queue, ptt, ready, ts}` (read by
  panel + listener).
- `req/*.json` — synth request queue (clients write, broker drains).
- `wav/*` — temp audio buffers.
- `backend`, `lang`, `voice_<engine>`, `volume`, `rate`, `exag`, `persona` —
  control files written by the panel, read by broker/brain.
- `chat.jsonl` (36 KB) + `chat.cutoff` — the **panel-accumulated conversation**.
- `ready`, `broker.pid`, `broker.log` — process/health markers.

Both trees are volatile `/tmp` and serve as the system's de-facto database.

---

## 5. Source-of-truth violations (files/flags that must not be truth)

| Flag / file | Used as | v4.1 replacement |
|-------------|---------|------------------|
| `/tmp/dan-listen/PTT` | "is listening" gate | `ListeningLease` row ([ADR-006](DECISIONS.md#adr-006)) |
| `/tmp/dan-voice/state.json` | runtime state | daemon `/state` + `Event` store |
| `/tmp/dan-voice/req/*.json` | voice queue | `voice_queue` table |
| `/tmp/dan-voice/chat.jsonl` | conversation | `conversations` + `turns` |
| `/tmp/dan-voice/{persona,backend,volume,…}` | settings | `settings` table / API |
| `/tmp/dan-voice/ready`, `broker.pid` | liveness | daemon health + supervisor |

**Principle:** `/tmp` is transport, not memory ([ADR-008](DECISIONS.md#adr-008)).

---

## 6. Who can speak / listen / call the brain directly (old vs v4.1)

| Capability | Old DAN (direct) | v4.1 |
|------------|------------------|------|
| **Speak** (play audio) | broker (`afplay`), but also `say.py`, `voice.py`, `xtts_server.py`, panel (`pkill afplay`), hooks | **Only** the voice broker, via one player adapter ([ADR-005](DECISIONS.md#adr-005)) |
| **Listen** (mic) | `listen_ozzy.py` directly, gated by `/tmp` PTT flag | `jarvis/voice` STT under a `ListeningLease`, devices via `AudioDeviceManager` |
| **Call Claude/Codex** | `auto_jarvis.py` → `cli_brain.run/run_chat`; toolbelt `delegate`/`ops`; `run()` is full-access | Stateless brain adapters + worker broker; actions only via registry + approval ([ADR-003](DECISIONS.md#adr-003), [ADR-009](DECISIONS.md#adr-009)) |

---

## 7. Audio device facts (from diagnostics)

`audio-system-profiler.txt` (`system_profiler SPAudioDataType`):

| Device | Transport | Role at snapshot | Sample rate |
|--------|-----------|------------------|-------------|
| **Mikrofon (MacBook Air)** | Built-in | **Default Input Device** | 48000 |
| **Głośniki (MacBook Air)** | Built-in | Default System Output | 48000 |
| **Bose Revolve+ II SoundLink** | **Bluetooth** | **Default Output Device** (+ exposes a 16 kHz BT **input**) | 44100 / 16000 |

`defaults read com.apple.sound` was empty (no custom sound defaults).

**Implications for v4.1 audio policy ([ADR-012](DECISIONS.md#adr-012)):**
- The preferred input `Mikrofon (MacBook Air)` is exactly the current default
  input — the policy matches reality.
- A **Bluetooth** device (Bose) is the default *output* and also presents a
  16 kHz Bluetooth *microphone* — this is precisely the "bluetooth mic
  warns/disabled by default" case. The `AudioDeviceManager` must not silently
  capture from the BT mic.

---

## 8. Panel ownership violations

- The current panel **reads and writes `/tmp`** as truth: reads broker
  `state.json`, writes `volume`/`persona`/engine control files, and accumulates
  `chat.jsonl`. (Its own header: "panel pisze flagi (/tmp/dan-listen,
  /tmp/dan-voice)".)
- The old `dan_panel.py` even **owns the PTT flag** (creates/removes
  `/tmp/dan-listen/PTT_ACTIVE`).
- **v4.1:** the panel renders daemon truth and posts intents only; it owns no
  canonical state and performs no `/tmp` canonical I/O
  ([ADR-002](DECISIONS.md#adr-002), [PANEL_CONTRACT.md](PANEL_CONTRACT.md)).

---

## 9. Worker / tool permission risks

- **Full-access brain path:** `cli_brain.run()` uses
  `--dangerously-skip-permissions` (Bash/Edit/Write without confirmation); the
  only brake was the human pressing PTT. A `--sandbox` attempt was ignored
  (`trust_level="trusted"`).
- **Routing risk:** `auto_jarvis.py`'s heuristic can send a spoken sentence to
  that full-access path.
- **Confirmation model exists but is in-process:** `dan_core/tools/*` gate risky
  actions via `ctx.confirm` (verified by `test_shell_safety.py` /
  `test_tool_confirmations.py`), with `delegate`/`ops` plan-vs-apply modes — a
  good concept to migrate, but it lives inside the agent, not behind a daemon.
- **v4.1:** registry + `ApprovalGate`; rejected/blocked tool calls never execute;
  destructive blocked by default; secrets redacted; **no reliance on provider
  sandbox flags** ([ADR-010](DECISIONS.md#adr-010),
  [SECURITY_MODEL.md](SECURITY_MODEL.md)).

---

## 10. Likely conflicts with the future `com.ozzy.jarvisd`

1. **Label confusion:** old `com.ozzy.jarvis` vs new `com.ozzy.jarvisd` differ by
   one letter. The supervisor must match exactly and surface both.
2. **Installed legacy agent:** `com.dan.voice-broker.plist` is in
   `~/Library/LaunchAgents` and can be loaded at any login; if loaded it will
   contend for the speaker/mic with `jarvisd`.
3. **Device contention:** legacy `voice_broker.py` + `listen_ozzy.py` (seen
   running) own the speaker and mic; starting `jarvisd`'s audio while they run
   would double-capture / double-speak.
4. **`/tmp` ambiguity:** stale `/tmp/dan-voice/state.json`, `broker.pid`, `ready`
   could mislead any tool that still trusts `/tmp`.
5. **TCC trap:** any new agent must keep its script/logs out of `~/Documents`
   (use `~/.jarvis`) to avoid the "can't open input file" thrash seen for
   `com.dan.voice-broker`.

The `RuntimeSupervisor` (Prompt 08) records these as
`RuntimeProcessObservation`s and warns via `/state` and `/runtime/processes`.

---

## 11. DO NOT automate cleanup yet

This prompt is inventory only. **None of the following is performed now, and
none may be automated by later build steps without explicit human approval:**

- ❌ No `launchctl load` / `bootstrap` / `unload` of any label.
- ❌ No deletion of `~/Library/LaunchAgents/com.dan.voice-broker.plist`.
- ❌ No `kill`/`pkill` of `voice_broker.py`, `listen_ozzy.py`, `auto_jarvis.py`,
  or any other process.
- ❌ No deletion of `/tmp/dan-voice/*` or `/tmp/dan-listen/*`.
- ❌ No modification of the old `dan` repo.
- ❌ No starting of the broker, listener, panel, workers or TTS servers.

Cleanup helpers are **diagnose-and-print only** and arrive later (Prompt 24),
to be run manually by the human. The supervisor's job is to **detect and report**
conflicts first; the human decides what to stop and when
([ADR-007](DECISIONS.md#adr-007), [LAUNCH_SUPERVISION.md](LAUNCH_SUPERVISION.md)).
