"""Central, deterministic secret redaction helpers."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


REDACTION_PLACEHOLDER = "[REDACTED]"

SENSITIVE_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "apikey",
        "authorization",
        "client_secret",
        "cookie",
        "pass",
        "passwd",
        "password",
        "private_key",
        "refresh_token",
        "secret",
        "set_cookie",
        "ssh_key",
        "token",
    }
)
SENSITIVE_KEY_SUFFIXES = (
    "_access_token",
    "_api_key",
    "_apikey",
    "_authorization",
    "_client_secret",
    "_cookie",
    "_pass",
    "_passwd",
    "_password",
    "_private_key",
    "_refresh_token",
    "_secret",
    "_set_cookie",
    "_ssh_key",
    "_token",
)
SENSITIVE_KEY_PREFIXES = (
    "access_token_",
    "api_key_",
    "apikey_",
    "authorization_",
    "client_secret_",
    "cookie_",
    "pass_",
    "passwd_",
    "password_",
    "private_key_",
    "refresh_token_",
    "secret_",
    "set_cookie_",
    "ssh_key_",
    "token_",
)

SECRET_VALUE_PATTERNS = (
    re.compile(r"(?i)(\bAuthorization\s*[:=]\s*Bearer\s+)[^\s,;\"']+"),
    re.compile(r"(?i)(\bBearer\s+)[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)(\bAuthorization\s*[:=]\s*Basic\s+)[A-Za-z0-9+/=._-]+"),
    re.compile(r"(?i)(\bBasic\s+)[A-Za-z0-9+/=._-]{8,}"),
    re.compile(r"(?<![A-Za-z0-9_])github_pat_[A-Za-z0-9_]{8,}"),
    re.compile(r"(?<![A-Za-z0-9_])ghp_[A-Za-z0-9_]{8,}"),
    re.compile(r"(?<![A-Za-z0-9_])sk-[A-Za-z0-9][A-Za-z0-9._-]*"),
)


def redact_secrets(value: Any) -> Any:
    """Return a redacted copy of JSON-like data without mutating the caller value."""

    if isinstance(value, Mapping):
        redacted: dict[Any, Any] = {}
        for key, item in value.items():
            if is_sensitive_key(key):
                redacted[key] = REDACTION_PLACEHOLDER
            else:
                redacted[key] = redact_secrets(item)
        return redacted

    if isinstance(value, (list, tuple)):
        return [redact_secrets(item) for item in value]

    if isinstance(value, str):
        return redact_secret_text(value)

    return value


def redact_secret_text(value: str) -> str:
    """Redact secret-looking substrings while preserving ordinary surrounding text."""

    redacted = value
    for pattern in SECRET_VALUE_PATTERNS:
        redacted = pattern.sub(_replace_match, redacted)
    return redacted


def is_sensitive_key(key: Any) -> bool:
    normalized = _normalize_key(key)
    return (
        normalized in SENSITIVE_KEYS
        or normalized.endswith(SENSITIVE_KEY_SUFFIXES)
        or normalized.startswith(SENSITIVE_KEY_PREFIXES)
    )


def _replace_match(match: re.Match[str]) -> str:
    if match.lastindex:
        return f"{match.group(1)}{REDACTION_PLACEHOLDER}"
    return REDACTION_PLACEHOLDER


def _normalize_key(key: Any) -> str:
    text = str(key).strip().lower()
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


__all__ = [
    "REDACTION_PLACEHOLDER",
    "redact_secret_text",
    "redact_secrets",
]
