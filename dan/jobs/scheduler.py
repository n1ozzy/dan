"""Minimal persistent job scheduler living inside dand.

One daily wall-clock slot per job. Submission goes through VoiceService —
never around it. State (enabled, time, last run day, import source) is a
small JSON file under ~/.dan written atomically, so the schedule and the
"already fired today" fact survive daemon restarts.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

_TIME = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


class SchedulerError(RuntimeError):
    """Invalid schedule configuration or state."""


class SubmitService(Protocol):
    def submit(self, intent):  # pragma: no cover - structural typing only
        ...


class ScheduledJob(Protocol):
    name: str

    def build_intent(self):  # returns SpeechIntent | None
        ...


def _parse_time(time_str: str) -> tuple[int, int]:
    match = _TIME.match(time_str)
    if match is None:
        raise SchedulerError(f"invalid HH:MM time: {time_str!r}")
    return int(match.group(1)), int(match.group(2))


def _parse_at(at: str | datetime) -> datetime:
    if isinstance(at, datetime):
        return at
    try:
        return datetime.fromisoformat(at)
    except ValueError as exc:
        raise SchedulerError(f"invalid tick timestamp: {at!r}") from exc


def load_state(state_path: Path) -> dict[str, dict[str, object]]:
    if not state_path.is_file():
        return {}
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SchedulerError(f"could not load job state {state_path}: {exc}") from exc
    return data if isinstance(data, dict) else {}


def save_state(state_path: Path, state: dict[str, dict[str, object]]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_path.parent / f".{state_path.name}.dan-jobs-tmp"
    tmp.write_text(
        json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.chmod(tmp, 0o600)
    os.replace(tmp, state_path)


@dataclass
class _Registration:
    job: ScheduledJob
    enabled: bool
    hour: int
    minute: int


class JobScheduler:
    def __init__(self, service: SubmitService, state_path: Path) -> None:
        self.service = service
        self.state_path = Path(state_path)
        self._jobs: dict[str, _Registration] = {}
        self._state = load_state(self.state_path)

    def register(self, job: ScheduledJob, *, enabled: bool, time_str: str) -> None:
        stored = self._state.get(job.name) or {}
        stored_time = stored.get("time")
        if isinstance(stored_time, str) and _TIME.match(stored_time):
            time_str = stored_time
        stored_enabled = stored.get("enabled")
        if isinstance(stored_enabled, bool):
            enabled = stored_enabled
        hour, minute = _parse_time(time_str)
        self._jobs[job.name] = _Registration(job=job, enabled=enabled, hour=hour, minute=minute)
        entry = self._state.setdefault(job.name, {})
        entry.setdefault("enabled", enabled)
        entry.setdefault("time", f"{hour:02d}:{minute:02d}")
        save_state(self.state_path, self._state)

    def tick(self, at: str | datetime) -> int:
        now = _parse_at(at)
        fired = 0
        for name, registration in self._jobs.items():
            if not registration.enabled:
                continue
            slot = now.replace(
                hour=registration.hour,
                minute=registration.minute,
                second=0,
                microsecond=0,
            )
            if now < slot:
                continue
            today = now.date().isoformat()
            entry = self._state.setdefault(name, {})
            if entry.get("last_run_date") == today:
                continue
            intent = registration.job.build_intent()
            # Fired-or-silent, the day is consumed either way: a job with no
            # material must stay silent instead of retrying into the night.
            entry["last_run_date"] = today
            save_state(self.state_path, self._state)
            if intent is None:
                continue
            self.service.submit(intent)
            fired += 1
        return fired


__all__ = ["JobScheduler", "SchedulerError", "load_state", "save_state"]
