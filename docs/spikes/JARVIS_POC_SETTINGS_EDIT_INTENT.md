# JARVIS POC Settings Edit Intent

Status: spike / proposed contract, not implemented
Authority: non-authoritative design note
Scope: future backend edit-intent schema for safe settings editing

This spike defines a backend-owned edit-intent shape for future settings UI work.
It explicitly does not authorize the panel to mutate raw `/settings`, and it does
not introduce any mutation endpoint.

## Problem

The panel needs to become capable of showing which settings are safe to edit,
which are runtime-derived, and which are blocked by dependencies or restart
requirements. Raw settings are not enough for that because they expose storage
shape rather than product intent.

For POC safety, Jarvis should first expose backend-curated edit metadata. The
panel can render this metadata later, but all mutation rules must stay owned by
`jarvisd`.

## Proposed edit-intent schema

Each editable or inspectable setting should be represented as one intent row:

```json
{
  "setting_id": "persona.profile",
  "group": "persona",
  "current_value": "gangus-3",
  "effective_value": "gangus-3",
  "allowed_values": ["default", "gangus-3"],
  "disabled_reason": null,
  "requires_restart": false,
  "requires_dependency_check": false,
  "invalidates_children": [],
  "preview_warning": null
}
```

Field contract:

- `setting_id`: stable backend identifier, not a raw config path by default.
- `group`: UI grouping hint, for example `persona`, `brain`, `voice`, `audio`, or `model`.
- `current_value`: stored configured value, if present.
- `effective_value`: value Jarvis is actually using after defaults, fallbacks, dependency checks, and runtime gates.
- `allowed_values`: backend-curated options; `null` means free-form is theoretically possible but not approved for POC editing.
- `disabled_reason`: human-readable blocker when the setting must not be edited now.
- `requires_restart`: true when a change cannot be applied safely to the running daemon.
- `requires_dependency_check`: true when Jarvis must probe local tools, devices, models, provider CLIs, or files before accepting the edit.
- `invalidates_children`: dependent `setting_id` values that become stale or unsafe if this value changes.
- `preview_warning`: warning to show before a future apply flow, especially when behavior may degrade or become unavailable.

## Backend rules

- The panel must not write raw `/settings`.
- The panel must not infer editability from config structure.
- `jarvisd` must be the only owner of editability, validation, dependency checks, restart requirements, and persistence.
- Every future mutation must target stable `setting_id` values, not arbitrary TOML or JSON paths.
- High-risk settings should be read-only until Jarvis has dependency probes, rollback semantics, and focused regression coverage.
- Effective runtime state should win over stored config when they disagree.

## Low-risk future edit candidates

### `persona.profile`

Reason: persona profile selection is product-facing and comparatively isolated.
It should be a first candidate if the backend can enumerate available profiles
and reject missing files.

Suggested intent:

```json
{
  "setting_id": "persona.profile",
  "group": "persona",
  "current_value": "gangus-3",
  "effective_value": "gangus-3",
  "allowed_values": ["default", "gangus-3"],
  "disabled_reason": null,
  "requires_restart": false,
  "requires_dependency_check": true,
  "invalidates_children": [],
  "preview_warning": "Changing persona affects future brain prompts only; existing conversation history is unchanged."
}
```

### `brain.adapter`

Reason: brain adapter may be a valid POC edit if Jarvis can clearly separate
configured adapter from available adapter and can fail closed when dependencies
are missing. This is lower risk than voice plumbing but still needs backend
validation.

Suggested intent:

```json
{
  "setting_id": "brain.adapter",
  "group": "brain",
  "current_value": "local_cli",
  "effective_value": "local_cli",
  "allowed_values": ["local_cli", "mock"],
  "disabled_reason": null,
  "requires_restart": true,
  "requires_dependency_check": true,
  "invalidates_children": ["brain.model"],
  "preview_warning": "Changing adapter can change response quality and tool-call behavior."
}
```

### `input.ptt_hotkey`

Reason: push-to-talk hotkey is useful but should be treated as later-stage POC
editing because it touches user input capture and platform-specific behavior.
It should not be first unless the backend has hotkey conflict detection.

Suggested intent:

```json
{
  "setting_id": "input.ptt_hotkey",
  "group": "input",
  "current_value": "ctrl+space",
  "effective_value": "ctrl+space",
  "allowed_values": null,
  "disabled_reason": "Deferred until hotkey conflict detection exists.",
  "requires_restart": false,
  "requires_dependency_check": true,
  "invalidates_children": [],
  "preview_warning": "Invalid or conflicting hotkeys can make voice capture hard to control."
}
```

## High-risk edits to keep read-only first

### `voice.enabled`

Risk: this gates voice runtime composition, recorder access, routes, broker
lifecycle, listening leases, and user expectations around microphone/speaker
state.

POC stance: read-only until runtime enable/disable has explicit lifecycle
semantics and regression coverage.

### `voice.default_stt`

Risk: changing STT can break transcription, provider dependencies, latency,
language behavior, and fallback handling.

POC stance: read-only until Jarvis has backend STT probes and clear fallback
reporting.

### `voice.default_tts`

Risk: changing TTS can break the single-speaker contract, queue behavior,
playback timing, and voice identity.

POC stance: read-only until Jarvis can probe TTS providers and preserve broker
ownership.

### `audio.playback_engine`

Risk: playback engines touch local OS audio, subprocesses, device selection,
queue drain behavior, and CI safety boundaries.

POC stance: read-only until backend validates engine availability and separates
manual smoke tests from automated checks.

### `model.local_path`

Risk: local model paths can be missing, huge, incompatible, slow, or point at
unexpected files. Bad edits may make the daemon appear hung or unusable.

POC stance: read-only until Jarvis has file existence checks, model metadata
checks, timeout handling, and rollback behavior.

## Non-goals

- No `POST /settings` endpoint.
- No patch, PUT, or arbitrary config mutation endpoint.
- No panel-side write flow.
- No raw TOML path editing.
- No schema or migration changes.
- No live dependency probes in this spike.

## Future endpoint sketch

A future read-only endpoint could expose edit intents separately from raw
settings:

```http
GET /settings/edit-intents
```

A future mutation endpoint, if added later, should accept only stable backend
IDs and values:

```http
POST /settings/edit-intents/{setting_id}/preview
POST /settings/edit-intents/{setting_id}/apply
```

The preview step should be mandatory for any setting with
`requires_restart`, `requires_dependency_check`, `invalidates_children`, or
`preview_warning`.
