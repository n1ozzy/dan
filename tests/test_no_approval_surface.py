"""The local runtime has one direct, observable tool path and no approval API."""

from pathlib import Path

import pytest

from dan.api.routes_input import post_text_input
from dan.api.routes_runtime import get_runtime_settings
from dan.api.routes_tools import post_tool_request
from dan.brain.claude_cli_adapter import ClaudeCliAdapter
from dan.brain.manager import BrainManager
from dan.brain.test_adapter import TestBrainAdapter as HermeticBrainAdapter
from dan.daemon.app import create_daemon_app
from tests.test_api_smoke import request_json, running_server, write_config


def test_approval_routes_are_not_exposed(tmp_path: Path) -> None:
    config_path = write_config(tmp_path / "dan.toml", tmp_path / "home" / "dan.db")
    app = create_daemon_app(config_path)
    try:
        app.start()
        with running_server(app) as base_url:
            get_status, _ = request_json("GET", f"{base_url}/approvals")
            post_status, _ = request_json(
                "POST",
                f"{base_url}/approvals/obsolete/approve",
            )

        assert get_status == 404
        assert post_status == 404
    finally:
        app.close()


def test_active_runtime_payloads_do_not_advertise_approvals(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_real_provider_is_reached(*args: object, **kwargs: object) -> None:
        raise AssertionError("automatic test reached the production Claude CLI route")

    monkeypatch.setattr(ClaudeCliAdapter, "generate", fail_if_real_provider_is_reached)
    config_path = write_config(tmp_path / "dan.toml", tmp_path / "home" / "dan.db")
    app = create_daemon_app(config_path)
    production_manager = app.brain_manager
    app.brain_manager = BrainManager(
        [HermeticBrainAdapter(default_model="test-model")],
        default_adapter="test",
    )
    if production_manager is not None:
        production_manager.close()
    try:
        app.start()

        payload = post_text_input(app, {"text": "ping", "source": "panel"})
        snapshot = app.snapshot_state()
        runtime_settings = get_runtime_settings(app)
        tool_result = post_tool_request(
            app,
            {
                "tool_name": "echo",
                "arguments": {"text": "direct"},
                "requested_by": "panel",
            },
        )

        assert "approvals" not in payload
        assert "pending_approval_count" not in snapshot
        assert "approvals" not in runtime_settings
        assert tool_result["status"] == "finished"
        assert tool_result["output"] == {"arguments": {"text": "direct"}}
        assert "approval_id" not in tool_result
    finally:
        app.close()
