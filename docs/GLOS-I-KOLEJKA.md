# Voice and queue

## Broker

The voice broker runs **inside `dand`** and is the sole owner of synthesis
and playback. Every producer (the CLI, the panel, a hook, a skill, other
agents) speaks through the API/CLI — nobody plays WAV files directly and
nobody starts a broker of their own. The live engine is Supertonic; a missing
engine, voice or asset ends the request with an explicit error — there is no
silent fallback.

## Queue statuses

The queue is persistent in SQLite (`~/.dan/dan.db`, table `voice_queue`). A
request moves through:

| Status | Meaning |
|---|---|
| `queued` | accepted, render snapshot complete, waiting for synthesis |
| `synthesizing` | the broker is generating audio |
| `speaking` | audio is actually playing from the speaker |
| `done` | played and confirmed (`playback_confirmed`) |
| `cancelled` | cancelled (individually or by a session flush) |
| `failed` | explicit synthesis/playback error, described in the `error` field |

The broker takes exactly one item for playback at a time.

## Render snapshot

The intent (text + persona) and the queue record are two contracts. Before a
request gets `queued`, `dand` resolves an **immutable render snapshot**: the
engine and its version, voice/style, tempo, mastering, DSP, pronunciation,
gain and the SHA-256 of the assets used. An incomplete snapshot = an error
before the write, not a partial record. This makes it known exactly what every
utterance was rendered with, even after the configuration changes.

## Changing a persona voice (panel)

The persona catalog `config/voice/personas.toml` is a versioned asset: the
config registry rejects `voice.voice_id`, `voice.voice_profile` and
`voice.speed`, so the runtime-settings path can never change how a persona
sounds. The panel therefore edits the catalog itself through two routes:

- `GET /voice/personas` — the routes from the file plus the allowed values
  (built-in Supertonic ids and every blend already used in the file,
  the mastering profiles, the speed range).
- `POST /voice/personas/apply` `{persona, voice?, speed?, mastering?}` —
  `dan.voice.persona_editor` validates and rewrites only the target section
  (comments elsewhere survive, the write is atomic), then
  `DaemonApp.reload_voice_catalog()` rebuilds the resolver **in process**.

The reload is why no daemon restart is needed: `VoiceCatalog` freezes the
file's SHA-256 at load time and the resolver re-checks it on every speak, so
an edited file without a reload fails closed with an SHA-256 mismatch.
Swapping the resolver instead of restarting keeps queued and playing audio
alive — already-resolved snapshots are immutable, only new submits see the
new catalog. If the rebuild rejects the new catalog, the file is rolled back
to its previous bytes so disk and resolver never diverge.

## The old feeder vs Release 1 behavior

The old path: a bash feeder watched a growing playlist file and every appended
line started playing immediately; DSP was driven by a smuggled-in `profile`
field. Release 1 has no such path: a playlist is imported transactionally as
session segments, appending anything to the old file after the import
**triggers nothing**, progress lives in the database (a restart neither
duplicates nor loses anything), and live content enters through the same API
as just another segment — there is no second queue.

## Offline render

Prepared lines (the Chatterbox V3 pipeline for Żaneta) are an explicit
**offline** pipeline — it renders files outside the live queue and is never an
automatic live engine. Entry via the voice directory (`dan/voice/pipelines/`),
the `offline_pipeline` route in the persona catalog.

## CLI examples (copy/paste)

```bash
# Basic utterance with Polish characters:
dan speak --as dan "Zażółć gęślą jaźń — to jest test dykcji, chłopie."

# Second persona:
dan speak --as danusia "Dobra, moja kolej. Posłuchaj uważnie."

# JSON via stdin (result also in JSON):
dan speak --as danusia --json --stdin <<< "Święta prawda, mówię to z pliku."

# What is in the queue:
dan queue list --json --limit 10

# Cancel a single request (id from queue list):
dan queue cancel 42

# Flush an entire session (e.g. the radio one):
dan queue flush --session radio

# Where the current configuration came from (file, env, default):
dan config explain --json

# Voice hook switch:
dan voice hook off
dan voice hook on
dan voice hook status
```
