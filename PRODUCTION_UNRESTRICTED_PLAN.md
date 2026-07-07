# Jarvis Production Unrestricted Plan

**Status:** Planning document. No changes made yet.
**Goal:** Convert Jarvis from "safety-first default-off" to "production unrestricted" — single-user, full capabilities, no approval gates, no feature flags blocking anything.
**Philosophy:** "Wredny AI, skurwysyn z krwi i kości" — personality stays, all guards down for the operator.

---

## 1. Config: Create `config/jarvis.production.toml`

**New file** with everything enabled. Based on `jarvis.example.toml` but flipped to ON.

```toml
# config/jarvis.production.toml
# Production unrestricted config for single-user Mac operator.
# Load with: JARVIS_CONFIG=config/jarvis.production.toml jarvis daemon run

[daemon]
name = "jarvisd"
host = "127.0.0.1"
port = 41741
log_level = "INFO"
log_max_bytes = 10485760
log_backup_count = 5

[database]
path = "~/.jarvis/jarvis.db"

[brain]
default_adapter = "claude_cli"
default_model = "sonnet"
timeout_seconds = 120
context_budget_chars = 24000
provider_sessions_are_memory = false

[brain.claude_cli]
enabled = true
command = "claude"
args = ["-p"]
model = "claude-sonnet-5"
timeout_seconds = 180

[brain.codex_cli]
enabled = true
command = "codex"
args = []
model = "gpt-5.5"
timeout_seconds = 180

[memory]
enabled = true
max_active_blocks = 100
max_context_chars = 24000
worker_candidates_require_promotion = false   # Auto-promote worker candidates
compiled_context_enabled = true               # MEMORY OS ON
compiled_context_max_items = 10
compiled_context_max_chars = 4000
compiled_context_include_procedural = true

[voice]
enabled = true
speak_responses = true
broker_enabled = true
default_tts = "supertonic"
default_stt = "mlx_whisper"
ptt_mode = "hold"
queue_persisted = true
# Fillers: KEEP AS-IS — personality feature
fillers = [
  "Sekundę, myślę...",
  "Daj chwilę, analizuję...",
  "Już sprawdzam...",
  "Moment, sprawdzam...",
  "Zaraz to sprawdzę...",
]
filler_after_ms = 300
supertonic_speed = 1.35
supertonic_short_sentence_chars = 24
supertonic_short_sentence_speed = 1.0
supertonic_voice = "M1"
supertonic_lang = "pl"
supertonic_steps = 14

[audio]
enabled = true
input_policy = "pin_builtin_mic"
preferred_input = "Mikrofon (MacBook Air)"
output_policy = "follow_system_default"
allow_bluetooth_microphone = true
always_listen_enabled = false

[panel]
enabled = true
api_base_url = "http://127.0.0.1:41741"
width = 480
height = 760

[security]
localhost_only = true
api_token_required = true          # Keep token auth (auto-generated in ~/.jarvis/runtime/api-token)
require_approval_for_shell = false
require_approval_for_file_write = false
require_approval_for_network = false
destructive_tools_enabled = true
# Fail-open for single user: home directory as root
approved_roots = ["~"]
# Voice auto-approve within approved_roots
voice_auto_approve_tools = true
# MODEL_ORIGINATED = auto-approve everything non-destructive
auto_approve_mode = "all"
# Trusted scopes (optional, for extra auto-approve paths)
# trusted_scopes = [
#   { name = "jarvis-dev", path = "~/Documents/dev/jarvis", tools = ["file_read", "file_write", "shell_read"], ttl_minutes = 0 }
# ]

[runtime]
home = "~/.jarvis"
logs_dir = "~/.jarvis/logs"
runtime_dir = "~/.jarvis/runtime"
pid_file = "~/.jarvis/runtime/jarvisd.pid"
legacy_detection = "report_only"

[launchd]
enabled = false
label = "com.ozzy.jarvisd"
install_automatically = false
```

**Key flips from example.toml:**
| Setting | Example | Production | Reason |
|---------|---------|------------|--------|
| `memory.compiled_context_enabled` | false | **true** | Memory OS ON |
| `voice.enabled` | false | **true** | Voice ON |
| `voice.broker_enabled` | false | **true** | TTS broker ON |
| `security.require_approval_for_*` | true | **false** | No approval gates |
| `security.destructive_tools_enabled` | false | **true** | Full power |
| `security.approved_roots` | ["~/Documents/dev"] | **["~"]** | Fail-open home |
| `security.voice_auto_approve_tools` | false | **true** | Voice = full auto |
| `security.auto_approve_mode` | "model" | **"all"** | Model = full auto |
| `memory.worker_candidates_require_promotion` | true | **false** | Workers auto-promote |

---

## 2. Code Changes: Remove Approval Gates

### 2.1 `jarvis/tools/registry.py` — `ToolRegistry.request_tool()`

**Current flow (lines 139-192):**
```python
def request_tool(self, request, permission_policy, source, approval_gate=None):
    permission = self.evaluate_permission(...)
    if permission.decision == BLOCKED: return blocked
    if permission.decision == APPROVAL_REQUIRED:
        if approval_gate: create_approval()  # ← BLOCKS HERE
        return approval_required
    return self.execute_tool(request)  # Only ALLOW executes
```

**Change:** Add `auto_approve_mode` check. If `permission_policy.auto_approve_mode == "all"` and decision != BLOCKED → execute immediately.

```python
def request_tool(self, request, permission_policy, source, approval_gate=None):
    permission = self.evaluate_permission(...)
    if permission.decision == ToolDecision.BLOCKED:
        return blocked_result
    
    # NEW: Auto-approve mode "all" = execute immediately for any non-blocked
    if getattr(permission_policy, "auto_approve_mode", "off") == "all":
        if permission.decision in (ToolDecision.ALLOW, ToolDecision.APPROVAL_REQUIRED):
            return self.execute_tool(request)
    
    # Existing approval flow for other modes
    if permission.decision == ToolDecision.APPROVAL_REQUIRED:
        # ... existing approval_gate logic ...
    
    return self.execute_tool(request)
```

**Also:** `DaemonApp.request_tool()` (lines 960-1031) calls `tool_registry.request_tool()` — ensure it passes `permission_policy` with `auto_approve_mode` from config.

### 2.2 `jarvis/tools/permissions.py` — `ToolPermissionPolicy.decide()`

**Current:** Source-sensitive matrix. `MODEL_ORIGINATED` requires approval for mutating tools (FILE_WRITE, SHELL_WRITE, NETWORK, UI_ACT, TERMINAL_WRITE, MEMORY_WRITE) unless `auto_approve_mode == "model"` + trusted scope.

**Change:** When `auto_approve_mode == "all"` → treat `MODEL_ORIGINATED` same as `DIRECT_USER_COMMAND` for all non-destructive tools.

```python
def decide(self, risk, *, source, tool_name, payload=None):
    # NEW: Auto-approve all mode
    if self.auto_approve_mode == "all":
        if risk in {PERMISSION_CLASS.DESTRUCTIVE}:
            # Destructive still blocked unless explicitly enabled
            if not self.destructive_tools_enabled:
                return _blocked(...)
            return _approval_required(...)  # Still require approval for destructive
        # Everything else: ALLOW for any source
        if source in {REQUEST_SOURCE.MODEL_ORIGINATED, REQUEST_SOURCE.DIRECT_USER_COMMAND, 
                      REQUEST_SOURCE.PANEL_COMMAND, REQUEST_SOURCE.VOICE_COMMAND}:
            return _allow(...)
    
    # Existing logic for other modes...
```

**Effect:** With `auto_approve_mode = "all"` + `destructive_tools_enabled = true`:
- Model can: read/write files, run shell, network, UI act, terminal paste, memory write — **all auto-executed**
- Only destructive tools (if any defined) still need approval

### 2.3 `jarvis/brain/context_builder.py` — Simplify Compiled Memory Gate

**Current:** `_resolve_compiled_memory_enabled()` (lines 337-357) has 5-layer gate:
1. `[memory].enabled` config
2. `compiled_memory_force_disabled` (kill switch)
3. `compiled_memory_enabled_override` (request-scoped)
4. `compiled_memory_enabled` (global config flag)
5. `compiled_memory_scope_gate_enabled` + `compiled_memory_enabled_session_profiles` (session/profile allowlist)

**Change:** Collapse to 2-layer:
```python
def _resolve_compiled_memory_enabled(self, *, conversation_id, persona_profile, override):
    if override is not None:
        return bool(override)
    # Simple: memory.enabled AND compiled_context_enabled
    return (_config_bool(self._config, ("memory", "enabled"), True) and
            _config_bool(self._config, ("memory", "compiled_context_enabled"), False))
```

**Remove:** `compiled_memory_force_disabled`, `compiled_memory_scope_gate_enabled`, `compiled_memory_enabled_session_profiles` from `ContextBuilder.__init__` and config wiring.

**Config impact:** `MemoryConfig` keeps `compiled_context_enabled` — that's the single switch.

---

## 3. Config Loading: Support Production Config

**Current:** `jarvis/config.py` loads from `JARVIS_CONFIG` env var → `config/jarvis.toml` → `config/jarvis.example.toml`.

**No code change needed.** Just set:
```bash
export JARVIS_CONFIG=config/jarvis.production.toml
```
Or pass `--config config/jarvis.production.toml` to CLI.

**Verify:** `jarvis config show` should show production values.

---

## 4. Voice: Keep Personality, Ensure Works

**No code changes.** Fillers stay in `config.py` (DEFAULT_VOICE_FILLERS) and `jarvis.production.toml` overrides with same list.

**Verify voice pipeline works with production config:**
- `voice.enabled = true` → daemon builds STT/TTS/broker/recorder
- `broker_enabled = true` → VoiceBroker starts with Supertonic engine
- `speak_responses = true` → TurnOrchestrator queues speech after each turn
- `auto_approve_mode = "all"` + `voice_auto_approve_tools = true` → voice commands execute tools without approval

---

## 5. Memory OS: Full Enable

**Config only** (see `jarvis.production.toml`):
- `compiled_context_enabled = true`
- `compiled_context_max_items = 10` (was 3)
- `compiled_context_max_chars = 4000` (was 1200)
- `compiled_context_include_procedural = true`
- `worker_candidates_require_promotion = false` → workers auto-create memory items

**Code:** ContextBuilder gate simplification (section 2.3) removes session/profile allowlist requirement. Compiled memory runs for **all conversations** when enabled.

---

## 6. Docs Cleanup: Archive Historical Files

**Move to `docs/archive/` (create dir):**

| File | Reason |
|------|--------|
| `docs/MASTER_PLAN.md` | Superseded by Memory OS branch |
| `docs/JARVIS_HISTORY.md` | Archaeology only |
| `docs/LEGACY_RUNTIME_FINDINGS.md` | Diagnostic snapshot 2026-06-30 |
| `docs/JARVIS_FIX_TASKS_HANDOFF.md` | Voice fix tasks all DONE |
| `docs/REVIEW_HANDOFF.md` | v4.2 review, wrong branch |
| `docs/JARVIS_PROJECT_RULES.md` | **Duplicate** of `PROJECT_RULES.md` |
| `docs/spikes/JARVIS_POC_SETTINGS_EDIT_INTENT.md` | Spike, non-authoritative |
| `docs/spikes/JARVIS_RUNTIME_POC_FINAL_HANDOFF.md` | Spike, non-authoritative |
| `docs/spikes/JARVIS_POC_RUNTIME_SETTINGS_GAP_AUDIT.md` | Spike, non-authoritative |
| `docs/spikes/README.md` | Spike index |
| `docs/reviews/2026-07-02-gate-g-voice-safety-review.md` | Historical review |
| `docs/reviews/2026-07-02-legacy-dan-leftovers.md` | Historical review |
| `docs/reviews/GATE_C_TEXT_PIPELINE_REVIEW.md` | Historical review |
| `docs/reviews/2026-07-02-voice-tools-inventory.md` | Historical review |
| `docs/superpowers/specs/2026-07-03-setup-cleanup-design.md` | Spike spec |
| `docs/superpowers/plans/2026-07-03-setup-cleanup.md` | Spike plan |

**Update `docs/JARVIS_ROADMAP.md`:**
- Move "Final Memory OS handoff" from **Now** → **Done**
- Add new "Production Unrestricted" section under **Next** or **Later**

**Keep (authoritative/current):**
- `AGENTS.md`, `docs/PROJECT_RULES.md`, `docs/STATUS.md` (trinity)
- `docs/DECISIONS.md`, `docs/adr/ADR-001-memory-os-data-model.md`
- `docs/CONTRACTS.md`, `docs/SECURITY_MODEL.md`, `docs/PANEL_CONTRACT.md`, `docs/MACOS_OPERATOR_CONTRACT.md`, `docs/MEMORY_CONTRACT.md`
- `docs/MEMORY_OS_ARCHITECTURE.md`, `docs/MEMORY_COMPILER.md`
- `docs/JARVIS_CURRENT_STATE.md`, `docs/JARVIS_ARCHITECTURE.md`, `docs/JARVIS_CHANGE_GUARDS.md`, `docs/JARVIS_DO_NOT_TOUCH.md`
- All `docs/runbooks/*` (13 files)
- `docs/PRODUCT.md`, `docs/TURN_PIPELINE.md`, `docs/AUDIO_RUNTIME.md`, `docs/LAUNCH_SUPERVISION.md`, `docs/MACOS_CAPABILITIES.md`, `docs/MEMORY_ARCHITECTURE.md`, `docs/MIGRATION_INVENTORY.md`

---

## 7. Verification Checklist

After changes, run:

```bash
# 1. Config loads correctly
JARVIS_CONFIG=config/jarvis.production.toml jarvis config show | grep -E "(compiled_context|voice.enabled|auto_approve|destructive|approved_roots)"

# 2. Unit tests pass
.venv/bin/python -m pytest -q tests/test_tool_permissions.py tests/test_tool_registry.py tests/test_context_builder.py tests/test_memory_compiler.py

# 3. Integration: daemon starts with production config
JARVIS_CONFIG=config/jarvis.production.toml jarvis daemon run &
sleep 3
jarvis health --url http://127.0.0.1:41741
jarvis state --url http://127.0.0.1:41741

# 4. Tool execution without approval
jarvis input text "utwórz plik ~/test_production.txt z treścią 'hello'" --url http://127.0.0.1:41741
# Should return finished, not approval_required

# 5. Voice pipeline initializes
# Check logs for: "VoiceBroker started", "STT engine: mlx_whisper", "TTS engine: supertonic"

# 6. Memory OS compiled context active
jarvis input text "zapamiętaj że lubię kawę" --url http://127.0.0.1:41741
# Next turn should have compiled memory in context

# 7. Smoke scripts
for s in scripts/smoke-*.sh; do "$s" >/dev/null 2>&1 && echo "PASS $s" || echo "FAIL $s"; done
```

---

## 8. Rollback Plan

If production config breaks things:

1. **Revert config:** `export JARVIS_CONFIG=config/jarvis.example.toml` (or delete env var)
2. **Revert code:** `git checkout HEAD~3` (three commits: registry, permissions, context_builder)
3. **DB unaffected:** Config changes only, no schema migration

---

## 9. Commit Plan (One Task = One Commit)

| Commit | Scope | Files |
|--------|-------|-------|
| 1 | Config: add production config | `config/jarvis.production.toml` (new) |
| 2 | Code: auto-approve mode "all" in registry | `jarvis/tools/registry.py` |
| 3 | Code: auto-approve mode "all" in permissions | `jarvis/tools/permissions.py` |
| 4 | Code: simplify compiled memory gate | `jarvis/brain/context_builder.py` |
| 5 | Docs: archive historical files | `docs/archive/` (16 files moved), `docs/JARVIS_ROADMAP.md` updated |
| 6 | Verify + smoke test | (no files, just validation) |

---

## 10. Open Questions / Decisions Needed

1. **`api_token_required = true`** — Keep? (Auto-token in `~/.jarvis/runtime/api-token` works for CLI/panel). If `false`, any local process can call API.
2. **`approved_roots = ["~"]`** — Fail-open home directory. OK for single-user?
3. **`destructive_tools_enabled = true`** — No destructive tools defined yet, but if added (e.g., `shell_write` with `rm`), they'd run without approval.
4. **Voice fillers** — Kept as-is (personality). Confirm.
5. **Worker auto-promotion** — `worker_candidates_require_promotion = false` means background workers create active memory items directly. Confirm.

---

**Next step:** Review this plan. If approved, execute commits 1-6 in order with verification after each.