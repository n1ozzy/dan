# Persistent Claude Session Implementation Plan

> **For agentic workers:** Execute inline with strict red-green-refactor cycles. Do not commit or push this task.

**Goal:** Replace the production cold Claude CLI transport with one durable, serialized stream-json process/session while preserving Jarvis-owned conversation, persona, tool, and memory truth.

**Architecture:** `ClaudeCliAdapter` remains the only production adapter and owns a persistent transport. It checkpoints only resumable execution state to a mode-0600 atomic JSON file under the runtime directory; the checkpoint is never treated as Jarvis memory. The existing orchestrator continues to execute model-originated tools once and sends their durable result continuation through the same adapter/session.

**Tech Stack:** Python 3, `subprocess.Popen`, JSONL stream protocol, `threading`, `queue`, atomic `os.replace`, pytest fake processes.

## Global Constraints

- Exactly one production adapter name: `claude_cli`.
- No real Claude, TTS, audio, broker, cloud cache, approval, worker, commit, or push activity.
- Fresh canonical `$HOME/Documents/dev/dan/config/persona/DAN.md` is the real system prompt at initial bootstrap and every recovery.
- Subsequent healthy-session messages contain only the new input or tool-result continuation.
- Context actions occur at exact inclusive thresholds 70, 80, and 90 percent and re-arm only below 70 percent.

### Task 1: Persistent transport and checkpoint policy

**Files:**
- Create: `tests/test_brain_cli_persistent_session.py`
- Modify: `jarvis/brain/claude_cli_adapter.py`

- [ ] Write fake-process tests for one spawn/two generations, incremental payloads, exact bootstrap persona, state mode, and close.
- [ ] Run the focused tests and record RED.
- [ ] Implement the minimal persistent JSONL transport and state store.
- [ ] Run the focused tests and record GREEN.
- [ ] Add RED/GREEN tests for crash-resume, corrupt-resume fresh rebuild, timeout, cancellation, and stderr drain.
- [ ] Add RED/GREEN boundary tests below/at 70, 80, and 90 percent.

### Task 2: Manager, daemon, and orchestrator lifecycle

**Files:**
- Modify: `jarvis/brain/manager.py`
- Modify: `jarvis/daemon/app.py`
- Modify: `jarvis/turns/orchestrator.py`
- Modify: `jarvis/api/routes_runtime.py`
- Test: `tests/test_brain_cli_persistent_session.py`
- Test: `tests/test_tool_result_continuation.py`
- Test: `tests/test_api_smoke.py`

- [ ] Write RED tests proving text-only generation uses the persistent stream, tool continuation stays on the same session, stop closes it, and runtime state is redacted.
- [ ] Wire runtime state path, lazy start/close, no-op deltas, and safe runtime projection.
- [ ] Run focused GREEN tests.

### Task 3: Configuration and stale contracts

**Files:**
- Modify: `jarvis/config.py`
- Modify: `config/jarvis.example.toml`
- Modify: `tests/test_config.py`
- Modify: `tests/test_brain_cli_adapters.py`
- Modify: `tests/test_brain_cli_streaming.py`
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `docs/STATUS.md`

- [ ] Write RED config tests for context window and thresholds.
- [ ] Add validated defaults and pass them into the adapter.
- [ ] Replace stale cold/stateless assertions and documentation.
- [ ] Run focused brain/config/tool/API tests, then report unrelated baseline failures separately.
