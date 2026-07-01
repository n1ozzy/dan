# Memory API and CLI

Jarvis-owned memory blocks are durable rows in `memory_blocks`. They are small
pieces of local context that `jarvisd` can include in later brain requests when
they are active and fit the configured context budget.

Memory blocks are not provider session memory. Claude, Codex, OpenAI and other
brain adapters remain stateless from Jarvis's point of view. This is also not
semantic memory or vector search; Prompt 16 exposes explicit CRUD-style
management for the existing SQLite-backed blocks.

## Supported Kinds

- `identity`
- `user_preference`
- `project`
- `fact`
- `summary`
- `temporary`

Unknown kinds are rejected with JSON `400`.

## API

All memory endpoints require `app.started`. If the daemon app is initialized but
not started, they return JSON `503`.

`GET /memory`

Query parameters:

- `active_only`: optional boolean, default `false`
- `kind`: optional, repeatable or comma-separated
- `limit`: optional, default `100`, maximum `500`

Returns:

```json
{
  "memory": [],
  "active_only": false,
  "limit": 100
}
```

This endpoint is read-only. It does not create memory and does not append
events.

`POST /memory`

Request:

```json
{
  "kind": "fact",
  "title": "Some title",
  "body": "Some body",
  "priority": 0,
  "active": true,
  "metadata": {}
}
```

Creates a block through `MemoryManager` and returns `201` with the created
block. Invalid JSON, non-object bodies, invalid kinds, empty title/body, and
non-object metadata return JSON `400`.

`GET /memory/{id}`

Returns one block. Missing IDs return JSON `404`. This endpoint does not append
events.

`PATCH /memory/{id}`

Request may include any of:

```json
{
  "title": "Updated title",
  "body": "Updated body",
  "priority": 1,
  "active": true,
  "metadata": {}
}
```

Only provided fields are updated. Missing IDs return JSON `404`; malformed
payloads return JSON `400`.

`DELETE /memory/{id}`

Soft-disables a block by calling `MemoryManager.disable_block`. It does not
delete the row and does not cascade. Missing IDs return JSON `404`.

## Events and Context

Create, update and disable operations emit `memory.updated` when the
`MemoryManager` has an `EventStore`.

`ContextBuilder` reads active memory only. Disabled memory blocks are excluded
from future brain requests.

Workers do not write committed memory facts directly. Worker output can be a
candidate for later promotion, but committed memory remains explicitly managed
through Jarvis-owned paths.

## CLI

The CLI talks to a running daemon over HTTP. It does not start `jarvisd`, does
not initialize SQLite, and does not call `MemoryManager` directly.

List memory:

```bash
python -m jarvis.cli memory list --active-only --kind fact --limit 50
```

Create memory:

```bash
python -m jarvis.cli memory create \
  --kind fact \
  --title "Some title" \
  --body "Some body" \
  --priority 0 \
  --metadata-json '{"source":"manual"}'
```

Show one block:

```bash
python -m jarvis.cli memory show --id MEMORY_ID
```

Update a block:

```bash
python -m jarvis.cli memory update \
  --id MEMORY_ID \
  --title "Updated title" \
  --body "Updated body" \
  --priority 1 \
  --active true \
  --metadata-json '{"source":"manual-update"}'
```

Disable a block:

```bash
python -m jarvis.cli memory disable --id MEMORY_ID
```

All memory CLI commands accept:

- `--url BASE_URL`
- `--timeout SECONDS`

The CLI prints JSON. It exits non-zero for unreachable daemons, HTTP errors, or
invalid local `--metadata-json`.
