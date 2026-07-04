# Jarvis Change Guards

Classification: operational guard catalogue.

## Purpose

This document gives reusable shell guards and file-boundary rules. It exists so future tasks do not slowly mutate into repo-wide goo. A noble cause, since humans keep inventing “while here”.

## Guard philosophy

- Guard the forbidden files for the task.
- Run `git diff --name-only` before commit.
- Fail loudly if a task touched the wrong area.
- Do not rely on memory or vibes.
- Guards supplement review; they do not replace review.

## Common inspection commands

```sh
git status --short --branch
git diff --stat
git diff --name-only
git diff --check
```

## Schema guard

Use when schema is forbidden.

```sh
git diff --name-only | grep -E '^jarvis/store/schema.sql|^jarvis/store/migrations.py' \
  && echo "ERROR: schema/migrations changed unexpectedly" && exit 1 || true
```

## API guard

Use when preview/API routes are forbidden.

```sh
git diff --name-only | grep -E '^jarvis/api/' \
  && echo "ERROR: API files changed unexpectedly" && exit 1 || true
```

For MemoryCompiler preview-only protection:

```sh
git diff --name-only | grep -E '^jarvis/api/routes_memory.py' \
  && echo "ERROR: memory API changed unexpectedly" && exit 1 || true
```

## Runtime/config/CI guard

```sh
git diff --name-only | grep -E '^jarvis/(daemon|runtime|tools|voice|panel)/|^config/|^\.github/|^scripts/|^launchd/' \
  && echo "ERROR: runtime/config/CI files changed unexpectedly" && exit 1 || true
```

## Provider guard

```sh
git diff --name-only | grep -E '^jarvis/brain/(claude|codex|openai|.*adapter)' \
  && echo "ERROR: provider adapter changed unexpectedly" && exit 1 || true
```

## Docs guard

Use when docs are forbidden.

```sh
git diff --name-only | grep -E '^docs/|^README.md|^AGENTS.md|^FIXME.md' \
  && echo "ERROR: docs changed unexpectedly" && exit 1 || true
```

## Docs-only guard

Use for docs-only tasks.

```sh
git diff --name-only | grep -E '^(jarvis/|tests/|config/|\.github/|scripts/|launchd/|README.md|pyproject.toml|package|.*lock)' \
  && echo "ERROR: non-docs files changed unexpectedly" && exit 1 || true
```

## Tests-only guard

Use for tests-only tasks.

```sh
git diff --name-only | grep -vE '^tests/' \
  && echo "ERROR: non-test files changed unexpectedly" && exit 1 || true
```

## Storage/API/compiler guard

Use when ContextBuilder or tests may change but storage/API/compiler must not.

```sh
git diff --name-only | grep -E '^jarvis/store/|^jarvis/api/routes_memory.py|^jarvis/memory/compiler.py' \
  && echo "ERROR: storage/API/compiler changed unexpectedly" && exit 1 || true
```

## Task boundaries

### Schema tasks

Allowed:

- `jarvis/store/schema.sql`
- `jarvis/store/migrations.py`
- schema tests
- docs only if explicitly scoped

Forbidden by default:

- runtime behavior
- provider adapters
- voice/panel
- MemoryCompiler selection logic

### MemoryCompiler tasks

Allowed by explicit scope only:

- `jarvis/memory/compiler.py`
- `tests/test_memory_compiler*.py`
- small fixture updates

Forbidden by default:

- schema/migrations
- ContextBuilder prompt wiring
- runtime/daemon
- API routes
- provider adapters
- voice/panel/config/CI/docs

### ContextBuilder tasks

Allowed by explicit scope only:

- `jarvis/brain/context_builder.py`
- `tests/test_context_builder.py`
- `tests/test_memory_compiler_wire.py`

Forbidden by default:

- schema/migrations
- API routes
- provider adapters
- tools/voice/panel/config/CI

### Runtime/daemon tasks

Allowed by explicit scope only:

- `jarvis/daemon/`
- `jarvis/runtime/`
- selected API smoke tests

Forbidden by default:

- schema/migrations
- provider behavior
- voice live engine changes
- panel UX rewrites

### Preview API tasks

Allowed by explicit scope only:

- `jarvis/api/routes_memory.py`
- daemon route registration if required
- preview API tests

Forbidden by default:

- compiler governance changes
- ContextBuilder prompt behavior
- schema/migrations unless explicitly scoped

### Voice tasks

Allowed by explicit scope only:

- `jarvis/voice/`
- `jarvis/audio/`
- voice tests
- voice runbooks if scoped

Forbidden by default:

- Memory OS
- ContextBuilder compiled memory
- schema/migrations unless explicitly scoped
- provider adapter behavior

### Panel tasks

Allowed by explicit scope only:

- `jarvis/panel/`
- panel assets/tests

Forbidden by default:

- daemon ownership changes
- memory compiler logic
- schema/migrations
- provider adapters

### Docs-only tasks

Allowed:

- explicitly scoped docs files

Forbidden:

- all code and tests
- config
- scripts
- launchd
- CI
- README and package/lock files

## Review checklist

Before commit, confirm:

- `git diff --check` clean.
- Only expected files changed.
- Focused tests passed.
- Full regression set passed when task requires it.
- Guards passed.
- Review verdict is CLEAN.
- Commit adds only actual changed files.
