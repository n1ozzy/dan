"""Web fetch tool — HTTP(S) GET so the model can read the open internet.

Ozzy 2026-07-08: ANY URL, no domain allowlist. The guards here are hygiene,
not policy — they keep a hostile or broken server from hanging or flooding the
daemon, they do NOT restrict where Jarvis may go:
- http/https only (no file:// / ftp:// / gopher:// reaching into the local box),
- a request timeout,
- a response size cap (read max_bytes+1, report truncation),
- urllib's default redirect cap.

Risk class is NETWORK, so ToolPermissionPolicy still gates the call; it is
auto-approved only when the operator sets security.auto_approve_mode. The
(redacted) body reaches the model via the transient tool result; the durable
store caps long strings (registry.PERSIST_MAX_STRING_CHARS) as elsewhere.
"""

from __future__ import annotations

import ssl
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

from jarvis.tools.registry import Tool, ToolExecutionError


def _build_ssl_context() -> ssl.SSLContext | None:
    """TLS verification context. Homebrew's Python ships no root CAs, so a bare
    urlopen fails every https:// with CERTIFICATE_VERIFY_FAILED; certifi's
    bundle fixes it. Fall back to the stdlib default if certifi is absent."""
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # pragma: no cover - certifi is a hard dep, defensive only
        return None


_SSL_CONTEXT = _build_ssl_context()


DEFAULT_MAX_BYTES = 524_288
HARD_MAX_BYTES = 2_097_152
DEFAULT_TIMEOUT = 15.0
HARD_TIMEOUT = 60.0
_USER_AGENT = "Jarvis/4.2 (+local assistant)"


def _clamp_int(value: Any, default: int, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return max(low, min(high, value))


def _clamp_float(value: Any, default: float, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return max(low, min(high, float(value)))


def _decode(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace")


class WebFetchTool(Tool):
    name = "web_fetch"
    description = "Fetch any http(s) URL with a GET request and return its text body."
    risk = "network"
    input_schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "http:// or https:// URL to GET."},
            "max_bytes": {
                "type": "integer",
                "description": f"Response byte budget (default {DEFAULT_MAX_BYTES}, max {HARD_MAX_BYTES}).",
            },
            "timeout_seconds": {
                "type": "number",
                "description": f"Request timeout (default {DEFAULT_TIMEOUT}, max {HARD_TIMEOUT}).",
            },
        },
        "required": ["url"],
    }

    def run(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        url = arguments.get("url")
        if not isinstance(url, str) or not url.strip():
            raise ToolExecutionError("web_fetch requires a non-empty 'url'.")
        url = url.strip()

        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise ToolExecutionError(
                f"web_fetch only supports http/https, got: {parsed.scheme or '(none)'}"
            )
        if not parsed.netloc:
            raise ToolExecutionError("web_fetch URL has no host.")

        max_bytes = _clamp_int(arguments.get("max_bytes"), DEFAULT_MAX_BYTES, 1, HARD_MAX_BYTES)
        timeout = _clamp_float(arguments.get("timeout_seconds"), DEFAULT_TIMEOUT, 0.1, HARD_TIMEOUT)

        request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT}, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=_SSL_CONTEXT) as response:
                status = getattr(response, "status", None) or response.getcode()
                content_type = response.headers.get("Content-Type", "") if response.headers else ""
                raw = response.read(max_bytes + 1)
                final_url = response.geturl()
        except urllib.error.HTTPError as exc:
            # An HTTP error (404/500/...) still has a usable body — surface it
            # instead of blowing up, so the model can read the error page.
            body = b""
            try:
                body = exc.read(max_bytes + 1)
            except Exception:  # pragma: no cover - defensive
                pass
            return {
                "ok": False,
                "url": url,
                "status": exc.code,
                "content_type": exc.headers.get("Content-Type", "") if exc.headers else "",
                "returned_bytes": min(len(body), max_bytes),
                "truncated": len(body) > max_bytes,
                "content": _decode(body[:max_bytes]),
                "error": f"HTTP {exc.code}",
            }
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise ToolExecutionError(f"web_fetch failed for {url}: {exc}") from exc

        return {
            "ok": True,
            "url": url,
            "final_url": final_url,
            "status": status,
            "content_type": content_type,
            "returned_bytes": min(len(raw), max_bytes),
            "truncated": len(raw) > max_bytes,
            "content": _decode(raw[:max_bytes]),
        }


__all__ = ["WebFetchTool", "DEFAULT_MAX_BYTES", "HARD_MAX_BYTES"]
