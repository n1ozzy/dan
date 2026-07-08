# Jarvis Codebase Audit — Issues to Fix

**Audit date:** 2026-07-08  
**HEAD:** `7fbba95 sync`  
**Branch:** `main` (diverged from Memory OS branch `rescue/audt-gpt5.5pro-limit-cdn`)

This audit covers the entire Jarvis v4.2 codebase. Issues found by reading all source files, running tests, and cross-referencing with docs (`FIXME.md`, `STATUS.md`, `PROJECT_RULES.md`, `DECISIONS.md`).

---

## 🔴 CRITICAL (Security / Data Loss / Crash)

### C-01: Tests Cannot Run Without External CLI Tools
**Files:** `jarvis/brain/manager.py:133`, `tests/conftest.py`  
**Problem:** `BrainManager.from_config()` requires at least one brain adapter (Claude CLI, Codex CLI, or GROQ_API_KEY). Tests fail with `BrainManagerError` if none available. The `conftest.py` mocks only work for `routes_runtime` module, not for `BrainManager.from_config()` which calls `detect_all_providers()` directly.  
**Impact:** Entire test suite blocked (CORS, voice, tools, API tests all fail at fixture setup).  
**Fix:** Make `BrainManager.from_config()` accept a `mock_adapters` parameter or auto-register mock adapter in test mode. Or fix `conftest.py` to mock `auto_detect.detect_all_providers()`.

### C-02: ThreadLocalConnection — Potential Connection Leak
**Files:** `jarvis/store/db.py:87-176`  
**Problem:** `ThreadLocalConnection` creates new connections per thread but relies on `close_current_thread()` being called. If a thread dies without calling it, connection stays in `self._all` and leaks. `_all` list grows unbounded.  
**Impact:** Connection exhaustion, "database is locked" errors, memory leak.  
**Fix:** Add weakref-based cleanup or periodic sweep of dead threads. Consider using `threading.excepthook` to catch thread death.

### C-03: VoiceBroker — Uncaught Exception in `_run()` Loop Swallows Errors
**Files:** `jarvis/voice/broker.py:72-89`  
**Problem:** `_run()` catches `Exception` broadly, logs, backs off, retries. But if `_claim()` returns `None` (empty queue), loop exits `drain_all()` returning 0, then `_run()` waits `poll_interval`. However, if `_with_queue()` or `queue.recover_orphans()` throws, the outer try/except catches it but `backoff` logic may mask repeated failures.  
**Impact:** Silent degradation — broker appears alive but not processing queue.  
**Fix:** Add metrics/counter for consecutive failures; alert after N failures. Distinguish transient (DB lock) vs permanent (config error) failures.

### C-04: CancellationCoordinator — Race Between Tombstone and Queue Cancel
**Files:** `jarvis/voice/cancellation.py:103-116`  
**Problem:** `cancel_active_speech()` calls `_registry.cancel_all()` → `_tombstone_turns(generation_turn_ids)` → `_cancel_queued()` → `_tombstone_turns(queue_turn_ids)`. But between `cancel_all()` and `_tombstone_turns()`, a streaming adapter could register a new handle for same turn_id (if generation already started new turn). The new handle won't be cancelled.  
**Impact:** Late delta from cancelled generation could enqueue voice row.  
**Fix:** Tombstone BEFORE cancelling generation (already partially done in FIX-09 but race window exists). Use atomic "cancel + tombstone" transaction.

### C-05: MemoryCompiler — No Protection Against Malformed `memory_id` in `_project_memory_id`
**Files:** `jarvis/memory/compiler.py:345-347`  
**Problem:** `_project_memory_id()` uses `hashlib.sha256(memory_id.encode())`. If `memory_id` contains null bytes or is extremely long, hashing works but downstream may break. No validation.  
**Impact:** Potential DoS via crafted memory_id (unlikely but possible).  
**Fix:** Validate `memory_id` format (UUID-like) before hashing.

---

## 🟠 HIGH (Bugs / Incorrect Behavior)

### H-01: Config Loads `jarvis.example.toml` Instead of `jarvis.toml` as Default
**Files:** `jarvis/config.py:456-459`, `tests/test_config.py:146`  
**Problem:** `_select_config_path()` prefers `config/jarvis.example.toml` over `config/jarvis.toml` when neither env var nor explicit path given. Example config has `default_adapter = "claude_cli"` and production fillers, but test expects `"mock"`.  
**Impact:** Tests fail; production may load example config accidentally.  
**Fix:** Change priority: explicit path → `JARVIS_CONFIG` env → `config/jarvis.toml` → `config/jarvis.example.toml`. Update test to match.

### H-02: Voice Fillers Mismatch Between Code and Example Config
**Files:** `jarvis/config.py:31-89`, `config/jarvis.example.toml:59-65`, `tests/test_config.py:301`  
**Problem:** `DEFAULT_VOICE_FILLERS` in code (53 Polish/DAN fillers) ≠ `fillers` in example.toml (6 polite fillers). Test expects code's fillers.  
**Impact:** Test fails; inconsistent behavior depending on config source.  
**Fix:** Decide: keep DAN fillers as default in code, or move all to config. Update test accordingly.

### H-03: `ALLOWED_CORS_ORIGINS` Missing Panel Origin
**Files:** `jarvis/daemon/lifecycle.py:109`, `docs/FIXME.md` (FIX-16)  
**Problem:** `ALLOWED_CORS_ORIGINS = {"http://127.0.0.1:41800", "http://localhost:41800"}`. Panel loads via `file://` (Origin: `null`). FIX-16 fixed panel by adding `allowUniversalAccessFromFileURLs` to WKWebView, but CORS still rejects `null` origin for ANY file:// page.  
**Impact:** Any file://-loaded UI (not just panel) blocked.  
**Fix:** Either add explicit panel URL to CORS, or handle `file://` origin specially (already done in panel but not daemon).

### H-04: `ToolRegistry.request_tool()` — Approval Gate Not Used for `auto_approve_mode="all"`
**Files:** `jarvis/tools/registry.py:139-192`  
**Problem:** `request_tool()` always checks `permission.decision == APPROVAL_REQUIRED` and calls `approval_gate.create_approval()`. No code path for `auto_approve_mode="all"` to skip approval.  
**Impact:** Production unrestricted plan cannot work without code change.  
**Fix:** Add `if permission_policy.auto_approve_mode == "all" and permission.decision != BLOCKED: return execute_tool(request)` before approval logic.

### H-05: `ToolPermissionPolicy.decide()` — `auto_approve_mode="all"` Not Fully Implemented
**Files:** `jarvis/tools/permissions.py:251-282`  
**Problem:** `auto_approve_mode == "all"` only allows MODEL_ORIGINATED for mutation tools. It doesn't handle VOICE_COMMAND, PANEL_COMMAND, or DIRECT_USER_COMMAND uniformly. Also `voice_auto_approve` is separate flag.  
**Impact:** Inconsistent auto-approve behavior across sources.  
**Fix:** Refactor: if `auto_approve_mode == "all"` and source in USER_SOURCES ∪ {MODEL_ORIGINATED}, return ALLOW for all non-destructive tools.

### H-06: ContextBuilder Compiled Memory Gate Over-Complex
**Files:** `jarvis/brain/context_builder.py:337-357`  
**Problem:** `_resolve_compiled_memory_enabled()` has 5 layers: config flag, force_disabled, request override, global enabled, scope_gate + session_profiles. Session/profile allowlist is internal-only per docs but code enforces it.  
**Impact:** Compiled memory effectively disabled unless allowlist populated (which nothing does).  
**Fix:** Simplify to: `return config.memory.enabled and config.memory.compiled_context_enabled and not force_disabled` (plus optional override).

### H-07: `file_read` Tool — No Size Limit Enforcement at Policy Level
**Files:** `jarvis/tools/file_tool.py:27-28`, `jarvis/tools/permissions.py:316-359`  
**Problem:** `DEFAULT_MAX_BYTES = 262_144`, `HARD_MAX_BYTES = 1_048_576` in file_tool, but permission policy doesn't enforce size. Model can request 1MB file, tool reads it, but tool_runs/events store truncated (4096 chars). Model gets full content, durable store gets preview.  
**Impact:** Memory pressure if many large files read; secret redaction on 1MB text slow.  
**Fix:** Add `max_bytes` to permission policy check; reject at policy level if > threshold.

### H-08: `shell_read` — Whitelist Bypass via Command Substitution
**Files:** `jarvis/tools/shell_tool.py:27-41`, `jarvis/tools/shell_tool.py:108-112`  
**Problem:** Whitelist matches exact normalized command. But `git status --short` whitelisted; `git status --short && evil` NOT whitelisted (good). However, `git status --short; evil` also not whitelisted. BUT: if whitelist has `ls`, then `ls $(evil)` passes normalization (`ls $(evil)` → `ls $(evil)` not in whitelist).  
**Impact:** Low (exact match required), but command substitution in args could bypass if not careful.  
**Fix:** Ensure whitelist entries have no shell metacharacters; validate args separately.

### H-09: VoiceBroker — `prefetched` Future Not Cancelled on Stop
**Files:** `jarvis/voice/broker.py:98-122`, `jarvis/voice/broker.py:60-70`  
**Problem:** `drain_all()` submits synthesis to executor (`prefetched = executor.submit(...)`). If `stop()` called while `prefetched` running, `executor.shutdown(cancel_futures=True)` cancels it, but `drain_all()` may still call `prefetched.result()` and raise `CancelledError` unhandled.  
**Impact:** Exception in broker loop on shutdown.  
**Fix:** Check `self._stop.is_set()` before `prefetched.result()`; catch `CancelledError`.

### H-10: `ListeningLease` — Lazy Expiry Allows Stale Leases
**Files:** `jarvis/voice/listening.py:161-165`, `docs/reviews/2026-07-02-gate-g-voice-safety-review.md:33-36`  
**Problem:** Leases expire only on next API call (`_expire_stale()` called from `acquire/release/active`). No background sweeper for hold-mode (30s TTL). If panel crashes, lease stays `active` until next API call.  
**Impact:** Microphone stays hot indefinitely if panel crashes during hold-mode.  
**Fix:** Daemon-side sweeper (FIX-04b) exists but only runs every 5s. Ensure sweeper calls `_expire_stale()` AND stops recorder for expired leases.

---

## 🟡 MEDIUM (Code Smells / Performance / Tech Debt)

### M-01: Duplicate Schema Definitions — `schema.sql` vs `migrations.py:_ensure_memory_os_sidecar_tables()`
**Files:** `jarvis/store/schema.sql:69-180`, `jarvis/store/migrations.py:92-211`  
**Problem:** Memory OS tables defined in BOTH places. `schema.sql` has full definitions; `migrations.py` re-defines them in `_ensure_memory_os_sidecar_tables()` (called every `ensure_schema()`).  
**Impact:** Maintenance burden; drift risk. `migrations.py` version lacks some indexes from `schema.sql`.  
**Fix:** Remove `_ensure_memory_os_sidecar_tables()` — tables already in `schema.sql`. Or make migrations.py the single source of truth.

### M-02: `ThreadLocalConnection` — No Connection Pooling / Reuse Limit
**Files:** `jarvis/store/db.py:87-176`  
**Problem:** Each thread gets ONE connection, kept forever until `close_current_thread()` or `close()`. No max pool size, no idle timeout. Long-running daemon with many worker threads = many connections.  
**Impact:** SQLite connection limit (default 1000), file descriptor exhaustion.  
**Fix:** Add max connections; close idle connections after TTL.

### M-03: `redact_secrets()` — Recursive Without Depth Limit
**Files:** `jarvis/security/redaction.py:113-131`  
**Problem:** `redact_secrets()` recurses on dicts/lists without depth tracking. Deeply nested malicious payload could cause RecursionError.  
**Impact:** DoS via crafted JSON.  
**Fix:** Add `max_depth` parameter (default 100); raise if exceeded.

### M-04: `ContextBuilder._fit_budget()` — O(n²) Message Trimming
**Files:** `jarvis/brain/context_builder.py:623-655`  
**Problem:** `_fit_budget()` uses `del fitted_messages[core_message_count]` in loop — O(n) per deletion, called repeatedly. With many messages, quadratic.  
**Impact:** Slow context building for long conversations.  
**Fix:** Use `deque` or track start index instead of deleting.

### M-05: `VoiceRuntime` Projection — Catches All Exceptions Silently
**Files:** `jarvis/api/routes_voice.py:593-625` (`_safe_audio_state`, `_safe_voice_queue`, `_safe_latest_events`)  
**Problem:** All diagnostic helpers use bare `except Exception: return None/[]`. Real errors (DB corruption, permission denied) hidden.  
**Impact:** Silent failures; panel shows "ok" when voice broken.  
**Fix:** Log exception at WARNING level before returning safe default.

### M-06: `CancellationCoordinator._cancel_queued()` — N+1 Queries
**Files:** `jarvis/voice/cancellation.py:143-173`  
**Problem:** Fetches all queued/speaking rows, then for each turn_id calls `queue.cancel_turn()` which does separate DB query.  
**Impact:** Slow cancellation for large queues.  
**Fix:** Batch cancel in single query/transaction.

### M-07: `BrainManager.from_config()` — Hardcoded Priority Order
**Files:** `jarvis/brain/manager.py:55, 141-146`  
**Problem:** Priority order `["claude_cli", "codex_cli", "groq"]` hardcoded. No config override.  
**Impact:** Can't prefer Codex over Claude without code change.  
**Fix:** Read priority from config `brain.adapter_priority`.

### M-08: `DaemonApp.start()` — Voice Components Built Even When Disabled
**Files:** `jarvis/daemon/app.py:174-340`  
**Problem:** `voice_recorder` built unconditionally (line 275-280). STT/TTS/broker/gateway only if `config.voice.enabled`. But recorder created before check.  
**Impact:** Recorder initialization (sox validation) runs even when voice disabled.  
**Fix:** Move recorder creation inside `if config.voice.enabled:`.

### M-09: `EventStore.append()` — No Batch Insert
**Files:** `jarvis/store/event_store.py` (not read but inferred from usage)  
**Problem:** Each event = separate INSERT + commit. High-volume events (stream, voice queue) cause many commits.  
**Impact:** Write throughput limited.  
**Fix:** Add `append_batch()` for bulk inserts.

### M-10: `tool_runs` Table — `output_json` Stores Truncated Data
**Files:** `jarvis/tools/registry.py:813-842` (`_cap_persisted_strings`)  
**Problem:** `PERSIST_MAX_STRING_CHARS = 4096` truncates tool output in durable store. But model gets full output via `ToolResult.output`. Inconsistency: model sees full, audit sees truncated.  
**Impact:** Debugging hard; audit trail incomplete.  
**Fix:** Document clearly; or store full output in separate blob table with reference.

---

## 🟢 LOW (Nits / Cleanup / Docs)

### L-01: `FIXME.md` — Many Items Marked DONE But Code Changed Since
**Files:** `FIXME.md` (entire file)  
**Problem:** FIX-01 through FIX-17 marked DONE with commit SHAs. But current HEAD `7fbba95` is ahead of some fix commits. Some fixes may have been reverted or modified.  
**Action:** Verify each fix against current code.

### L-02: `JARVIS_PROJECT_RULES.md` Duplicate of `PROJECT_RULES.md`
**Files:** `docs/JARVIS_PROJECT_RULES.md`, `docs/PROJECT_RULES.md`  
**Problem:** Identical 186-line files.  
**Action:** Delete duplicate.

### L-03: Historical Docs Not Archived
**Files:** `docs/MASTER_PLAN.md`, `docs/JARVIS_HISTORY.md`, `docs/LEGACY_RUNTIME_FINDINGS.md`, `docs/JARVIS_FIX_TASKS_HANDOFF.md`, `docs/REVIEW_HANDOFF.md`, `docs/spikes/*`, `docs/reviews/*`, `docs/superpowers/*`  
**Problem:** Per `DOCS_INDEX.md`, these are "historical/legacy" but sit in main docs dir.  
**Action:** Move to `docs/archive/`.

### L-04: `DEFAULT_VOICE_FILLERS` — 53 Hardcoded Strings in Config
**Files:** `jarvis/config.py:31-89`  
**Problem:** 53 filler strings in source code. Should be in config file.  
**Action:** Move to `jarvis.example.toml` only; code reads from config.

### L-05: `config.py` — `_build_security_config()` Parses `trusted_scopes` Manually
**Files:** `jarvis/config.py:520-543`  
**Problem:** Manual TOML parsing for `trusted_scopes` array of tables. Dataclass field `trusted_scopes` not auto-populated.  
**Action:** Use `__post_init__` or custom decoder.

### L-06: `routes_voice.py` — `_probe_executable()` Duplicates `shutil.which()`
**Files:** `jarvis/api/routes_voice.py:447-468`  
**Problem:** `_resolve_executable()` reimplements `shutil.which()` with path expansion.  
**Action:** Use `shutil.which()` + `os.access(path, os.X_OK)`.

### L-07: `state_machine.py` — `DaemonState` / `StateMachine` Aliases
**Files:** `jarvis/daemon/state_machine.py:254-255`  
**Problem:** Backward-compat aliases `DaemonState = RuntimeState`, `StateMachine = RuntimeStateMachine`. Not used anywhere (grep shows no imports).  
**Action:** Remove.

### L-08: `memory_tool.py` — `MemorySaveTool` Has Two Code Paths (`propose` vs `run`)
**Files:** `jarvis/tools/memory_tool.py:61-128`  
**Problem:** `propose()` creates candidate+evidence; `run()` activates candidate. But `run()` re-validates payload against candidate. Duplication.  
**Action:** Unify validation; `run()` should trust candidate.

### L-09: `cli.py` — `jarvis input text` Uses `source="cli"` Not in `ALLOWED_TEXT_INPUT_SOURCES`
**Files:** `jarvis/cli.py:299`, `jarvis/api/routes_input.py:14`  
**Problem:** CLI sends `source="cli"`; `ALLOWED_TEXT_INPUT_SOURCES = {"api", "cli", "panel", "text"}`. OK but inconsistent with voice source handling.  
**Action:** Document source taxonomy.

### L-10: `config/jarvis.example.toml` — `approved_roots = ["~/Documents/dev"]` Hardcoded
**Files:** `config/jarvis.example.toml:99`  
**Problem:** Example config assumes specific path. Should be empty or commented.  
**Action:** Change to `approved_roots = []` with comment.

---

## 🧪 TEST GAPS

| Area | Missing Tests |
|------|---------------|
| `ThreadLocalConnection` | Concurrent access, leak detection, close_current_thread |
| `VoiceBroker` | Stop during synthesis, cancelled future handling, backoff logic |
| `CancellationCoordinator` | Race: generation cancel + new generation same turn_id |
| `ContextBuilder` | Compiled memory gate with session/profile allowlist |
| `ToolPermissionPolicy` | `auto_approve_mode="all"` behavior across all sources |
| `file_read` | Symlink escape with TOCTOU, binary file rejection |
| `shell_read` | Git config RCE, whitelist bypass attempts |
| `BrainManager` | No adapters available (mock fallback), priority config |
| `MemoryCompiler` | Malformed memory_id, budget edge cases |
| `DaemonApp` | Start/stop voice when disabled, concurrent start calls |

---

## 📋 PRIORITY ORDER FOR FIXES

1. **C-01** — Unblock tests (critical for CI)
2. **H-01** — Config loading priority (affects all runs)
3. **H-04/H-05** — Auto-approve mode implementation (production plan blocker)
4. **H-06** — Simplify compiled memory gate (Memory OS unusable)
5. **C-02/C-03** — Connection/broker stability
6. **M-01** — Schema duplication (maintenance)
7. **H-03/H-10** — Voice CORS + lease expiry (security/UX)
8. **M-03/M-04** — DoS vectors + performance
9. **L-02/L-03** — Docs cleanup
10. Remaining MEDIUM/LOW items

---

## 🔗 REFERENCES

- `FIXME.md` — 47 findings, 17 fix tasks (some DONE)
- `STATUS.md` — Current branch `rescue/audt-gpt5.5pro-limit-cdn` at `58cca12`
- `PROJECT_RULES.md` — Architecture laws, change discipline
- `DECISIONS.md` — ADR-001 through ADR-021 (frozen)
- `PRODUCTION_UNRESTRICTED_PLAN.md` — Target config for unrestricted mode
- `docs/DOCS_INDEX.md` — Doc precedence hierarchy