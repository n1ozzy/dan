# Chatterbox V3 local installer/runtime contract

The Zaneta Chatterbox V3 route is offline-only and local-only. The installer may
obtain approved assets, but the runtime never downloads, resolves `main`, searches
HOME caches, or substitutes another package, model, reference, or acceptance gate.

## Installer output

The installer must provision the paths named by these environment variables:

- `DAN_CHATTERBOX_V3_DIRECT_URL`: the installed `chatterbox-tts` distribution's
  `direct_url.json` for the source revision pinned in the pipeline TOML.
- `DAN_CHATTERBOX_V3_MODEL_DIR`: a local snapshot directory containing every file
  and exact SHA-256 pinned by `[model_files]`.
- `DAN_CHATTERBOX_V3_PYTHON`: the isolated environment's executable whose version,
  executable hash, package version, source revision, and package-tree hash match the
  pipeline TOML.
- `DAN_ZANETA_ACCEPTANCE_GATE`: the local acceptance-gate program.
- `DAN_ZANETA_REFERENCE_WAV`: the non-redistributed local reference whose SHA-256
  matches `[reference]`.

After all model files are present, the installer must hash their bytes and atomically
write `${DAN_CHATTERBOX_V3_MODEL_DIR}/snapshot-lock.json` with this exact shape:

```json
{
  "schema_version": 1,
  "repo_id": "ResembleAI/chatterbox",
  "revision": "5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18",
  "files": {
    "<filename from [model_files]>": "<matching SHA-256>"
  }
}
```

The `files` object must equal the complete `[model_files]` map in
`config/voice/pipelines/chatterbox-v3-zaneta.toml`; a subset, extra entry, changed
hash, forged revision, or different repository ID is invalid.

## Runtime verification

Before generation, the runtime verifies the source metadata, snapshot lock, exact
model file bytes, interpreter bytes, isolated package provenance, acceptance gate,
and local reference. Probe and generation both use the configured interpreter with
`-I`, no inherited `PYTHON*` variables, offline flags, and the verified model
directory as the working directory. Any missing or mismatched input is a capability
error. There is no network fallback.
