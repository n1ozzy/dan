# Jarvis v4.2 — Voice Sentence-Streaming Contract (G0)

Status: DESIGN (G0, docs-only). This document is the contract that G3 (TTS
broker + queue) and G4 (STT/anti-echo/barge-in) implement. It changes **no
runtime code and no schema**. It exists because the legacy requirement (§4a
of MASTER_PLAN) is hard: **first-sound ≤ ~2 s** for a spoken answer, while
the old DAN took 8–10 s by waiting for the full completion before speaking.

Decree anchors: TTS engines are **Supertonic + Chatterbox** (voice-clone),
with **edgeTTS, piper and XTTS banned** (MASTER_PLAN §7.3). STT is MLX
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
- jarvisd owns truth; adapters stay stateless; **only the broker speaks**
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

Claude CLI (`--output-format stream-json`) and Codex CLI streaming arrive
with G3/G4; G0 only fixes the shape they must fit.

## 3. SentenceChunker (jarvisd-owned, deterministic)

A pure, deterministic state machine living in jarvisd (module planned as
`jarvis/voice/chunker.py` in G3) — not in the adapter (adapters stay dumb
pipes) and not in the broker (the broker only plays what is queued):

- Input: a sequence of delta strings (or one final string). Output: a
  sequence of **sentence chunks**.
- Cut points: `.`, `!`, `?`, `…`, and hard newlines — followed by
  whitespace/end; a chunk is only emitted at `min_chars` (default 12) so a
  bare "Ok." does not fire the TTS pipeline for nothing.
- Abbreviation guard: a dotted token from a fixed Polish/English list
  ("np.", "tzn.", "itd.", "dr.", "mr.", "e.g.", "i.e.", …) is not a cut
  point. The list is config data, not code.
- `flush()` at end-of-stream emits whatever remains, cut or not.
- Determinism is a hard requirement: same input sequence → same chunks,
  because tests (G3) assert exact chunk boundaries.

## 4. Tool-call safety on a stream (fail-closed)

`<jarvis_tool_call>` blocks flow **inside** model text. On a stream they can
arrive split across deltas. The rule is **fail-closed hold**:

- The moment the chunker's buffer tail could be the beginning of
  `<jarvis_tool_call>` (any prefix of the tag), sentence emission **holds**:
  nothing from the suspicious point onward is emitted until the buffer
  resolves the suspicion (it was ordinary text) or completes the block.
- A completed tool-call block is **never spoken** and never lands in a
  sentence chunk. Text before the block is speakable; the block itself is
  handed to the existing tool-call parser path (which drives the approval
  loop exactly as today — nothing about approvals changes here).
- A turn that produced tool calls typically parks in `awaiting_approval`;
  speech for that turn simply stops at whatever was safely emitted. The
  continuation after execute-approved is a new brain call and streams again.

This mirrors, on the stream, the same source-of-truth rule as everywhere
else: the model proposes, jarvisd decides; a spoken tool block would be the
voice-track version of auto-execution and is therefore forbidden.

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

- Trigger: no first sentence chunk within `voice.filler_after_ms` (default
  1200) of `brain.requested` for a turn that will be spoken.
- Pool: `voice.fillers` config list (Polish, persona-neutral, e.g. "Już
  sprawdzam.", "Chwila."). Data, not code; Ozzy tunes it in TOML.
- Hard limits: **at most one** filler per turn; never after the first real
  sentence was queued; never when voice is disabled; never for worker jobs
  (workers are silent — ADR-009); never for turns that immediately park in
  `awaiting_approval` before the filler timer fires.
- A filler is a normal `VoiceRequest` with `metadata_json.kind="filler"`
  and `interrupt_policy` allowing the first real sentence to cut it off
  mid-playback rather than delay it.

## 7. Cancellation and barge-in (contract for G4)

Cancelling a spoken turn (user barge-in, panel stop, turn failure) is one
idempotent operation with three legs:

1. **Generation**: the adapter subprocess is terminated; pending deltas are
   discarded (they were never truth).
2. **Queue**: this turn's `queued` VoiceRequests flip to `cancelled` with
   `voice.speak.cancelled` events.
3. **Playback**: the broker stops the currently `speaking` request (same
   event); only the broker touches audio — cancellation never spawns a
   second speaker path.

Barge-in detection itself (mic-side) is G4 scope; G0 only fixes what
"cancel" means so G3 builds the queue with the right semantics from day one.

## 8. What G0 explicitly does NOT do

- No runtime code, no schema change, no new events beyond the frozen
  `voice.speak.*` family already reserved in CONTRACTS.
- No cockpit live-text rendering of deltas (would require an ADR-019
  amendment; deliberately out of scope for the voice track).
- No engine integration: Supertonic (first real engine) and Chatterbox
  (voice-clone) arrive in G3/G5; **banned engines stay banned** (edgeTTS,
  piper, XTTS — decree §7.3). Tests use a mock engine exclusively.
- No always-on listening; PTT/leases are G2 and unchanged by this design.

## 9. Consumption map

| Stage | Consumes from this contract |
|-------|------------------------------|
| G1 | nothing (devices); unaffected |
| G2 | nothing (leases); unaffected |
| G3 | SentenceChunker, sentence→VoiceRequest mapping, fillers policy, per-engine chunk preparation (§4a: next chunk synthesized while the previous one plays) |
| G4 | `on_delta` in CLI adapters, cancellation legs 1–3, barge-in trigger |
| G5 | nothing new; Chatterbox obeys the same queue/broker contract (MLX inference on its dedicated thread — §4a fact) |
