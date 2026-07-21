# macOS Operator Contract

> **Naming:** `jarvisd` / `com.ozzy.jarvisd` below = today's `dand` /
> `com.dan.dand` (API `127.0.0.1:41741`).
>
> ## ⚠️ Every "Approval default" in this document is an ASPIRATION (2026-07-21)
>
> There is no approval gate in the runtime. `ToolPermissionPolicy.decide()`
> returns ALLOW unconditionally and `ToolRegistry.request_tool()` executes
> immediately — see [SECURITY_MODEL.md](SECURITY_MODEL.md) §2. So wherever a
> table below says "approval required", "blocked", or "confirmation before
> send", read it as *what this contract wants*, not as what happens.
>
> Several capabilities this document marks **Future are already BUILT and
> registered** in `dan/daemon/app.py` and run with no gate at all:
> `ui_active_app`, `ui_read_window`, **`ui_click`**, **`ui_type`**,
> `ui_focus_app`, `screen_read_window`, `screen_ocr_region`,
> `terminal_read_screen`, **`terminal_paste`**, `web_fetch`, `file_read`,
> `file_write`, `shell_read`, `memory_save`, `memory_recall`.
>
> In plain terms: DAN can click, type, read the screen and paste into a
> terminal without asking. That is the owner's deliberate configuration for a
> single-user machine, not a defect — but this contract must not be read as
> evidence that something stops it.
>
> What still constrains these tools is inside the tools themselves
> (approved roots, the shell allowlist, the scrubbed environment) plus the
> macOS TCC grants, which are real and are enforced by the OS.

## Purpose

This document defines Jarvis as a local macOS operator. The operator layer is
core product direction, not an optional later gimmick: Jarvis is meant to act on
the user's Mac through controlled local capabilities, not merely describe what
the user should click.

This is an architectural contract only. It does not implement Accessibility,
screen capture, OCR, browser control, SMS, phone, passkey, voice, or live visual
operator capabilities yet.

## Core principle

If the user can do an action through the Mac UI, Jarvis may be designed to do it
on the user's behalf through controlled runtime capabilities.

The model never operates the Mac directly. `dand` operates the Mac through
`ToolRegistry`, the backend adapters and `EventStore`. Models propose operator
actions; the daemon executes and records them.

It does **not** decide whether they are allowed: `PermissionPolicy` allows
everything and `ApprovalGate` is not in the execution path. The mediation that
remains is adaptation and audit — a real and useful property, but not a veto.

## Examples vs commitments

Examples such as online games, SMS, phone calls, passkey-assisted login, and
browser workflows illustrate operator capability classes. They are not automatically implementation commitments.

A concrete capability becomes committed only when it is promoted by a later scoped prompt, contract, test plan, and permission model. This contract defines
the shape of possible operator work, not a promise to implement every example
immediately.

## Operator-class capability areas and examples

Operator-class capabilities may include, after explicit promotion and design:

- Accessibility API capability areas: observe the active app/window, inspect the
  focused element, read selected or focused text, click UI elements, set focus,
  type or paste text, press hotkeys, and drag or move the mouse.
- ScreenCaptureKit and Vision OCR capability areas: read visible terminal,
  browser, panel, or screen state under explicit scope controls.
- Browser operation examples: open URLs, fill forms, navigate website workflows,
  or coordinate with a passkey/user-presence flow.
- Credential/user-presence examples: trigger a passkey prompt or Keychain
  unlock while the user remains responsible for Touch ID, device password, or
  another system confirmation.
- External communication examples: compose or send messages, SMS, email, posts,
  or call-initiation requests after a separate communication policy exists.
- Terminal/iTerm examples: observe terminal state, paste prepared commands, or
  operate an approved terminal workflow.
- Live visual control examples: complete a visual website task or manipulate a
  web canvas through an `OperatorSession` loop.
- File/repo observation examples: monitor approved repo/worktree roots through
  FSEvents.
- Notification examples: use UserNotifications for approval, worker, or
  operator-session status.
- Secret reference examples: use Keychain for credential references without
  exposing secret values to models or event logs.

Accessibility API, ScreenCaptureKit, and Vision OCR are core macOS capability
areas for the operator direction. Specific tools built on them still require
separate design prompts, contracts, test plans, and permission policy before
implementation.

## macOS capability mapping

| Capability | macOS technology | Replaces old DAN-style approach | Risk | Approval default | Status |
|------------|------------------|----------------------------------|------|------------------|--------|
| UI observation and action | Accessibility API | Uncontrolled AppleScript and shell glue | High: can click, type, and manipulate apps | Read-only allow for approved roots/surfaces; actions require approval by default | Future |
| Active window and screen state | ScreenCaptureKit | Screenshot shell snippets and ad-hoc polling | Medium/high: may expose private screen content | Approval for capture scopes; visible session indicator required | Future |
| Text recognition from screen/terminal | Vision OCR | Fragile terminal scraping and manual transcript guessing | Medium: can expose sensitive visible text | Approval for broad capture; allow only narrow capture when user directly requests it | Future |
| Speech input | Speech/SpeechAnalyzer | Separate listener loops with drift-prone state | Medium: microphone privacy and accidental capture | Explicit listening lease; no always-on capture by default | Future |
| Audio capture/playback | AVFoundation/Core Audio | Multiple player/capture paths outside the daemon | Medium: mic/speaker control and privacy | Broker/device policy only; voice runtime separately approved | Future |
| Credentials and secrets | Keychain | Secrets in env/logs/state files | High: credential exposure | User presence and explicit approval; never expose secret values to models | Future |
| External communication examples | Messages/Shortcuts/App Intents | Ad-hoc AppleScript-style control | High: external communication and billing/social risk | Confirmation before send/call unless trusted policy explicitly allows | Example class; not committed until promoted |
| User notifications | UserNotifications | Terminal noise and background log watching | Low/medium: attention and privacy | Allow for status; approval for sensitive content previews | Future |
| File/repo change observation | FSEvents | Polling watchers and loose file flags | Low/medium: filesystem metadata exposure | Allow inside approved repo roots | Future |
| Local language/model helpers | NaturalLanguage/Core ML/MLX | Provider-only inference for local tasks | Medium: local model outputs may affect actions | Same policy as the action they prepare or trigger | Future |
| PDF inspection | PDFKit | Shell extraction and brittle text scraping | Low/medium: document content exposure | Allow file-read policy within approved roots | Future |

## Operator action classes

| Class | Examples | Default approval behavior |
|-------|----------|---------------------------|
| observe/read-only | Active app/window, focused element, selected text, narrow screen read, repo status | Allow only in approved scopes; broad screen capture requires approval. |
| prepare/compose | Draft message, prepare command, fill form text without submitting | Allow when side-effect-free; submitting remains separately gated. |
| reversible UI action | Focus a field, switch tab, click non-submitting UI, paste draft text | Approval required unless direct user command and trusted surface policy allow it. |
| external communication | Send SMS, send Message, send email, post form, initiate call | Example class; requires separate communication policy, contact resolution, audit model, and confirmation rules before implementation. |
| credential/user-presence action | Trigger passkey prompt, unlock Keychain item, continue login after Touch ID | Example class; user presence required, and Jarvis never owns or extracts credentials. |
| destructive/high-risk action | Delete files, overwrite data, submit payment, change account settings | Blocked or explicit approval only; no default auto-approval. |
| live visual control session | Browser task loop, simple online game, multi-step UI workflow | Starts only with explicit approval, produces events, exposes stop controls, and has timeout/interrupt conditions. |

## Passkey / user presence model

Passkey-assisted login is an example of a credential/user-presence flow, not an
automatic implementation commitment. Jarvis must never extract, serialize, own,
store, or bypass passkeys. A promoted future capability may navigate to a login
page, select fields, trigger the passkey flow, and continue after the user
confirms through Touch ID, device password, or another system user-presence
confirmation.

The user-presence confirmation remains with the user and macOS. `EventStore`
records the flow, target app/site, and decision metadata without secrets,
passkey material, passwords, or raw credential payloads.

## External communication examples

SMS, Messages, email, posting, and phone call initiation are external communication examples, not committed immediate roadmap items. If any of these
examples are promoted later, message composition should be auditable: Jarvis
records what it intended to send, to which contact identifier class, and under
which user command or approval, while avoiding private secret payloads in
events.

A promoted communication capability requires separate communication policy,
contact resolution, audit model, and confirmation rules before implementation.
The default policy can require confirmation before sending or calling unless
later user config allows trusted contacts, narrow direct commands, or other
explicitly accepted shortcuts. Autonomous phone conversation would be a separate,
higher-risk capability than call initiation or sending a prepared message.

## Live visual operator sessions

Some tasks are not one-shot tools. Online pool/billiards, multi-step web forms,
web canvas manipulation, and interactive browser tasks are examples of live
visual control sessions, not promised features. Any concrete live visual session
requires separate feasibility and design work before implementation.

The architectural shape is `OperatorSession`, not ordinary one-step tool
execution. An operator session has a start event, step/progress events,
stop/cancel events, policy state, user-visible stop controls, timeout limits,
and a final outcome. The session loop still uses registered capabilities; it
does not give the model direct control of the Mac.

## Relationship to approval loop

Prompt 19A, Prompt 19B, and Prompt 19C created the foundation for approval
decision events, `PermissionPolicy`, and `awaiting_approval` turns. Prompt 19D-mini
implements only current one-shot continuation-eligible tool results after
explicit execute-approved. It is not the full `OperatorSession` model and does
not treat every example in this document as a committed tool or every future
operator capability as one-shot.

Future user-presence, external communication, worker, and live visual session
result classes stay separate from one-shot continuation until promoted by later
scoped prompts.

`ApprovalGate` is not meant to make Jarvis useless; it is meant to manage risk.
Direct user commands may have different policy than model-originated autonomous
actions, but both still pass through daemon-owned policy and audit.

## Relationship to voice

Voice is not the only interface. Jarvis should be able to operate via text,
panel, voice, and eventually wake word. Operator actions must not depend on
voice.

Voice can trigger operator tasks, but execution belongs to `jarvisd`. A spoken
request enters the same daemon-owned turn and tool/operator pipeline as typed
text or panel intent.

## Relationship to old DAN

Old DAN is legacy reference only. Old DAN-style shell glue, `/tmp` state,
uncontrolled AppleScript, ad-hoc watchers, and direct model action should not be
copied.

Useful intent from old DAN is preserved: local operator ambition, sharp
personality, voice-first ambition, and terminal/project assistance. Jarvis
replaces glue with controlled macOS capabilities mediated by `jarvisd`.

## Non-goals for the current phase

- No Accessibility implementation yet.
- No browser automation yet.
- No SMS sending yet.
- No phone calls yet.
- No live game playing yet.
- No passkey automation yet.
- No voice runtime yet.
- This document defines the contract before implementation.

## Near-term implementation guidance

1. Before concrete macOS operator tools are implemented, add a scoped capability
   inventory and permission model.
2. Promote specific capabilities only through later scoped prompts, contracts,
   test plans, and permission policy. SMS, phone, passkey-assisted login,
   browser workflows, and live visual sessions remain examples until promoted.
3. Voice/PTT/wake word work remains separate unless a later prompt explicitly
   scopes it.

## Reviewer checklist

- Does this design preserve `jarvisd` as source of truth?
- Does any model operate the Mac directly?
- Are user-presence actions separated from normal UI actions?
- Are external communications auditable?
- Are live sessions modeled differently from one-shot tools?
- Does approval policy distinguish direct user command from model-originated
  action?
