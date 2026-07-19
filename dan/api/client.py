"""Thin typed HTTP client for the local dand API (used by the CLI)."""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from dan.security.transport import API_TOKEN_HEADER, TransportTokenError, load_api_token


DEFAULT_TIMEOUT_SECONDS = 5.0
BASE_URL_ENV = "DAN_API_URL"


class DaemonClientError(Exception):
    """Base error for local daemon API client failures."""


class DaemonUnreachableError(DaemonClientError):
    """The daemon did not answer on the configured base URL."""


class DaemonAPIError(DaemonClientError):
    """The daemon answered with a non-2xx JSON error."""

    def __init__(self, status: int, payload: dict[str, Any]):
        super().__init__(str(payload.get("error") or f"HTTP {status}"))
        self.status = status
        self.payload = payload


class DaemonClient:
    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    @classmethod
    def from_config(
        cls,
        config: Any,
        *,
        base_url: str | None = None,
        token: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> DaemonClient:
        resolved = (
            base_url
            or os.environ.get(BASE_URL_ENV)
            or f"http://{config.daemon.host}:{config.daemon.port}"
        )
        if token is None:
            from dan.paths import resolve_runtime_paths

            try:
                token = load_api_token(resolve_runtime_paths(config).runtime_dir)
            except TransportTokenError:
                token = None
        return cls(resolved, token=token, timeout=timeout)

    def request(
        self,
        method: str,
        path: str,
        *,
        payload: Any | None = None,
        query: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{urlencode(query)}"
        headers = {"Accept": "application/json"}
        if self.token is not None:
            headers[API_TOKEN_HEADER] = self.token
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        http_request = Request(url, data=data, headers=headers, method=method)
        try:
            with urlopen(http_request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            try:
                body = json.loads(exc.read().decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                body = {"error": str(exc), "status": exc.code}
            if not isinstance(body, dict):
                body = {"error": str(exc), "status": exc.code}
            raise DaemonAPIError(exc.code, body) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise DaemonUnreachableError(str(exc)) from exc

    def get(self, path: str, *, query: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.request("GET", path, query=query)

    def post(self, path: str, payload: Any | None = None) -> dict[str, Any]:
        return self.request("POST", path, payload=payload if payload is not None else {})

    def put(self, path: str, payload: Any) -> dict[str, Any]:
        return self.request("PUT", path, payload=payload)

    def health(self) -> dict[str, Any]:
        return self.get("/health")

    def intake_state(self) -> dict[str, Any]:
        return self.get("/runtime/intake")

    def close_intake(
        self,
        operation_id: str,
        reason: str,
        *,
        reopen_policy: str = "daemon",
        timeout_seconds: float = 30.0,
    ) -> dict[str, Any]:
        return self.post(
            "/runtime/intake/close",
            {
                "operation_id": operation_id,
                "reason": reason,
                "reopen_policy": reopen_policy,
                "timeout_seconds": timeout_seconds,
            },
        )

    def open_intake(self, operation_id: str) -> dict[str, Any]:
        return self.post("/runtime/intake/open", {"operation_id": operation_id})

    def speak(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.post("/voice/speak", payload)

    def voice_queue(self, *, limit: int = 20) -> dict[str, Any]:
        return self.get("/voice/queue", query={"limit": limit})

    def cancel_request(self, request_id: str) -> dict[str, Any]:
        return self.post(f"/voice/queue/{quote(request_id, safe='')}/cancel")

    def flush_session(self, session: str) -> dict[str, Any]:
        return self.post("/voice/queue/flush", {"session": session})

    def pause(self) -> dict[str, Any]:
        return self.post("/voice/pause")

    def resume(self) -> dict[str, Any]:
        return self.post("/voice/resume")

    def voice_runtime(self) -> dict[str, Any]:
        return self.get("/voice/runtime")

    def explain_setting(self, key: str) -> dict[str, Any]:
        return self.get(f"/settings/explain/{quote(key, safe='')}")

    def put_setting(self, key: str, value: Any) -> dict[str, Any]:
        return self.put(f"/settings/{quote(key, safe='')}", {"value": value})


__all__ = [
    "BASE_URL_ENV",
    "DEFAULT_TIMEOUT_SECONDS",
    "DaemonAPIError",
    "DaemonClient",
    "DaemonClientError",
    "DaemonUnreachableError",
]
