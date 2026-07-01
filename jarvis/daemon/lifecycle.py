"""HTTP lifecycle helpers for the local Jarvis daemon."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Protocol
from urllib.parse import parse_qs, urlencode, urlparse

from jarvis.api.routes_events import get_events
from jarvis.api.routes_health import get_health
from jarvis.api.routes_input import text_input_not_implemented
from jarvis.api.routes_settings import get_settings, update_settings
from jarvis.api.routes_state import get_state
from jarvis.daemon.app import DaemonApp, DaemonAppError
from jarvis.store.event_store import EventStoreError


MAX_REQUEST_BODY_BYTES = 1_048_576
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


class LifecycleHook(Protocol):
    def startup(self) -> None:
        """Run before daemon dependencies are exposed."""

    def shutdown(self) -> None:
        """Run during graceful daemon shutdown."""


class DaemonServerError(Exception):
    """Raised when the local daemon HTTP server cannot be built or run."""


class DaemonServer:
    def __init__(self, httpd: ThreadingHTTPServer, app: DaemonApp):
        self.httpd = httpd
        self.app = app

    @property
    def server_address(self) -> tuple[str, int]:
        host, port = self.httpd.server_address[:2]
        return str(host), int(port)

    @property
    def base_url(self) -> str:
        host, port = self.server_address
        return f"http://{host}:{port}"

    def serve_forever(self) -> None:
        self.httpd.serve_forever()

    def shutdown(self) -> None:
        self.httpd.shutdown()

    def server_close(self) -> None:
        self.httpd.server_close()


def build_server(app: DaemonApp, host: str, port: int) -> DaemonServer:
    if app.config.security.localhost_only and host not in LOCAL_HOSTS:
        raise DaemonServerError("Jarvis API may only bind to localhost when localhost_only=true.")

    handler_class = _make_handler(app)
    httpd = ThreadingHTTPServer((host, port), handler_class)
    return DaemonServer(httpd, app)


def serve_forever(app: DaemonApp, host: str, port: int) -> None:
    server = build_server(app, host, port)
    try:
        server.serve_forever()
    finally:
        server.server_close()


def _make_handler(app: DaemonApp) -> type[BaseHTTPRequestHandler]:
    class JarvisRequestHandler(BaseHTTPRequestHandler):
        server_version = "jarvisd"
        error_content_type = "application/json"

        def do_GET(self) -> None:
            _dispatch(self, app, "GET")

        def do_POST(self) -> None:
            _dispatch(self, app, "POST")

        def log_message(self, format: str, *args: object) -> None:
            return None

    return JarvisRequestHandler


def _dispatch(handler: BaseHTTPRequestHandler, app: DaemonApp, method: str) -> None:
    parsed = urlparse(handler.path)
    path = parsed.path
    query = parse_qs(parsed.query)

    try:
        if method == "GET" and path == "/health":
            _write_json(handler, 200, get_health(app))
            return

        if method == "GET" and path == "/state":
            _write_json(handler, 200, get_state(app))
            return

        if method == "GET" and path == "/events":
            after_id = _query_int(query, "after_id", default=0)
            limit = _query_int(query, "limit", default=100)
            _write_json(handler, 200, get_events(app, after_id=after_id, limit=limit))
            return

        if method == "GET" and path == "/settings":
            _write_json(handler, 200, get_settings(app))
            return

        if method == "POST" and path == "/settings":
            request_payload = _read_json_body(handler)
            if not isinstance(request_payload, dict):
                raise ValueError("Request JSON must be an object.")
            _write_json(handler, 200, update_settings(app, request_payload))
            return

        if path == "/input/text" and method in {"GET", "POST"}:
            _write_json(handler, 501, text_input_not_implemented())
            return

        _write_json(handler, 404, {"error": "Not found", "status": 404})
    except (ValueError, DaemonAppError, EventStoreError) as exc:
        _write_json(handler, 400, {"error": str(exc), "status": 400})
    except Exception:
        _write_json(handler, 500, {"error": "Internal server error", "status": 500})


def _query_int(query: dict[str, list[str]], key: str, *, default: int) -> int:
    if key not in query:
        return default
    raw_value = query[key][0]
    try:
        return int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{key} must be an integer.") from exc


def _read_json_body(handler: BaseHTTPRequestHandler) -> Any:
    length_header = handler.headers.get("Content-Length")
    if length_header is None:
        raise ValueError("Content-Length is required.")
    try:
        length = int(length_header)
    except ValueError as exc:
        raise ValueError("Content-Length must be an integer.") from exc
    if length > MAX_REQUEST_BODY_BYTES:
        raise ValueError("Request body is too large.")

    body = handler.rfile.read(length)
    try:
        return json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Malformed JSON: {exc.msg}") from exc


def _write_json(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def build_events_query(after_id: int, limit: int) -> str:
    return urlencode({"after_id": after_id, "limit": limit})


__all__ = [
    "DaemonServer",
    "DaemonServerError",
    "LifecycleHook",
    "build_events_query",
    "build_server",
    "serve_forever",
]
