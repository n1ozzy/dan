"""Release 1 direct-execution tool policy.

Legacy panel approval settings may still exist in persisted state, but they
must not reintroduce an approval row or awaiting-approval turn.
"""

from __future__ import annotations

from dan.daemon.app import _policy_with_settings_overlay
from dan.tools.permissions import RequestSource, ToolDecision, ToolPermissionPolicy


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


def test_panel_absent_setting_does_not_restore_config_approval_gate() -> None:
    base = ToolPermissionPolicy(require_approval_for_shell=True)

    live = _policy_with_settings_overlay(base, {})

    assert _decide_shell(live) == ToolDecision.ALLOW


def test_malformed_legacy_setting_does_not_restore_approval_gate() -> None:
    base = ToolPermissionPolicy(require_approval_for_shell=True)

    live = _policy_with_settings_overlay(
        base, {"security.require_approval_for_shell": "nope"}
    )

    assert _decide_shell(live) == ToolDecision.ALLOW


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
    assert _decide(live, "shell_read") == ToolDecision.ALLOW


def test_ui_terminal_memory_execute_without_approval_by_default() -> None:
    base = ToolPermissionPolicy()

    live = _policy_with_settings_overlay(base, {})

    assert _decide(live, "ui_act") == ToolDecision.ALLOW
    assert _decide(live, "terminal_write") == ToolDecision.ALLOW
    assert _decide(live, "memory_write") == ToolDecision.ALLOW


def test_permission_policy_does_not_add_source_specific_gates() -> None:

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
        assert result.decision == ToolDecision.ALLOW, risk


def test_unknown_legacy_auto_approve_mode_cannot_disable_direct_execution() -> None:

    base = ToolPermissionPolicy(auto_approve_mode="off")

    live = _policy_with_settings_overlay(
        base, {"security.auto_approve_mode": "yolo-everything"}
    )

    assert live.auto_approve_mode == "all"
    assert _decide_shell(live) == ToolDecision.ALLOW


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
