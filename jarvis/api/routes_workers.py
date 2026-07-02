"""Worker job API payloads (FAZA E2).

`POST /workers/jobs` is a daemon-owned mutation riding the central
transport-token gate (C1, permission model §5). No tool exposes job
creation, so a model-originated worker job is structurally impossible.
Workers themselves only advise: their results become memory *candidates*,
never actions or speech (CONTRACTS.md §13, ADR-009).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from jarvis.daemon.app import DaemonApp


ROUTE_GROUP = "workers"


class WorkerRequestValidationError(ValueError):
    """Raised when a worker API request payload is invalid."""


def post_worker_job(app: DaemonApp, request_payload: Any) -> dict[str, Any]:
    if not isinstance(request_payload, Mapping):
        raise WorkerRequestValidationError("Request JSON must be an object.")
    worker_kind = _required_field(request_payload, "worker_kind")
    prompt = _required_field(request_payload, "prompt")
    requested_by = _required_field(request_payload, "requested_by")
    metadata = request_payload.get("metadata")
    if metadata is not None and not isinstance(metadata, Mapping):
        raise WorkerRequestValidationError("metadata must be a JSON object.")
    job = app.create_worker_job(
        worker_kind=worker_kind,
        prompt=prompt,
        requested_by=requested_by,
        metadata=metadata,
    )
    return {"job": job}


def get_worker_jobs(
    app: DaemonApp, *, limit: int = 50, status: str | None = None
) -> dict[str, Any]:
    return {"jobs": app.list_worker_jobs(limit=limit, status=status)}


def get_worker_job(app: DaemonApp, job_id: str) -> dict[str, Any]:
    return {"job": app.get_worker_job(job_id)}


def _required_field(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise WorkerRequestValidationError(f"{key} must be a non-empty string.")
    return value.strip()


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
