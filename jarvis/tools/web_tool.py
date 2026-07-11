"""Web fetch tool — HTTP(S) GET so the model can read the open internet.

Ozzy 2026-07-08: ANY URL, no domain allowlist. The guards here are hygiene,
not policy — they keep a hostile or broken server from hanging or flooding the
daemon, they do NOT restrict where Jarvis may go:
- http/https only (no file:// / ftp:// / gopher:// reaching into the local box),
- an SSRF guard: the target host, AND every redirect hop, must resolve to a
  public address — loopback / private-LAN / link-local / metadata (169.254.x)
  IPs are refused. "ANY URL" still means the whole public internet; it never
  means the operator's own daemon endpoints or LAN services,
- a request timeout,
- a response size cap (read max_bytes+1, report truncation),
- urllib's default redirect cap.

Risk class is NETWORK, so ToolPermissionPolicy still gates the call; it is
auto-approved only when the operator sets security.auto_approve_mode. The
(redacted) body reaches the model via the transient tool result; the durable
store caps long strings (registry.PERSIST_MAX_STRING_CHARS) as elsewhere.
"""

from __future__ import annotations

import ipaddress
import socket
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


def _ip_is_forbidden(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _host_is_blocked(host: str) -> bool:
    """True when ``host`` is, or resolves to, a non-public address.

    An SSRF guard, not a domain policy: it never blocks a public host, it only
    stops web_fetch (and any redirect it follows) from turning into a request
    against the local box, a private-LAN service, or the cloud metadata IP
    (169.254.169.254). Hostnames are resolved, so a name pointing at 127.0.0.1
    is caught too. A resolution failure is NOT treated as blocked — urlopen will
    fail on its own; we don't want every transient DNS error surfaced as a
    security refusal."""

    if not host:
        return False
    candidate = host.strip("[]").split("%", 1)[0]
    try:  # literal IP — no DNS
        return _ip_is_forbidden(ipaddress.ip_address(candidate))
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        addr = info[4][0].split("%", 1)[0]
        try:
            if _ip_is_forbidden(ipaddress.ip_address(addr)):
                return True
        except ValueError:
            continue
    return False


class _GuardedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Re-apply the scheme/SSRF guard to every redirect hop.

    urllib follows 30x automatically; without this a public URL the operator
    approved could bounce web_fetch onto http://127.0.0.1/ or the metadata IP
    (the operator never saw the final host). A blocked hop raises URLError, which
    run() surfaces as a ToolExecutionError."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlparse(newurl)
        if parsed.scheme not in ("http", "https"):
            raise urllib.error.URLError(
                f"web_fetch refused redirect to non-http(s) scheme: {parsed.scheme or '(none)'}"
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(
    _GuardedRedirectHandler(),
    urllib.request.HTTPSHandler(context=_SSL_CONTEXT),
)


def _perform_request(request: urllib.request.Request, timeout: float):
    """Single seam for the actual GET (patched in tests). Uses the guarded
    opener so redirects are re-validated hop by hop."""

    return _OPENER.open(request, timeout=timeout)


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
            with _perform_request(request, timeout) as response:
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
