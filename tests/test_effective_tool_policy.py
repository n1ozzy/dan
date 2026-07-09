"""Live tool-permission policy: panel settings overlaid on config defaults.

The panel writes to the settings table; each turn rebuilds the policy from
config (TOML seed) overlaid with those settings, so what the operator sets in
the panel is what the engine enforces on the next turn — no restart.
"""

from __future__ import annotations

from jarvis.daemon.app import _policy_with_settings_overlay
from jarvis.tools.permissions import RequestSource, ToolDecision, ToolPermissionPolicy


def _decide_shell(policy: ToolPermissionPolicy) -> ToolDecision:
    return policy.decide(
        "shell_read",
        source=RequestSource.MODEL_ORIGINATED,
        tool_name="shell.read",
        payload={},
    ).decision


def test_panel_setting_disables_shell_approval_over_config_default() -> None:
    base = ToolPermissionPolicy(require_approval_for_shell=True)

    live = _policy_with_settings_overlay(
        base, {"security.require_approval_for_shell": False}
    )

    assert _decide_shell(live) == ToolDecision.ALLOW


def test_panel_absent_setting_keeps_config_default() -> None:
    base = ToolPermissionPolicy(require_approval_for_shell=True)

    live = _policy_with_settings_overlay(base, {})

    assert _decide_shell(live) == ToolDecision.APPROVAL_REQUIRED


def test_malformed_setting_falls_back_to_config_default_fail_closed() -> None:
    base = ToolPermissionPolicy(require_approval_for_shell=True)

    live = _policy_with_settings_overlay(
        base, {"security.require_approval_for_shell": "nope"}
    )

    assert _decide_shell(live) == ToolDecision.APPROVAL_REQUIRED


def test_master_auto_run_switch_allows_mutation_via_settings() -> None:
    base = ToolPermissionPolicy(auto_approve_mode="off")

    live = _policy_with_settings_overlay(
        base, {"security.auto_approve_mode": "all"}
    )

    assert _decide_shell(live) == ToolDecision.ALLOW


def _decide(policy: ToolPermissionPolicy, risk: str) -> ToolDecision:
    return policy.decide(
        risk,
        source=RequestSource.MODEL_ORIGINATED,
        tool_name=f"probe.{risk}",
        payload={},
    ).decision


def test_panel_settings_disable_ui_terminal_and_memory_approval() -> None:
    """The panel grants cover EVERY switchable mutation class, not just the
    original three: ui_act (clicking/typing), terminal_write (pasting into a
    terminal) and memory_write (operator's explicit override of the ADR-009
    default) each have their own live settings key."""

    base = ToolPermissionPolicy()

    live = _policy_with_settings_overlay(
        base,
        {
            "security.require_approval_for_ui": False,
            "security.require_approval_for_terminal": False,
            "security.require_approval_for_memory": False,
        },
    )

    assert _decide(live, "ui_act") == ToolDecision.ALLOW
    assert _decide(live, "terminal_write") == ToolDecision.ALLOW
    assert _decide(live, "memory_write") == ToolDecision.ALLOW
    # The switches are independent — shell was not touched and still gates.
    assert _decide(live, "shell_read") == ToolDecision.APPROVAL_REQUIRED


def test_ui_terminal_memory_default_to_requiring_approval() -> None:
    base = ToolPermissionPolicy()

    live = _policy_with_settings_overlay(base, {})

    assert _decide(live, "ui_act") == ToolDecision.APPROVAL_REQUIRED
    assert _decide(live, "terminal_write") == ToolDecision.APPROVAL_REQUIRED
    assert _decide(live, "memory_write") == ToolDecision.APPROVAL_REQUIRED


def test_unattended_sources_stay_blocked_even_with_every_grant_on() -> None:
    """Grants apply to attended sources only: an unattended scheduled job may
    not mutate anything no matter what the panel says."""

    base = ToolPermissionPolicy()

    live = _policy_with_settings_overlay(
        base,
        {
            "security.require_approval_for_shell": False,
            "security.require_approval_for_file_write": False,
            "security.require_approval_for_network": False,
            "security.require_approval_for_ui": False,
            "security.require_approval_for_terminal": False,
            "security.require_approval_for_memory": False,
        },
    )

    for risk in ("shell_write", "file_write", "network", "ui_act", "terminal_write", "memory_write"):
        result = live.decide(
            risk,
            source=RequestSource.SCHEDULED_WORKER,
            tool_name=f"probe.{risk}",
            payload={},
        )
        assert result.decision == ToolDecision.BLOCKED, risk


def test_unknown_auto_approve_mode_falls_back_to_off() -> None:
    """An unrecognized mode string must not silently half-work: it is rejected
    at overlay time and the base mode stays in force (fail-closed)."""

    base = ToolPermissionPolicy(auto_approve_mode="off")

    live = _policy_with_settings_overlay(
        base, {"security.auto_approve_mode": "yolo-everything"}
    )

    assert live.auto_approve_mode == "off"
    assert _decide_shell(live) == ToolDecision.APPROVAL_REQUIRED


def test_overlay_preserves_approved_roots_from_base(tmp_path) -> None:
    base = ToolPermissionPolicy(approved_roots=[str(tmp_path)])

    live = _policy_with_settings_overlay(base, {})

    result = live.decide(
        "file_read",
        source=RequestSource.DIRECT_USER_COMMAND,
        tool_name="file.read",
        payload={"path": str(tmp_path / "notes.txt")},
    )
    assert result.decision == ToolDecision.ALLOW


def test_destructive_runs_when_explicitly_enabled_in_auto_run(tmp_path) -> None:
    """Runtime-lab branch: auto_approve_mode=all means all when destructive is enabled."""

    base = ToolPermissionPolicy(destructive_tools_enabled=True, auto_approve_mode="off")

    live = _policy_with_settings_overlay(
        base,
        {
            "security.auto_approve_mode": "all",
            "security.require_approval_for_shell": False,
            "security.require_approval_for_file_write": False,
            "security.require_approval_for_network": False,
        },
    )

    result = live.decide(
        "destructive",
        source=RequestSource.MODEL_ORIGINATED,
        tool_name="delete_everything",
        payload={},
    )
    assert result.decision == ToolDecision.ALLOW
