"""Report-only runtime supervisor placeholder."""

from __future__ import annotations

from jarvis.runtime.models import RuntimeProcessObservation


class RuntimeSupervisor:
    """Future observer for launch mode and legacy conflicts."""

    def observe(self) -> RuntimeProcessObservation:
        raise NotImplementedError("runtime observation is not implemented yet")
