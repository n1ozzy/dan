"""The evening standup job, scheduled inside dand.

Timing was historically owned by the launchd job com.ozzy.voice-standup
(StartCalendarInterval 22:00 running ~/.claude/bin/voice-standup.sh).
`import_standup_schedule` reads that plist ONCE, records the source in the
scheduler state and never clobbers later operator changes. Disabling the
old plist itself is journaled cutover work (Task 12/14), not this module.

Behavior preserved: no material means silence, never filler; the spoken
voice is the canonical DAN persona; submission goes through VoiceService.
"""

from __future__ import annotations

import plistlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from dan.jobs.scheduler import load_state, save_state
from dan.voice.models import SpeechIntent

STANDUP_SOURCE = "standup"
STANDUP_SESSION = "standup"
STANDUP_PERSONA = "dan"
DEFAULT_TIME = "22:00"


@dataclass(frozen=True)
class StandupSchedule:
    enabled: bool
    time_str: str
    imported_from: str


class StandupJob:
    name = STANDUP_SOURCE

    def __init__(self, text_provider: Callable[[], str | None]) -> None:
        self._text_provider = text_provider

    def build_intent(self) -> SpeechIntent | None:
        text = self._text_provider()
        if text is None or not str(text).strip():
            return None  # silence beats filler
        return SpeechIntent(
            text=str(text),
            persona=STANDUP_PERSONA,
            source=STANDUP_SOURCE,
            session=STANDUP_SESSION,
            participant=STANDUP_PERSONA,
            priority=0,
            lane="background",
            interrupt_policy="finish_current",
            utterance_index=0,
        )


def import_standup_schedule(plist_path: Path, *, state_path: Path) -> StandupSchedule:
    """One-time import of enabled/time from the legacy plist, source recorded.

    If the state already carries a standup entry with an import source, the
    stored values win — operator changes are never overwritten by re-import.
    """

    state = load_state(state_path)
    entry = state.get(STANDUP_SOURCE) or {}
    if entry.get("imported_from"):
        return StandupSchedule(
            enabled=bool(entry.get("enabled", True)),
            time_str=str(entry.get("time", DEFAULT_TIME)),
            imported_from=str(entry["imported_from"]),
        )

    time_str = DEFAULT_TIME
    enabled = True
    plist_path = Path(plist_path)
    if plist_path.is_file():
        data = plistlib.loads(plist_path.read_bytes())
        calendar = data.get("StartCalendarInterval") or {}
        hour = calendar.get("Hour")
        minute = calendar.get("Minute")
        if isinstance(hour, int) and isinstance(minute, int):
            time_str = f"{hour:02d}:{minute:02d}"
    else:
        enabled = False

    entry = state.setdefault(STANDUP_SOURCE, {})
    entry["enabled"] = enabled
    entry["time"] = time_str
    entry["imported_from"] = str(plist_path)
    save_state(state_path, state)
    return StandupSchedule(enabled=enabled, time_str=time_str, imported_from=str(plist_path))


__all__ = [
    "DEFAULT_TIME",
    "STANDUP_PERSONA",
    "STANDUP_SESSION",
    "STANDUP_SOURCE",
    "StandupJob",
    "StandupSchedule",
    "import_standup_schedule",
]
