# Jarvis v4.1 — Security Model (FROZEN)

> **Status:** FROZEN (Prompt 00A). Defines the tool permission model, the
> approval gate, secret redaction, and the boundaries on brains and workers.
> Field shapes are in [CONTRACTS.md](CONTRACTS.md).

---

## 1. Threat model (single-user, local)

Jarvis runs as the user, on the user's Mac, with the user's privileges. The risk
is **not** a remote attacker — it is **autonomous over-reach**: a brain or worker
proposing an action (delete files, run a shell command, hit the network) that
runs without the human realizing it. The old `dan` system ran its command path
with `--dangerously-skip-permissions` and relied on push-to-talk as the only
brake. v4.1 replaces that with an explicit registry + approval gate.

The two pillars:

1. **Every tool goes through the registry and a permission policy**
   ([ADR-010](DECISIONS.md#adr-010)).
2. **Brains and workers cannot reach the world directly** — no speaking, no
   memory-fact writes, no unsandboxed filesystem/shell
   ([ADR-003](DECISIONS.md#adr-003), [ADR-009](DECISIONS.md#adr-009)).

---

## 2. Tool registry & permission classes (Prompt 12)

Every callable capability is a registered tool with a **permission class**. The
default policy:

| Permission class | Default | Meaning |
|------------------|---------|---------|
| `safe_read` | **allow** | read-only, side-effect-free reads |
| `safe_status` | **allow** | status/inspection of the running system |
| `file_read` | **allow within approved roots** | read files under approved roots only |
| `file_write` | **approval required** | writing files |
| `shell_read` | **approval required (initially)** | read-only shell commands |
| `shell_write` | **approval required** | state-changing shell commands |
| `network` | **approval required** | any outbound network access |
| `destructive` | **blocked-by-default; enabled in Ozzy mode** | irreversible/dangerous ops |

### Rules (FROZEN)

- A **rejected** or **blocked** `ToolCall` **never executes**.
- An **approved** call **records a `tool_run`** (in `tool_runs`).
- `destructive` is **blocked** unless explicitly enabled in configuration
  (e.g., `security.destructive_tools_enabled=true` in `~/.jarvis/jarvis.toml`).
  Normally never auto-approved even when enabled; in Ozzy mode with
  `auto_approve_mode="all"`, may be auto-approved for model-originated requests.
- File access is **root-scoped**: `file_read` is only allowed within approved
  roots; absolute paths and parent-escapes (`..`) outside approved roots are
  refused.

---

## 3. Approval gate (Prompt 12)

A gated `ToolCall` produces an `Approval` (see [CONTRACTS.md](CONTRACTS.md)):

```
ToolCall (proposed)
      ▼
permission requires approval?
      ▼ yes
Approval (pending)  +  daemon state TOOLING  +  approval.requested
      ▼
approval.granted ──► TOOLING ──► tool_run recorded
approval.rejected ─► ToolCall rejected ──► NEVER executes
```

- The gated action **never runs before `approved`**.
- Destructive actions are **normally never auto-approved**; in Ozzy mode
  (`auto_approve_mode="all"`), they may be auto-approved for model-originated
  requests.
- Decisions are persisted (`approvals`) and event-logged (`approval.*`).
- `WAITING_APPROVAL` is not a v4.1 runtime state; approval waiting is modeled by
  `approvals`, approval/tool events and, when the runtime is actively waiting as
  part of a turn, `TOOLING`.

---

## 4. Secret redaction (Prompts 04 & 12)

Secrets must never be written to the event store or to `tool_run` records in the
clear.

- A **policy helper redacts secrets in event payloads before write**
  ([ADR-010](DECISIONS.md#adr-010)).
- `EventStore.append` is the final persistence guard: every event payload is
  passed through the central redactor immediately before JSON serialization, so
  the SQLite `events.payload_json` row and `/events` API payloads cannot expose
  raw secrets even if a caller forgot to redact earlier.
- `ToolCall.args` and `ToolCall.result` are **redacted** wherever they appear in
  events.
- Callers may still redact earlier for display, approval rows, tool run rows or
  logs, but earlier redaction is defense-in-depth rather than the event-store
  guarantee.
- API keys / tokens live outside the DB (environment / config), never in
  `events`.

---

## 5. Brain boundary ([ADR-003](DECISIONS.md#adr-003))

A brain adapter is a **stateless** function `BrainRequest → BrainResponse`:

- It receives a fully-formed `BrainRequest` and returns a `BrainResponse`.
- It **does not** speak, **does not** write memory, **does not** touch the panel,
  **does not** persist its own session.
- The provider's server-side session is **not** Jarvis memory — context always
  comes from Jarvis's DB + config.
- A missing CLI yields `adapter_unavailable`; a subprocess error yields
  `BrainAdapterError`. Timeouts are configurable. (Prompt 11.)

If the brain wants to *act*, it proposes a `ToolCall` — which is then subject to
the registry and the approval gate. The brain never executes anything itself.

> **Legacy evidence.** The old `cli_brain.run()` invoked
> `claude -p --dangerously-skip-permissions` (full Bash/Edit/Write, no prompt),
> and an attempted `--sandbox workspace-write` was *ignored* because
> `trust_level="trusted"`. v4.1 therefore does **not** rely on any provider
> sandbox flag for safety — the registry + approval gate is the only control.
> See [LEGACY_RUNTIME_FINDINGS.md](LEGACY_RUNTIME_FINDINGS.md) §9.

---

## 6. Worker boundary ([ADR-009](DECISIONS.md#adr-009))

A worker (Codex/Claude background job) is even more constrained than a brain:

- A worker **never speaks** — it cannot enqueue a `VoiceRequest` on its own
  authority ([ADR-005](DECISIONS.md#adr-005)).
- A worker **never writes a memory fact directly** — its result is a **memory
  candidate** that a human or policy must promote.
- Worker activity is fully visible in the EventStore stream (`worker.job.*`
  entries in the general `events` table).

---

## 7. Network & destructive posture (default configuration)

- **Network is approval-gated by default** (`network` requires approval). In
  Ozzy mode with `auto_approve_mode="all"`, network calls like `web_fetch` are
  auto-approved for model-originated requests. No tool reaches out without an
  explicit configuration decision or approval.
- **Destructive operations are blocked by default**, not merely gated — enabling
  them is a deliberate, separate configuration act
  (`security.destructive_tools_enabled=true`), and even when enabled, individual
  calls normally require approval (unless `auto_approve_mode="all"`).
- **No automatic killing** of processes (see
  [LAUNCH_SUPERVISION.md](LAUNCH_SUPERVISION.md)).
- **No destructive cleanup** of the old `dan` repo, ever, automatically.

---

## 7a. Ozzy mode (bypass configuration)

When `~/.jarvis/jarvis.toml [security]` sets:
- `auto_approve_mode = "all"`
- `destructive_tools_enabled = true`
- `[brain.claude_cli] permission_mode = "bypassPermissions"`

The runtime auto-approves **all non-blocked tools** for model-originated
requests. Network calls (`web_fetch`), file writes, shell commands, and even
destructive operations bypass the approval gate. This is the current Jarvis
configuration for Ozzy. The `ToolPermissionPolicy` still classifies and audits
all actions; bypass is a configuration choice, not a removal of the gate.

---

## 8. macOS operator capability boundary

macOS operator capabilities are high power. The model must not directly operate
the Mac; `jarvisd` mediates all operator actions through `ToolRegistry`,
`PermissionPolicy`, `ApprovalGate`, `EventStore`, and audited adapters. The
architecture contract is in
[MACOS_OPERATOR_CONTRACT.md](MACOS_OPERATOR_CONTRACT.md).

Credential and passkey flows require user presence if a later scoped prompt
promotes them into implementation. Jarvis may navigate to a login flow and
trigger a system prompt, but Touch ID, device password, passkey confirmation,
and Keychain unlock remain with the user and macOS. Secrets and credential
material are not exposed to the model or persisted in events.

External communication examples such as SMS, Messages, and phone initiation
require a separate communication policy, contact resolution, audit model, and
confirmation rules before implementation. The default posture is confirmation
before sending or calling unless an explicit trusted-contact/direct-command
policy narrows that risk.

---

## 9. Invariants (FROZEN)

1. No tool runs without passing the registry + permission policy.
2. Rejected/blocked tool calls never execute.
3. Secrets are redacted before any event/DB write.
4. Brains are stateless and mute; they can only *propose* actions.
5. Workers are mute and cannot write memory facts directly.
6. Destructive is blocked-by-default (enabled + auto-approved in Ozzy mode);
   network is approval-by-default (auto-approved in Ozzy mode with
   `auto_approve_mode="all"`).
7. Models never operate macOS directly; `jarvisd` mediates operator capabilities.
