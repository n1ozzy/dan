# Voice decisions — current contract

This file deliberately does not duplicate voice ids, speeds, mastering values,
gains, DSP chains, or pronunciation entries.

The only current sources of truth are:

- `config/voice/personas.toml` — the complete public cast and each route;
- `config/voice/gains.json` — the exact calibrated route keys;
- `config/voice/pronunciations.toml` — spoken rewrites;
- `dan/voice/policy.py` — the public cast boundary;
- `MUST-READ-GLOS-PROZODIA.md` — the authoring and verification contract.

The runtime must load these files directly and fail closed when they disagree.
Documentation, migration archives, old listening verdicts, backup catalogs,
panel state, and conversation memory are evidence only. They may not introduce
a persona, profile, speed, effect, fallback, or route.

## Supersession

The previous contents of this file described the July 2026 migration matrix.
That matrix contained routes and fixed acting assumptions which are no longer
part of the product. It was removed from the working tree so an agent cannot
mistake migration evidence for active configuration. Git history retains it
when provenance is needed.

## Stable decisions

- Supertonic is the only TTS engine.
- Public speech is limited to the owner cast declared by the current catalog
  and enforced by policy.
- One character resolves to one catalog route.
- Baseline configuration is not acting direction. Per-utterance direction is
  explicit and travels in an immutable render snapshot.
- Missing or contradictory configuration is an error; there is no silent
  fallback to an old route.
- A listening result may justify a later catalog change, but it does not change
  the runtime until the canonical files and their regression tests change
  together.
