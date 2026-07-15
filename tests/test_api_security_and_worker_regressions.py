"""Targeted production-risk regressions for transport/auth and worker gates."""

from __future__ import annotations

from pathlib import Path

import json
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from jarvis.daemon.app import create_daemon_app
from jarvis.security.transport import API_TOKEN_HEADER
from tests.test_api_smoke import running_server, write_config


def _token_config(tmp_path: Path) -> Path:
    """Create a config with API-token requirement enabled."""

    config_path = write_config(
        tmp_path / "jarvis.toml", tmp_path / "home" / "jarvis.db"
    )
    text = config_path.read_text(encoding="utf-8")
    text = text.replace("api_token_required = false", "api_token_required = true")
    config_path.write_text(text, encoding="utf-8")
    return config_path


def _request(
    method: str,
    url: str,
    *,
    token: str | None = None,
    payload: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    request_headers: dict[str, str] = {"Accept": "application/json"}
    if token is not None:
        request_headers[API_TOKEN_HEADER] = token
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        request_headers["Content-Type"] = "application/json"

    req = Request(url, data=data, headers=request_headers, method=method)
    try:
        with urlopen(req, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


@pytest.mark.parametrize(
    "path",
    ("/settings", "/runtime/settings", "/conversations", "/memory", "/voice/queue"),
)
def test_token_required_read_routes_reject_without_token(
    tmp_path: Path, path: str
) -> None:
    app = create_daemon_app(_token_config(tmp_path))
    try:
        with running_server(app) as base_url:
            status, payload = _request("GET", f"{base_url}{path}")
    finally:
        app.close()

    assert status == 401
    assert payload["status"] == 401
    assert payload["error"] == "Unauthorized"


def test_token_required_read_routes_accept_with_token(tmp_path: Path) -> None:
    app = create_daemon_app(_token_config(tmp_path))
    try:
        with running_server(app) as base_url:
            settings_status, settings_payload = _request(
                "GET", f"{base_url}/settings", token=app.api_token
            )
            runtime_status, runtime_payload = _request(
                "GET", f"{base_url}/runtime/settings", token=app.api_token
            )
    finally:
        app.close()

    assert settings_status == 200
    assert runtime_status == 200
    assert isinstance(settings_payload, dict)
    assert "settings" in settings_payload
    assert "runtime" in runtime_payload


def test_token_required_post_routes_reject_without_and_accept_with_token(
    tmp_path: Path,
) -> None:
    app = create_daemon_app(_token_config(tmp_path))
    try:
        with running_server(app) as base_url:
            unauthorized_status, unauthorized_payload = _request(
                "POST",
                f"{base_url}/settings",
                payload={"settings": {"ui.theme": "dark"}},
            )
            authorized_status, authorized_payload = _request(
                "POST",
                f"{base_url}/settings",
                token=app.api_token,
                payload={"settings": {"ui.theme": "dark"}},
            )
    finally:
        app.close()

    assert unauthorized_status == 401
    assert unauthorized_payload["status"] == 401
    assert unauthorized_payload["error"] == "Unauthorized"
    assert authorized_status == 200
    assert authorized_payload["settings"]["ui.theme"] == "dark"


def test_workers_jobs_endpoint_requires_api_token_for_mutating_request(
    tmp_path: Path,
) -> None:
    app = create_daemon_app(_token_config(tmp_path))
    try:
        with running_server(app) as base_url:
            status, payload = _request("POST", f"{base_url}/workers/jobs")
    finally:
        app.close()

    assert status == 401
    assert payload["status"] == 401
    assert payload["error"] == "Unauthorized"


def test_workers_jobs_unknown_kind_is_disabled_via_api(tmp_path: Path) -> None:
    app = create_daemon_app(
        write_config(tmp_path / "jarvis.toml", tmp_path / "home" / "jarvis.db")
    )
    app.start()
    try:
        with running_server(app) as base_url:
            status, payload = _request(
                "POST",
                f"{base_url}/workers/jobs",
                token=app.api_token,
                payload={
                    "worker_kind": "codex_cli",
                    "prompt": "run candidate scan",
                    "requested_by": "api-test",
                },
            )
    finally:
        app.stop()
        app.close()

    assert status == 201
    assert payload == {
        "ok": False,
        "status": 410,
        "error": (
            "workers are disabled on this runtime branch; "
            "use the main Jarvis brain directly"
        ),
        "jobs": [],
    }


def test_workers_jobs_former_mock_kind_is_disabled_when_tokened(tmp_path: Path) -> None:
    app = create_daemon_app(
        write_config(tmp_path / "jarvis.toml", tmp_path / "home" / "jarvis.db")
    )
    app.start()
    try:
        with running_server(app) as base_url:
            status, payload = _request(
                "POST",
                f"{base_url}/workers/jobs",
                token=app.api_token,
                payload={
                    "worker_kind": "mock",
                    "prompt": "run candidate scan",
                    "requested_by": "api-test",
                },
            )
    finally:
        app.stop()
        app.close()

    assert status == 201
    assert payload == {
        "ok": False,
        "status": 410,
        "error": (
            "workers are disabled on this runtime branch; "
            "use the main Jarvis brain directly"
        ),
        "jobs": [],
    }


def test_workers_have_no_default_broker(
    tmp_path: Path,
) -> None:
    config_path = write_config(
        tmp_path / "jarvis.toml", tmp_path / "home" / "jarvis.db"
    )
    app = create_daemon_app(config_path)
    app.start()
    try:
        assert app.worker_broker is None
    finally:
        app.stop()
        app.close()
