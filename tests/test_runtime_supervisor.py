"""Prompt 07 RuntimeSupervisor report-only behavior tests."""

from __future__ import annotations

import inspect
import subprocess
from pathlib import Path
from typing import Any

from dan.runtime.models import RuntimeProcessObservation, RuntimeRisk
from dan.runtime.supervisor import RuntimeSupervisor


FIXED_NOW = "2026-07-01T12:00:00+00:00"


def provider(*commands: str) -> list[dict[str, Any]]:
    return [
        {
            "pid": index + 100,
            "process_name": "python",
            "command": command,
        }
        for index, command in enumerate(commands)
    ]


def labels(observations: list[RuntimeProcessObservation]) -> set[str | None]:
    return {observation.label for observation in observations}


def supervisor_for_processes(*commands: str) -> RuntimeSupervisor:
    return RuntimeSupervisor(process_provider=lambda: provider(*commands), now=lambda: FIXED_NOW)


def test_voice_broker_process_is_detected_as_high_risk() -> None:
    observations = supervisor_for_processes("python voice_broker.py").observe_processes()

    assert len(observations) == 1
    assert observations[0].label == "legacy_voice_broker"
    assert observations[0].kind == "process"
    assert observations[0].status == "running"
    assert observations[0].risk == RuntimeRisk.HIGH


def test_listener_loop_process_is_detected_as_high_risk() -> None:
    observations = supervisor_for_processes("python listen_ozzy.py loop").observe_processes()

    assert labels(observations) == {"legacy_listener"}
    assert observations[0].risk == RuntimeRisk.HIGH


def test_auto_jarvis_process_is_detected_as_high_risk() -> None:
    observations = supervisor_for_processes("python auto_jarvis.py").observe_processes()

    assert labels(observations) == {"legacy_auto_jarvis"}
    assert observations[0].risk == RuntimeRisk.HIGH


def test_panel_web_process_is_detected_as_high_risk() -> None:
    observations = supervisor_for_processes("python dan_panel_web.py").observe_processes()

    assert labels(observations) == {"legacy_panel_web"}
    assert observations[0].risk == RuntimeRisk.HIGH


def test_benign_process_is_ignored() -> None:
    observations = supervisor_for_processes("python unrelated.py").observe_processes()

    assert observations == []


def test_long_process_command_is_truncated() -> None:
    long_command = "python voice_broker.py " + ("x" * 900)

    observations = supervisor_for_processes(long_command).observe_processes()

    assert len(observations[0].command or "") <= 500
    assert observations[0].details["command_truncated"] is True


def test_process_command_redacts_secret_like_values() -> None:
    observations = supervisor_for_processes("python voice_broker.py DAN_API_KEY=sk-secret123").observe_processes()

    assert "sk-secret123" not in (observations[0].command or "")
    assert "DAN_API_KEY=[REDACTED]" in (observations[0].command or "")


def test_launch_agent_file_existence_is_reported_from_injected_home(tmp_path: Path) -> None:
    home = tmp_path / "home"
    launch_agents = home / "Library" / "LaunchAgents"
    launch_agents.mkdir(parents=True)
    (launch_agents / "com.dan.voice-broker.plist").write_text("placeholder", encoding="utf-8")

    supervisor = RuntimeSupervisor(home=home, process_provider=lambda: [], now=lambda: FIXED_NOW)

    observations = supervisor.observe_launch_agents()

    assert labels(observations) == {"legacy_voice_broker_launch_agent"}
    assert observations[0].kind == "launch_agent"
    assert observations[0].status == "installed"
    assert observations[0].risk == RuntimeRisk.HIGH
    assert observations[0].details["loaded"] == "not_checked"


def test_official_plist_metadata_reports_not_checked_loaded_state(tmp_path: Path) -> None:
    home = tmp_path / "home"
    launch_agents = home / "Library" / "LaunchAgents"
    launch_agents.mkdir(parents=True)
    (launch_agents / "com.dan.dand.plist").write_text("placeholder", encoding="utf-8")

    supervisor = RuntimeSupervisor(home=home, process_provider=lambda: [], now=lambda: FIXED_NOW)

    snapshot = supervisor.startup_snapshot()

    assert snapshot.official_label == "com.dan.dand"
    assert snapshot.official_plist_installed is True
    assert snapshot.official_plist_loaded == "not_checked"


def test_temp_artifact_existence_is_reported_from_injected_temp_dir(tmp_path: Path) -> None:
    temp_dir = tmp_path / "temp"
    (temp_dir / "dan-voice").mkdir(parents=True)

    supervisor = RuntimeSupervisor(temp_dir=temp_dir, process_provider=lambda: [], now=lambda: FIXED_NOW)

    observations = supervisor.observe_temp_artifacts()

    assert labels(observations) == {"legacy_temp_dan_voice"}
    assert observations[0].kind == "temp_artifact"
    assert observations[0].status == "present"
    assert observations[0].risk == RuntimeRisk.MEDIUM
    assert observations[0].details["kind"] == "dan-voice"


def test_startup_snapshot_contains_required_fields_and_warnings(tmp_path: Path) -> None:
    home = tmp_path / "home"
    launch_agents = home / "Library" / "LaunchAgents"
    launch_agents.mkdir(parents=True)
    (launch_agents / "com.dan.xtts-server.plist").write_text("placeholder", encoding="utf-8")

    supervisor = RuntimeSupervisor(
        home=home,
        process_provider=lambda: provider("python xtts_server.py"),
        now=lambda: FIXED_NOW,
    )

    snapshot = supervisor.startup_snapshot()
    payload = snapshot.to_dict()

    assert payload["pid"] > 0
    assert payload["launch_mode"] == "cli"
    assert payload["official_label"] == "com.dan.dand"
    assert payload["official_plist_loaded"] == "not_checked"
    assert payload["warnings"]


def test_legacy_conflicts_returns_only_high_or_critical_legacy_observations(tmp_path: Path) -> None:
    temp_dir = tmp_path / "temp"
    (temp_dir / "dan-listen").mkdir(parents=True)
    supervisor = RuntimeSupervisor(
        home=tmp_path / "home",
        temp_dir=temp_dir,
        process_provider=lambda: provider("python voice_broker.py"),
        now=lambda: FIXED_NOW,
    )

    conflicts = supervisor.legacy_conflicts()

    assert labels(conflicts) == {"legacy_voice_broker"}
    assert {conflict.risk for conflict in conflicts} <= {RuntimeRisk.HIGH, RuntimeRisk.CRITICAL}


def test_no_launchctl_is_executed_when_using_fake_process_provider(
    monkeypatch: object,
    tmp_path: Path,
) -> None:
    def fail_run(*args: object, **kwargs: object) -> None:
        raise AssertionError("subprocess.run should not be called")

    monkeypatch.setattr(subprocess, "run", fail_run)
    supervisor = RuntimeSupervisor(
        home=tmp_path / "home",
        temp_dir=tmp_path / "temp",
        process_provider=lambda: provider("python auto_jarvis.py"),
        now=lambda: FIXED_NOW,
    )

    assert supervisor.observe_all()
    assert supervisor.startup_snapshot().official_plist_loaded == "not_checked"


def test_runtime_supervisor_has_no_cleanup_kill_or_unload_behavior() -> None:
    public_names = {name for name in dir(RuntimeSupervisor) if not name.startswith("_")}
    source = inspect.getsource(RuntimeSupervisor)

    assert "cleanup" not in public_names
    assert "delete" not in public_names
    assert "kill" not in public_names
    assert "unload" not in public_names
    assert ".unlink(" not in source
    assert "os.kill" not in source
    assert "rmtree" not in source
    assert "launchctl" not in source


def test_runtime_code_does_not_contain_forbidden_legacy_strings() -> None:
    root = Path(__file__).resolve().parents[1]
    forbidden = (
        "/Users/" "n1_ozzy" "/Documents/dev/dan",
        "/tmp/dan",
        "afplay",
        "--dangerously-skip-permissions",
    )
    offenders: list[tuple[str, str]] = []

    for path in (root / "dan" / "runtime").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for snippet in forbidden:
            if snippet in text:
                offenders.append((str(path.relative_to(root)), snippet))

    assert offenders == []
