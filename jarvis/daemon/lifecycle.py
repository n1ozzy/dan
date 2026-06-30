"""Daemon lifecycle contracts."""

from __future__ import annotations

from typing import Protocol


class LifecycleHook(Protocol):
    def startup(self) -> None:
        """Run before daemon dependencies are exposed."""

    def shutdown(self) -> None:
        """Run during graceful daemon shutdown."""
