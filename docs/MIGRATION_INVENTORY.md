# Jarvis v4.1 — Migration Inventory (AUTHORITATIVE)

> **Status:** AUTHORITATIVE (Prompt 00B). Built from a read-only inspection of
> the old repo and the diagnostic archive
> `jarvis-diagnostic-20260630-194208.tar.gz`. The old repo was **not modified**;
> no old runtime code was copied. Runtime-level observations (processes, launchd,
> `/tmp`, audio) live in [LEGACY_RUNTIME_FINDINGS.md](LEGACY_RUNTIME_FINDINGS.md).
>
> **Reference repo (read-only, never modified):**
> `$HOME/Documents/dev/dan`
>
> Decisions are one of: **KEEP** (idea is sound, reimplement clean — no file
> copied), **REWRITE** (responsibility needed, implementation replaced),
> **DISCARD** (liability not carried forward), **REFERENCE-ONLY** (read for
> understanding / data, never imported).

---

## 1. Component migration matrix

| # | Component | Old path | Purpose | Decision | New target | Risk | Evidence (file:line) | Prompt |
|---|-----------|----------|---------|----------|-----------|------|----------------------|:------:|
| 1 | Voice broker | `tools/jarvis/voice_broker.py` (822 L) | Single audio conductor: FIFO+priority queue, mutex (one sound), plugin TTS engines, content anti-echo, `afplay` player, state→`/tmp` | **REWRITE** (concept KEEP) | `jarvis/voice` (`queue`, `broker`, `tts`) + `voice_queue` table | **HIGH** | `REPO=` L32; `PTT_FLAG="/tmp/dan-listen/PTT"` L48; `afplay` in `play_wav()` L529/L541; state.json writer | 16, 17 |
| 2 | Listener / STT | `tools/jarvis/listen_ozzy.py` (268 L) | sox/rec + MLX whisper (PL), PTT-gated capture, anti-echo + hallucination filter | **REWRITE** (concept KEEP) | `jarvis/voice` (`stt`, `vad`, `anti_echo`, `listening`) | **HIGH** | `sys.path.insert(0,"…/dan")` L30; raw gate `if not os.path.exists(PTT_FLAG)` L209 | 15, 17 |
| 3 | Orchestration loop | `tools/jarvis/auto_jarvis.py` (355 L) | Tail `ozzy.log` → route chat vs command → brain → stream sentences to broker | **REWRITE** | `jarvis/turns/orchestrator.py` | **HIGH** | hardcode L24, L27, L121; command heuristic can route to full-access brain | 10 |
| 4 | Brain (CLI driver) | `dan_core/cli_brain.py` (316 L) | claude/codex subprocess; `run_chat()` net-only, `run()` full-access agent | **REWRITE** (concept KEEP: stateless adapter) | `jarvis/brain` (`claude_cli_adapter`, `codex_cli_adapter`, `manager`) | **HIGH** | `run()` builds `--dangerously-skip-permissions` L61; `run_chat()` `--tools WebSearch WebFetch` L122; `--sandbox` ignored by `trust_level="trusted"` L23–24 | 11 |
| 5 | Memory | `dan_core/memory.py` (68 L) | `facts.txt` (injected every prompt) + `chat.jsonl` rolling log; no DB, no lock | **REWRITE** (concept KEEP) | `jarvis/memory` + `memory_blocks` table | MED | `LOG=STATE_DIR/chat.jsonl` L9; `FACTS=…/facts.txt` L10; `MAX_LOG_LINES=600` L12; no locking | 9 |
| 6 | Config | `dan_core/config.py` (1019 L) | `config.toml` + `overrides.json` truth-layering; secrets only via `.env` | **REWRITE** (concept KEEP precedence) | `jarvis/config.py` + `jarvis/paths.py` | MED | `DAN_HOME=Path(__file__).parent.parent` L308; "overrides wygrywają nad config.toml" L988; secret guard L744 | 2 |
| 7 | Persona | `dan_core/persona.py` (442 L) | Multiple personas + intensity levels; deliberately profane (user's creative choice) | **REFERENCE-ONLY** → re-express as config data | `config/persona/jarvis.md` | LOW | `LEVELS=[…]` L5; persona tables (explicit content **not reproduced**) | 1 |
| 8 | Toolbelt | `dan_core/tools/*` (`base`, `shell`, `files`, `web`, `repo`, `delegate`, `ops`, `mac`, `mem`, `projects`, `elevenlabs_tooling`, `agent_tasks`) | Agent tools with a `ctx.confirm` gate | **REWRITE** (concept KEEP) | `jarvis/tools` (`registry`, `permissions`, `shell_tool`, `file_tool`, `system_tool`) | **HIGH** | `class ToolContext` + `confirm` callback `base.py` L27–30; `BASELINE_RISKY` in `shell.py` | 12 |
| 9 | Panel (current) | `tools/jarvis/panel/dan_panel_web.py` (821 L) | Menu-bar WKWebView; reads broker `state.json`, writes `/tmp` control files, accumulates `chat.jsonl` in `/tmp` | **REWRITE** (concept KEEP shell) | `jarvis/panel` (thin client over daemon API) | **HIGH** | "panel pisze flagi (/tmp/dan-listen,/tmp/dan-voice)" L8; `broker_state()` reads `state.json` L143; `CHAT_LOG=VOICE+"/chat.jsonl"` L148; PTT read L298 | 18, 19, 20 |
| 10 | Panel (legacy dup) | `tools/jarvis/dan_panel.py` | Older panel; **creates/removes** PTT flag; `pkill afplay` | **DISCARD** | — | MED | `PTT_FLAG=LISTEN+"/PTT_ACTIVE"` L37; create/remove L572–576; `pkill -x afplay` L625/L635 | — |
| 11 | Direct TTS (pre-broker) | `dan_core/say.py`, `dan_core/voice.py` | Direct `afplay` playback before the broker existed | **DISCARD / AVOID** | broker player adapter only | **HIGH** | `afplay` `say.py` L288/L349/L581/L599; `voice.py` L119 | 16 |
| 12 | XTTS server | `tools/jarvis/xtts_server.py` | Removed TTS engine (Kazuhiko); still on disk | **DISCARD** | — | LOW | `afplay` L171; replaced by broker (XTTS cut) | — |
| 13 | launchd `com.ozzy.jarvis` | `com.ozzy.jarvis.plist` + `start-jarvis.sh` | Autostart listener + auto_jarvis (RunAtLoad+KeepAlive) | **DISCARD label**; REWRITE concept | `launchd/com.ozzy.jarvisd.plist.example` | **HIGH** | Label `com.ozzy.jarvis`; `/bin/zsh …/start-jarvis.sh` under `~/Documents` (TCC) | 21 |
| 14 | launchd `com.dan.voice-broker` | `com.dan.voice-broker.plist` + `start-voice-broker.sh` | Autostart broker + listener | **DISCARD** | — | **HIGH** | installed in `~/Library/LaunchAgents`; err thrash "can't open input file" (TCC) | 21, 8 |
| 15 | launchd `com.dan.xtts-server` | `com.dan.xtts-server.plist` + `start-xtts-server.sh` | Autostart XTTS server | **DISCARD** | — | LOW | deprecated; `XTTS_SPEAKER="Kazuhiko Atallah"` | — |
| 16 | `/tmp` transport surface | `/tmp/dan-voice/*`, `/tmp/dan-listen/*` | De-facto database: queue, state, control, transcripts, chat log, PTT flag | **REWRITE** to DB; `/tmp` reference/transport only | SQLite `~/.jarvis/jarvis.db` | **HIGH** | full surface enumerated in §3.2 / findings | 3–17 |
| 17 | Test: shell safety | `tests/test_shell_safety.py` (96 L) | `BASELINE_RISKY` patterns; deny→no-run, confirm→run | **REWRITE** (concept KEEP) | `tests/test_tool_permissions.py` | MED | baseline catches `rm -rf /`, `git push --force`, …; `_run` blocks without confirm | 12 |
| 18 | Test: tool confirmations | `tests/test_tool_confirmations.py` (101 L) | delegate/ops require confirm; `plan` (no edits) vs `apply` (gated) | **REWRITE** (concept KEEP) | `tests/test_tool_permissions.py`, worker tests | MED | confirm required L17–23; plan vs apply L41–78 | 12, 13 |

---

## 2. Concept → contract mapping (where ideas land)

| Old idea | v4.1 contract | Decision |
|----------|---------------|----------|
| "jeden dyrygent głosu" (broker mutex) | `VoiceRequest` + voice queue + sole broker | KEEP idea, REWRITE onto DB |
| PTT flag `/tmp/dan-listen/PTT` | `ListeningLease` (`hold`/`locked`, expiry) | REWRITE |
| `ozzy.log` transcript lines | `input.voice.transcribed` events + `Turn` | REWRITE |
| broker `state.json` | daemon `/state` + `Event` store | REWRITE |
| `chat.jsonl` (in `/tmp` and in `state/`) | `Conversation` + `turns` | REWRITE |
| `facts.txt` injected globally | `MemoryBlock` (enabled, budgeted) | REWRITE |
| `run_chat()` net-only chat | stateless brain adapter | KEEP idea, REWRITE |
| `run()` `--dangerously-skip-permissions` | tools via registry + approval | DISCARD unsafe path |
| `ctx.confirm` tool gate | `ApprovalGate` + `Approval` | KEEP idea, REWRITE |
| role "sessions" (DAN-głos/robot) | `WorkerJob` + speaker boundary | KEEP idea, REWRITE |
| persona text/levels | persona config file | REFERENCE-ONLY → config |
| launchd `com.*` autostarts | one `com.ozzy.jarvisd` + supervisor | DISCARD labels |

---

## 3. Explicit risk notes (verified)

Line numbers below are **verified** against the read-only repo during 00B.

### 3.1 Hardcoded `$HOME/Documents/dev/dan`

Confirmed in (non-exhaustive, source tree only — venv excluded):
`tools/jarvis/voice_broker.py:32`, `tools/jarvis/auto_jarvis.py:24,27,121`,
`tools/jarvis/listen_ozzy.py:30`, `tools/jarvis/dan_panel.py:29`,
`tools/jarvis/panel/gen_voices.py:19`, `tools/jarvis/panel/voices.json:36,40`,
`tools/jarvis/start-jarvis.sh:6`, `tools/jarvis/start-voice-broker.sh:21`,
`tools/jarvis/start-xtts-server.sh:5`, all three `*.plist` `ProgramArguments`,
`dan_core/tui_screens.py:459`, `tests/test_jarvis.py:12`.
**Note:** `dan_core/config.py:308` derives `DAN_HOME` from `__file__` (portable),
but the runtime scripts/plimports hardcode the absolute path.
**v4.1 rule:** no hardcoded repo path; paths from `jarvis/paths.py` + config (Prompt 02).

### 3.2 `/tmp/dan-*` as legacy transport

- `/tmp/dan-listen/`: `PTT` (gate flag), `SPEAKING` (anti-echo flag), `ozzy.log`
  (transcripts), `listen.out`, `spoken-recent.txt` (anti-echo ring), `threshold`.
- `/tmp/dan-voice/`: `state.json` (runtime truth), `req/*.json` (synth queue),
  `wav/*`, `backend`, `lang`, `voice_<engine>`, `volume`, `rate`, `exag`,
  `persona`, `chat.jsonl` + `chat.cutoff` (panel conversation log), `ready`,
  `broker.pid`, `broker.log`.
**v4.1 rule:** truth lives in the DB; `/tmp` is compatibility transport only
([ADR-008](DECISIONS.md#adr-008)).

### 3.3 Direct `afplay`

Scattered across modules (the opposite of "one speaker"):
`tools/jarvis/voice_broker.py:541` (broker's own `play_wav`),
`tools/jarvis/xtts_server.py:171`, `tools/jarvis/dan_panel.py:625,635` (`pkill afplay`),
`dan_core/say.py:288,349,581,599`, `dan_core/voice.py:119`,
plus `dan_core/__main__.py`, `tui.py`, `tui_screens.py`.
**v4.1 rule:** only the broker's player adapter calls a player; no `afplay`
elsewhere ([ADR-005](DECISIONS.md#adr-005)).

### 3.4 Panel-owned state / `/tmp` source-of-truth

The current panel (`dan_panel_web.py`) treats `/tmp` as runtime truth: it **reads**
broker `state.json` (L143) and **writes** control files (`volume` L105–112,
`persona` PERSONA_FILE), and **accumulates** the conversation into
`/tmp/dan-voice/chat.jsonl` (L148). Its own banner says "panel pisze flagi
(/tmp/dan-listen, /tmp/dan-voice)" (L8). There is no daemon, so `/tmp` *is* the
shared state the panel both reads and writes.
**v4.1 rule:** panel is a thin client over the daemon API; no `/tmp` canonical
reads/writes ([ADR-002](DECISIONS.md#adr-002), [PANEL_CONTRACT.md](PANEL_CONTRACT.md)).

### 3.5 Raw PTT file behavior

Listening is gated solely by the existence of `/tmp/dan-listen/PTT`
(`listen_ozzy.py:209`); the broker mirrors it (`voice_broker.py:48,109`); the old
panel `dan_panel.py` creates/removes the flag (L572–576). No expiry, no
hold-vs-lock distinction, lingers on crash.
**v4.1 rule:** `ListeningLease` with mode + expiry ([ADR-006](DECISIONS.md#adr-006)).

### 3.6 CLI brain full-access risk

`cli_brain.run()` invokes `claude -p --dangerously-skip-permissions` (L61):
Bash/Edit/Write run **without confirmation** ("świadoma decyzja"). A prior
`--sandbox workspace-write` attempt was **ignored** because `trust_level="trusted"`
(L23–24). The chat path `run_chat()` is restricted to `WebSearch WebFetch` (L122).
`auto_jarvis.py`'s command heuristic can route speech to the full-access `run()`.
**v4.1 rule:** brains are stateless and mute; any action goes through the tool
registry + approval gate, and v4.1 does **not** rely on provider sandbox flags
([ADR-003](DECISIONS.md#adr-003), [ADR-010](DECISIONS.md#adr-010),
[SECURITY_MODEL.md](SECURITY_MODEL.md)).

### 3.7 Old launchd labels

Three legacy labels exist as plists in the repo:
`com.ozzy.jarvis` (→ `start-jarvis.sh`), `com.dan.voice-broker`
(→ `start-voice-broker.sh`), `com.dan.xtts-server` (→ `start-xtts-server.sh`).
Only `com.dan.voice-broker.plist` is installed in `~/Library/LaunchAgents` (none
loaded at diagnostic time). The new official label is `com.ozzy.jarvisd` — note
the easy confusion with the old `com.ozzy.jarvis`.
**v4.1 rule:** one official label `com.ozzy.jarvisd`; legacy labels detected and
reported, never adopted or killed ([ADR-007](DECISIONS.md#adr-007),
[LAUNCH_SUPERVISION.md](LAUNCH_SUPERVISION.md)).

---

## 4. MUST NOT be copied directly into the new runtime

Hard list — none of these is imported/copied into `jarvis/`:

1. **Any file containing a hardcoded `$HOME/Documents/dev/dan`.**
2. **Runtime code:** `voice_broker.py`, `listen_ozzy.py`, `auto_jarvis.py`,
   `dan_panel.py`, `panel/dan_panel_web.py`, `xtts_server.py`,
   `dan_core/say.py`, `dan_core/voice.py`.
3. **`cli_brain.py`** — especially the `--dangerously-skip-permissions` path.
4. **Any `*.plist` or `start-*.sh`** — legacy labels + `~/Documents`/TCC paths.
5. **`persona.py` code** — re-express the persona as **config data**
   (`config/persona/jarvis.md`); do not import the module.
6. **Any `/tmp/dan-*` file** as a source of truth.
7. **Toolbelt modules** (`dan_core/tools/*`) — reimplement behind the v4.1
   registry; do not copy the confirmation plumbing verbatim.

Concepts are migrated; code is not. Tests are **re-derived** (same safety
guarantees), not copied.

---

## 5. Open items needing runtime evidence (UNKNOWN)

- Exact engine plugin interface for v4.1 voice (`supertonic`/`chatterbox`/`eleven`
  equivalents) — out of scope until real audio is enabled.
- Whether any consumer still depends on `/tmp` bridges (compat transport) during
  the migration window — to be decided per [ADR-008](DECISIONS.md#adr-008).

See [LEGACY_RUNTIME_FINDINGS.md](LEGACY_RUNTIME_FINDINGS.md) for the live runtime
picture that informed these classifications.
