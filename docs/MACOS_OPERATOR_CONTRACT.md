# macOS Operator Contract

## Purpose

This document defines Jarvis as a local macOS operator. The operator layer is
core product scope, not an optional later gimmick: Jarvis is meant to act on the
user's Mac through controlled local capabilities, not merely describe what the
user should click.

This is an architectural contract only. It does not implement Accessibility,
screen capture, OCR, browser control, SMS, phone, passkey, voice, or live visual
operator capabilities yet.

## Core principle

If the user can do an action through the Mac UI, Jarvis may be designed to do it
on the user's behalf through controlled runtime capabilities.

The model does not directly operate the Mac. `jarvisd` operates the Mac through
`ToolRegistry`, `PermissionPolicy`, `ApprovalGate`, `EventStore`, and audited
adapters. Models may propose operator actions; the daemon decides whether and
how those actions are allowed, approved, executed, recorded, or refused.

## What Jarvis should eventually be able to do

- Observe the active app and window.
- Inspect the focused element.
- Read selected or focused text.
- Click UI elements.
- Set focus.
- Type or paste text.
- Press hotkeys.
- Drag or move the mouse.
- Open URLs and control browser flows.
- Assist passkey login flows with user presence.
- Send SMS/messages on user command.
- Initiate calls on user command.
- Read the screen or terminal through ScreenCaptureKit and Vision OCR.
- Operate Terminal/iTerm workflows.
- Run live visual operator sessions, for example simple online games or web
  tasks.
- Monitor repo changes through FSEvents later.
- Notify the user through UserNotifications later.

## macOS capability mapping

| Capability | macOS technology | Replaces old DAN-style approach | Risk | Approval default | Status |
|------------|------------------|----------------------------------|------|------------------|--------|
| UI observation and action | Accessibility API | Uncontrolled AppleScript and shell glue | High: can click, type, and manipulate apps | Read-only allow for approved roots/surfaces; actions require approval by default | Future |
| Active window and screen state | ScreenCaptureKit | Screenshot shell snippets and ad-hoc polling | Medium/high: may expose private screen content | Approval for capture scopes; visible session indicator required | Future |
| Text recognition from screen/terminal | Vision OCR | Fragile terminal scraping and manual transcript guessing | Medium: can expose sensitive visible text | Approval for broad capture; allow only narrow capture when user directly requests it | Future |
| Speech input | Speech/SpeechAnalyzer | Separate listener loops with drift-prone state | Medium: microphone privacy and accidental capture | Explicit listening lease; no always-on capture by default | Future |
| Audio capture/playback | AVFoundation/Core Audio | Multiple player/capture paths outside the daemon | Medium: mic/speaker control and privacy | Broker/device policy only; voice runtime separately approved | Future |
| Credentials and secrets | Keychain | Secrets in env/logs/state files | High: credential exposure | User presence and explicit approval; never expose secret values to models | Future |
| Messages, SMS, calls, shortcuts | Messages/Shortcuts/App Intents | Ad-hoc AppleScript-style control | High: external communication and billing/social risk | Confirmation before send/call unless trusted policy explicitly allows | Future |
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
| external communication | Send SMS, send Message, send email, post form, initiate call | Confirmation required by default; event log records recipient/action metadata without secrets. |
| credential/user-presence action | Trigger passkey prompt, unlock Keychain item, continue login after Touch ID | User presence required; Jarvis never owns or extracts credentials. |
| destructive/high-risk action | Delete files, overwrite data, submit payment, change account settings | Blocked or explicit approval only; no default auto-approval. |
| live visual control session | Browser task loop, simple online game, multi-step UI workflow | Starts only with explicit approval, produces events, exposes stop controls, and has timeout/interrupt conditions. |

## Passkey / user presence model

Jarvis should not extract, store, or own passkeys. Jarvis can navigate to a
login page, select fields, trigger the passkey flow, and continue after the user
confirms through Touch ID, device password, or another system user-presence
confirmation.

The user-presence confirmation remains with the user and macOS. `EventStore`
records the flow, target app/site, and decision metadata without secrets,
passkey material, passwords, or raw credential payloads.

## SMS and phone model

SMS sending is a target capability. Message composition should be auditable:
Jarvis records what it intended to send, to which contact identifier class, and
under which user command or approval, while avoiding private secret payloads in
events.

The default policy can require confirmation before sending unless user config
allows trusted contacts, narrow direct commands, or other explicitly accepted
shortcuts. Calling is also a target capability. Full autonomous phone
conversation is later and higher risk than initiating a call or sending SMS.

## Live visual operator sessions

Some tasks are not one-shot tools. Playing online pool/billiards, completing a
multi-step web form, or steering an interactive browser task requires screen
capture, visual state recognition, mouse/keyboard control, loop timing, stop
conditions, and interruption.

Such sessions should be modeled as `OperatorSession`, not ordinary one-step
tool execution. An operator session has a start event, step/progress events,
stop/cancel events, policy state, user-visible stop controls, timeout limits,
and a final outcome. The session loop still uses registered capabilities; it
does not give the model direct control of the Mac.

## Relationship to approval loop

Prompt 19A, Prompt 19B, and Prompt 19C created the foundation for approval
decision events, `PermissionPolicy`, and `awaiting_approval` turns. Prompt 19D
tool-result continuation must account for both future one-shot tools and longer
operator sessions.

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

## Near-term implementation sequence

1. Prompt 19D: tool-result continuation MVP, aware of future operator sessions.
2. Prompt 20B: macOS capability inventory and permission model.
3. Prompt 21A: Accessibility read-only adapter.
4. Prompt 21B: Accessibility action adapter with approvals.
5. Prompt 21C: ScreenCaptureKit + Vision OCR bridge.
6. Prompt 21D: Terminal/iTerm operator profile.
7. Prompt 22A: Messages/SMS tool.
8. Prompt 22B: Browser/passkey-assisted flow.
9. Prompt 23+: live visual operator sessions.
10. Voice/PTT/wake word after operator fundamentals or in parallel only if
    isolated.

## Reviewer checklist

- Does this design preserve `jarvisd` as source of truth?
- Does any model operate the Mac directly?
- Are user-presence actions separated from normal UI actions?
- Are external communications auditable?
- Are live sessions modeled differently from one-shot tools?
- Does approval policy distinguish direct user command from model-originated
  action?
