"""HTTP lifecycle helpers for the local DAN daemon."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Protocol
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.parse import unquote

from dan.api.routes_brain import (
    BrainRequestValidationError,
    get_brain_adapters,
    get_brain_current,
    post_brain_switch,
)
from dan.api.routes_events import get_events
from dan.api.routes_health import get_health
from dan.api.routes_history import get_conversations, get_turns
from dan.api.routes_input import (
    TextInputValidationError,
    get_text_input_method_error,
    post_text_input,
)
from dan.api.routes_memory import (
    MemoryRequestValidationError,
    activate_memory_candidate,
    approve_memory_candidate,
    delete_memory,
    get_memory,
    get_memory_block,
    get_memory_candidate,
    get_memory_candidate_evidence,
    get_memory_candidates,
    get_memory_item,
    get_memory_items,
    patch_memory,
    post_memory,
    post_memory_recall,
    post_memory_compile_preview,
    post_memory_candidate,
    post_memory_candidate_evidence,
    reject_memory_candidate,
)
from dan.api.routes_runtime import (
    RuntimeSettingsApplyError,
    get_runtime_legacy,
    get_runtime_settings,
    get_runtime_processes,
    get_runtime_startup,
    post_runtime_restart,
    post_runtime_settings_apply,
)
from dan.api.routes_audio import get_audio_devices
from dan.api.routes_sessions import get_sessions
from dan.api.routes_settings import (
    explain_setting,
    get_settings,
    put_setting,
    update_settings,
)
from dan.api.routes_voice import (
    VoiceDisabledError,
    VoicePersonaReloadError,
    VoiceRequestValidationError,
    get_voice_personas,
    get_voice_queue,
    get_voice_runtime,
    get_listening,
    post_listen_lock,
    post_listen_unlock,
    post_ptt_down,
    post_ptt_up,
    post_voice_pause,
    post_voice_personas_apply,
    post_voice_queue_cancel,
    post_voice_queue_current_cancel,
    post_voice_queue_flush,
    post_voice_resume,
    post_voice_speak,
)
from dan.api.routes_state import get_state
from dan.api.routes_tools import ToolRequestValidationError, get_tools, post_tool_request
from dan.api.routes_workers import (
    WorkerRequestValidationError,
    get_worker_job,
    get_worker_jobs,
    post_worker_job,
)
from dan.api.websocket import (
    EventStreamSession,
    WebSocketHandshakeError,
    extract_token_candidates,
    select_subprotocol,
    validate_websocket_upgrade,
    websocket_accept_key,
)
from dan.daemon.app import (
    DaemonApp,
    DaemonAppBusyError,
    DaemonAppConflictError,
    DaemonAppError,
    DaemonAppNotFoundError,
    DaemonAppNotStartedError,
)
from dan.daemon.intake import IntakeClosedError
from dan.logging import get_logger
from dan.panel.menubar_app import resolve_panel_asset
from dan.security.transport import API_TOKEN_HEADER, verify_api_token
from dan.store.event_store import EventStoreError
from dan.memory import MemoryError
from dan.tools.registry import ToolRegistryError
from dan.turns.models import ConversationRepositoryError, TurnRepositoryError
from dan.turns.orchestrator import TurnOrchestratorBusyError, TurnOrchestratorError
from dan.config_registry import ConfigRegistryError
from dan.voice.assets import AssetVerificationError
from dan.voice.listening import ListeningLeaseError
from dan.voice.queue import (
    QueueBackpressure,
    VoiceQueueCancelledError,
    VoiceQueueError,
)
from dan.voice.resolver import VoiceResolverError


MAX_REQUEST_BODY_BYTES = 1_048_576
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
ALLOWED_CORS_ORIGINS = {
    "http://127.0.0.1:41800",
    "http://localhost:41800",
}
# PUT is deliberately absent: the browser panel never issues it, so it is not
# advertised cross-origin; the CLI talks same-origin with the transport token.
CORS_ALLOW_METHODS = "GET, POST, PATCH, DELETE, OPTIONS"
CORS_ALLOW_HEADERS = f"Content-Type, {API_TOKEN_HEADER}"
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
# Slowloris guard: drop a connection whose request never completes (FIX-06).
HANDLER_TIMEOUT_SECONDS = 10.0
# Each /stream session holds its own SQLite handle + worker thread; cap them.
MAX_STREAM_SESSIONS = 8
# Private-data reads gated behind the transport token (FIX-06 follow-up): after
# removing CORS null and validating Host, an untokened GET of conversations,
# memory or settings was the remaining "any local process reads your data"
# vector. Status/mechanism reads (/health, /state, /events, /tools) stay open
# for monitoring and panel bootstrap.
TOKEN_PROTECTED_GET_PATHS = {
    "/conversations",
    "/turns",
    "/memory",
    "/memory/candidates",
    "/memory/items",
    "/workers/jobs",
    "/runtime/legacy",
    "/runtime/processes",
    "/runtime/startup",
    "/settings",
    "/runtime/settings",
    "/voice/personas",
    "/voice/queue",
    "/voice/runtime",
    "/sessions",
}


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
        raise DaemonServerError("DAN API may only bind to localhost when localhost_only=true.")

    handler_class = _make_handler(app)
    httpd = ThreadingHTTPServer((host, port), handler_class)
    # Bound the number of concurrent /stream sessions (FIX-06); read here so a
    # test can lower the cap before the server is built.
    httpd.stream_session_semaphore = threading.BoundedSemaphore(MAX_STREAM_SESSIONS)
    return DaemonServer(httpd, app)


def serve_forever(app: DaemonApp, host: str, port: int) -> None:
    server = build_server(app, host, port)
    try:
        server.serve_forever()
    finally:
        server.server_close()


def _make_handler(app: DaemonApp) -> type[BaseHTTPRequestHandler]:
    class DANRequestHandler(BaseHTTPRequestHandler):
        server_version = "dand"
        error_content_type = "application/json"
        # Socket-level timeout so a slow/partial request cannot pin a worker
        # thread forever (FIX-06). The /stream session clears this itself after
        # upgrade (settimeout(None) in EventStreamSession).
        timeout = HANDLER_TIMEOUT_SECONDS

        def do_GET(self) -> None:
            self._dispatch_request("GET")

        def do_POST(self) -> None:
            self._dispatch_request("POST")

        def do_PUT(self) -> None:
            self._dispatch_request("PUT")

        def do_PATCH(self) -> None:
            self._dispatch_request("PATCH")

        def do_DELETE(self) -> None:
            self._dispatch_request("DELETE")

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

        def _dispatch_request(self, method: str) -> None:
            try:
                _dispatch(self, app, method)
            finally:
                _release_request_connection(app)

    return DANRequestHandler


def _release_request_connection(app: DaemonApp) -> None:
    release = getattr(app.conn, "close_current_thread", None)
    if callable(release):
        release()


def _host_header_is_local(handler: BaseHTTPRequestHandler) -> bool:
    """Reject a request whose Host is not loopback (DNS-rebinding guard, FIX-06).

    Loopback binding alone does not stop a browser on a foreign origin that
    resolves to 127.0.0.1: the Host header then carries the attacker's name.
    """

    host = handler.headers.get("Host")
    if not host:
        return False
    hostname = host.strip().lower()
    if hostname.startswith("["):  # bracketed IPv6, e.g. [::1] or [::1]:41800
        end = hostname.find("]")
        if end == -1:
            return False
        hostname = hostname[1:end]
    elif ":" in hostname:
        hostname = hostname.rsplit(":", 1)[0]
    return hostname in LOCAL_HOSTS


def _is_token_protected_read(method: str, path: str) -> bool:
    """A GET that returns private user data and needs the transport token."""

    if method != "GET":
        return False
    if path in TOKEN_PROTECTED_GET_PATHS:
        return True
    if _settings_explain_key(path) is not None:
        return True
    if _worker_job_resource_id(path) is not None:
        return True
    if _memory_candidate_evidence_resource_id(path) is not None:
        return True
    if _memory_item_resource_id(path) is not None:
        return True
    if _memory_candidate_resource_id(path) is not None:
        return True
    return _memory_resource_id(path) is not None  # GET /memory/<id>


def _dispatch(handler: BaseHTTPRequestHandler, app: DaemonApp, method: str) -> None:
    if not _host_header_is_local(handler):
        _write_json(handler, 403, {"error": "Forbidden", "status": 403}, close=True)
        return

    parsed = urlparse(handler.path)
    path = parsed.path
    query = parse_qs(parsed.query)

    if method == "GET" and path == "/stream":
        _handle_stream(handler, app, query)
        return

    if method == "GET" and (path == "/panel" or path == "/panel/" or path.startswith("/panel/")):
        _handle_panel_asset(handler, path)
        return

    if method in MUTATING_METHODS and not _transport_authorized(handler, app):
        _write_json(handler, 401, {"error": "Unauthorized", "status": 401}, close=True)
        return

    if _is_token_protected_read(method, path) and not _transport_authorized(handler, app):
        _write_json(handler, 401, {"error": "Unauthorized", "status": 401}, close=True)
        return

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
            latest = _query_bool(query, "latest", default=False)
            _write_json(
                handler,
                200,
                get_events(app, after_id=after_id, limit=limit, latest=latest),
            )
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

        if method == "GET" and path == "/audio/devices":
            _write_json(handler, 200, get_audio_devices(app))
            return

        if method == "GET" and path == "/brain/adapters":
            _write_json(handler, 200, get_brain_adapters(app))
            return

        if method == "GET" and path == "/brain/current":
            _write_json(handler, 200, get_brain_current(app))
            return

        if method == "POST" and path == "/brain/switch":
            request_payload = _read_json_body(handler)
            _write_json(handler, 200, post_brain_switch(app, request_payload))
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

        if method == "GET" and path == "/runtime/settings":
            _write_json(handler, 200, get_runtime_settings(app))
            return

        if method == "POST" and path == "/runtime/restart":
            request_payload = _read_optional_json_body(handler)
            # 202: the restart is accepted and happens right after this
            # response flushes; the coordinator never calls launchctl/pkill.
            _write_json(handler, 202, post_runtime_restart(app, request_payload))
            return

        if method == "POST" and path == "/runtime/settings/apply":
            request_payload = _read_json_body(handler)
            if not isinstance(request_payload, dict):
                raise RuntimeSettingsApplyError("Request JSON must be an object.")
            _write_json(handler, 200, post_runtime_settings_apply(app, request_payload))
            return

        if method == "GET" and path == "/tools":
            _write_json(handler, 200, get_tools(app))
            return

        if method == "POST" and path == "/tools/request":
            request_payload = _read_json_body(handler)
            _write_json(handler, 200, post_tool_request(app, request_payload))
            return

        if method == "POST" and path == "/workers/jobs":
            request_payload = _read_json_body(handler)
            _write_json(handler, 201, post_worker_job(app, request_payload))
            return

        if method == "GET" and path == "/workers/jobs":
            limit = _query_int(query, "limit", default=50)
            status_filter = query["status"][0] if "status" in query else None
            _write_json(
                handler,
                200,
                get_worker_jobs(app, limit=limit, status=status_filter),
            )
            return

        worker_job_id = _worker_job_resource_id(path)
        if method == "GET" and worker_job_id is not None:
            _write_json(handler, 200, get_worker_job(app, worker_job_id))
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

        if method == "POST" and path == "/memory/compile-preview":
            request_payload = _read_json_body(handler)
            _write_json(handler, 200, post_memory_compile_preview(app, request_payload))
            return

        if method == "POST" and path == "/memory/recall":
            request_payload = _read_json_body(handler)
            _write_json(handler, 200, post_memory_recall(app, request_payload))
            return

        if method == "GET" and path == "/memory/candidates":
            status_filter = query["status"][0] if "status" in query else None
            _write_json(
                handler,
                200,
                get_memory_candidates(app, status=status_filter),
            )
            return

        if method == "POST" and path == "/memory/candidates":
            request_payload = _read_json_body(handler)
            _write_json(handler, 201, post_memory_candidate(app, request_payload))
            return

        if method == "GET" and path == "/memory/items":
            _write_json(handler, 200, get_memory_items(app))
            return

        memory_item_id = _memory_item_resource_id(path)
        if method == "GET" and memory_item_id is not None:
            _write_json(handler, 200, get_memory_item(app, memory_item_id))
            return

        evidence_candidate_id = _memory_candidate_evidence_resource_id(path)
        if evidence_candidate_id is not None:
            if method == "GET":
                _write_json(
                    handler,
                    200,
                    get_memory_candidate_evidence(app, evidence_candidate_id),
                )
                return
            if method == "POST":
                request_payload = _read_json_body(handler)
                _write_json(
                    handler,
                    201,
                    post_memory_candidate_evidence(
                        app,
                        evidence_candidate_id,
                        request_payload,
                    ),
                )
                return

        candidate_action = _memory_candidate_action(path)
        if method == "POST" and candidate_action is not None:
            candidate_id, action = candidate_action
            if action == "approve":
                _write_json(handler, 200, approve_memory_candidate(app, candidate_id))
                return
            if action == "reject":
                _write_json(handler, 200, reject_memory_candidate(app, candidate_id))
                return
            if action == "activate":
                _write_json(handler, 200, activate_memory_candidate(app, candidate_id))
                return

        candidate_id = _memory_candidate_resource_id(path)
        if method == "GET" and candidate_id is not None:
            _write_json(handler, 200, get_memory_candidate(app, candidate_id))
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

        if method == "POST" and path == "/settings":
            request_payload = _read_json_body(handler)
            if not isinstance(request_payload, dict):
                raise ValueError("Request JSON must be an object.")
            _write_json(handler, 200, update_settings(app, request_payload))
            return

        if method == "POST" and path == "/voice/speak":
            request_payload = _read_json_body(handler)
            _write_json(handler, 201, post_voice_speak(app, request_payload))
            return

        if method == "POST" and path == "/voice/queue/flush":
            request_payload = _read_optional_json_body(handler)
            _write_json(handler, 200, post_voice_queue_flush(app, request_payload))
            return

        # "current" is a reserved queue resource: skip the claimed row (Task
        # 10 panel intent). It must match before the generic {id}/cancel rule.
        if method == "POST" and path == "/voice/queue/current/cancel":
            request_payload = _read_optional_json_body(handler)
            _write_json(
                handler,
                200,
                post_voice_queue_current_cancel(app, request_payload),
            )
            return

        voice_cancel_id = _voice_queue_cancel_resource_id(path)
        if method == "POST" and voice_cancel_id is not None:
            request_payload = _read_optional_json_body(handler)
            _write_json(
                handler,
                200,
                post_voice_queue_cancel(app, voice_cancel_id, request_payload),
            )
            return

        if method == "POST" and path == "/voice/pause":
            request_payload = _read_optional_json_body(handler)
            _write_json(handler, 200, post_voice_pause(app, request_payload))
            return

        if method == "POST" and path == "/voice/resume":
            request_payload = _read_optional_json_body(handler)
            _write_json(handler, 200, post_voice_resume(app, request_payload))
            return

        settings_explain_key = _settings_explain_key(path)
        if method == "GET" and settings_explain_key is not None:
            _write_json(handler, 200, explain_setting(app, settings_explain_key))
            return

        settings_key = _settings_resource_key(path)
        if method == "PUT" and settings_key is not None:
            request_payload = _read_json_body(handler)
            if not isinstance(request_payload, dict):
                raise ValueError("Request JSON must be an object.")
            _write_json(handler, 200, put_setting(app, settings_key, request_payload))
            return

        if method == "GET" and path == "/voice/listening":
            _write_json(handler, 200, get_listening(app))
            return

        if method == "GET" and path == "/sessions":
            _write_json(handler, 200, get_sessions(app))
            return

        if method == "GET" and path == "/voice/personas":
            _write_json(handler, 200, get_voice_personas(app))
            return

        if method == "POST" and path == "/voice/personas/apply":
            request_payload = _read_json_body(handler)
            _write_json(handler, 200, post_voice_personas_apply(app, request_payload))
            return

        if method == "GET" and path == "/voice/queue":
            limit = _query_int(query, "limit", default=20)
            _write_json(handler, 200, get_voice_queue(app, limit=limit))
            return

        if method == "GET" and path == "/voice/runtime":
            _write_json(handler, 200, get_voice_runtime(app))
            return

        if method == "POST" and path in {
            "/voice/ptt/down",
            "/voice/ptt/up",
            "/voice/listen/lock",
            "/voice/listen/unlock",
        }:
            request_payload = _read_json_body(handler)
            voice_handlers = {
                "/voice/ptt/down": post_ptt_down,
                "/voice/ptt/up": post_ptt_up,
                "/voice/listen/lock": post_listen_lock,
                "/voice/listen/unlock": post_listen_unlock,
            }
            _write_json(handler, 200, voice_handlers[path](app, request_payload))
            return

        if method == "GET" and path == "/input/text":
            _write_json(handler, 405, get_text_input_method_error())
            return

        if method == "POST" and path == "/input/text":
            request_payload = _read_json_body(handler)
            _write_json(handler, 200, post_text_input(app, request_payload))
            return

        if method in {"PUT", "PATCH", "DELETE"}:
            _write_json(
                handler,
                405,
                {"error": f"{method} {path} is not implemented.", "status": 405},
            )
            return

        _write_json(handler, 404, {"error": "Not found", "status": 404})
    except (
        BrainRequestValidationError,
        MemoryRequestValidationError,
        TextInputValidationError,
        ToolRequestValidationError,
        VoiceRequestValidationError,
        WorkerRequestValidationError,
    ) as exc:
        _write_json(handler, 400, {"error": str(exc), "status": 400})
    except RuntimeSettingsApplyError as exc:
        _write_json(
            handler,
            exc.status_code,
            {
                "error": str(exc),
                "status": exc.apply_status,
                "http_status": exc.status_code,
                "applied": [],
                "applied_keys": [],
                "rejected_keys": exc.rejected_keys,
                "unchanged_keys": exc.unchanged_keys,
                "requires_restart_keys": exc.requires_restart_keys,
                "blockers": exc.blockers,
                "warnings": exc.warnings,
                "runtime_settings": get_runtime_settings(app),
            },
        )
    except VoiceDisabledError as exc:
        _write_json(handler, 409, {"error": str(exc), "status": 409})
    except VoicePersonaReloadError as exc:
        # The persona edit was already rolled back; surface a server fault so
        # the panel does not read this as bad input.
        _write_json(handler, 500, {"error": str(exc), "status": 500})
    except (VoiceResolverError, AssetVerificationError) as exc:
        # A speak whose snapshot cannot be resolved is rejected before any
        # queue/event write: clear client error, empty queue.
        _write_json(handler, 400, {"error": str(exc), "status": 400})
    except QueueBackpressure as exc:
        _write_json(handler, 429, {"error": str(exc), "status": 429})
    except VoiceQueueCancelledError as exc:
        _write_json(handler, 409, {"error": str(exc), "status": 409})
    except VoiceQueueError as exc:
        _write_json(handler, 400, {"error": str(exc), "status": 400})
    except ListeningLeaseError as exc:
        # Unknown listening source/mode is bad client input, not a fault (FIX-17).
        _write_json(handler, 400, {"error": str(exc), "status": 400})
    except IntakeClosedError as exc:
        _write_json(
            handler,
            503,
            {
                "error": str(exc),
                "code": "intake_closed",
                "status": 503,
                "operation_id": exc.operation_id,
                "reason": exc.reason,
            },
        )
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
        ConfigRegistryError,
        DaemonAppError,
        EventStoreError,
        MemoryError,
        ConversationRepositoryError,
        TurnRepositoryError,
    ) as exc:
        _write_json(handler, 400, {"error": str(exc), "status": 400})
    except Exception:
        _write_json(handler, 500, {"error": "Internal server error", "status": 500})


def _handle_stream(
    handler: BaseHTTPRequestHandler,
    app: DaemonApp,
    query: dict[str, list[str]],
) -> None:
    """Upgrade GET /stream to the read-only websocket event stream (ADR-019).

    Everything up to the 101 answers as plain JSON; past the upgrade the
    socket speaks websocket frames only, so failures just drop the
    connection instead of writing an HTTP body into the frame stream.
    """

    handler.close_connection = True

    # Fail closed: the stream carries event history, so the handshake needs
    # the same transport token as mutating requests. Browsers cannot set
    # custom websocket headers, hence the dan-token.<token> subprotocol.
    if app.config.security.api_token_required:
        candidates = extract_token_candidates(handler.headers)
        if not any(verify_api_token(app.api_token, candidate) for candidate in candidates):
            _write_json(handler, 401, {"error": "Unauthorized", "status": 401})
            return

    try:
        key = validate_websocket_upgrade(handler.headers)
        after_id = _stream_after_id(query)
    except (WebSocketHandshakeError, ValueError) as exc:
        _write_json(handler, 400, {"error": str(exc), "status": 400})
        return

    # Refuse over the cap BEFORE the upgrade, while an HTTP error channel still
    # exists (FIX-06). The slot is held for the session's whole lifetime.
    semaphore = getattr(handler.server, "stream_session_semaphore", None)
    if semaphore is not None and not semaphore.acquire(blocking=False):
        _write_json(
            handler,
            503,
            {"error": "Too many concurrent stream sessions", "status": 503},
        )
        return

    try:
        subprotocol = select_subprotocol(handler.headers)
        response_lines = [
            "HTTP/1.1 101 Switching Protocols",
            "Upgrade: websocket",
            "Connection: Upgrade",
            f"Sec-WebSocket-Accept: {websocket_accept_key(key)}",
        ]
        if subprotocol is not None:
            response_lines.append(f"Sec-WebSocket-Protocol: {subprotocol}")
        handler.connection.sendall(("\r\n".join(response_lines) + "\r\n\r\n").encode("ascii"))

        session = EventStreamSession(handler.connection, handler.rfile, app, after_id=after_id)
        try:
            session.run()
        except Exception:
            # Past the upgrade there is no HTTP error channel left; the daemon
            # must survive any single stream connection dying.
            get_logger(__name__).exception("Event stream session failed")
            return
    finally:
        if semaphore is not None:
            semaphore.release()


def _stream_after_id(query: dict[str, list[str]]) -> int | None:
    if "after_id" not in query:
        return None
    after_id = _query_int(query, "after_id", default=0)
    if after_id < 0:
        raise ValueError("after_id must be zero or positive.")
    return after_id


def _transport_authorized(handler: BaseHTTPRequestHandler, app: DaemonApp) -> bool:
    """Authorize a mutating request against the local transport token.

    Not fail-closed, despite the shape of the call: when
    `security.api_token_required` is false — the default, and the live setting
    on this machine — every mutating request is authorized without inspecting a
    header, and nothing downstream re-checks who sent it. What that costs is in
    `docs/SECURITY_MODEL.md` §2, which is the only copy of the analysis.
    """

    if not app.config.security.api_token_required:
        return True
    provided = handler.headers.get(API_TOKEN_HEADER)
    return verify_api_token(app.api_token, provided)


def _query_int(query: dict[str, list[str]], key: str, *, default: int) -> int:
    if key not in query:
        return default
    raw_value = query[key][0]
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{key} must be an integer.") from exc
    if value < 0:
        raise ValueError(f"{key} must be a non-negative integer.")
    return value


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


def _voice_queue_cancel_resource_id(path: str) -> str | None:
    parts = [part for part in path.split("/") if part]
    if (
        len(parts) != 4
        or parts[0] != "voice"
        or parts[1] != "queue"
        or parts[3] != "cancel"
    ):
        return None
    request_id = unquote(parts[2]).strip()
    if not request_id:
        return None
    return request_id


def _settings_explain_key(path: str) -> str | None:
    parts = [part for part in path.split("/") if part]
    if len(parts) != 3 or parts[0] != "settings" or parts[1] != "explain":
        return None
    key = unquote(parts[2]).strip()
    if not key:
        return None
    return key


def _settings_resource_key(path: str) -> str | None:
    parts = [part for part in path.split("/") if part]
    if len(parts) != 2 or parts[0] != "settings":
        return None
    key = unquote(parts[1]).strip()
    if not key or key == "explain":
        return None
    return key


def _worker_job_resource_id(path: str) -> str | None:
    parts = [part for part in path.split("/") if part]
    if len(parts) != 3 or parts[0] != "workers" or parts[1] != "jobs":
        return None
    job_id = unquote(parts[2]).strip()
    if not job_id:
        return None
    return job_id


def _memory_resource_id(path: str) -> str | None:
    parts = [part for part in path.split("/") if part]
    if len(parts) != 2 or parts[0] != "memory":
        return None
    memory_id = unquote(parts[1]).strip()
    if not memory_id:
        return None
    return memory_id


def _memory_candidate_resource_id(path: str) -> str | None:
    parts = [part for part in path.split("/") if part]
    if len(parts) != 3 or parts[0] != "memory" or parts[1] != "candidates":
        return None
    candidate_id = unquote(parts[2]).strip()
    if not candidate_id:
        return None
    return candidate_id


def _memory_item_resource_id(path: str) -> str | None:
    parts = [part for part in path.split("/") if part]
    if len(parts) != 3 or parts[0] != "memory" or parts[1] != "items":
        return None
    memory_id = unquote(parts[2]).strip()
    if not memory_id:
        return None
    return memory_id


def _memory_candidate_evidence_resource_id(path: str) -> str | None:
    parts = [part for part in path.split("/") if part]
    if (
        len(parts) != 4
        or parts[0] != "memory"
        or parts[1] != "candidates"
        or parts[3] != "evidence"
    ):
        return None
    candidate_id = unquote(parts[2]).strip()
    if not candidate_id:
        return None
    return candidate_id


def _memory_candidate_action(path: str) -> tuple[str, str] | None:
    parts = [part for part in path.split("/") if part]
    if len(parts) != 4 or parts[0] != "memory" or parts[1] != "candidates":
        return None
    candidate_id = unquote(parts[2]).strip()
    action = parts[3]
    if not candidate_id or action not in {"approve", "reject", "activate"}:
        return None
    return candidate_id, action


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


def _write_json(
    handler: BaseHTTPRequestHandler,
    status: int,
    payload: dict[str, Any],
    *,
    close: bool = False,
) -> None:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    # close=True ends the connection: the request body may be unread (e.g. a
    # rejected mutation), and leaving the socket keep-alive would desync the
    # next request against those leftover bytes (FIX-06).
    if close:
        handler.close_connection = True
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    if close:
        handler.send_header("Connection", "close")
    send_cors_headers = getattr(handler, "_send_cors_headers", None)
    if callable(send_cors_headers):
        send_cors_headers()
    handler.end_headers()
    handler.wfile.write(body)


def _handle_panel_asset(handler: BaseHTTPRequestHandler, path: str) -> None:
    if path == "/panel" or path == "/panel/":
        asset_url_path = "/index.html"
    elif path.startswith("/panel/"):
        asset_url_path = "/" + unquote(path[len("/panel/") :])
    else:
        _write_json(handler, 404, {"error": "Panel asset not found", "status": 404})
        return

    asset_path, mime = resolve_panel_asset(asset_url_path)
    if asset_path is None:
        _write_json(handler, 404, {"error": "Panel asset not found", "status": 404})
        return

    body = asset_path.read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type", mime)
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
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
