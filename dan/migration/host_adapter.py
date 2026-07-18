"""Production host boundary for a Release 1 cutover."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any, Callable
from urllib.request import urlopen

from dan.daemon.intake import IntakeGate, IntakeGateError
from dan.migration.cutover import CutoverBlocked, CutoverManifest, LaunchAgent


class SystemCutoverHostAdapter:
    """Executes the explicit macOS lifecycle operations owned by cutover."""

    def __init__(
        self,
        *,
        launchctl: Path = Path("/bin/launchctl"),
        command_runner: Callable[..., Any] = subprocess.run,
        health_url: str = "http://127.0.0.1:41741/health",
        health_timeout_seconds: float = 30.0,
    ) -> None:
        self._launchctl = Path(launchctl)
        self._run = command_runner
        self._health_url = health_url
        self._health_timeout_seconds = health_timeout_seconds
        self._source_database: Path | None = None

    @property
    def intake_database(self) -> Path:
        if self._source_database is None:
            raise CutoverBlocked("production host adapter was not validated")
        return self._source_database

    def validate(
        self,
        manifest: CutoverManifest,
        home: Path,
        *,
        operation_id: str,
    ) -> None:
        del home
        if not self._launchctl.is_file() or not os.access(self._launchctl, os.X_OK):
            raise CutoverBlocked(f"launchctl is unavailable: {self._launchctl}")
        candidates = [
            database
            for database in manifest.databases
            if self._has_intake_gate(database)
        ]
        if len(candidates) != 1:
            raise CutoverBlocked(
                "production host adapter requires exactly one database with "
                f"a durable intake gate; found {len(candidates)}"
            )
        source_database = candidates[0]
        connection = sqlite3.connect(source_database)
        try:
            state = IntakeGate(connection).snapshot()
        finally:
            connection.close()
        if state.state == "closed" and state.operation_id != operation_id:
            raise CutoverBlocked(
                "durable intake gate is already closed by "
                f"operation_id={state.operation_id or 'unknown'}"
            )
        self._source_database = source_database

    def close_intake(
        self,
        *,
        operation_id: str,
        reason: str,
        before_close: Callable[[Path, dict[str, object]], None],
    ) -> None:
        gate, connection = self._open_source_gate()
        try:
            def record_state(state) -> None:
                closed_at, reopened_at = connection.execute(
                    "SELECT closed_at, reopened_at FROM intake_gate WHERE singleton = 1"
                ).fetchone()
                before_close(
                    self.intake_database,
                    {
                        "state": state.state,
                        "operation_id": state.operation_id,
                        "reason": state.reason,
                        "reopen_policy": state.reopen_policy,
                        "closed_at": closed_at,
                        "reopened_at": reopened_at,
                    },
                )

            gate.close(
                operation_id=operation_id,
                reason=reason,
                reopen_policy="external",
                before_close=record_state,
            )
        except CutoverBlocked:
            raise
        except Exception as exc:
            raise CutoverBlocked(f"failed to close intake: {exc}") from exc
        finally:
            connection.close()

    def wait_for_intake_drain(self) -> None:
        gate, connection = self._open_source_gate()
        try:
            gate.wait_for_drain(timeout_seconds=30.0)
        except IntakeGateError as exc:
            raise CutoverBlocked(str(exc)) from exc
        finally:
            connection.close()

    def stop_launch_agent(self, agent: LaunchAgent) -> None:
        service = f"gui/{os.getuid()}/{agent.label}"
        if not self._service_loaded(service):
            return
        result = self._launchctl_result("bootout", service)
        if result.returncode != 0 and self._service_loaded(service):
            self._raise_launchctl_error(("bootout", service), result)

    def bootstrap_launch_agent(self, *, label: str, plist: Path) -> None:
        service = f"gui/{os.getuid()}/{label}"
        if self._service_loaded(service):
            return
        arguments = ("bootstrap", f"gui/{os.getuid()}", str(plist))
        result = self._launchctl_result(*arguments)
        if result.returncode != 0 and not self._service_loaded(service):
            self._raise_launchctl_error(arguments, result)

    def start_runtime(self, new_root: Path) -> dict:
        deadline = time.monotonic() + self._health_timeout_seconds
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                with urlopen(self._health_url, timeout=1.0) as response:
                    if response.status == 200:
                        return {
                            "health": "ok",
                            "root": str(new_root),
                            "url": self._health_url,
                        }
            except Exception as exc:  # noqa: BLE001 - bounded readiness probe
                last_error = exc
            time.sleep(0.1)
        raise CutoverBlocked(
            f"new runtime did not become healthy at {self._health_url}: {last_error}"
        )

    def reopen_intake(self, *, database: Path, operation_id: str) -> None:
        connection = sqlite3.connect(database)
        try:
            IntakeGate(connection).reopen(operation_id=operation_id)
        except IntakeGateError as exc:
            raise CutoverBlocked(str(exc)) from exc
        finally:
            connection.close()

    def _open_source_gate(self) -> tuple[IntakeGate, sqlite3.Connection]:
        if self._source_database is None:
            raise CutoverBlocked("production host adapter was not validated")
        connection = sqlite3.connect(self._source_database)
        return IntakeGate(connection), connection

    def _service_loaded(self, service: str) -> bool:
        return self._launchctl_result("print", service).returncode == 0

    def _launchctl_result(self, *arguments: str):
        return self._run(
            [str(self._launchctl), *arguments],
            capture_output=True,
            check=False,
            text=True,
        )

    @staticmethod
    def _raise_launchctl_error(arguments: tuple[str, ...], result) -> None:
        detail = str(result.stderr or result.stdout).strip()
        raise CutoverBlocked(
            f"launchctl {' '.join(arguments)} failed: {detail or result.returncode}"
        )

    @staticmethod
    def _has_intake_gate(database: Path) -> bool:
        if not database.is_file():
            return False
        uri = f"{database.resolve().as_uri()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        try:
            row = connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND name = 'intake_gate'
                """
            ).fetchone()
            if row is None:
                return False
            return connection.execute(
                "SELECT COUNT(*) FROM intake_gate WHERE singleton = 1"
            ).fetchone()[0] == 1
        finally:
            connection.close()


__all__ = ["SystemCutoverHostAdapter"]
