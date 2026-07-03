"""FIX-08: the persistence-side redaction of tool payloads (registry._redact).

Two things that the durable path must do and the model path must NOT:

- Sensitive keys with separators (``api-key``, ``API.KEY``) must be masked
  before landing in tool_runs/events — the old substring rule diverged from the
  central ``is_sensitive_key`` (which normalizes separators).
- A huge tool payload string (a 256 KB ``file_read`` body) must be size-capped
  before it is persisted, so the durable store never hoards whole file bodies.
  The MODEL continuation reads the transient ``ToolResult.output`` through the
  shared ``redact_secrets`` (no cap) — so it still gets the full redacted
  content and ``file_read`` stays useful. This test pins that asymmetry.
"""

from __future__ import annotations

from jarvis.security.redaction import REDACTION_PLACEHOLDER, redact_secrets
from jarvis.tools.registry import _redact


def test_persisted_redaction_masks_separator_variant_keys() -> None:
    persisted = _redact(
        {"api-key": "raw1", "API.KEY": "raw2", "x_access-token": "raw3", "safe": "ok"}
    )

    assert persisted["api-key"] == REDACTION_PLACEHOLDER
    assert persisted["API.KEY"] == REDACTION_PLACEHOLDER
    assert persisted["x_access-token"] == REDACTION_PLACEHOLDER
    assert persisted["safe"] == "ok"


def test_persisted_redaction_still_masks_credential_keys() -> None:
    # The unification onto the shared helpers must not regress the credential
    # coverage the old registry rule had.
    persisted = _redact({"credentials": "raw", "aws_credential": "raw"})

    assert persisted["credentials"] == REDACTION_PLACEHOLDER
    assert persisted["aws_credential"] == REDACTION_PLACEHOLDER


def test_persisted_long_string_is_size_capped() -> None:
    body = "A" * 50_000
    persisted = _redact({"content": body})

    capped = persisted["content"]
    assert len(capped) < len(body)
    assert capped.startswith("A")
    assert "TRUNCATED" in capped


def test_model_path_is_not_size_capped() -> None:
    # The shared redactor (what the brain continuation sees) keeps full content;
    # only the durable persistence path caps. file_read must stay useful.
    body = "B" * 50_000
    seen_by_model = redact_secrets({"content": body})

    assert seen_by_model["content"] == body


def test_persisted_redaction_still_masks_secret_values() -> None:
    persisted = _redact({"stdout": "leak sk-testpersistsecret123 here"})

    assert "sk-testpersistsecret123" not in persisted["stdout"]
    assert REDACTION_PLACEHOLDER in persisted["stdout"]
