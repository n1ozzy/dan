"""Legacy approval settings stay in storage compatibility, not active runtime API."""

from __future__ import annotations

import json
from pathlib import Path

from jarvis.daemon.app import create_daemon_app
from tests.test_api_smoke import request_json, running_server, write_config


LEGACY_APPROVAL_SETTING_KEYS = (
    "security.require_approval_for_file_write",
    "security.require_approval_for_memory",
    "security.require_approval_for_network",
    "security.require_approval_for_shell",
    "security.require_approval_for_terminal",
    "security.require_approval_for_ui",
)


def test_post_runtime_settings_apply_rejects_legacy_approval_settings_without_persisting(
    tmp_path: Path,
) -> None:
    config_path = write_config(tmp_path / "jarvis.toml", tmp_path / "home" / "jarvis.db")
    app = create_daemon_app(config_path)
    try:
        before = app.get_settings()
        requested = {key: False for key in LEGACY_APPROVAL_SETTING_KEYS}

        with running_server(app) as base_url:
            status, payload = request_json(
                "POST",
                f"{base_url}/runtime/settings/apply",
                {"settings": requested},
            )

        assert status == 400
        assert payload["status"] == "blocked"
        assert payload["applied_keys"] == []
        assert payload["rejected_keys"] == sorted(LEGACY_APPROVAL_SETTING_KEYS)
        assert app.get_settings() == before
        assert not set(LEGACY_APPROVAL_SETTING_KEYS).intersection(app.get_settings())
    finally:
        app.close()


def test_runtime_settings_do_not_project_legacy_approval_rows(
    tmp_path: Path,
) -> None:
    config_path = write_config(tmp_path / "jarvis.toml", tmp_path / "home" / "jarvis.db")
    app = create_daemon_app(config_path)
    try:
        app.update_settings({key: True for key in LEGACY_APPROVAL_SETTING_KEYS})

        with running_server(app) as base_url:
            status, payload = request_json("GET", f"{base_url}/runtime/settings")

        assert status == 200
        assert set(LEGACY_APPROVAL_SETTING_KEYS).issubset(app.get_settings())

        tools = payload["tools"]
        assert "network_policy" not in tools
        assert "approval_required_tools" not in tools

        tools_capabilities = payload["capability_graph"]["tools_capabilities"]
        assert "network_policy" not in tools_capabilities
        assert "approval_required_tools" not in tools_capabilities
        assert not set(LEGACY_APPROVAL_SETTING_KEYS).intersection(
            tools_capabilities["apply_capabilities"]
        )

        preview_fields = payload["settings_preview"]["sections"]["tools_internet"]["fields"]
        assert "network_policy" not in preview_fields
        assert "approval_required_tools" not in preview_fields

        serialized = json.dumps(payload, sort_keys=True)
        assert "security.require_approval_for_" not in serialized
        assert '"network_policy"' not in serialized
        assert '"approval_required_tools"' not in serialized
    finally:
        app.close()
