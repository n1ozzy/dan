"""Local cockpit CORS tests for the Jarvis HTTP API."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from jarvis.daemon.app import DaemonApp, create_daemon_app
from tests.git_guards import assert_schema_and_migrations_unchanged
from tests.test_api_smoke import event_types, running_server, write_config


ROOT = Path(__file__).resolve().parents[1]
ALLOWED_ORIGINS = (
    "http://127.0.0.1:41800",
    "http://localhost:41800",
)
ALLOWED_METHODS = ("GET", "POST", "PATCH", "DELETE", "OPTIONS")
FORBIDDEN_RUNTIME_SNIPPETS = (
    "/Users/n1_ozzy/Documents/dev/dan",
    "/tmp/dan",
    "afplay",
    "--dangerously-skip-permissions",
)


@pytest.fixture
def app(tmp_path: Path) -> Iterator[DaemonApp]:
    config_path = write_config(tmp_path / "jarvis.toml", tmp_path / "home" / "jarvis.db")
    daemon_app = create_daemon_app(config_path)
    try:
        yield daemon_app
    finally:
        daemon_app.close()


def request_raw(
    method: str,
    url: str,
    *,
    origin: str | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, object, str]:
    request_headers = {"Accept": "application/json"}
    if origin is not None:
        request_headers["Origin"] = origin
    if headers:
        request_headers.update(headers)

    request = Request(url, headers=request_headers, method=method)
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, response.headers, response.read().decode("utf-8")
    except HTTPError as exc:
        return exc.code, exc.headers, exc.read().decode("utf-8")


def assert_allowed_cors_headers(headers: object, origin: str) -> None:
    assert headers.get("Access-Control-Allow-Origin") == origin
    assert headers.get("Vary") == "Origin"
    assert headers.get("Access-Control-Allow-Credentials") != "true"
    assert headers.get("Access-Control-Allow-Headers") == "Content-Type, X-Jarvis-Token"
    methods = {method.strip() for method in headers.get("Access-Control-Allow-Methods", "").split(",")}
    assert methods == set(ALLOWED_METHODS)


@pytest.mark.parametrize("origin", ALLOWED_ORIGINS)
def test_get_health_reflects_allowed_local_cockpit_origin(app: DaemonApp, origin: str) -> None:
    with running_server(app) as base_url:
        status, headers, _body = request_raw("GET", f"{base_url}/health", origin=origin)

    assert status == 200
    assert_allowed_cors_headers(headers, origin)


def test_get_health_rejects_null_origin(app: DaemonApp) -> None:
    """Origin "null" (file:// pages) must never be reflected — local pages could read private GETs."""
    with running_server(app) as base_url:
        status, headers, _body = request_raw("GET", f"{base_url}/health", origin="null")

    assert status == 200
    assert headers.get("Access-Control-Allow-Origin") is None
    assert headers.get("Access-Control-Allow-Credentials") != "true"


def test_get_health_does_not_allow_arbitrary_origin(app: DaemonApp) -> None:
    with running_server(app) as base_url:
        status, headers, _body = request_raw("GET", f"{base_url}/health", origin="http://evil.example")

    assert status == 200
    assert headers.get("Access-Control-Allow-Origin") is None
    assert headers.get("Access-Control-Allow-Credentials") != "true"


@pytest.mark.parametrize("path", ("/health", "/input/text"))
def test_options_preflight_returns_cors_headers_without_mutating_events(
    app: DaemonApp,
    path: str,
) -> None:
    before_events = event_types(app)

    with running_server(app) as base_url:
        status, headers, body = request_raw(
            "OPTIONS",
            f"{base_url}{path}",
            origin="http://127.0.0.1:41800",
            headers={
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Content-Type",
            },
        )

    assert status in {200, 204}
    assert body == ""
    assert_allowed_cors_headers(headers, "http://127.0.0.1:41800")
    assert event_types(app) == before_events


def test_error_response_includes_cors_headers_for_allowed_origin(app: DaemonApp) -> None:
    with running_server(app) as base_url:
        status, headers, body = request_raw(
            "GET",
            f"{base_url}/missing",
            origin="http://localhost:41800",
        )

    assert status == 404
    assert '"status": 404' in body
    assert_allowed_cors_headers(headers, "http://localhost:41800")


def test_no_origin_health_response_keeps_curl_behavior(app: DaemonApp) -> None:
    with running_server(app) as base_url:
        status, headers, body = request_raw("GET", f"{base_url}/health")

    assert status == 200
    assert '"service": "jarvisd"' in body
    assert headers.get("Access-Control-Allow-Origin") is None
    assert headers.get("Access-Control-Allow-Credentials") != "true"


def test_schema_and_migrations_are_unchanged() -> None:
    assert_schema_and_migrations_unchanged(ROOT)


def test_runtime_code_avoids_forbidden_legacy_strings() -> None:
    allowed_contracts = {("jarvis/voice/shared_broker.py", "/tmp/dan")}
    text_suffixes = {".py", ".sql", ".toml", ".md", ".sh", ".example", ".html", ".js", ".css", ""}
    offenders: list[tuple[str, str]] = []

    for root in (ROOT / "jarvis", ROOT / "scripts"):
        for path in root.rglob("*"):
            if "__pycache__" in path.parts or not path.is_file() or path.suffix not in text_suffixes:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            relative = str(path.relative_to(ROOT))
            for snippet in FORBIDDEN_RUNTIME_SNIPPETS:
                if snippet in text and (relative, snippet) not in allowed_contracts:
                    offenders.append((relative, snippet))

    assert offenders == []
