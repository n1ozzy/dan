# DAN Release 1 Git ref and WIP decisions

Audit date: 2026-07-16

Repository: `Documents/dev/jarvis`

Chosen integration line: `feat/dan-foundation-release1` at
`18417950a4653e5d666df745c62023778cfeb153`, created from
`spike/jarvis-local-runtime-check` at the same SHA.

The chosen head contains the accepted consolidation specification commit
`f60c42c457e07703383bf0418e9fcdbd3594fb20`. The original checkout had only the
user-owned untracked `.superpowers/`; the isolated worktree was clean before
Task 1 edits.

## Decision vocabulary

- `merge`: integrate the complete ref as a merge.
- `cherry-pick`: integrate named commits without merging the complete ref.
- `superseded`: equivalent behavior or a newer accepted contract already exists
  on the integration line.
- `archive`: preserve the ref and SHA as evidence, but do not integrate its tree.

`archive` never means delete. All four divergent refs remain untouched. No merge
or cherry-pick was executed during Task 1.

## Divergent refs

These are the complete results of local and remote `--no-merged` checks against
`feat/dan-foundation-release1`.

| Ref | Head SHA | Unique commits | Decision | Evidence | Resulting commit |
|---|---|---:|---|---|---|
| `refs/heads/claude/amazing-hawking-c80907` | `06ba7421a1287f1b4fda50d24ac3631aa0296f5d` | 1 | `archive` | The snapshot adds 453 lines around `codex_cli_adapter` and orchestration. The active branch contract requires cold Claude CLI only and forbids a provider chain. | No tree change; ref retained at `06ba7421a1287f1b4fda50d24ac3631aa0296f5d`. |
| `refs/heads/claude/fix-brain-wiring` | `5d92e987e5e550f077a6383b2c2259089d65b67c` | 4 | `archive` | One patch is already equivalent on the chosen line; the remaining work registers a warm Claude adapter, changes Groq wiring, and edits four copied persona files. That conflicts with cold Claude CLI and the sole `DAN.md` canon. | No tree change; ref retained at `5d92e987e5e550f077a6383b2c2259089d65b67c`. |
| `refs/remotes/origin/feat/live-audio-resilience` | `cd92f98d163e66f7a6f4a882e1c3836335c4289d` | 5 | `archive` | The ref adds 1,317 production lines and no tests. Its `tts.py` path predates the Release 1 immutable `RenderSnapshot`, native sole-player, and resolver contracts. Useful concepts remain donor evidence for Tasks 6 and 7, not code to merge before their RED tests. | No tree change; remote ref frozen at `cd92f98d163e66f7a6f4a882e1c3836335c4289d`. |
| `refs/remotes/origin/spike/jarvis-local-runtime-gpt-fixing` | `7333b13fd525a326fe47ef7f0c74cbae09a12cb8` | 7 | `archive` | End-state comparison against the chosen head changes 172 files, removes current memory/panel/persona/voice work, and reintroduces stale Jarvis-era docs/config. The cold-Claude requirement remains authoritative, but this old branch is not a safe patch onto the evolved runtime. | No tree change; remote ref frozen at `7333b13fd525a326fe47ef7f0c74cbae09a12cb8`. |

## Commit-level resolution for divergent work

### `claude/amazing-hawking-c80907`

| Commit | Decision | Evidence / replacement |
|---|---|---|
| `06ba742` | `archive` | Codex adapter ownership conflicts with the chosen cold-Claude-only production brain. The ref stays available as historical WIP. |

### `claude/fix-brain-wiring`

| Commit | Decision | Evidence / replacement |
|---|---|---|
| `40f2166` | `superseded` | `git cherry feat/dan-foundation-release1 claude/fix-brain-wiring` reports this patch with `-`, proving patch-equivalent content is already reachable from the chosen line. Result: `1841795`. |
| `8c8ea12` | `archive` | Registers `claude_cli_warm`; active contract explicitly forbids warm/session reuse. |
| `cfb739c` | `archive` | Groq lifecycle work belongs to a provider path excluded by the single cold-Claude production contract. |
| `5d92e98` | `archive` | Edits `gangus-*`, `jarvis.md`, and `mentor.md`; Release 1 permits only the canonical external `DAN.md` until Task 5 moves that single canon into the product. |

### `origin/feat/live-audio-resilience`

| Commit | Decision | Evidence / replacement |
|---|---|---|
| `daa5179` | `archive` | Live-resilience primitives have no accompanying tests on this ref and must be reconsidered behind Task 7 snapshot/queue RED tests. |
| `cd6b320` | `archive` | Polish alignment is useful donor research, but it has no test integration with the selected runtime or the final snapshot contract. |
| `1e7def2` | `archive` | Naturalness ranking is outside Task 1 and cannot select a hidden fallback route under the accepted design. |
| `4efeb9c` | `archive` | The PCM protocol predates the required long-lived native CoreAudio owner and immutable playback snapshot. |
| `cd92f98` | `archive` | The hardening patch rewrites legacy `tts.py`; Task 7 deliberately replaces that ownership path test-first. |

### `origin/spike/jarvis-local-runtime-gpt-fixing`

| Commit | Decision | Evidence / replacement |
|---|---|---|
| `54e6a83` | `superseded` | Current `config/jarvis.example.toml` already selects `claude_cli`; result is the chosen head `1841795`. |
| `6c8daed` | `archive` | Its cold subprocess implementation is conceptually aligned, but the patch targets an obsolete adapter/manager shape and cannot be applied without replacing later persistent-conversation, persona, tool, and memory work. The requirement remains in current `AGENTS.md`; Task 1 changes no brain code. |
| `a8b9d7b` | `archive` | Broad streamlining is inseparable from the obsolete branch tree and is not a bounded Task 1 patch. |
| `3ed2100` | `archive` | Jarvis-only operations documentation is stale relative to the accepted DAN consolidation spec and plan. |
| `a3cc8bf` | `superseded` | Release 1 defines stronger task-by-task RED/GREEN/regression/diff gates and a full isolated baseline in the accepted plan at `1841795`. |
| `5814176` | `archive` | A new network-tool surface is unrelated to source freezing and must not enter through a ref audit without its own scoped tests and product decision. |
| `7333b13` | `superseded` | The selected `BrainManager.from_config()` already constructs only `ClaudeCliAdapter`; legacy mock config cannot become a production provider. Result: `1841795`. |

## Reachable and duplicate refs

For every row below, `git merge-base --is-ancestor <head>
feat/dan-foundation-release1` returned exit code `0`, and `git rev-list --count
<ref> --not feat/dan-foundation-release1` returned `0`. Their content is already
present on the selected integration line.

| Ref | Head SHA | Resolution | Resulting commit |
|---|---|---|---|
| `refs/heads/feat/dan-foundation-release1` | `18417950a4653e5d666df745c62023778cfeb153` | selected integration line | `18417950a4653e5d666df745c62023778cfeb153` |
| `refs/heads/spike/jarvis-local-runtime-check` | `18417950a4653e5d666df745c62023778cfeb153` | identical selected source line | `18417950a4653e5d666df745c62023778cfeb153` |
| `refs/heads/main` | `8a5a0f0d502f3a55afc64d7c4ebb4d135346b503` | `superseded` by accepted runtime and consolidation commits | `18417950a4653e5d666df745c62023778cfeb153` |
| `refs/remotes/origin/HEAD` | `8a5a0f0d502f3a55afc64d7c4ebb4d135346b503` | duplicate of `origin/main`, already reachable | `18417950a4653e5d666df745c62023778cfeb153` |
| `refs/remotes/origin/main` | `8a5a0f0d502f3a55afc64d7c4ebb4d135346b503` | `superseded` by accepted runtime and consolidation commits | `18417950a4653e5d666df745c62023778cfeb153` |
| `refs/heads/rescue/audt-gpt5.5pro` | `cdf19558fb957486ae61c1b695a03f8d388c17bb` | rescue work already reachable | `18417950a4653e5d666df745c62023778cfeb153` |
| `refs/remotes/origin/rescue/audit-8a5a0f0` | `cdf19558fb957486ae61c1b695a03f8d388c17bb` | duplicate rescue ref, already reachable | `18417950a4653e5d666df745c62023778cfeb153` |
| `refs/remotes/origin/rescue/audt-gpt5.5pro` | `cdf19558fb957486ae61c1b695a03f8d388c17bb` | duplicate rescue ref, already reachable | `18417950a4653e5d666df745c62023778cfeb153` |
| `refs/heads/rescue/audt-gpt5.5pro-limit-cdn` | `0b5ea9d11eb97b829cdd84950e6477579e1bbc00` | complete Memory OS branch already reachable | `18417950a4653e5d666df745c62023778cfeb153` |
| `refs/remotes/origin/spike/jarvis-local-runtime-check` | `b18143d4a192c0e0e1414f1418c8c464d5be7d48` | remote base already reachable; local line adds five accepted consolidation commits | `18417950a4653e5d666df745c62023778cfeb153` |

## WIP safety conclusion

- No WIP ref was merged, cherry-picked, deleted, force-moved, stashed, or reset.
- The integration line stays `feat/dan-foundation-release1` at the accepted
  runtime head plus Task 1's narrow inventory commit.
- Divergent code remains addressable by exact SHA for later comparison, but no
  later task may silently import it. A later transfer requires a new failing
  test and the owner contract from the accepted Release 1 plan.
- The old cold-Claude spike exposes a real release risk: the requirement is
  authoritative while its obsolete implementation is archived. Task 1 records
  that gap rather than smuggling a 172-file regression into the foundation.
