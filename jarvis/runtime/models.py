"""Runtime supervision data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class RuntimeLaunchMode(StrEnum):
    CLI = "cli"
    LAUNCHD = "launchd"
    UNKNOWN = "unknown"


class RuntimeRisk(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RuntimeObservationKind(StrEnum):
    PROCESS = "process"
    LAUNCH_AGENT = "launch_agent"
    TEMP_ARTIFACT = "temp_artifact"
    STARTUP = "startup"
    WARNING = "warning"


@dataclass(frozen=True)
class RuntimeProcessObservation:
    id: str
    created_at: str
    label: str | None
    pid: int | None
    process_name: str | None
    command: str | None
    kind: str
    status: str
    risk: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "label": self.label,
            "pid": self.pid,
            "process_name": self.process_name,
            "command": self.command,
            "kind": str(self.kind),
            "status": self.status,
            "risk": str(self.risk),
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class RuntimeStartupSnapshot:
    created_at: str
    pid: int
    launch_mode: str
    official_label: str
    official_plist_installed: bool
    official_plist_loaded: str
    legacy_launch_agents: list[dict[str, Any]]
    legacy_temp_artifacts: list[dict[str, Any]]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "created_at": self.created_at,
            "pid": self.pid,
            "launch_mode": str(self.launch_mode),
            "official_label": self.official_label,
            "official_plist_installed": self.official_plist_installed,
            "official_plist_loaded": self.official_plist_loaded,
            "legacy_launch_agents": list(self.legacy_launch_agents),
            "legacy_temp_artifacts": list(self.legacy_temp_artifacts),
            "warnings": list(self.warnings),
        }


__all__ = [
    "RuntimeLaunchMode",
    "RuntimeObservationKind",
    "RuntimeProcessObservation",
    "RuntimeRisk",
    "RuntimeStartupSnapshot",
]
