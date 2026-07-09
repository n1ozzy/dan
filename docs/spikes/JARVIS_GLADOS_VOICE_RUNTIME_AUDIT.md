# JARVIS-GLaDOS Voice Runtime Audit

**Mode:** Read-only audit. No implementation, no commit.  
**Repo:** `/Users/n1_ozzy/Documents/dev/jarvis`  
**Branch:** `spike/jarvis-local-runtime-check`  
**Date:** Thu 2026-07-09 22:40 GMT+2  

## Top 10 Findings

1. **Jarvis uses energy-based VAD (G4b)** – `jarvis/voice/vad.py` implements an energy/RMS gate with configurable thresholds (voiced_rms_threshold, min_voiced_seconds, voiced_ratio). It processes entire captures post-hoc, not streaming chunks.  
2. **No pre-activation/pre-roll buffer** – Audio capture starts only when a lease is active (`ListeningLeaseManager` → `Recorder.start()`). No rolling buffer exists to capture pre-utterance audio.  
3. **Silence detection is lease-driven** – Silence (no speech) is not detected; captures end when leases expire (PTT release or lock timeout). No voice activity timeout within a capture.  
4. **Barge-in is mechanical audio cancel** – Interruption (PTT press during playback) stops audio via `broker.py`’s `interrupt()` (cancels TTS, clears queue) but does not clip or semantically mark the interrupted utterance as incomplete.  
5. **Priority lanes exist implicitly** – PTT (`hold`) and lock (`locked`) leases grant immediate microphone access; background tasks (e.g., proactive suggestions) would need a separate lease mode (not present).  
6. **TTS/playback queue is broker-managed** – `jarvis/voice/broker.py` serializes speech via a single `speaker` (Supertonic) and a FIFO queue; interruption clears the queue and cancels current TTS.  
7. **Chunker enables pseudo-streaming** – `jarvis/voice/chunker.py` splits LLM output into sentence-like chunks for incremental TTS, but audio playback is still chunk‑by‑chunk (not true streaming VAD on output).  
8. **Recorder is backend‑agnostic** – `jarvis/voice/recorder.py` abstracts `sox`/mock; audio policy (sample rate, highpass gain) lives in config, not the recorder.  
9. **Lease sweeper prevents stale leases** – `ListeningLeaseSweeper` periodically expires leases, preventing stuck recordings if a client crashes.  
10. **API routes expose runtime status** – `jarvis/voice/gateway.py` provides `/voice/status` (active leases, recorder state) and `/voice/metrics` (queue depth, TTS stats).  

## Top 5 Things Jarvis Already Does Better

1. **Lease-based arbitration** – Centralized, persistent (`listening_leases` table) arbitration via `ListeningLeaseManager` avoids race conditions; GLaDOS relies on in‑process flags.  
2. **Configurable VAD thresholds** – Energy‑based VAD (`CaptureGate`) is tunable via `config.yaml` (`stt_min_rms`, `stt_min_voiced_seconds`, `stt_min_voiced_ratio`) without code changes.  
3. **Persistence‑safe recorder** – Recorder writes to a private, mode‑0600 workdir and unlinks WAVs immediately after capture; no persistent artifacts.  
4. **Multi‑source lease arbitration** – Explicitly allows only PTT, global hotkey, and lock as sources (`ALLOWED_SOURCES`), preventing model/automation from hijacking the mic.  
5. **Chunked TTS pipeline** – `chunker.py` enables sentence‑level TTS streaming, reducing perceived latency; GLaDOS appears to use utterance‑level TTS.  

## Top 5 GLaDOS Concepts Worth Copying

1. **Streaming VAD with 32ms chunks** – Replace energy‑gate VAD with a streaming Silero VAD that processes 30ms PCM chunks, enabling real‑time voice activity detection without waiting for a full capture.  
2. **Pre‑activation (pre‑roll) buffer** – Maintain a rolling buffer (e.g., 500ms) of recent audio; when VAD triggers, prepend buffer to capture to avoid clipping utterance onset.  
3. **Silence‑based utterance termination** – End captures after N ms of silence (e.g., 800ms) detected via streaming VAD, rather than relying solely on lease expiry.  
4. **Semantic interruption handling** – On barge-in, mark the current TTS utterance as “interrupted” (clip audio, store partial transcript) and allow the LLM to generate a continuation or apology.  
5. **Explicit lane separation** – Introduce explicit lease modes: `user` (PTT/lock, high priority), `background` (scheduled tasks, low priority), and `autonomy` (proactive suggestions, medium priority) with priority‑based preemption.  

## Top 5 Things NOT to Port

1. **GLaDOS‑specific voice persona** – Jarvis uses Supertonic; do not replace TTS engine or attempt to mimic GLaDOS’s voice.  
2. **End‑to‑end neural VAD replacement if energy gate suffices** – Only replace VAD if streaming latency or accuracy demands it; otherwise, keep the deterministic, tunable energy gate.  
3. **Complex interruption state machine** – Keep interruption simple: stop current TTS, clear queue, and optionally signal the LLM to interrupt. Avoid over‑engineering semantic clip merging unless required.  
4. **Global audio bus architecture** – Jarvis’s broker‑mediated, lease‑gated recorder is sufficient; avoid replacing with a shared audio ringbus.  
5. **Hard‑coded VAD thresholds** – Keep VAD thresholds configurable via `config.yaml`; do not bake in Silero’s default thresholds without exposing them.  

## First 3 Implementation Tasks

1. **Add streaming VAD prototype** – Integrate Silero VAD (via `torch.hub.load`) in `jarvis/voice/vad.py` as an optional backend, processing 30ms chunks from the recorder’s audio stream (requires modifying `SoxRecorder` to yield chunks).  
2. **Implement pre‑roll buffer** – Add a ring buffer (e.g., `collections.deque`) in `Recorder` or a new `PreRollBuffer` wrapper that holds the last N ms of audio and prefixes it to captures when VAD triggers.  
3. **Add silence‑based capture termination** – Modify `ListeningLeaseManager` to accept a `voice_activity_timeout_ms` config; when streaming VAD reports silence for the threshold, automatically release the lease (equivalent to PTT release).  

## Verification

- `git status --short --branch` shows we are on `spike/jarvis-local-runtime-check` with no changes.  
- `git diff --check` reports no whitespace errors.  

**Report:** File created at `docs/spikes/JARVIS_GLADOS_VOICE_RUNTIME_AUDIT.md`.  
**Next Step:** Review the audit with the human; no code changes made.  
**Commit:** None (read‑only audit).