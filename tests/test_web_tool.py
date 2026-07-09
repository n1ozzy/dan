"""WebFetchTool tests (2026-07-08).

Any URL is allowed (Ozzy's decree) — the tests verify the hygiene guards
(scheme, size cap, timeout/error surfacing) rather than any domain policy.
No test reaches the real network: urlopen is monkeypatched.
"""

from __future__ import annotations

import io
import urllib.error
from email.message import Message

import pytest

from jarvis.tools.registry import ToolExecutionError
from jarvis.tools.web_tool import DEFAULT_MAX_BYTES, WebFetchTool


class _FakeResponse:
    def __init__(self, body: bytes, *, status: int = 200, content_type: str = "text/html", url: str = "http://x/"):
        self._buf = io.BytesIO(body)
        self.status = status
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        self._url = url

    def read(self, n: int = -1) -> bytes:
        return self._buf.read(n)

    def geturl(self) -> str:
        return self._url

    def getcode(self) -> int:
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _patch_urlopen(monkeypatch, factory):
    # web_fetch performs the GET through the guarded opener via _perform_request;
    # patching that seam keeps tests off the real network while exercising run().
    monkeypatch.setattr("jarvis.tools.web_tool._perform_request", factory)


def test_fetches_body_and_metadata(monkeypatch):
    captured = {}

    def fake(req, timeout=None):
        captured["url"] = req.full_url
        captured["timeout"] = timeout
        captured["ua"] = req.get_header("User-agent")
        return _FakeResponse(b"<html>hi</html>", url="http://example.com/")

    _patch_urlopen(monkeypatch, fake)
    result = WebFetchTool().run({"url": "http://example.com"})

    assert result["ok"] is True
    assert result["status"] == 200
    assert result["content"] == "<html>hi</html>"
    assert result["truncated"] is False
    assert captured["url"] == "http://example.com"
    assert captured["ua"]  # a User-Agent is always sent


def test_rejects_non_http_scheme(monkeypatch):
    _patch_urlopen(monkeypatch, lambda *a, **k: pytest.fail("must not open a connection"))
    for bad in ("file:///etc/passwd", "ftp://host/x", "gopher://h", "javascript:1"):
        with pytest.raises(ToolExecutionError):
            WebFetchTool().run({"url": bad})


def test_rejects_empty_url():
    with pytest.raises(ToolExecutionError):
        WebFetchTool().run({"url": "   "})
    with pytest.raises(ToolExecutionError):
        WebFetchTool().run({})


def test_size_cap_truncates(monkeypatch):
    big = b"a" * (DEFAULT_MAX_BYTES + 500)
    _patch_urlopen(monkeypatch, lambda req, timeout=None: _FakeResponse(big))
    result = WebFetchTool().run({"url": "http://example.com", "max_bytes": 1000})
    assert result["truncated"] is True
    assert result["returned_bytes"] == 1000
    assert len(result["content"]) == 1000


def test_http_error_surfaces_body_not_raise(monkeypatch):
    hdrs = Message()
    hdrs["Content-Type"] = "text/plain"

    def raise_http(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", hdrs, io.BytesIO(b"nope"))

    _patch_urlopen(monkeypatch, raise_http)
    result = WebFetchTool().run({"url": "http://example.com/missing"})
    assert result["ok"] is False
    assert result["status"] == 404
    assert result["content"] == "nope"


def test_network_error_raises_tool_error(monkeypatch):
    def boom(req, timeout=None):
        raise urllib.error.URLError("connection refused")

    _patch_urlopen(monkeypatch, boom)
    with pytest.raises(ToolExecutionError):
        WebFetchTool().run({"url": "http://example.com"})


def test_risk_is_network():
    assert WebFetchTool().risk == "network"


@pytest.mark.parametrize(
    "blocked",
    [
        "http://127.0.0.1/",
        "http://127.0.0.1:41741/state",
        "http://localhost/",
        "http://[::1]/",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/",
        "http://192.168.1.1/admin",
        "http://0.0.0.0/",
    ],
)
def test_ssrf_guard_refuses_non_public_hosts(monkeypatch, blocked):
    # Must refuse BEFORE opening any connection.
    _patch_urlopen(monkeypatch, lambda *a, **k: pytest.fail("must not open a connection"))
    with pytest.raises(ToolExecutionError):
        WebFetchTool().run({"url": blocked})


def test_ssrf_guard_allows_public_host(monkeypatch):
    # A public literal IP passes the guard and is fetched normally.
    _patch_urlopen(
        monkeypatch,
        lambda req, timeout=None: _FakeResponse(b"ok", url="http://93.184.216.34/"),
    )
    result = WebFetchTool().run({"url": "http://93.184.216.34/"})
    assert result["ok"] is True
    assert result["content"] == "ok"


def test_redirect_to_loopback_is_refused():
    # The guarded redirect handler rejects a 30x hop onto a non-public host,
    # even when the originally-approved URL was public.
    from email.message import Message as _Msg

    from jarvis.tools.web_tool import _GuardedRedirectHandler

    handler = _GuardedRedirectHandler()
    req = urllib.request.Request("http://example.com/")
    with pytest.raises(urllib.error.URLError):
        handler.redirect_request(
            req, io.BytesIO(b""), 302, "Found", _Msg(), "http://127.0.0.1:41741/state"
        )


def test_redirect_to_public_host_is_allowed():
    from email.message import Message as _Msg

    from jarvis.tools.web_tool import _GuardedRedirectHandler

    handler = _GuardedRedirectHandler()
    req = urllib.request.Request("http://example.com/")
    new = handler.redirect_request(
        req, io.BytesIO(b""), 302, "Found", _Msg(), "http://93.184.216.34/next"
    )
    assert new is not None  # a real Request to follow
