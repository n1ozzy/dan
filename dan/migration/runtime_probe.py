"""Injectable read-only runtime probes for the cutover engine.

The engine never inspects the live system directly: it asks a probe. Tests
inject :class:`FakeProbe`; the CLI in fixture mode uses an empty FakeProbe;
a real ``SystemProbe`` (read-only ``ps`` / ``lsof`` / ``launchctl print``)
is only constructed explicitly for a real preflight. Nothing in this module
ever kills, boots out or mutates anything.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ProbedProcess:
    pid: int
    command: str


@dataclass(frozen=True)
class ProbedHandle:
    pid: int
    command: str


@dataclass(frozen=True)
class ProbedListener:
    pid: int
    command: str
    port: int


@dataclass
class FakeProbe:
    """Deterministic probe for tests and fixture-mode CLI runs."""

    _processes: list[ProbedProcess] = field(default_factory=list)
    _db_handles: dict[str, list[ProbedHandle]] = field(default_factory=dict)
    _listeners: list[ProbedListener] = field(default_factory=list)
    _launchd_labels: list[str] = field(default_factory=list)

    def add_process(self, process: ProbedProcess) -> None:
        self._processes.append(process)

    def add_db_handle(self, path: Path | str, handle: ProbedHandle) -> None:
        self._db_handles.setdefault(str(Path(path)), []).append(handle)

    def add_listener(self, listener: ProbedListener) -> None:
        self._listeners.append(listener)

    def add_launchd_label(self, label: str) -> None:
        self._launchd_labels.append(label)

    # ------------------------------------------------------------ protocol
    def processes(self) -> list[ProbedProcess]:
        return list(self._processes)

    def db_handles(self, path: Path) -> list[ProbedHandle]:
        return list(self._db_handles.get(str(Path(path)), []))

    def listeners(self) -> list[ProbedListener]:
        return list(self._listeners)

    def launchd_labels(self) -> list[str]:
        return list(self._launchd_labels)


class SystemProbe:
    """Read-only live probe. Construct only for a real host preflight."""

    def __init__(self, *, patterns: tuple[str, ...] = ()) -> None:
        self._patterns = patterns

    def processes(self) -> list[ProbedProcess]:
        completed = subprocess.run(
            ["ps", "-axo", "pid=,command="],
            capture_output=True,
            check=False,
            text=True,
        )
        if completed.returncode != 0:
            raise RuntimeError(f"ps failed: {completed.stderr.strip()}")
        rows: list[ProbedProcess] = []
        for line in completed.stdout.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            pid_text, _, command = stripped.partition(" ")
            try:
                pid = int(pid_text)
            except ValueError:
                continue
            command = command.strip()
            if not self._patterns or any(p in command for p in self._patterns):
                rows.append(ProbedProcess(pid=pid, command=command))
        return rows

    def db_handles(self, path: Path) -> list[ProbedHandle]:
        from dan.migration.sqlite_backup import _lsof_handles

        return [
            ProbedHandle(pid=handle.pid, command=handle.command)
            for handle in _lsof_handles(Path(path))
        ]

    def listeners(self) -> list[ProbedListener]:
        completed = subprocess.run(
            ["lsof", "-nP", "-iTCP", "-sTCP:LISTEN", "-Fpcn"],
            capture_output=True,
            check=False,
            text=True,
        )
        if completed.returncode not in (0, 1):
            raise RuntimeError(f"lsof failed: {completed.stderr.strip()}")
        rows: list[ProbedListener] = []
        pid: int | None = None
        command = "unknown"
        for line in completed.stdout.splitlines():
            if line.startswith("p"):
                try:
                    pid = int(line[1:])
                except ValueError:
                    pid = None
                command = "unknown"
            elif line.startswith("c"):
                command = line[1:] or "unknown"
            elif line.startswith("n") and pid is not None:
                _, _, port_text = line.rpartition(":")
                try:
                    port = int(port_text)
                except ValueError:
                    continue
                rows.append(ProbedListener(pid=pid, command=command, port=port))
        return rows

    def launchd_labels(self) -> list[str]:
        completed = subprocess.run(
            ["launchctl", "list"],
            capture_output=True,
            check=False,
            text=True,
        )
        if completed.returncode != 0:
            raise RuntimeError(f"launchctl list failed: {completed.stderr.strip()}")
        labels: list[str] = []
        for line in completed.stdout.splitlines()[1:]:
            parts = line.split("\t")
            if len(parts) >= 3 and parts[2].strip():
                labels.append(parts[2].strip())
        return labels


__all__ = [
    "FakeProbe",
    "ProbedHandle",
    "ProbedListener",
    "ProbedProcess",
    "SystemProbe",
]
