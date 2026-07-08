# Jarvis Runtime POC Final Handoff

Status: spike handoff. Non-authoritative. Preserve for later mainline porting.
Scope: final review result for the local runtime POC branch.

Branch: `spike/jarvis-local-runtime-check`
Original clean-review checkpoint HEAD: `163c279`
Latest reviewed checkpoint before this docs refresh: `80dcbb5`

## Review Result

- P0: none.
- P1: unresolved as of `80dcbb5`.
  - Runtime settings/PTT smoke coverage still expects PTT-down barge-in
    cancellation, while the current rescue contract says PTT-down must not
    cancel active speech.
  - Missing `brain.model` handling suppresses the structured
    `brain_model_missing` warning in invalid preview fixtures.
- P2: Memory OS is materially coupled into the runtime POC branch.
- P2: panel PTT paths do not match backend contracts.
  - The Test PTT request must use an allowed listening source: `ptt`,
    `global_hotkey`, or `lock`.
  - PTT shortcut validation must match backend side-aware hotkey tokens parsed
    by `jarvis.panel.hotkey.parse_hotkey`.
- P3: `routes_runtime.py` and `app.js` have monolith risk.
- P3: `scripts/jarvis` has health identity risk.

## Validation

- `218 passed`.
- `git diff --check` passed.
- `node --check jarvis/panel/assets/app.js` passed.
- The validation above belongs to the original clean-review checkpoint. It is
  not fresh evidence for `80dcbb5`.

## What The POC Proved

- Backend-owned runtime truth.
- Runtime settings projection.
- Explicit unsupported, missing, and invalid states.
- Safe REST and WebSocket event payload policy.
- `stream.hello` cursor invariant.
- MLX readiness requires `mlx_lm`, not base `mlx` only.
- Preview provider recompute.
- Voice runtime split.
- PTT interruption trace, with the caveat that the current branch now treats
  PTT-down itself as a non-cancelling lease acquisition unless a later scoped
  fix intentionally changes that contract again.
- Newest-first debug timeline.
- Panel live refresh.

## Do Not Port Blindly

- Memory OS, schema, and policy changes.
- Monolithic `routes_runtime.py` and `app.js` structure.
- Lifecycle cleanup behavior.
- Panel-side capability fallback.

## Main Port Order

1. Safe event policy for REST and WebSocket.
2. Stream cursor invariant.
3. Minimal runtime settings projection slice.
4. Minimal voice runtime split slice.
5. Settings preview contract.
6. MLX readiness fix.
7. PTT interruption trace.
8. Panel live/debug timeline.
9. Lifecycle identity hardening.
10. Broader settings cockpit only after the above are stable.

## Handoff Warnings

- Do not wholesale merge this branch.
- Split Memory OS into a separate workstream.
- Split the runtime route and panel JavaScript before serious mainline work.
