"""HTTP lifecycle helpers for the local Jarvis daemon."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Protocol
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.parse import unquote

from jarvis.api.routes_approvals import (
    ApprovalRequestValidationError,
    approve_approval,
    execute_approval,
    get_approvals,
    reject_approval,
)
from jarvis.api.routes_events import get_events
from jarvis.api.routes_health import get_health
from jarvis.api.routes_history import get_conversations, get_turns
from jarvis.api.routes_input import (
    TextInputValidationError,
    get_text_input_method_error,
    post_text_input,
)
from jarvis.api.routes_memory import (
    MemoryRequestValidationError,
    delete_memory,
    get_memory,
    get_memory_block,
    patch_memory,
    post_memory,
)
from jarvis.api.routes_runtime import (
    get_runtime_legacy,
    get_runtime_processes,
    get_runtime_startup,
)
from jarvis.api.routes_settings import get_settings, update_settings
from jarvis.api.routes_state import get_state
from jarvis.api.routes_tools import ToolRequestValidationError, get_tools, post_tool_request
from jarvis.daemon.app import (
    DaemonApp,
    DaemonAppBusyError,
    DaemonAppConflictError,
    DaemonAppError,
    DaemonAppNotFoundError,
    DaemonAppNotStartedError,
)
from jarvis.store.event_store import EventStoreError
from jarvis.memory import MemoryError
from jarvis.tools.registry import ToolRegistryError
from jarvis.turns.models import ConversationRepositoryError, TurnRepositoryError
from jarvis.turns.orchestrator import TurnOrchestratorBusyError, TurnOrchestratorError


MAX_REQUEST_BODY_BYTES = 1_048_576
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
ALLOWED_CORS_ORIGINS = {"http://127.0.0.1:41800", "http://localhost:41800", "null"}
CORS_ALLOW_METHODS = "GET, POST, PATCH, DELETE, OPTIONS"
CORS_ALLOW_HEADERS = "Content-Type"


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

        def do_PATCH(self) -> None:
            _dispatch(self, app, "PATCH")

        def do_DELETE(self) -> None:
            _dispatch(self, app, "DELETE")

        def do_OPTIONS(self) -> None:
            if self.headers.get("Origin") is None:
                self.send_error(501, "Unsupported method ('OPTIONS')")
                return

            self.send_response(204)
            self._send_cors_headers()
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _allowed_cors_origin(self) -> str | None:
            origin = self.headers.get("Origin")
            if origin in ALLOWED_CORS_ORIGINS:
                return origin
            return None

        def _send_cors_headers(self) -> None:
            origin = self._allowed_cors_origin()
            if origin is None:
                return

            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", CORS_ALLOW_METHODS)
            self.send_header("Access-Control-Allow-Headers", CORS_ALLOW_HEADERS)

        def send_error(
            self,
            code: int,
            message: str | None = None,
            explain: str | None = None,
        ) -> None:
            error_message = message or self.responses.get(code, ("Error",))[0]
            _write_json(self, code, {"error": error_message, "status": code})

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

        if method == "GET" and path == "/conversations":
            limit = _query_int(query, "limit", default=50)
            include_archived = _query_bool(query, "include_archived", default=False)
            _write_json(
                handler,
                200,
                get_conversations(
                    app,
                    limit=limit,
                    include_archived=include_archived,
                ),
            )
            return

        if method == "GET" and path == "/turns":
            conversation_id = _query_required_text(query, "conversation_id")
            limit = _query_int(query, "limit", default=50)
            newest_first = _query_bool(query, "newest_first", default=False)
            _write_json(
                handler,
                200,
                get_turns(
                    app,
                    conversation_id=conversation_id,
                    limit=limit,
                    newest_first=newest_first,
                ),
            )
            return

        if method == "GET" and path == "/settings":
            _write_json(handler, 200, get_settings(app))
            return

        if method == "GET" and path == "/runtime/processes":
            _write_json(handler, 200, get_runtime_processes(app))
            return

        if method == "GET" and path == "/runtime/startup":
            _write_json(handler, 200, get_runtime_startup(app))
            return

        if method == "GET" and path == "/runtime/legacy":
            _write_json(handler, 200, get_runtime_legacy(app))
            return

        if method == "GET" and path == "/tools":
            _write_json(handler, 200, get_tools(app))
            return

        if method == "POST" and path == "/tools/request":
            request_payload = _read_json_body(handler)
            _write_json(handler, 200, post_tool_request(app, request_payload))
            return

        if method == "GET" and path == "/memory":
            active_only = _query_bool(query, "active_only", default=False)
            limit = _query_int(query, "limit", default=100)
            kinds = _query_memory_kinds(query, "kind")
            _write_json(
                handler,
                200,
                get_memory(
                    app,
                    active_only=active_only,
                    kinds=kinds,
                    limit=limit,
                ),
            )
            return

        if method == "POST" and path == "/memory":
            request_payload = _read_json_body(handler)
            _write_json(handler, 201, post_memory(app, request_payload))
            return

        memory_id = _memory_resource_id(path)
        if memory_id is not None:
            if method == "GET":
                _write_json(handler, 200, get_memory_block(app, memory_id))
                return
            if method == "PATCH":
                request_payload = _read_json_body(handler)
                _write_json(handler, 200, patch_memory(app, memory_id, request_payload))
                return
            if method == "DELETE":
                _write_json(handler, 200, delete_memory(app, memory_id))
                return

        if method == "GET" and path == "/approvals":
            limit = _query_int(query, "limit", default=50)
            _write_json(handler, 200, get_approvals(app, limit=limit))
            return

        approval_action = _approval_action(path)
        if method == "POST" and approval_action is not None:
            approval_id, action = approval_action
            request_payload = _read_optional_json_body(handler)
            if action == "approve":
                _write_json(handler, 200, approve_approval(app, approval_id, request_payload))
                return
            if action == "reject":
                _write_json(handler, 200, reject_approval(app, approval_id, request_payload))
                return
            if action == "execute":
                _write_json(handler, 200, execute_approval(app, approval_id, request_payload))
                return

        if method == "POST" and path == "/settings":
            request_payload = _read_json_body(handler)
            if not isinstance(request_payload, dict):
                raise ValueError("Request JSON must be an object.")
            _write_json(handler, 200, update_settings(app, request_payload))
            return

        if method == "GET" and path == "/input/text":
            _write_json(handler, 405, get_text_input_method_error())
            return

        if method == "POST" and path == "/input/text":
            request_payload = _read_json_body(handler)
            _write_json(handler, 200, post_text_input(app, request_payload))
            return

        if method in {"PATCH", "DELETE"}:
            _write_json(
                handler,
                405,
                {"error": f"{method} {path} is not implemented.", "status": 405},
            )
            return

        _write_json(handler, 404, {"error": "Not found", "status": 404})
    except (
        ApprovalRequestValidationError,
        MemoryRequestValidationError,
        TextInputValidationError,
        ToolRequestValidationError,
    ) as exc:
        _write_json(handler, 400, {"error": str(exc), "status": 400})
    except DaemonAppNotStartedError as exc:
        _write_json(handler, 503, {"error": str(exc), "status": 503})
    except DaemonAppBusyError as exc:
        _write_json(handler, 409, {"error": str(exc), "status": 409})
    except DaemonAppConflictError as exc:
        _write_json(handler, 409, {"error": str(exc), "status": 409})
    except DaemonAppNotFoundError as exc:
        _write_json(handler, 404, {"error": str(exc), "status": 404})
    except TurnOrchestratorBusyError as exc:
        _write_json(handler, 409, {"error": str(exc), "status": 409})
    except TurnOrchestratorError:
        _write_json(handler, 500, {"error": "Text turn failed.", "status": 500})
    except ToolRegistryError as exc:
        status = 404 if str(exc).startswith(("Unknown tool:", "Unknown approval:")) else 400
        _write_json(handler, status, {"error": str(exc), "status": status})
    except (
        ValueError,
        DaemonAppError,
        EventStoreError,
        MemoryError,
        ConversationRepositoryError,
        TurnRepositoryError,
    ) as exc:
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


def _query_bool(query: dict[str, list[str]], key: str, *, default: bool) -> bool:
    if key not in query:
        return default
    raw_value = query[key][0].strip().lower()
    if raw_value in {"true", "1", "yes"}:
        return True
    if raw_value in {"false", "0", "no"}:
        return False
    raise ValueError(f"{key} must be true/false, 1/0, or yes/no.")


def _query_required_text(query: dict[str, list[str]], key: str) -> str:
    if key not in query:
        raise ValueError(f"{key} is required.")
    value = query[key][0].strip()
    if not value:
        raise ValueError(f"{key} must be a non-empty string.")
    return value


def _query_memory_kinds(query: dict[str, list[str]], key: str) -> list[str] | None:
    if key not in query:
        return None
    kinds: list[str] = []
    for raw_value in query[key]:
        for item in raw_value.split(","):
            normalized = item.strip()
            if not normalized:
                raise ValueError(f"{key} must not contain empty values.")
            kinds.append(normalized)
    return kinds


def _memory_resource_id(path: str) -> str | None:
    parts = [part for part in path.split("/") if part]
    if len(parts) != 2 or parts[0] != "memory":
        return None
    memory_id = unquote(parts[1]).strip()
    if not memory_id:
        return None
    return memory_id


def _approval_action(path: str) -> tuple[str, str] | None:
    parts = [part for part in path.split("/") if part]
    if len(parts) != 3 or parts[0] != "approvals":
        return None
    approval_id, action = parts[1], parts[2]
    if action not in {"approve", "reject", "execute"}:
        return None
    if not approval_id:
        return None
    return approval_id, action


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


def _read_optional_json_body(handler: BaseHTTPRequestHandler) -> Any | None:
    length_header = handler.headers.get("Content-Length")
    if length_header is None:
        return None
    try:
        length = int(length_header)
    except ValueError as exc:
        raise ValueError("Content-Length must be an integer.") from exc
    if length == 0:
        return None
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
    send_cors_headers = getattr(handler, "_send_cors_headers", None)
    if callable(send_cors_headers):
        send_cors_headers()
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
