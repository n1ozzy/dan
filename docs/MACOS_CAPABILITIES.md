# Jarvis v4.2 — macOS Capability Inventory

> **Status:** DESIGN (FAZA B1, [MASTER_PLAN.md](MASTER_PLAN.md)). This document
> inventories macOS capability classes Jarvis may be designed to use. It is an
> **inventory, not a roadmap**: nothing here is an implementation commitment
> until promoted by a scoped stage in MASTER_PLAN.md
> ([MACOS_OPERATOR_CONTRACT.md](MACOS_OPERATOR_CONTRACT.md): examples ≠
> commitments). No runtime code accompanies this document.

Every capability below is described with:

- **Frameworks** — the macOS API surface involved.
- **Gives Jarvis** — what the capability enables, in operator terms.
- **Risk class** — proposed permission class (existing classes from
  [SECURITY_MODEL.md](SECURITY_MODEL.md) plus operator classes defined in
  [MACOS_PERMISSION_MODEL.md](MACOS_PERMISSION_MODEL.md)).
- **macOS permission (TCC)** — what the user must grant in System Settings.
- **Privacy/security concern** — what can go wrong.
- **Future tool names** — reserved names; a name here is a *reservation*, not
  a promise.
- **Status** — one of:
  - `planned:<stage>` — assigned to a MASTER_PLAN stage,
  - `later` — desired, not yet assigned to any stage,
  - `design-only` — needs its own contract before any stage assignment,
  - `not-selected` — considered and rejected or overridden by a user decree,
  - `out` — explicitly out of scope for v4.x.

Decision authority: tool and framework choices are **decreed by Ozzy**
(MASTER_PLAN §7.5). This inventory recommends; it does not decide.

---

## 1. Screen reading — ScreenCaptureKit + Vision OCR

- **Frameworks:** ScreenCaptureKit (capture of displays/windows), Vision
  (`VNRecognizeTextRequest`, on-device OCR, barcode detection).
- **Gives Jarvis:** "look at my terminal", "read this error", "what does the
  panel show" — capture a window or region, OCR it, feed the text into a turn
  as tool result. Fully on-device.
- **Risk class:** `screen_read` (narrow, user-directed) / broad capture is a
  separate, more restricted shape of the same class.
- **macOS permission:** Screen Recording (TCC). One-time grant to the process
  hosting jarvisd's capture adapter.
- **Privacy/security concern:** the screen can contain secrets (password
  managers, tokens in terminals). OCR output MUST pass EventStore redaction
  before persistence; captures themselves are transient artifacts, never
  written into the DB.
- **Future tool names:** `screen_read_current_window`, `screen_ocr_region`,
  `screen_ocr_terminal`.
- **Status:** `planned:D4`.

## 2. UI observation and action — Accessibility API

- **Frameworks:** ApplicationServices / AXUIElement, NSWorkspace (frontmost
  app), CGEvent (synthetic input as last resort).
- **Gives Jarvis:** active app/window names, focused text field, control
  labels and values (read); clicking buttons, typing into fields, menu
  actions (act). "Paste this into the terminal", "switch to Chrome".
- **Risk class:** `ui_read` (observation) / `ui_act` (actions). Two distinct
  classes — never conflated.
- **macOS permission:** Accessibility (TCC). Grant is process-wide, which is
  why actions still go through ApprovalGate per call.
- **Privacy/security concern:** the single most dangerous capability in this
  inventory. `ui_act` without per-call gating is "a model with a mouse".
  Focused-field reads can capture passwords mid-typing — `ui_read` results
  pass redaction, and secure input fields (kSecureTextField) are never read.
- **Future tool names:** `ui_active_app`, `ui_read_window`, `ui_click`,
  `ui_type`, `ui_focus_app`.
- **Status:** `planned:D1` (`ui_read`), `planned:D2` (`ui_act`).

## 3. Speech-to-text — MLX whisper (decreed)

- **Frameworks:** MLX (whisper models on Apple Silicon); recording via
  Core Audio/AVFoundation + sox-class pipeline.
- **Gives Jarvis:** local STT for PTT voice input. Empirical facts recorded in
  MASTER_PLAN §4a apply (MLX holds model+stream per thread; whisper
  hallucinates on silence — garbage filters required; recording gain must be
  applied before silence detection).
- **Risk class:** `audio_input` — gated by ListeningLease
  ([CONTRACTS.md](CONTRACTS.md)); no always-on listening, ever.
- **macOS permission:** Microphone (TCC).
- **Privacy/security concern:** open mic. Lease model (hold/locked/expiry) is
  the control; transcripts pass redaction like any event payload.
- **Future tool names:** none — STT is not a model-callable tool; it is an
  input path into TurnOrchestrator.
- **Status:** `planned:G4`. Engine decreed by Ozzy (MASTER_PLAN §7.4).

### 3a. Apple Speech / SpeechAnalyzer — not selected

- **Frameworks:** Speech (`SFSpeechRecognizer`), SpeechAnalyzer (newer API).
- **Why listed:** system-native STT with **custom vocabulary** support —
  the one real advantage over whisper for project names ("Jarvis", "Codex",
  "launchd", "EventStore") that generic STT mangles.
- **Status:** `not-selected` — overridden by the MLX decree (§7.4). If PL
  recognition quality or vocabulary handling in MLX whisper disappoints in
  G4, this is the documented alternative; re-selection is Ozzy's call.
  A cheaper middle path exists: post-STT correction table for project terms.

## 4. Text-to-speech — Supertonic + Chatterbox (decreed)

- **Frameworks:** Supertonic (ONNX, fast/medium quality), Chatterbox (MLX,
  voice-clone), playback via AVFoundation/afplay under the voice broker.
- **Gives Jarvis:** the voice. Target: cloned/custom Jarvis voice (G5);
  until then, available voices from the allowed engines.
- **Risk class:** `audio_output` — broker-mediated only (ADR-005: sole
  speaker).
- **macOS permission:** none (playback).
- **Privacy/security concern:** low; broker discipline is an architecture
  concern, not a privacy one.
- **Forbidden engines (decree §7.3):** edgeTTS, piper, XTTS.
- **Status:** `planned:G3` (broker + Supertonic), `planned:G5` (Chatterbox
  voice-clone, custom voice).

## 5. File watching — FSEvents

- **Frameworks:** FSEvents (CoreServices).
- **Gives Jarvis:** watch approved repo roots — "Codex changed files",
  "a commit appeared", "tests produced an artifact". Events, not polling.
- **Risk class:** `fs_watch` (passive observation within approved roots).
- **macOS permission:** none beyond file access to watched paths (Files and
  Folders TCC if outside home-accessible scope).
- **Privacy/security concern:** path names can leak project information into
  events — payloads pass redaction; watch roots are the same fail-closed
  approved roots as `file_read`.
- **Future tool names:** `fs_watch_add`, `fs_watch_list`, `fs_watch_remove`
  (configuration tools; the watcher itself emits events).
- **Status:** `later` — natural companion to FAZA E workers; assign when
  E-stage scope is cut.

## 6. Notifications — UserNotifications

- **Frameworks:** UserNotifications (UNUserNotificationCenter).
- **Gives Jarvis:** system notifications instead of speaking everything:
  "worker finished", "approval pending", "runtime conflict detected".
- **Risk class:** `notify` (low risk; sensitive previews are the only catch).
- **macOS permission:** Notifications (user grants on first request).
- **Privacy/security concern:** notification previews on a locked/shared
  screen; sensitive content requires approval-gated preview or generic text.
- **Future tool names:** `notify_user`.
- **Status:** `later` — most valuable once approvals/workers run long
  operations (FAZA E/F); assign when E-stage scope is cut.

## 7. Secrets — Keychain

- **Frameworks:** Security.framework (Keychain Services).
- **Gives Jarvis:** secrets live in Keychain; config holds only references
  (`keychain://jarvis/<name>`). No secret in TOML, .env, DB, events or logs.
- **Risk class:** `secret_ref` — resolution happens **inside jarvisd only**;
  there is deliberately no tool that returns a secret value to a model.
- **macOS permission:** Keychain item ACL (per-item, per-app).
- **Privacy/security concern:** the point of the class is that models and
  events never see values. Redaction remains the second line of defense.
- **Future tool names:** none model-callable; CLI/config surface only
  (`jarvis secret set/list/rm`).
- **Status:** `design-only` — needs a small contract (ref format, resolution
  points, migration from env) before assignment; earliest sensible slot is
  FAZA C (before real network/API tools need keys).

## 8. Whitelisted automations — Shortcuts / App Intents

- **Frameworks:** Shortcuts (via `shortcuts run` CLI or App Intents).
- **Gives Jarvis:** "tools lite" — user-authored, whitelisted automations
  (open project, collect logs, make review snapshot). Jarvis triggers them;
  their content is authored and audited by the user in Shortcuts.app.
- **Risk class:** `automation_run` — whitelist-only; a non-whitelisted
  shortcut name is `blocked`, not `approval`.
- **macOS permission:** Automation (TCC) prompts per target app on first run.
- **Privacy/security concern:** a shortcut can do anything the user can;
  hence whitelist + approval defaults, and the whitelist lives in config,
  not in model-writable state.
- **Future tool names:** `automation_run`, `automation_list`.
- **Status:** `later`.

## 9. AppleScript / JXA / Automator — deferred in favor of 8

- **Gives Jarvis:** app scripting (Finder, Terminal, Safari, Mail, Notes).
- **Why deferred:** overlaps with Shortcuts/App Intents (8) which offer the
  same reach with a user-auditable authoring surface and cleaner TCC story.
  Raw AppleScript execution from a model path is a shell in a trenchcoat.
- **Status:** `design-only` — only if a concrete need exceeds what 8 covers;
  would require its own contract and a `shell_write`-grade risk treatment.

## 10. Local language processing — NaturalLanguage

- **Frameworks:** NaturalLanguage (NLLanguageRecognizer, NLTokenizer,
  NLTagger).
- **Gives Jarvis:** cheap, local, pre-brain routing: PL/EN/NOR detection,
  tokenization, entity hints, memory tagging (decision/fact/preference) —
  before spending tokens on a big model.
- **Risk class:** `safe_read` (pure computation, no side effects).
- **macOS permission:** none.
- **Privacy/security concern:** none beyond ordinary payload handling.
- **Future tool names:** none — internal pipeline helpers, not model tools.
- **Status:** `later`.

## 11. Local ML helpers — MLX / Core ML

- **Frameworks:** MLX (already in stack for STT/TTS), Core ML for packaged
  `.mlmodel` classifiers.
- **Gives Jarvis:** local embeddings, memory reranking, intent classification
  ("is this a voice command?", "does this need approval?", "is this worth
  remembering?"). Auxiliary local brains — not the main brain.
- **Risk class:** `safe_read` for pure inference; anything acting on the
  result inherits the acted-on class.
- **macOS permission:** none.
- **Privacy/security concern:** classifier outputs influencing policy would
  be a policy bypass — classification may *suggest*, PermissionPolicy
  *decides*. This boundary is contractual.
- **Status:** `later` (embeddings/reranking earliest after MVP-operator).

## 12. Documents — PDFKit + Quick Look + Vision

- **Frameworks:** PDFKit (text extraction, rendering), Quick Look (preview),
  Vision OCR for scanned pages.
- **Gives Jarvis:** read manuals, invoices, technical PDFs within approved
  roots.
- **Risk class:** `file_read` (existing class; fail-closed roots apply).
- **macOS permission:** none beyond file access.
- **Future tool names:** `pdf_read`, `pdf_ocr_page`.
- **Status:** `later`.

## 13. Clipboard — Pasteboard API

- **Frameworks:** AppKit NSPasteboard.
- **Gives Jarvis:** "take the text from my clipboard" (read), "put this on
  the clipboard" (write).
- **Risk class:** `clipboard_read` / `clipboard_write`. Clipboards routinely
  contain passwords — read is NOT a safe_read.
- **macOS permission:** none (macOS 15+ shows paste prompts for cross-app
  reads in some contexts).
- **Privacy/security concern:** password managers put secrets on the
  clipboard; reads pass redaction and are approval-gated for model-originated
  calls.
- **Future tool names:** `clipboard_read`, `clipboard_write`.
- **Status:** `later`.

## 14. Considered and parked

| Capability | Frameworks | Verdict |
|---|---|---|
| Translation | Translation.framework | `out` for v4.x — brains translate well enough; revisit only for offline-only flows |
| Spotlight search / Core Spotlight index | CoreSpotlight, NSMetadataQuery | `later` — useful for "find that file" tools and memory indexing; no stage until a concrete need |
| SoundAnalysis (audio classification) | SoundAnalysis | `later` — potential VAD/trigger refinement in G-phase; MLX/sox path comes first |
| Network.framework | Network | `out` — localhost HTTP suffices; no peer-to-peer requirement exists |
| Metal / Accelerate / BNNS | MPS, Accelerate | `out` as direct dependencies — used transitively by MLX/ONNX runtimes |
| App Sandbox / hardened runtime packaging | — | `later` — packaging concern for FAZA H |
| launchd | launchd | already `planned:F2` in MASTER_PLAN (not an operator capability) |

---

## 15. Priority view (operator value, per MASTER_PLAN)

1. **D1/D2** Accessibility (`ui_read` → `ui_act`) — the operator's hands.
2. **D4** ScreenCaptureKit + Vision OCR (`screen_read`) — the operator's eyes.
3. **G3/G4/G5** TTS/STT per decrees — the operator's mouth and ears.
4. **Keychain** (`secret_ref`) — design-only, earliest FAZA C.
5. **FSEvents / UserNotifications** — with FAZA E workers.
6. **Shortcuts, NL, MLX-aux, PDFKit, Clipboard, Spotlight** — `later`, each
   promoted only via its own scoped stage.

Everything above executes exclusively through
`ToolRegistry → PermissionPolicy → ApprovalGate → EventStore`
([SECURITY_MODEL.md](SECURITY_MODEL.md)); the source-sensitive decision
matrix lives in [MACOS_PERMISSION_MODEL.md](MACOS_PERMISSION_MODEL.md).
