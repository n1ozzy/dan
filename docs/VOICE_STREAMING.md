# DAN v4.2 — Voice Sentence-Streaming Contract (G0)

Status: **IMPLEMENTED** (verified against the code 2026-07-21). This started as
a docs-only G0 design; G3/G4 have since shipped it, so read it as a description
of how the running system behaves, not as a plan. The modules are
`dan/voice/chunker.py` (SentenceChunker), `dan/voice/speech.py`
(SpeechPipeline / SpeechStreamSession / FillerTimer), `dan/voice/queue.py`
(VoiceQueue) and `dan/voice/broker.py` (the only speaker). It exists because
the requirement (§4a of MASTER_PLAN) is hard: **first-sound ≤ ~2 s** for a
spoken answer, while the old DAN took 8–10 s by waiting for the full completion
before speaking.

Decree anchors: the live TTS engine is **Supertonic**; **Chatterbox** is still
reserved as a live engine (`RESERVED_ENGINES` in `dan/voice/tts.py`) and exists
only as an offline render pipeline. **edgeTTS, piper and XTTS are banned**
(MASTER_PLAN §7.3) — requesting one raises `BannedEngineError`. STT is MLX
whisper (§7.4). Tests use mock engines only.

---

## 1. The problem

A turn today is: `BrainRequest → adapter.generate() → BrainResponse.text →
turn.finished`. Everything downstream sees text only when the model is done.
For voice that means silence for the whole generation time. The fix is to
speak **sentence by sentence while the model is still generating**, without
breaking any frozen contract:

- `BrainResponse.text` stays **canonical** (CONTRACTS §5 already allows
  "may stream in deltas; final text is canonical" — G0 exploits that
  allowance instead of amending the contract).
- dand owns truth; adapters stay stateless; **only the broker speaks**
  (ADR-005). The model never gains any new authority.

## 2. Adapter surface: optional `on_delta`

`BrainAdapter.generate(request)` gains one **optional** keyword:

```python
def generate(self, request: BrainRequest, *, on_delta=None) -> BrainResponse
```

- `on_delta(text: str)` is called zero or more times with incremental text
  fragments, in order, on the adapter's worker thread.
- Calling it is **best effort and optional**: an adapter that cannot stream
  (mock today, any CLI without a streaming mode) simply never calls it and
  returns the full `BrainResponse` as before. **Every existing adapter keeps
  working unchanged** — degradation is: the chunker receives the final text
  in one piece and sentence-cuts it after the fact.
- The returned `BrainResponse.text` remains the single canonical answer.
  Downstream state (Turn.final_text, `brain.responded`) is built from it,
  **never** from a reassembly of deltas. If deltas and final text ever
  disagree, final text wins and the discrepancy is a bug in the adapter.
- Deltas carry **no authority**: no tool execution, no memory writes, no
  state transitions are ever driven by a delta.

State of the adapters (2026-07-21): the **Claude CLI adapter streams** — the
flags ride in `[brain.claude_cli].stream_args`
(`--output-format stream-json --verbose --include-partial-messages`). The
**Codex CLI adapter does not**: `CodexCliAdapter.supports_streaming = False`
and it accepts `on_delta` only to ignore it, which is exactly the documented
degradation — the final text is sentence-cut after the fact.

## 3. SentenceChunker (dand-owned, deterministic)

A pure, deterministic state machine living in dand (`dan/voice/chunker.py`) —
not in the adapter (adapters stay dumb pipes) and not in the broker (the broker
only plays what is queued):

- Input: a sequence of delta strings (or one final string). Output: a
  sequence of **sentence chunks**.
- Cut points: `.`, `!`, `?`, `…`, and hard newlines — followed by
  whitespace/end; a chunk is only emitted once it reaches the minimum length,
  so a bare "Ok." does not fire the TTS pipeline for nothing. The minimum is
  `[voice].min_sentence_chars` in the runtime config (the chunker's own
  `DEFAULT_MIN_CHARS` is only the fallback) — do not quote a number here.
- Abbreviation guard: a dotted token from a fixed Polish/English list is not a
  cut point. **The list lives in code**, as `ABBREVIATIONS` in
  `dan/voice/chunker.py` — it is not config data; extend it there when a new
  abbreviation misfires.
- `flush()` at end-of-stream emits whatever remains, cut or not.
- Determinism is a hard requirement: same input sequence → same chunks,
  because tests (G3) assert exact chunk boundaries.

## 4. Tool-call safety on a stream (fail-closed)

Canonical `<dan_tool_call>` blocks flow **inside** model text. The legacy
`<jarvis_tool_call>` form is accepted as input compatibility but is never
emitted. On a stream either form can arrive split across deltas. The rule is
**fail-closed hold**:

- The moment the chunker's buffer tail could be the beginning of
  either tool-call tag (any prefix of either tag), sentence emission **holds**:
  nothing from the suspicious point onward is emitted until the buffer
  resolves the suspicion (it was ordinary text) or completes the block.
- A completed tool-call block is **never spoken** and never lands in a
  sentence chunk. Text before the block is speakable; the block itself is
  handed to the existing tool-call parser path. The parser validates the
  payload, the registered tool remains authoritative, and raw JSON never
  reaches speech.
- A turn that produced tool calls stops speech at whatever was safely emitted.
  Direct tool execution and its continuation use the normal runtime path; no
  approval row or awaiting-approval turn is inserted.

This mirrors, on the stream, the same source-of-truth rule as everywhere
else: the model proposes, dand validates and executes; a spoken tool block
would leak control payload into the voice track and is therefore forbidden.

## 5. Sentence → VoiceRequest mapping (no schema change)

Each emitted sentence chunk becomes one `VoiceRequest` row in `voice_queue`
(CONTRACTS §7), enqueued immediately — this is what makes first-sound fast:

- `turn_id` = the producing turn; provenance is mandatory.
- `priority` = the normal turn-speech priority.
- `metadata_json.seq` = monotonically increasing per turn; the broker plays
  a turn's requests strictly in `seq` order. **No new columns**: the
  existing `voice_queue` schema carries everything (zero schema change;
  the schema/migrations guard test stays green through all of FAZA G).
- `metadata_json.kind` = `"sentence"` (vs `"filler"` below).
- Status lifecycle exactly as frozen: `queued → speaking → done | cancelled
  | failed`, events `voice.speak.queued/started/finished/cancelled`.

Deltas themselves are **not persisted**: no `brain.delta` event exists, the
EventStore audit trail records sentences only as `voice.speak.*` and the
final canonical text as `brain.responded`, exactly as today. The `/stream`
websocket (ADR-019) carries only DB events — so it never carries raw deltas
either. Partial tokens are transport, not truth (the same instinct as
"/tmp is transport, not memory").

## 6. Fillers policy

When generation is slow, silence is the enemy. Fillers are the compromise —
short canned utterances that buy time without pretending to be an answer:

- Trigger: no first sentence chunk within `voice.filler_after_ms` of
  `brain.requested` for a turn that will be spoken. The delay is a config
  value Ozzy tunes — it is deliberately not quoted here.
- Pool: the `voice.fillers` config list; the shipped fallback is
  `DEFAULT_VOICE_FILLERS` in `dan/config.py`. Data, not code; Ozzy tunes it in
  TOML. Selection rotates through the pool (`itertools.count`), it is not
  random.
- Hard limits: **at most one** filler per turn (`FillerTimer`: disarm wins if
  it arrives first); never after the first real sentence was queued; never when
  voice is disabled; never for worker jobs (workers are silent — ADR-009).
  (The old "never for turns that park in `awaiting_approval`" clause is gone
  with the approval gate — tool execution no longer parks a turn.)
- A filler is a normal `VoiceRequest` with `metadata_json.kind="filler"`
  and `interrupt_policy` allowing the first real sentence to cut it off
  mid-playback rather than delay it.

## 7. Cancellation and barge-in

Cancelling a spoken turn (user barge-in, panel stop, turn failure) is one
idempotent operation with three legs:

1. **Generation**: the adapter subprocess is terminated; pending deltas are
   discarded (they were never truth).
2. **Queue**: this turn's `queued` VoiceRequests flip to `cancelled` with
   `voice.speak.cancelled` events.
3. **Playback**: the broker stops the currently `speaking` request (same
   event); only the broker touches audio — cancellation never spawns a
   second speaker path.

All three legs are implemented. Which cancel also writes a tombstone (and
therefore blocks further enqueues under that id) is **not** uniform — a
generation turn id is tombstoned, a session/channel name is not. That
distinction is the one that used to mute named agent channels for an hour;
the current rules live in `docs/GLOS-I-KOLEJKA.md` ("Cancelling") and in
`dan/voice/queue.py`.

## 8. Boundaries this contract still holds

The original "G0 does not do any of this yet" list is obsolete — G3/G4 shipped.
What remains true as a *boundary*, verified 2026-07-21:

- **No schema change was needed and none was made.** `voice_queue` carries
  everything; the sentence/filler distinction rides in `metadata_json`. No
  event type outside the `voice.speak.*` family was added for streaming
  (`voice.speak.synthesis.started` / `.completed` are part of that family).
- **Deltas are still not persisted and not broadcast.** There is no
  `brain.delta` event and `/stream` carries only DB events.
- **No cockpit live-text rendering of deltas** (would require an ADR-019
  amendment; deliberately out of scope for the voice track).
- **Chatterbox is still not a live engine** — reserved in `dan/voice/tts.py`;
  it exists only as the offline render pipeline. **Banned engines stay banned**
  (edgeTTS, piper, XTTS — decree §7.3). Tests use a mock engine exclusively.
- **No always-on listening**; PTT/leases are unchanged by this design.
