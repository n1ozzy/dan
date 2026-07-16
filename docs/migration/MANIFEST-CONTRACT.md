# DAN Release 1 source manifest contract

The Release 1 manifest freezes machine-local source truth before rename, data
migration, or runtime cutover. It is a private evidence file, not a product
configuration file and not a migration input that may silently change behavior.

## Location and permissions

The canonical machine manifest is written to:

```text
$HOME/.dan/migration/release1-source-manifest.json
```

`scripts/dan-inventory` writes through a sibling temporary file, calls `fsync`,
uses `os.replace`, and leaves the destination mode at `0600`. The containing
directory is mode `0700`. The CLI creates or hardens only the exact canonical
`$HOME/.dan/migration` directory. For any other output location, the writer
requires an existing directory to be `0700` and fails rather than silently
changing it or following a directory symlink. The file stays outside Git because
it contains machine paths, safe runtime identities, database counts, and local
topology. Raw process command lines, arguments, prompts, and environment values
are never serialized.

The output path is always a structural exclusion. A previous manifest may exist
while a replacement observation is collected, but it must never become a
`runtime_paths` row or contribute its previous SHA-256 to its successor.

Generation is read-only with respect to all inventoried sources. It may create
only the destination directory, temporary manifest, and final manifest. It does
not stop or signal processes, unload launch agents, write SQLite pragmas, copy
WAL/SHM files, follow a symlink for mutation, or start audio.

## Schema version 1

The root object contains:

- `schema_version`: exactly `1`;
- `generated_at`: UTC timestamp for this observation;
- `selected_base`: inventory worktree, branch/ref, and head SHA;
- `roots`: observed home, repository, temporary root, and structural exclusions;
- `surfaces`: exactly the fourteen required Release 1 surfaces.

The required surfaces are:

| Surface | Evidence recorded |
|---|---|
| `repositories` | path, existence, Git/non-Git state, branch, head, dirty paths and content-free WIP/patch SHA-256 values |
| `git_refs` | every local/remote/rescue/spike ref, head, upstream, commits unreachable from the chosen base |
| `processes` | matching live PID, parent PID, classified role, executable basename, content-free runtime signature, observation status |
| `launchd` | relevant plist hashes plus matching loaded labels/PIDs/last exit state |
| `databases` | `user_version`, `schema_version`, journal/WAL mode, table names, and row counts |
| `voice_assets` | paths, file sizes, modes, symlink targets, and SHA-256 hashes |
| `config_sources` | every known persona, voice, override, installation, owner, secret-path, host setting, and global/repository instruction source |
| `skills` | active and plugin-provided skill trees for Agents, Claude, Codex, OpenClaw, and every inventoried repository-local adapter |
| `hooks` | active Claude hooks and helper binaries |
| `symlinks` | link path, raw and normalized target, broken/existing state, relative/absolute form, inside/outside-root result, scope decision, and an allowed regular-target SHA-256 when size permits |
| `producers` | executable/config/injected-instruction or historical-memory files containing a known speech/request contract, with reference class and named activity evidence |
| `request_formats` | each discovered old/new request format and its producer evidence |
| `runtime_paths` | `$HOME/.dan`, `$HOME/.jarvis`, `/tmp/dan-*`, and `/tmp/claude-loud-thinking` metadata |
| `input_materials` | old Radio plan, desktop visualizer, private research summaries, recursively hashed Voice Lab evidence, and named quarantine candidates |

Every regular file entry is content-free and may contain only path metadata and
its SHA-256. Symlink targets are fully normalized without uncontrolled traversal.
An outside-root, excluded, broken, non-regular, oversized, changed, or unreadable
target is not hashed; the symlink row retains its safe state and scope decision.
Only an existing regular target inside an allowed root and below the size limit
may be opened and hashed. A missing optional source remains `missing`; a missing
required source is an unresolved path error. Neither is silently omitted.

Dirty repositories additionally record porcelain status/path rows, the current
file or symlink SHA-256 for each WIP entry, separate tracked/staged/unstaged patch
SHA-256 values, and a canonical untracked-tree SHA-256. Patch bytes and file
contents are hashed in memory and never serialized. Unborn repositories use an
explicit staged-plus-unstaged basis instead of inventing a `HEAD`.
Git pathspec exclusions remove `.superpowers/`, VCS internals, virtualenvs,
dependency trees, bytecode, and generated test caches before WIP paths or patch
hashes are collected.
Filesystem walks use an error callback, and path, read, hash, race, permission,
decode, malformed-record, and non-zero probe failures become content-free
`path_error` or `probe_error` rows. One bad path cannot abort the rest of the
inventory. Git probes run with optional locks disabled. A failed status or diff probe is
recorded as `git-*-probe-error` and can never be mislabeled `clean`. Failed ref
or ancestry probes produce explicit `git_refs` error rows instead of an empty
surface that pretends there were no refs.

Every surface row also carries a non-empty `decision`. The decision names the
accepted Release 1 disposition (`migrate`, `retain`, `replace`, `disable`,
`archive/do-not-copy`, or the later task that owns the controlled transition).
Git refs remain physically unchanged in Task 1 and are additionally resolved at
commit level in `REF-DECISIONS.md`.

SQLite databases are opened with URI `mode=ro` and `PRAGMA query_only=ON`.
The surface allows only user/schema version, journal/WAL mode, table names, and
record counts. File SHA-256, mode, size, open handles, row values, conversation
text, memory text, tokens, and secrets are forbidden. Any open-handle comparison
used by a human review remains transient evidence outside both the SQLite surface
and the private manifest.

## Exclusions and historical candidates

`$HOME/.claude/archive` is always excluded structurally, even if the caller
forgets `--exclude`. Repository `.superpowers/`, VCS internals, virtualenvs,
caches generated by test tools, and dependency trees are not traversed.

The named production roots include active `$HOME/.agents`, `$HOME/.claude`,
`$HOME/.codex`, `$HOME/.openclaw`, all four repositories, global instructions,
and LaunchAgents. Producer discovery traverses their executable, config, skill,
hook, service-env, workspace, and injected-instruction subsets rather than
private session histories or logs.

Reference discovery additionally covers the current Claude project-memory
directories for the inventoried repositories and `$HOME/.openclaw/workspace/memory`.
It records only path/hash metadata, detected format classes, and reference
classification; memory contents never enter the manifest. Archives and session
histories remain excluded.

Every detected reference is classified as an active runtime producer, active
consumer/instruction, historical memory reference, inactive backup/archive
candidate, or unproven runtime reference. Runtime activity requires named
evidence such as a matching process, launchd/config/hook call, executable call,
or active-skill invocation. Executable files without a conventional suffix and
`.bak-*` files are still inspected, but executability, backup location, or a
textual mention alone does not prove active production.

The named quarantine candidates are not deleted or copied. The inventory scans
active code and injected instructions for callable consumers. Only named runtime
evidence marks a candidate `active-source`; a historical-memory mention or plain
text reference cannot revive it from `archive/do-not-copy`.
The collector implementation, its tests/migration docs, and its own short-lived
process are never accepted as producer or consumer evidence. Either result is
only a Task 1 decision input.

## Commands and validation

```bash
mkdir -p "$HOME/.dan/migration"
python scripts/dan-inventory \
  --output "$HOME/.dan/migration/release1-source-manifest.json" \
  --exclude "$HOME/.claude/archive"
chmod 600 "$HOME/.dan/migration/release1-source-manifest.json"
python scripts/dan-inventory \
  --check "$HOME/.dan/migration/release1-source-manifest.json"
shasum -a 256 "$HOME/.dan/migration/release1-source-manifest.json"
```

`--check` applies an exact allowlist to the root, all fourteen surfaces, and every
nested metadata/error/symlink/activity/WIP structure. It rejects unknown fields,
raw argv or command lines, text/content/record/payload containers, SQLite-private
metadata, empty decisions, `pending`, `TBD`, `TODO`, unresolved errors on required
surfaces, and any mode other than `0600`. It prints the manifest SHA-256 and
per-surface counts. It does not pretend that a later-changing live process or
database still matches the earlier observation. Runtime drift is evaluated
explicitly at the Task 1 review gate and forces regeneration before any later
cutover.

`--check` also rejects a manifest that inventories its own destination. Surface
rows are emitted in canonical JSON order, so identical observed state is stable
apart from `generated_at`; live-state changes remain explicit rather than being
masked by incidental `ps`, `launchctl`, or filesystem iteration order.

## Task 1 review gate

The manifest is acceptable only after a human-readable comparison against fresh
`ps`, `lsof`, `launchctl`, Git refs, and filesystem evidence. Every discovered
source, process, producer, request format, quarantine candidate, and WIP ref must
have a named decision. `pending`, `TBD`, an empty decision, or a missing active
producer keeps the gate red.
