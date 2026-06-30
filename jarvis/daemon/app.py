"""Daemon application placeholder."""

from __future__ import annotations

from dataclasses import dataclass

from jarvis.config import JarvisConfig
from jarvis.daemon.state_machine import DaemonState


@dataclass
class JarvisDaemon:
    config: JarvisConfig
    state: DaemonState = DaemonState.BOOTING

    def run(self) -> None:
        raise NotImplementedError("jarvisd runtime loop is not implemented yet")
