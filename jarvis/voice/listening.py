"""Listening lease manager placeholder."""

from __future__ import annotations

from jarvis.voice.models import ListeningLease


class ListeningLeaseManager:
    def active(self) -> tuple[ListeningLease, ...]:
        raise NotImplementedError("listening leases are not implemented yet")
