"""Daemon-served panel assets for the native shell."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from dan.daemon.app import DaemonApp, create_daemon_app
from tests.test_api_smoke import running_server, write_config


def _request_raw(url: str) -> tuple[int, object, bytes]:
    request = Request(url, headers={"Accept": "*/*"}, method="GET")
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, response.headers, response.read()
    except HTTPError as exc:
        return exc.code, exc.headers, exc.read()


def _app(tmp_path: Path) -> Iterator[DaemonApp]:
    config_path = write_config(tmp_path / "dan.toml", tmp_path / "home" / "dan.db")
    daemon_app = create_daemon_app(config_path)
    try:
        yield daemon_app
    finally:
        daemon_app.close()


def test_daemon_serves_panel_index_from_same_origin(tmp_path: Path) -> None:
    for daemon_app in _app(tmp_path):
        with running_server(daemon_app) as base_url:
            status, headers, body = _request_raw(f"{base_url}/panel/index.html")

    assert status == 200
    assert headers.get("Content-Type") == "text/html; charset=utf-8"
    assert b"./app.js" in body


def test_daemon_serves_panel_javascript_asset(tmp_path: Path) -> None:
    for daemon_app in _app(tmp_path):
        with running_server(daemon_app) as base_url:
            status, headers, body = _request_raw(f"{base_url}/panel/app.js")

    assert status == 200
    assert headers.get("Content-Type") == "text/javascript; charset=utf-8"
    assert b"requestJson" in body


def test_daemon_refuses_panel_asset_path_traversal(tmp_path: Path) -> None:
    for daemon_app in _app(tmp_path):
        with running_server(daemon_app) as base_url:
            status, _headers, body = _request_raw(
                f"{base_url}/panel/%2e%2e/daemon/lifecycle.py"
            )

    assert status == 404
    assert b"not found" in body.lower()
