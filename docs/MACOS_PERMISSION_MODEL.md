# DAN — Source-Sensitive Permission Model (UNIMPLEMENTED DESIGN)

> # ⚠️ THE PERMISSION MODEL IN THIS DOCUMENT WAS NEVER BUILT
>
> **Status as of 2026-07-21: §§1–4 and §6–7 are DESIGN ONLY, never implemented.
> Do not read any table below as a description of runtime behaviour.**
>
> **Exception: §5 (transport token) IS built** — see the note in that section.
>
> `ToolPermissionPolicy.decide()` returns **ALLOW unconditionally** — every
> risk class, every source. It does not consult the request source, approved
> roots, trusted scopes, `destructive_tools_enabled`, `auto_approve_mode` or
> any `require_approval_for_*` flag. `ToolRegistry.request_tool()` ignores the
> policy entirely and executes immediately.
>
> A previous version of this banner claimed *"Enforcement gates are in place in
> the code; permission modes relax them selectively per config."* **That was
> false**, and it sent a debugging session down the wrong path. Deleted
> 2026-07-21.
>
> For what actually constrains a tool today, read
> [SECURITY_MODEL.md](SECURITY_MODEL.md) §2. Short version: only the individual
> tools constrain anything.
>
> This document is kept because the design is still the intended direction if
> gating is ever built. **It is a plan, not a record.**
>
> **Naming:** `jarvisd` / `com.ozzy.jarvisd` / `~/.jarvis/` below are OLD names.
> Today: `dand` / `com.dan.dand` / `~/.dan/`, API on `127.0.0.1:41741`.

Motivation ([MACOS_OPERATOR_CONTRACT.md](MACOS_OPERATOR_CONTRACT.md) §5.4):
"the user says *click this*" is not the same event as "the model decided to
click". `ToolPermissionPolicy.decide()` does not distinguish them — it does not
distinguish anything at all. What follows is what it would take to make the
policy see the source and act on it.

---

## 1. Request sources

Canonical, closed enum (`RequestSource`):

| Source | Meaning |
|---|---|
| `direct_user_command` | typed by the user into CLI/API with an explicit instruction to perform this action |
| `panel_command` | clicked/submitted in the cockpit by the user |
| `voice_command` | spoken by the user under an active ListeningLease (G-phase; defined now, unused until then) |
| `model_originated` | proposed by a brain in `BrainResponse.tool_calls` |
| `scheduled_worker` | initiated by a worker job without a live user action |
| `hook_triggered` | initiated by an automated trigger (FSEvents, future HookRouter) |

Rules:

- Source is assigned by **jarvisd at the entry point**, never taken from the
  payload and never writable by a model. A brain cannot claim
  `direct_user_command` any more than it can set its own risk class
  (19B precedent: model-provided risk is ignored).
- **Unknown or missing source ⇒ `blocked`.** Fail-closed, same philosophy as
  A1 roots.
- Voice is *not* trusted more than text: `voice_command` maps to the same
  column as `panel_command` (see §3) — STT mishears, and a hot mic is not a
  signature.

## 2. Permission classes

Existing classes (SECURITY_MODEL.md, implemented): `safe_read`, `safe_status`,
`file_read`, `file_write`, `shell_read`, `shell_write`, `network`,
`destructive`.

New operator classes (from [MACOS_CAPABILITIES.md](MACOS_CAPABILITIES.md);
implemented only when their capability stage lands):

| Class | Covers | First stage |
|---|---|---|
| `ui_read` | Accessibility observation (windows, controls, focused field) | D1 |
| `ui_act` | Accessibility actions (click, type, focus, menu) | D2 |
| `screen_read` | ScreenCaptureKit capture + Vision OCR | D4 |
| `terminal_read` | observing a named terminal app's front session (ADR-021) | D5 |
| `terminal_write` | pasting a prepared command into a named terminal app (ADR-021) | D5 |
| `memory_write` | saving a durable memory block proposed by the model (`memory_save`) | post-H |
| `audio_input` | listening under ListeningLease | G2/G4 |
| `audio_output` | speech via voice broker | G3 |
| `fs_watch` | FSEvents observation within approved roots | E-phase |
| `notify` | UserNotifications | E-phase |
| `secret_ref` | Keychain reference resolution (jarvisd-internal) | C-phase (design-only) |
| `automation_run` | whitelisted Shortcuts | later |
| `clipboard_read` / `clipboard_write` | Pasteboard | later |

## 3. Decision matrix — source × class (DESIGNED, NOT BUILT)

*Reminder: no cell below is enforced. `decide()` returns ALLOW for every cell
in this table.*

Legend: **A** = allow, **AP** = approval required, **B** = blocked.
Columns: `user` = `direct_user_command` / `panel_command` / `voice_command`
(one column — see §1), `model` = `model_originated`, `auto` =
`scheduled_worker` / `hook_triggered`.

| Class | user | model | auto | Notes |
|---|---|---|---|---|
| `safe_read` | A | **AP** | AP | model column stays conservative (19B behavior); relaxation path in §6 |
| `safe_status` | A | **AP** | AP | as above |
| `file_read` | A (in roots) | AP (in roots) | AP (in roots) | outside roots: **B** for every source (A1, fail-closed) |
| `file_write` | AP | AP | **B** | no unattended writes, period |
| `shell_read` | AP | AP | **B** | |
| `shell_write` | AP | AP | **B** | |
| `network` | AP | AP | **B** | config: `require_approval_for_network` can relax user column to A |
| `destructive` | B / AP* | B / AP* | **B** | *AP only when `destructive_tools_enabled` (true in dev); `auto` is always B; config: `auto_approve_mode` can relax model column further |
| `ui_read` | A (approved surfaces) | AP | B | secure text fields never read, any source |
| `ui_act` | AP | AP | **B** | user column may earn per-surface trust later (§6) |
| `screen_read` (narrow) | A | AP | B | narrow = current window / named region |
| `screen_read` (broad) | AP | AP | **B** | full display / continuous |
| `terminal_read` | A | AP | B | front session of a named app ({Terminal, iTerm2}); output is secret-bearing — redaction applies, never streamed (ADR-021) |
| `terminal_write` | AP | AP | **B** | shell_write-grade: paste never submits, control chars rejected; never merged with `terminal_read` (ADR-021) |
| `memory_write` | AP | AP | **B** | a saved block feeds every future prompt; approved execution promotes the candidate, so ADR-009's human-sanctioned promotion holds |
| `audio_input` | lease-gated | **B** | **B** | only user sources can create a ListeningLease; a model can never start listening |
| `audio_output` | A (broker) | A (broker) | AP | speaking is low-risk; auto-sources queue via approval to avoid a 3 a.m. monologue |
| `fs_watch` | A (in roots) | AP | AP | registration is config-like; watching itself is passive |
| `notify` | A | A | A | generic previews; sensitive preview content ⇒ AP |
| `secret_ref` | — | **B** | **B** | not a tool; jarvisd-internal resolution only, no source ever receives values |
| `automation_run` | AP (whitelist) | AP (whitelist) | **B** | non-whitelisted name ⇒ **B** for every source |
| `clipboard_read` | A | AP | B | clipboards carry passwords; redaction applies |
| `clipboard_write` | A | AP | B | |
| *(unknown class)* | **B** | **B** | **B** | existing behavior, kept |

Reading the matrix:

- **The `auto` column is the strictest.** Scheduled/hook work may observe and
  notify; it does not mutate anything without a human in the loop. Workers
  needing writes produce *proposals* (approvals), not actions.
- **The `model` column would never contain a plain A** except broker-mediated
  speech. That is the designed conservative stance — see §6 for the
  earned-trust path. (In the running code the model column is A everywhere,
  because there is no gating at all.)
- **Approval would be human-gated**, as a human decision plus an explicit
  execute step (approve ≠ execute). No such step exists today; there is no
  configuration value that turns one on. Building it is the work, not
  configuring it.

## 4. User-presence model

Some actions are only meaningful with the user at the keyboard (`ui_act` on a
trusted surface, credential flows per operator contract). Presence is a
**signal jarvisd computes**, never an input a caller asserts:

```text
UserPresence = present | recently_active | absent
```

- `present` — a user-source request in the last N seconds (default 120),
  or an active hold ListeningLease, or an approval decided interactively.
- `recently_active` — last user-source activity within M minutes (default 10).
- `absent` — otherwise.

Usage (design):

- Presence **restricts**, it never loosens a cell below its matrix value —
  e.g. future per-surface trust for `ui_act` (§6) may require `present`;
  `absent` may demote selected user-column A cells (e.g. `screen_read`
  narrow) to AP, since "user-directed" is implausible without a user.
- Presence transitions emit no dedicated event type; presence is derived
  state, queryable via `/state`, recorded in approval/tool_run metadata.

## 5. Transport token — ✅ BUILT (the one implemented part of this document)

> **Shipped with different names, and switched off.** The header is
> `X-DAN-Token` (`dan/security/transport.py`), the token file is
> `~/.dan/runtime/api-token`, enforcement lives in `dan/daemon/lifecycle.py` —
> and every check is wrapped in `security.api_token_required`, which is `false`
> in the owner's live config, so what that leaves reachable is analysed in
> [SECURITY_MODEL.md](SECURITY_MODEL.md) §2 ("Who can reach a tool"). Read the
> code and that section rather than the design sketch below.


Localhost binding is not authentication: any local process (including a
browser page doing `fetch` to `127.0.0.1`) can hit the API today. Before
`file_write`/`shell_*` tools exist:

- jarvisd generates a random token on first start:
  `~/.jarvis/runtime/api-token`, mode `0600`, regenerated via CLI
  (`jarvis token rotate`).
- **Mutating endpoints** (`POST/PATCH/DELETE`, including `/input/text`,
  approvals, execute-approved, memory writes, settings) require header
  `X-Jarvis-Token`. Read-only `GET` endpoints stay tokenless in C1
  (re-evaluated before D2 `ui_act`).
- CLI reads the token file directly (same UID). The static cockpit prompts
  once and keeps it in `localStorage`; served-by-daemon cockpit (later) gets
  it injected.
- Constant-time comparison; missing/wrong token ⇒ `401` with no detail;
  the token itself is redaction-sensitive (never in events/logs — covered by
  key-based redaction: `token`).
- CORS stays as-is (restricted origins); the token is the second, independent
  layer.

## 6. Earned trust — the relaxation path (design, not scope)

The conservative `model` column is the starting point, not a religion. The
only sanctioned relaxation mechanism, all deferred until after MVP-operator:

1. **Per-tool trust flags** in config (`trusted_model_tools = ["echo", ...]`)
   flipping `model × safe_read/safe_status` from AP to A — explicit user act,
   auditable, reversible.
2. **Per-surface trust** for `ui_act` × user column (e.g. "typing into
   Terminal windows I named") — requires presence `present` + D-phase
   experience before design.
3. **Hard boundaries (by design):** the `auto` column (scheduled work), `audio_input`
   (mic is user-gated), `secret_ref` (no tool sees values), out-of-roots file
   access, non-whitelisted automations. Modes like `auto_approve_mode` relax
   other cells; these remain closed even in full bypass.

## 7. Implementation contract for FAZA C2 (summary)

- `decide(risk, *, source, tool_name, payload)` — `source: RequestSource`
  becomes a **required** keyword argument; no default (fail-closed at the
  call site, enforced by type and tests).
- Both existing call paths (direct tool request API, model tool-call capture)
  pass their true source; tests cover every cell of the §3 matrix that is
  implementable with existing classes.
- `ToolPermissionResult` gains `source` in its reason metadata; approval rows
  and `approval.created` events record the source (they already carry
  `requested_by` — semantics unified then).
- New operator classes enter the enum **only** when their capability stage
  lands (D1 adds `ui_read`, etc.) — the matrix column is designed now so the
  enum growth is mechanical, not architectural.

---

*GATE B: this document and MACOS_CAPABILITIES.md require Ozzy's review before
FAZA C implementation begins (MASTER_PLAN §5, Gate B).*
