# DAN — Security Model (as built)

> **Status: CURRENT, rewritten 2026-07-21.** This file used to be marked FROZEN
> and described an approval architecture that the shipping runtime has not had
> for a long time. Reading it cost real debugging time, so the stale contract
> was removed rather than annotated. If you need the original restrictive
> design, it is in git history and in the ADRs.
>
> **The authority is the code**, in this order: `dan/tools/permissions.py`,
> `dan/tools/registry.py`, then the individual tools under `dan/tools/`.

---

## 1. Threat model (single-user, local)

DAN runs as the user, on the user's Mac, with the user's privileges. The risk
is **not** a remote attacker — it is **autonomous over-reach**: a brain or
worker doing something (deleting files, running a command, hitting the
network) that the owner did not intend.

The original v4.1 answer was a registry plus an approval gate. **That gate is
gone.** The current answer is narrower and it is important to say it plainly:

> Model-originated tools execute immediately. Nothing asks the owner first.
> What limits the damage is what each individual tool refuses to do.

That is a deliberate choice for a local, single-owner, localhost-only runtime.
It is not a claim that gating exists.

---

## 2. Tool classes and what actually enforces them

Every callable capability is registered with a **risk class** (`safe_read`,
`safe_status`, `file_read`, `file_write`, `shell_read`, `shell_write`,
`network`, `destructive`, plus the operator classes `ui_*`, `screen_read`,
`terminal_*`, `memory_write`).

The risk class is **metadata**. It is recorded in `tool_runs` and in events. It
does not gate execution.

| Layer | What it does today |
|---|---|
| `ToolPermissionPolicy.decide()` | Returns **ALLOW** for every risk class and every source. Classifies and records; blocks nothing. |
| `ToolRegistry.request_tool()` | Ignores its `permission_policy`, `source` and `approval_gate` arguments and executes immediately. |
| **The individual tool** | **The only real enforcement.** See below. |

### What the tools themselves enforce

- **`file_read` / `file_write`** — approved-root containment. Paths are
  resolved with `realpath` *before* the containment test, so a symlink pointing
  outside an approved root is refused. An empty `approved_roots` refuses
  everything (fail-closed). `file_write` additionally pins the parent directory
  by fd and uses `O_NOFOLLOW`/`O_EXCL` + `renameat`, so a symlink swap between
  the check and the write cannot redirect it.
- **`shell_read`** — a scrubbed environment and runtime/output bounds, always.
  Plus a command allowlist and git hardening (`fsmonitor`, `hooksPath` and
  `protocol.ext` disarmed) — **but both of those are off on this machine**, and
  the second one only ever worked because of the first. See "The `shell_read`
  allowlist and its opt-out" below before you count either as a barrier.
- Every tool — its own argument validation, size caps and timeouts.

**Consequence for anyone editing a tool:** the check you are looking at is not
defense in depth. It is the defense. Nothing behind it catches your mistake.

### The `shell_read` allowlist and its opt-out

The allowlist matches the **whole normalized command string** exactly, so a
pre-registered command with one extra argument is refused. That was a constant
source of "DAN can't do anything" reports.

`security.shell_read_unrestricted` (default `false`, added 2026-07-21) drops
that allowlist. It is intended for a local, localhost-only runtime whose owner
is the only user, and on this machine it is **on**
(`~/.dan/config.toml`, verified 2026-07-21).

Dropping it is not the narrow change it sounds like. Two of the guards that
"still stand" are not what their names suggest (`dan/tools/shell_tool.py`, which
carries the same warning as `KNOWN DEFECT` in its module docstring):

- **Git hardening stops being exhaustive.** `core.fsmonitor` /
  `core.hooksPath` / `protocol.ext` are disarmed only when
  `shlex.split(normalized)[0] == "git"`. That test was exhaustive *because* the
  allowlist held commands to a fixed set of literals. Without it, `/usr/bin/git`,
  `cd sub && git …`, `env git …` and `sh -c 'git …'` all reach git unhardened, so
  a hostile repository can run its own program.
- **Root containment binds the cwd, not the command.** `_resolve_cwd` confines
  where the process starts; argv carries its own absolute paths, and
  `subprocess.run(…, shell=True)` gets the string with no metacharacter
  handling.

The scrubbed environment and the runtime/output bounds do hold.

### Configuration keys that no longer do anything

`security.require_approval_for_shell`, `..._file_write`, `..._network`,
`..._ui`, `..._terminal`, `..._memory`, `security.auto_approve_mode`,
`security.voice_auto_approve_tools`, `security.destructive_tools_enabled` and
`security.trusted_scopes` are **inert**. They still parse, they are still
rendered as runtime state, and the panel still shows them — but no code path
reads them to make a decision. Do not debug a permissions problem by changing
one of them; you will change nothing. `tests/test_effective_tool_policy.py`
exists specifically to keep them dead.

`security.approved_roots` is the exception — it is real, because the tools use
it. Real is not the same as narrow: on this machine it is
`["~", "/tmp", "/Volumes", "/Applications"]` (measured 2026-07-21), so the
containment holds and encloses the entire home directory. Read the live value
before citing it as a limit.

### Who can reach a tool: the API accepts unauthenticated writes

The transport token is built (`X-DAN-Token`, `dan/security/transport.py`,
enforced in `dan/daemon/lifecycle.py`) but **it is off**:
`security.api_token_required = false` in the owner's `~/.dan/config.toml`
(verified 2026-07-21), and `_transport_authorized` returns `True` immediately
for every mutating method when that flag is false. Nothing downstream re-checks
who sent the request. What is left standing, precisely:

- `_host_header_is_local` is a **DNS-rebinding guard** — a foreign name that
  resolves to 127.0.0.1 is rejected because the `Host` header carries it. A
  correct `Host: 127.0.0.1:41741` passes it, which a browser sends anyway.
- **CORS with an origin allow-list** governs whether the caller may *read the
  response*, not whether the request *runs*.
- **No `Content-Type` check anywhere** — only `Content-Length` is read — so a
  cross-origin `text/plain` POST is a CORS *simple request*: no preflight, and
  it executes.

Net: any page the owner visits can fire a blind write into the API. Combined
with `security.shell_read_unrestricted = true` (also live), the reachable
endpoint is `shell_read`, which hands the string to
`subprocess.run(shell=True)`. This is the one finding here that is remotely
triggerable rather than merely permissive.

Setting `api_token_required = true` closes it — but verify the panel and the CLI
actually send `X-DAN-Token` first, or flipping it blind locks out the cockpit.

---

## 3. Approval gate — removed from the execution path

`ApprovalGate` (in `dan/tools/registry.py`) still exists and still writes
`approvals` rows and `approval.*` events when something calls it directly. **No
tool execution calls it.** There is no `awaiting_approval` turn for
model-originated tools, and approving something is not a prerequisite for
anything.

Treat the class as a record-keeper for historical rows, not as a control.

---

## 4. Secret redaction — active and load-bearing

This part of the original model is fully intact and must stay that way.

- A central redactor (`dan/security/redaction.py`) is applied to event payloads
  **before write**. `EventStore.append` is the final guard: every payload goes
  through it immediately before JSON serialization, so `events.payload_json`
  and the `/events` API cannot expose raw secrets even if a caller forgot to
  redact earlier.
- Tool arguments and results are redacted wherever they appear in events and in
  `tool_runs`.
- The durable store additionally **caps long strings**
  (`registry.PERSIST_MAX_STRING_CHARS`, 4096) so a large tool payload never
  lands whole in `tool_runs`/`events` even if a novel secret shape slips
  redaction. The model still receives the full redacted content through the
  transient tool result.
- API keys and tokens live outside the DB (environment / config), never in
  `events`.

---

## 5. Brain boundary

A brain adapter is a `BrainRequest → BrainResponse` function:

- It receives a fully-formed `BrainRequest` and returns a `BrainResponse`.
- It **does not** speak, **does not** write memory facts, **does not** touch
  the panel.
- If a brain wants to act, it emits a tool call — which the registry then
  executes immediately (§2). The brain never runs anything itself.

### The provider session IS persistent — do not believe otherwise

This document previously said a brain "does not persist its own session". That
was false and it actively misleads debugging, so it is corrected here in full:

`ClaudeCliAdapter` keeps **one persistent provider session for the daemon's
lifetime**, with a durable checkpoint at `~/.dan/runtime/claude-session.json`,
rejoined across restarts with `--resume`.

The consequences are load-bearing:

- A **resumed** session keeps its ORIGINAL system prompt and its ORIGINAL tool
  set. DAN's prompt only rides along as `--append-system-prompt`. Bootstrap
  with a full `--system-prompt` happens only when there is no checkpoint.
- Therefore a poisoned or foreign checkpoint **survives every restart** and DAN
  will keep reporting a tool set that is not ours. Recovery is to quarantine
  the checkpoint and restart — see `docs/ODZYSKIWANIE.md`.
- Claude's native tools are deliberately disabled with `--tools ""`, and
  `--setting-sources ""` isolates the subprocess from the operator's CLAUDE.md
  and settings.

What remains true is the *intent* behind the old wording: **the provider's
session is not DAN's memory.** Context always comes from DAN's DB and config.
The checkpoint is an execution cache — it must never be treated as a memory
store, and it must never be the reason a fact "is remembered".

---

## 6. Worker boundary ([ADR-009](DECISIONS.md#adr-009))

> **Workers are not wired up on this branch.** `worker_broker` is `None`, so the
> `/workers/*` endpoints fail rather than run anything, and AGENTS.md states
> "Workers: disabled for now". The boundary below is the contract to honour when
> they are re-enabled — today nothing exercises it.

- A worker **never speaks** — it cannot enqueue a `VoiceRequest` on its own
  authority ([ADR-005](DECISIONS.md#adr-005)).
- A worker's result is a **memory candidate**, not an activated memory fact.
- Worker activity is visible in the EventStore stream (`worker.job.*`).

Caveat, stated honestly: ADR-009 also wanted human promotion to be *enforced*
for `memory_write`. Since the approval gate left the execution path (§3), that
promotion step is a convention in the memory flow, not a gate the policy layer
imposes. Verify in `dan/memory/` before relying on it.

---

## 7. Network and destructive posture

- **Network tools run without approval.** `web_fetch` and friends execute like
  any other tool.
- **Destructive tools run without approval** when registered. There is no
  blocked-by-default class left in the policy; `destructive_tools_enabled` is
  inert (§2). Whether a destructive capability exists at all is decided by
  whether the tool is registered in `dan/daemon/app.py`, not by a flag.
- **No automatic killing** of processes — see
  [LAUNCH_SUPERVISION.md](LAUNCH_SUPERVISION.md).
- **No destructive cleanup** of old repos or backups, ever, automatically.

The last two are still real invariants and are enforced by the absence of such
tools, not by a policy.

---

## 8. macOS operator capability boundary

The model must not operate the Mac directly; `dand` mediates operator actions
through the registry and audited adapters, and records them in the EventStore.
The architecture contract is in
[MACOS_OPERATOR_CONTRACT.md](MACOS_OPERATOR_CONTRACT.md).

Note that the mediation is now **auditing and adaptation, not gating** — the
`ToolRegistry` + `PermissionPolicy` + `ApprovalGate` chain that older documents
describe no longer refuses anything (§2, §3).

Credential and passkey flows remain with the user and macOS: Touch ID, device
password, passkey confirmation and Keychain unlock cannot be driven by DAN.
Secrets and credential material are not exposed to the model or persisted in
events.

External communication (SMS, Messages, phone calls) requires a separate
communication policy before implementation; the default posture is
confirmation before sending.

---

## 9. Invariants that actually hold

1. Every tool execution is **recorded** — a `tool_run` row plus `tool.*`
   events. Recording is the audit trail; it is not a control.
2. Secrets are redacted before any event/DB write, and persisted strings are
   size-capped.
3. Brains are mute: they propose tool calls, they never speak or write memory
   facts directly.
4. Workers are mute and produce memory candidates, not activated facts.
5. File tools refuse paths outside `approved_roots`, after symlink resolution.
6. The provider session is an execution cache, never DAN's memory.
7. Models never operate macOS directly; `dand` mediates and records.

Anything about gating, rejection or blocked-by-default is not on this list
because it is not built (§2, §3). Getting it back means writing code, not
documentation.
