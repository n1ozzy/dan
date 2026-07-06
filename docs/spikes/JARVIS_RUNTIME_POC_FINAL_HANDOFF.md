# Jarvis Runtime POC Final Handoff

Status: spike handoff. Non-authoritative. Preserve for later mainline porting.
Scope: final review result for the local runtime POC branch.

Branch: `spike/jarvis-local-runtime-check`
Checkpoint HEAD: `163c279`

## Review Result

- P0: none.
- P1: none.
- P2: Memory OS is materially coupled into the runtime POC branch.
- P3: `routes_runtime.py` and `app.js` have monolith risk.
- P3: `scripts/jarvis` has health identity risk.

## Validation

- `218 passed`.
- `git diff --check` passed.
- `node --check jarvis/panel/assets/app.js` passed.

## What The POC Proved

- Backend-owned runtime truth.
- Runtime settings projection.
- Explicit unsupported, missing, and invalid states.
- Safe REST and WebSocket event payload policy.
- `stream.hello` cursor invariant.
- MLX readiness requires `mlx_lm`, not base `mlx` only.
- Preview provider recompute.
- Voice runtime split.
- PTT interruption trace.
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
