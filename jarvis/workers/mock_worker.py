"""Deterministic mock worker for tests, smoke, and local scaffold operation.

The first real worker of FAZA E2. Real provider workers (codex/claude CLI)
stay unimplemented until their own stage — same pattern as brain adapters:
deterministic fake first, real subprocess behind a config flag later.
"""

from __future__ import annotations

from jarvis.workers.jobs import WorkerJob, WorkerMemoryCandidate, WorkerResult


MAX_PROMPT_ECHO_CHARS = 500


class MockWorker:
    """Stateless, deterministic worker: no subprocess, no network, no side effects."""

    kind = "mock"

    def run(self, job: WorkerJob) -> WorkerResult:
        prompt = _clip(job.prompt.strip() or "(empty prompt)")
        summary = f"Mock worker completed: {prompt}"
        candidate = WorkerMemoryCandidate(
            kind="fact",
            title=f"Worker result for job {job.id}",
            body=f"Mock worker analyzed the request and proposes remembering: {prompt}",
        )
        return WorkerResult(summary=summary, memory_candidate=candidate)


def _clip(text: str) -> str:
    if len(text) <= MAX_PROMPT_ECHO_CHARS:
        return text
    return text[:MAX_PROMPT_ECHO_CHARS] + "…"


__all__ = ["MockWorker", "MAX_PROMPT_ECHO_CHARS"]
