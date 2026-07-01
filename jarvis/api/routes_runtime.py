"""Runtime supervision route payloads."""

from __future__ import annotations

from typing import Any

from jarvis.daemon.app import DaemonApp
from jarvis.runtime.models import RuntimeProcessObservation, RuntimeRisk
from jarvis.runtime.supervisor import OFFICIAL_LABEL


ROUTE_GROUP = "runtime"

LEGACY_GUIDANCE = [
    "Legacy runtime items are detected only.",
    "no cleanup performed.",
    "Stop legacy components manually only after explicit human approval.",
]


def get_runtime_processes(app: DaemonApp) -> dict[str, Any]:
    observations = app.runtime_supervisor.observe_all()
    conflicts = _conflicts(observations)
    return {
        "observations": [observation.to_dict() for observation in observations],
        "conflicts": [observation.to_dict() for observation in conflicts],
        "conflict_count": len(conflicts),
        "report_only": True,
        "cleanup_automated": False,
    }


def get_runtime_startup(app: DaemonApp) -> dict[str, Any]:
    return {
        "startup": app.runtime_supervisor.startup_snapshot().to_dict(),
        "report_only": True,
        "official_label": OFFICIAL_LABEL,
    }


def get_runtime_legacy(app: DaemonApp) -> dict[str, Any]:
    conflicts = app.runtime_supervisor.legacy_conflicts()
    return {
        "legacy_conflicts": [observation.to_dict() for observation in conflicts],
        "legacy_conflict_count": len(conflicts),
        "guidance": list(LEGACY_GUIDANCE),
    }


def _conflicts(observations: list[RuntimeProcessObservation]) -> list[RuntimeProcessObservation]:
    return [
        observation
        for observation in observations
        if observation.risk in {RuntimeRisk.HIGH, RuntimeRisk.CRITICAL}
    ]


def register_routes(app: object) -> None:
    return None


__all__ = [
    "LEGACY_GUIDANCE",
    "ROUTE_GROUP",
    "get_runtime_legacy",
    "get_runtime_processes",
    "get_runtime_startup",
    "register_routes",
]
