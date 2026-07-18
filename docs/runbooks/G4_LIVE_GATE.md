# G4-LIVE-GATE — first real microphone (Ozzy's gate)

Status: TO BE PASSED. This gate is passed by **Ozzy personally** — the assistant
does not run it (session decree 2026-07-02: voice disabled until Ozzy's decision).
This is the equivalent of Gate 6 from PRO (voice safety review) for the G4a–G4d scope.

Goal: confront with reality the values that until now were only tested on
mocks — the energy gate thresholds (`stt_min_*`), the junk filter, the sox
chain (`gain`/`highpass`), the anti-echo threshold, and live barge-in.

---

## 0. Preconditions (do not start without them)

- [ ] **Legacy DAN shut down manually** (MASTER_PLAN §4a, operational note;
      commands in `~/Desktop/Jarvis/JARVIS-NEXT-STEPS-FOR-OZZY.md` §5). Two systems
      will fight over the microphone and speaker — check
      `launchctl list | grep dan` and `GET /runtime/processes`.
- [ ] `pytest` green and **22/22 smoke** PASS on the HEAD you are starting from.
- [ ] In the real config (not smoke): `[voice] enabled=true`,
      `speak_responses=true`, `broker_enabled=true`, `default_tts="supertonic"`,
      `default_stt="mlx_whisper"`, `recorder="sox"`.
- [ ] Audio input = `Mikrofon (MacBook Air)` (pin_builtin_mic policy;
      a BT mic must be rejected/warned about — check `GET /audio/current`).
- [ ] Whisper model in the cache (`mlx-community/whisper-large-v3-turbo`),
      `supertonic` and `sox`/`play` in venv/PATH — the daemon will kill itself
      at startup anyway if anything is missing (fail-closed, this is expected).

## 1. Real transcription (I speak — DAN transcribes)

- [ ] PTT down (`POST /voice/ptt/down`) → say one full sentence in
      Polish → PTT up. Check the events for exactly **one**
      `input.voice.transcribed` with sensible `text`, `rms`, `voiced_seconds`.
- [ ] The transcript became **one** turn with `source="voice"` and DAN
      answered by voice (rows in `voice_queue` → `done`).
- [ ] The response starts playing quickly (target: first sound ≤ ~2 s from
      `brain.requested`; sentence streaming from G4d + the filler should deliver this).
- [ ] Whispering / quiet speech: if the gate cuts it off (`too_quiet` in the
      debug log), note the `rms` from the logs — that is input for the calibration in §3.

## 2. Silence test for the filters (hallucination firewall)

- [ ] PTT down → **say nothing** for 3–5 s → PTT up. There must be NO
      `input.voice.transcribed` (the energy gate cuts before whisper).
- [ ] PTT down → background noise (fan, keyboard) → PTT up. If something
      passes the gate, whisper usually hallucinates „Dziękuję."/variants —
      that must die on `stt_junk_phrases` (log `junk transcript dropped`).
      **Add new hallucinations to the list in the TOML** (the list = data, not code).
- [ ] Neither of the above created a turn or an entry in `voice_queue`.

## 3. Threshold calibration (stt_min_* + sox gain/highpass)

Turn one knob at a time; after each change: config → daemon restart
→ repeat §1 and §2. Starting values (today's defaults):

| Key | Default | When to move it |
|---|---|---|
| `stt_min_rms` | 300 | silence gets through → raise; whispering cut off → lower |
| `stt_min_voiced_seconds` | 0.3 | short „tak/nie" replies cut off → lower (carefully) |
| `stt_min_voiced_ratio` | 0.05 | a long silence with a single knock gets through → raise |
| `recorder_gain_db` | 0.0 | weak words getting lost → +6..+10 dB (§4a fact: gain BEFORE the future silence) |
| `recorder_highpass_hz` | 80 | buzzing/hum in the recording → leave/raise; male voice too thin → lower |

- [ ] After calibration: 5/5 sentences spoken in a normal voice → 5 transcripts;
      3/3 silence/noise attempts → 0 transcripts.
- [ ] Put the final values into the real TOML and **note them in the handoff**.

## 4. Live anti-echo (its own TTS echo ≠ a turn)

- [ ] Ask a question DAN will answer at length. While he is speaking,
      hold PTT next to the speaker so the microphone picks up **his own voice**.
      NO new turn may be created (an `input.voice.transcribed` event may
      appear — the anti-echo gate cuts further down the line; in the log:
      `transcript rejected as echo`).
- [ ] If echo gets through: raise `anti_echo_window_seconds` (default 30)
      or lower `anti_echo_overlap_threshold` (default 0.75).
- [ ] If YOUR real interjections get knocked down as echo (false positive,
      because you speak while TTS is playing and the transcript picks up a
      mix of voices): raise the threshold. This is a known trade-off of
      content anti-echo — calibrate it, do not redesign it.

## 5. Live barge-in (the 3 legs from VOICE_STREAMING §7)

- [ ] DAN is speaking a long answer → PTT and say **something different**
      from what he is saying. Expected, in this order: generation killed
      (if it was still running), queue rows `cancelled` (+ `voice.speak.cancelled`
      events), playback silenced immediately (kill of the `play` process),
      after which DAN picks up YOUR new topic as a new turn with `source="voice"`.
- [ ] Idempotence in practice: two quick barge-ins in a row do not crash
      the daemon and do not leave hanging audio.
- [ ] After a barge-in `GET /health` is ok; no orphaned `play`/`sox` processes
      (`pgrep -fl "play|sox"`).

## 6. Exiting the gate

- [ ] Final config values saved (TOML + a note in the handoff).
- [ ] New junk phrases added to `stt_junk_phrases`.
- [ ] Ozzy's verdict: G4 CLOSED / fixes (a list) — only after that is it
      allowed to enter G5 (Chatterbox voice-clone, MLX in a dedicated thread).

Diagnostic notes: daemon logs (categories `voice.stt`, `voice.gateway`,
`voice.cancellation`, `voice.broker`), events in the DB (`events`), queue state
(`voice_queue`). The `smoke-voice-*.sh` smokes remain mocked — they are not
part of this gate and they do not speak or record anything.
