"""Read-only runtime supervision and legacy conflict detection."""

from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

from dan.events.models import utc_now_iso
from dan.logging import redact_secrets
from dan.runtime.models import (
    RuntimeLaunchMode,
    RuntimeObservationKind,
    RuntimeProcessObservation,
    RuntimeRisk,
    RuntimeStartupSnapshot,
)


MAX_COMMAND_CHARS = 500
OFFICIAL_LABEL = "com.dan.dand"
OFFICIAL_PLIST_NAME = f"{OFFICIAL_LABEL}.plist"
NOT_CHECKED = "not_checked"

LEGACY_PROCESS_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("legacy_voice_broker", ("voice_broker.py",)),
    ("legacy_listener", ("listen_ozzy.py", "loop")),
    ("legacy_auto_jarvis", ("auto_jarvis.py",)),
    ("legacy_panel_web", ("dan_panel_web.py",)),
    ("legacy_panel_old", ("dan_panel.py",)),
    ("legacy_xtts_server", ("xtts_server.py",)),
    ("legacy_start_script", ("start-voice-broker.sh",)),
    ("legacy_start_script", ("start-jarvis.sh",)),
)

LEGACY_LAUNCH_AGENTS: tuple[tuple[str, str], ...] = (
    ("legacy_voice_broker_launch_agent", "com.dan.voice-broker.plist"),
    ("legacy_ozzy_jarvis_launch_agent", "com.ozzy.jarvis.plist"),
    ("legacy_xtts_server_launch_agent", "com.dan.xtts-server.plist"),
)

LEGACY_TEMP_ARTIFACTS: tuple[tuple[str, str], ...] = (
    ("legacy_temp_dan_voice", "dan-voice"),
    ("legacy_temp_dan_listen", "dan-listen"),
)


class RuntimeSupervisorError(Exception):
    """Raised when runtime observations cannot be collected safely."""


class RuntimeSupervisor:
    def __init__(
        self,
        *,
        home: Path | None = None,
        temp_dir: Path | None = None,
        process_provider: Callable[[], Iterable[Mapping[str, Any]]] | None = None,
        now: Callable[[], str] | None = None,
    ):
        self.home = Path.home() if home is None else Path(home).expanduser()
        self.temp_dir = Path(tempfile.gettempdir()) if temp_dir is None else Path(temp_dir)
        self.process_provider = process_provider or _default_process_provider
        self.now = now or utc_now_iso

    def observe_processes(self) -> list[RuntimeProcessObservation]:
        observations: list[RuntimeProcessObservation] = []
        for process in self.process_provider():
            command = _coerce_optional_str(process.get("command") or process.get("cmdline"))
            process_name = _coerce_optional_str(process.get("process_name") or process.get("name"))
            search_text = " ".join(part for part in (process_name, command) if part)
            label = _match_legacy_process(search_text)
            if label is None:
                continue

            pid = _coerce_optional_int(process.get("pid"))
            redacted_command = redact_secrets(command) if command is not None else None
            safe_command, command_truncated = _truncate_command(redacted_command)
            observations.append(
                self._observation(
                    label=label,
                    pid=pid,
                    process_name=process_name,
                    command=safe_command,
                    kind=RuntimeObservationKind.PROCESS,
                    status="running",
                    risk=RuntimeRisk.HIGH,
                    details={
                        "command_truncated": command_truncated,
                        "matched_family": label,
                    },
                )
            )
        return observations

    def observe_launch_agents(self) -> list[RuntimeProcessObservation]:
        observations: list[RuntimeProcessObservation] = []
        launch_agents_dir = self.home / "Library" / "LaunchAgents"

        for label, plist_name in LEGACY_LAUNCH_AGENTS:
            plist_path = launch_agents_dir / plist_name
            if not plist_path.exists():
                continue
            observations.append(
                self._observation(
                    label=label,
                    pid=None,
                    process_name=None,
                    command=None,
                    kind=RuntimeObservationKind.LAUNCH_AGENT,
                    status="installed",
                    risk=RuntimeRisk.HIGH,
                    details={
                        "path": str(plist_path),
                        "loaded": NOT_CHECKED,
                    },
                )
            )

        official_path = self.official_plist_path
        if official_path.exists():
            observations.append(
                self._observation(
                    label="official_dand_launch_agent",
                    pid=None,
                    process_name=None,
                    command=None,
                    kind=RuntimeObservationKind.LAUNCH_AGENT,
                    status="installed",
                    risk=RuntimeRisk.INFO,
                    details={
                        "path": str(official_path),
                        "label": OFFICIAL_LABEL,
                        "loaded": NOT_CHECKED,
                    },
                )
            )

        return observations

    def observe_temp_artifacts(self) -> list[RuntimeProcessObservation]:
        observations: list[RuntimeProcessObservation] = []
        for label, dirname in LEGACY_TEMP_ARTIFACTS:
            artifact_path = self.temp_dir / dirname
            if not artifact_path.exists():
                continue
            observations.append(
                self._observation(
                    label=label,
                    pid=None,
                    process_name=None,
                    command=None,
                    kind=RuntimeObservationKind.TEMP_ARTIFACT,
                    status="present",
                    risk=RuntimeRisk.MEDIUM,
                    details={
                        "path": str(artifact_path),
                        "exists": True,
                        "kind": dirname,
                    },
                )
            )
        return observations

    def observe_all(self) -> list[RuntimeProcessObservation]:
        return [
            *self.observe_processes(),
            *self.observe_launch_agents(),
            *self.observe_temp_artifacts(),
        ]

    def startup_snapshot(self) -> RuntimeStartupSnapshot:
        launch_agent_observations = self.observe_launch_agents()
        temp_artifacts = self.observe_temp_artifacts()
        legacy_launch_agents = [
            observation.to_dict()
            for observation in launch_agent_observations
            if observation.label and observation.label.startswith("legacy_")
        ]
        legacy_temp_artifacts = [observation.to_dict() for observation in temp_artifacts]
        conflicts = self.legacy_conflicts()
        warnings = [
            f"Legacy runtime conflict detected: {observation.label}"
            for observation in conflicts
            if observation.label is not None
        ]
        if legacy_temp_artifacts:
            warnings.append("Legacy temp artifacts detected; report only, no cleanup performed.")

        return RuntimeStartupSnapshot(
            created_at=self.now(),
            pid=os.getpid(),
            launch_mode=RuntimeLaunchMode.CLI,
            official_label=OFFICIAL_LABEL,
            official_plist_installed=self.official_plist_path.exists(),
            official_plist_loaded=NOT_CHECKED,
            legacy_launch_agents=legacy_launch_agents,
            legacy_temp_artifacts=legacy_temp_artifacts,
            warnings=warnings,
        )

    def legacy_conflicts(self) -> list[RuntimeProcessObservation]:
        return [
            observation
            for observation in self.observe_all()
            if observation.risk in {RuntimeRisk.HIGH, RuntimeRisk.CRITICAL}
            and (observation.label or "").startswith("legacy_")
        ]

    @property
    def official_plist_path(self) -> Path:
        return self.home / "Library" / "LaunchAgents" / OFFICIAL_PLIST_NAME

    def _observation(
        self,
        *,
        label: str | None,
        pid: int | None,
        process_name: str | None,
        command: str | None,
        kind: RuntimeObservationKind,
        status: str,
        risk: RuntimeRisk,
        details: dict[str, Any],
    ) -> RuntimeProcessObservation:
        return RuntimeProcessObservation(
            id=_stable_id(kind=str(kind), label=label, pid=pid, command=command),
            created_at=self.now(),
            label=label,
            pid=pid,
            process_name=process_name,
            command=command,
            kind=str(kind),
            status=status,
            risk=str(risk),
            details=details,
        )


def _default_process_provider() -> Iterable[Mapping[str, Any]]:
    result = subprocess.run(
        ["ps", "-axo", "pid=,comm=,command="],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return []

    processes: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 2:
            continue
        pid, process_name = parts[0], parts[1]
        command = parts[2] if len(parts) > 2 else process_name
        processes.append(
            {
                "pid": pid,
                "process_name": process_name,
                "command": command,
            }
        )
    return processes


def _match_legacy_process(command: str) -> str | None:
    for label, required_fragments in LEGACY_PROCESS_PATTERNS:
        if all(fragment in command for fragment in required_fragments):
            return label
    return None


def _truncate_command(command: str | None) -> tuple[str | None, bool]:
    if command is None or len(command) <= MAX_COMMAND_CHARS:
        return command, False
    return command[: MAX_COMMAND_CHARS - 3] + "...", True


def _stable_id(*, kind: str, label: str | None, pid: int | None, command: str | None) -> str:
    raw = "|".join((kind, label or "", "" if pid is None else str(pid), command or ""))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _coerce_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _coerce_optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "MAX_COMMAND_CHARS",
    "NOT_CHECKED",
    "OFFICIAL_LABEL",
    "RuntimeSupervisor",
    "RuntimeSupervisorError",
]
