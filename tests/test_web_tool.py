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
    monkeypatch.setattr("jarvis.tools.web_tool.urllib.request.urlopen", factory)


def test_fetches_body_and_metadata(monkeypatch):
    captured = {}

    def fake(req, timeout=None, context=None):
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
    _patch_urlopen(monkeypatch, lambda req, timeout=None, context=None: _FakeResponse(big))
    result = WebFetchTool().run({"url": "http://example.com", "max_bytes": 1000})
    assert result["truncated"] is True
    assert result["returned_bytes"] == 1000
    assert len(result["content"]) == 1000


def test_http_error_surfaces_body_not_raise(monkeypatch):
    hdrs = Message()
    hdrs["Content-Type"] = "text/plain"

    def raise_http(req, timeout=None, context=None):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", hdrs, io.BytesIO(b"nope"))

    _patch_urlopen(monkeypatch, raise_http)
    result = WebFetchTool().run({"url": "http://example.com/missing"})
    assert result["ok"] is False
    assert result["status"] == 404
    assert result["content"] == "nope"


def test_network_error_raises_tool_error(monkeypatch):
    def boom(req, timeout=None, context=None):
        raise urllib.error.URLError("connection refused")

    _patch_urlopen(monkeypatch, boom)
    with pytest.raises(ToolExecutionError):
        WebFetchTool().run({"url": "http://example.com"})


def test_risk_is_network():
    assert WebFetchTool().risk == "network"
