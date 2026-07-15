"""Read-only local machine context for Jarvis."""

from __future__ import annotations

import getpass
import os
import platform
import socket
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jarvis.tools.registry import Tool


class SystemStatusTool(Tool):
    name = "system_status"
    description = (
        "Report live local-machine context: macOS version, architecture, host, "
        "user, home directory and the Jarvis daemon working directory."
    )
    risk = "safe_status"
    input_schema = {"type": "object"}

    def run(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        del arguments
        return {
            "ok": True,
            "system": platform.system(),
            "os_version": platform.mac_ver()[0] or platform.release(),
            "architecture": platform.machine(),
            "hostname": socket.gethostname(),
            "user": getpass.getuser(),
            "home": str(Path.home()),
            "daemon_cwd": os.getcwd(),
        }


class SystemTool(SystemStatusTool):
    """Backward-compatible name for the live status tool."""


__all__ = ["SystemStatusTool", "SystemTool"]
