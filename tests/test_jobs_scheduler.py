"""Task 11: the standup schedule lives inside dand, persistently.

The scheduler submits SpeechIntent(source="standup", session="standup")
through VoiceService (never around it), survives a daemon restart via its
state file, imports the legacy plist timing exactly once and is NOT the
Radio scheduler.
"""

from __future__ import annotations

import json
import plistlib
from pathlib import Path

import pytest

from dan.jobs.scheduler import JobScheduler
from dan.jobs.standup import STANDUP_SESSION, STANDUP_SOURCE, StandupJob, import_standup_schedule
from dan.voice.models import SpeechIntent


class RecordingService:
    """Stands in for VoiceService: submissions land here or nowhere."""

    def __init__(self) -> None:
        self.submissions: list[SpeechIntent] = []

    def submit(self, intent: SpeechIntent) -> SpeechIntent:
        self.submissions.append(intent)
        return intent


def _scheduler(tmp_path: Path, service: RecordingService, *, time_str: str = "09:00") -> JobScheduler:
    scheduler = JobScheduler(service=service, state_path=tmp_path / "jobs.json")
    scheduler.register(
        StandupJob(text_provider=lambda: "Meldunek wieczorny: wszystko gra."),
        enabled=True,
        time_str=time_str,
    )
    return scheduler


def test_standup_schedule_runs_inside_dand(tmp_path: Path) -> None:
    service = RecordingService()
    scheduler = _scheduler(tmp_path, service)
    scheduler.tick(at="2026-07-18T09:00")
    assert len(service.submissions) == 1
    intent = service.submissions[0]
    assert intent.source == STANDUP_SOURCE == "standup"
    assert intent.session == STANDUP_SESSION == "standup"
    assert intent.text == "Meldunek wieczorny: wszystko gra."


def test_tick_before_scheduled_time_is_silent(tmp_path: Path) -> None:
    service = RecordingService()
    scheduler = _scheduler(tmp_path, service)
    scheduler.tick(at="2026-07-18T08:59")
    assert service.submissions == []


def test_standup_fires_once_per_day(tmp_path: Path) -> None:
    service = RecordingService()
    scheduler = _scheduler(tmp_path, service)
    scheduler.tick(at="2026-07-18T09:00")
    scheduler.tick(at="2026-07-18T09:05")
    scheduler.tick(at="2026-07-18T23:59")
    assert len(service.submissions) == 1
    scheduler.tick(at="2026-07-19T09:01")
    assert len(service.submissions) == 2


def test_schedule_survives_a_restart(tmp_path: Path) -> None:
    service = RecordingService()
    scheduler = _scheduler(tmp_path, service)
    scheduler.tick(at="2026-07-18T09:00")
    assert len(service.submissions) == 1

    # New process, same state file: config kept, no double fire the same day.
    service2 = RecordingService()
    reborn = JobScheduler(service=service2, state_path=tmp_path / "jobs.json")
    reborn.register(StandupJob(text_provider=lambda: "znowu"), enabled=True, time_str="09:00")
    reborn.tick(at="2026-07-18T10:00")
    assert service2.submissions == []
    reborn.tick(at="2026-07-19T09:00")
    assert len(service2.submissions) == 1


def test_no_material_means_silence(tmp_path: Path) -> None:
    service = RecordingService()
    scheduler = JobScheduler(service=service, state_path=tmp_path / "jobs.json")
    scheduler.register(StandupJob(text_provider=lambda: None), enabled=True, time_str="09:00")
    scheduler.tick(at="2026-07-18T09:00")
    assert service.submissions == []


def test_disabled_job_never_fires(tmp_path: Path) -> None:
    service = RecordingService()
    scheduler = JobScheduler(service=service, state_path=tmp_path / "jobs.json")
    scheduler.register(StandupJob(text_provider=lambda: "tekst"), enabled=False, time_str="09:00")
    scheduler.tick(at="2026-07-18T09:00")
    assert service.submissions == []


def test_import_legacy_plist_once_and_record_source(tmp_path: Path) -> None:
    plist = tmp_path / "com.ozzy.voice-standup.plist"
    plist.write_bytes(
        plistlib.dumps(
            {
                "Label": "com.ozzy.voice-standup",
                "StartCalendarInterval": {"Hour": 22, "Minute": 0},
                "RunAtLoad": False,
            }
        )
    )
    state_path = tmp_path / "jobs.json"
    schedule = import_standup_schedule(plist, state_path=state_path)
    assert schedule.time_str == "22:00"
    assert schedule.enabled is True
    assert schedule.imported_from == str(plist)

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["standup"]["time"] == "22:00"
    assert state["standup"]["imported_from"] == str(plist)

    # Second import is a no-op: operator changes are never clobbered.
    state["standup"]["time"] = "21:30"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    again = import_standup_schedule(plist, state_path=state_path)
    assert again.time_str == "21:30"


def test_scheduler_is_not_the_radio_scheduler(tmp_path: Path) -> None:
    import dan.jobs.scheduler as scheduler_module
    import dan.jobs.standup as standup_module

    for module in (scheduler_module, standup_module):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "radio" not in source.lower()
        assert "playlist" not in source.lower()


def test_scheduler_state_write_is_atomic(tmp_path: Path) -> None:
    service = RecordingService()
    scheduler = _scheduler(tmp_path, service)
    scheduler.tick(at="2026-07-18T09:00")
    state_path = tmp_path / "jobs.json"
    assert state_path.is_file()
    leftovers = [p for p in tmp_path.iterdir() if p.name.startswith("jobs.json.") or p.suffix == ".tmp"]
    assert leftovers == []
    json.loads(state_path.read_text(encoding="utf-8"))
