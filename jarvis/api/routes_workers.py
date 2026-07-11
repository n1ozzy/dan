"""Worker endpoints are disabled on the runtime-lab branch.

Jarvis is a single active brain for now. Workers can come back later as isolated
technical task runs, but they must not create extra Jarvis chats/sessions or
memory paths while the main runtime is being stabilised.
"""

from __future__ import annotations

from typing import Any

from jarvis.daemon.app import DaemonApp


ROUTE_GROUP = "workers"


class WorkerRequestValidationError(ValueError):
    """Raised when callers hit disabled worker routes."""


def _disabled() -> dict[str, Any]:
    return {
        "ok": False,
        "status": 410,
        "error": "workers are disabled on this runtime branch; use the main Jarvis brain directly",
        "jobs": [],
    }


def post_worker_job(app: DaemonApp, request_payload: Any) -> dict[str, Any]:
    return _disabled()


def get_worker_jobs(
    app: DaemonApp, *, limit: int = 50, status: str | None = None
) -> dict[str, Any]:
    return _disabled()


def get_worker_job(app: DaemonApp, job_id: str) -> dict[str, Any]:
    return _disabled()


def register_routes(app: object) -> None:
    return None


__all__ = [
    "ROUTE_GROUP",
    "WorkerRequestValidationError",
    "get_worker_job",
    "get_worker_jobs",
    "post_worker_job",
    "register_routes",
]
