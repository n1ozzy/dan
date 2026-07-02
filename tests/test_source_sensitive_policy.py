"""FAZA C2: source-sensitive permission matrix tests.

Covers docs/MACOS_PERMISSION_MODEL.md §3 for the implemented permission
classes: user sources share one column, model_originated never gets a plain
allow, auto sources never mutate, unknown source is always blocked.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.tools.permissions import (
    AUTO_SOURCES,
    RequestSource,
    ToolDecision,
    ToolPermissionPolicy,
    USER_SOURCES,
)


USER = sorted(USER_SOURCES)
MODEL = [RequestSource.MODEL_ORIGINATED]
AUTO = sorted(AUTO_SOURCES)


def decide(
    risk: str,
    source: RequestSource | str,
    *,
    payload: dict | None = None,
    destructive_enabled: bool = False,
    roots: list[str] | None = None,
) -> ToolDecision:
    policy = ToolPermissionPolicy(
        destructive_tools_enabled=destructive_enabled,
        approved_roots=roots,
    )
    result = policy.decide(risk, source=source, tool_name="matrix", payload=payload or {})
    return ToolDecision(result.decision)


@pytest.mark.parametrize("source", USER)
@pytest.mark.parametrize("risk", ["safe_read", "safe_status"])
def test_safe_classes_allow_for_user_sources(risk: str, source: RequestSource) -> None:
    assert decide(risk, source) == ToolDecision.ALLOW


@pytest.mark.parametrize("source", MODEL + AUTO)
@pytest.mark.parametrize("risk", ["safe_read", "safe_status"])
def test_safe_classes_require_approval_for_model_and_auto(risk: str, source: RequestSource) -> None:
    assert decide(risk, source) == ToolDecision.APPROVAL_REQUIRED


@pytest.mark.parametrize("source", USER)
def test_file_read_in_roots_allows_for_user_sources(tmp_path: Path, source: RequestSource) -> None:
    target = tmp_path / "notes.txt"
    assert (
        decide("file_read", source, payload={"path": str(target)}, roots=[str(tmp_path)])
        == ToolDecision.ALLOW
    )


@pytest.mark.parametrize("source", MODEL + AUTO)
def test_file_read_in_roots_requires_approval_for_model_and_auto(
    tmp_path: Path, source: RequestSource
) -> None:
    target = tmp_path / "notes.txt"
    assert (
        decide("file_read", source, payload={"path": str(target)}, roots=[str(tmp_path)])
        == ToolDecision.APPROVAL_REQUIRED
    )


@pytest.mark.parametrize("source", USER + MODEL + AUTO)
def test_file_read_outside_roots_is_blocked_for_every_source(
    tmp_path: Path, source: RequestSource
) -> None:
    assert (
        decide(
            "file_read",
            source,
            payload={"path": str(tmp_path / "outside.txt")},
            roots=[str(tmp_path / "allowed")],
        )
        == ToolDecision.BLOCKED
    )


@pytest.mark.parametrize("source", USER + MODEL)
@pytest.mark.parametrize("risk", ["file_write", "shell_read", "shell_write", "network"])
def test_mutating_classes_require_approval_for_user_and_model(
    risk: str, source: RequestSource
) -> None:
    assert decide(risk, source) == ToolDecision.APPROVAL_REQUIRED


@pytest.mark.parametrize("source", AUTO)
@pytest.mark.parametrize("risk", ["file_write", "shell_read", "shell_write", "network"])
def test_mutating_classes_are_blocked_for_auto_sources(risk: str, source: RequestSource) -> None:
    assert decide(risk, source) == ToolDecision.BLOCKED


@pytest.mark.parametrize("source", USER + MODEL + AUTO)
def test_destructive_disabled_is_blocked_for_every_source(source: RequestSource) -> None:
    assert decide("destructive", source) == ToolDecision.BLOCKED


@pytest.mark.parametrize("source", USER + MODEL)
def test_destructive_enabled_requires_approval_for_user_and_model(source: RequestSource) -> None:
    assert decide("destructive", source, destructive_enabled=True) == ToolDecision.APPROVAL_REQUIRED


@pytest.mark.parametrize("source", AUTO)
def test_destructive_enabled_stays_blocked_for_auto_sources(source: RequestSource) -> None:
    assert decide("destructive", source, destructive_enabled=True) == ToolDecision.BLOCKED


@pytest.mark.parametrize("source", ["", "model", "user", "made_up_source", None])
def test_unknown_source_is_blocked_even_for_safe_read(source: object) -> None:
    policy = ToolPermissionPolicy()
    result = policy.decide("safe_read", source=source, tool_name="matrix", payload={})

    assert result.decision == ToolDecision.BLOCKED
    assert result.blocked is True


@pytest.mark.parametrize("source", USER + MODEL + AUTO)
def test_unknown_risk_is_blocked_for_every_source(source: RequestSource) -> None:
    assert decide("surprise_class", source) == ToolDecision.BLOCKED


def test_result_records_the_source() -> None:
    policy = ToolPermissionPolicy()
    result = policy.decide(
        "safe_read",
        source=RequestSource.MODEL_ORIGINATED,
        tool_name="matrix",
        payload={},
    )

    assert result.source == "model_originated"


def test_execute_approved_blocks_approval_without_stored_source(tmp_path: Path) -> None:
    from jarvis.daemon.app import create_daemon_app
    from tests.test_api_smoke import write_config

    config_path = write_config(tmp_path / "jarvis.toml", tmp_path / "home" / "jarvis.db")
    app = create_daemon_app(config_path)
    try:
        app.start()
        assert app.approval_gate is not None
        approval = app.approval_gate.create_approval(
            risk="shell_read",
            requested_by="api",
            action_type="tool:echo",
            payload={"tool_name": "echo", "arguments": {}, "requested_by": "api"},
        )
        app.approve(str(approval["id"]))

        response = app.execute_approved_tool(str(approval["id"]))

        assert response["ok"] is False
        assert response["status"] == "blocked"
        assert "no valid request source" in response["error"]
        assert app.conn is not None
        assert int(app.conn.execute("SELECT COUNT(*) FROM tool_runs").fetchone()[0]) == 0
    finally:
        app.stop(reason="test teardown")
        app.close()
