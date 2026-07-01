"""Minimal Jarvis daemon application wiring."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jarvis.brain.context_builder import ContextBuilder
from jarvis.brain.manager import BrainManager
from jarvis.config import JarvisConfig, load_config
from jarvis.events.bus import EventBus
from jarvis.events.models import Event, utc_now_iso
from jarvis.events.types import EventType
from jarvis.paths import RuntimePaths, ensure_runtime_dirs, resolve_runtime_paths
from jarvis.runtime.supervisor import RuntimeSupervisor
from jarvis.store.db import (
    close_quietly,
    connect_db,
    get_schema_version,
    initialize_database,
)
from jarvis.store.event_store import EventStore, create_event_store
from jarvis.daemon.state_machine import RuntimeState, RuntimeStateMachine
from jarvis.turns.orchestrator import TextTurnResult, TurnOrchestrator


class DaemonAppError(Exception):
    """Raised when the daemon app cannot be created or used safely."""


class DaemonAppNotStartedError(DaemonAppError):
    """Raised when a request needs a started daemon app."""


class DaemonAppBusyError(DaemonAppError):
    """Raised when a serialized app operation is already running."""


@dataclass
class DaemonApp:
    config: JarvisConfig
    paths: RuntimePaths
    conn: sqlite3.Connection | None
    event_store: EventStore | None
    event_bus: EventBus
    state_machine: RuntimeStateMachine | None
    runtime_supervisor: RuntimeSupervisor
    started: bool = False
    brain_manager: BrainManager | None = None
    context_builder: ContextBuilder | None = None
    text_turn_lock: Any = field(default_factory=threading.Lock)

    def start(self) -> None:
        """Start app-level state without running the long-lived HTTP loop."""

        if self.started:
            return
        event_store = self._require_event_store()
        state_machine = self._require_state_machine()

        event_store.append(EventType.DAEMON_STARTED, "daemon", {"service": "jarvisd"})
        state_machine.transition(RuntimeState.IDLE, reason="daemon started")
        self.started = True

    def stop(self, reason: str | None = None) -> None:
        event_store = self._require_event_store()
        state_machine = self._require_state_machine()

        if self.started:
            event_store.append(
                EventType.DAEMON_STOPPED,
                "daemon",
                {"service": "jarvisd", "reason": reason},
            )
        if state_machine.state is not RuntimeState.STOPPING:
            state_machine.transition(RuntimeState.STOPPING, reason=reason or "daemon stopped")
        self.started = False

    def snapshot_state(self) -> dict[str, Any]:
        state = self.state_machine.state.value if self.state_machine is not None else RuntimeState.BOOTING.value
        schema_version, latest_event_id = self._db_snapshot()
        return {
            "service": "jarvisd",
            "ok": self.conn is not None and state != RuntimeState.ERROR.value,
            "started": self.started,
            "state": state,
            "schema_version": schema_version,
            "latest_event_id": latest_event_id,
            "host": self.config.daemon.host,
            "port": self.config.daemon.port,
            "voice_enabled": self.config.voice.enabled,
            "brain_adapter": self.config.brain.default_adapter,
            "launchd_label": self.config.launchd.label,
        }

    def allowed_state_targets(self) -> list[str]:
        state_machine = self._require_state_machine()
        return sorted(state.value for state in state_machine.allowed_targets())

    def list_events_after(self, after_id: int, limit: int) -> list[Event]:
        conn = self._connect_existing()
        try:
            return create_event_store(conn).list_after(after_id, limit=limit)
        finally:
            close_quietly(conn)

    def get_settings(self) -> dict[str, Any]:
        conn = self._connect_existing()
        try:
            rows = conn.execute("SELECT key, value_json FROM settings ORDER BY key").fetchall()
            settings: dict[str, Any] = {}
            for key, value_json in rows:
                settings[str(key)] = json.loads(str(value_json))
            return settings
        finally:
            close_quietly(conn)

    def update_settings(self, updates: Mapping[str, Any]) -> dict[str, Any]:
        conn = self._connect_existing()
        now = utc_now_iso()
        try:
            with conn:
                for key, value in updates.items():
                    if not isinstance(key, str) or not key.strip():
                        raise DaemonAppError("Setting keys must be non-empty strings.")
                    value_json = json.dumps(value, ensure_ascii=False, sort_keys=True)
                    conn.execute(
                        """
                        INSERT INTO settings (key, value_json, updated_at, source)
                        VALUES (?, ?, ?, 'api')
                        ON CONFLICT(key) DO UPDATE SET
                          value_json = excluded.value_json,
                          updated_at = excluded.updated_at,
                          source = 'api'
                        """,
                        (key, value_json, now),
                    )
            rows = conn.execute("SELECT key, value_json FROM settings ORDER BY key").fetchall()
            return {str(key): json.loads(str(value_json)) for key, value_json in rows}
        finally:
            close_quietly(conn)

    def handle_text_input(
        self,
        *,
        text: str,
        conversation_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        source: str = "api",
    ) -> TextTurnResult:
        if not self.started:
            raise DaemonAppNotStartedError("Daemon app is not started.")

        if not self.text_turn_lock.acquire(blocking=False):
            raise DaemonAppBusyError("Another text turn is already running.")

        try:
            orchestrator = self._create_turn_orchestrator()
            return orchestrator.handle_text(
                text=text,
                conversation_id=conversation_id,
                metadata=metadata,
                source=source,
            )
        finally:
            self.text_turn_lock.release()

    def close(self) -> None:
        close_quietly(self.conn)
        self.conn = None
        self.event_store = None
        self.state_machine = None
        self.brain_manager = None
        self.context_builder = None
        self.started = False

    def _require_conn(self) -> sqlite3.Connection:
        if self.conn is None:
            raise DaemonAppError("Daemon app is not initialized with a database connection.")
        return self.conn

    def _require_event_store(self) -> EventStore:
        if self.event_store is None:
            raise DaemonAppError("Daemon app is not initialized with an event store.")
        return self.event_store

    def _require_state_machine(self) -> RuntimeStateMachine:
        if self.state_machine is None:
            raise DaemonAppError("Daemon app is not initialized with a state machine.")
        return self.state_machine

    def _require_brain_manager(self) -> BrainManager:
        if self.brain_manager is None:
            raise DaemonAppError("Daemon app is not initialized with a brain manager.")
        return self.brain_manager

    def _require_context_builder(self) -> ContextBuilder:
        if self.context_builder is None:
            raise DaemonAppError("Daemon app is not initialized with a context builder.")
        return self.context_builder

    def _create_turn_orchestrator(self) -> TurnOrchestrator:
        return TurnOrchestrator(
            conn=self._require_conn(),
            event_store=self._require_event_store(),
            event_bus=self.event_bus,
            state_machine=self._require_state_machine(),
            brain_manager=self._require_brain_manager(),
            context_builder=self._require_context_builder(),
        )

    def _connect_existing(self) -> sqlite3.Connection:
        if not self.paths.db_path.is_file():
            raise DaemonAppError(f"Database does not exist: {self.paths.db_path}")
        return connect_db(self.paths.db_path)

    def _db_snapshot(self) -> tuple[int, int]:
        if not self.paths.db_path.is_file():
            return 0, 0
        conn = connect_db(self.paths.db_path)
        try:
            schema_version = get_schema_version(conn)
            row = conn.execute("SELECT MAX(id) FROM events").fetchone()
            latest_event_id = 0 if row is None or row[0] is None else int(row[0])
            return schema_version, latest_event_id
        finally:
            close_quietly(conn)


JarvisDaemonApp = DaemonApp
JarvisDaemon = DaemonApp


def create_daemon_app(
    config_path: str | Path | None = None, *, initialize: bool = True
) -> DaemonApp:
    config = load_config(config_path)
    return create_daemon_app_from_config(config, initialize=initialize)


def create_daemon_app_from_config(config: JarvisConfig, *, initialize: bool = True) -> DaemonApp:
    paths = resolve_runtime_paths(config)
    event_bus = EventBus()
    runtime_supervisor = RuntimeSupervisor(home=paths.home)

    if not initialize:
        return DaemonApp(
            config=config,
            paths=paths,
            conn=None,
            event_store=None,
            event_bus=event_bus,
            state_machine=None,
            runtime_supervisor=runtime_supervisor,
        )

    ensure_runtime_dirs(paths)
    initialized_conn = initialize_database(paths.db_path)
    close_quietly(initialized_conn)
    conn = _connect_daemon_db(paths.db_path)
    event_store = create_event_store(conn)
    state_machine = RuntimeStateMachine(event_store, event_bus=event_bus)
    brain_manager = BrainManager.from_config(config)
    context_builder = ContextBuilder(conn, config=config, event_store=event_store)
    return DaemonApp(
        config=config,
        paths=paths,
        conn=conn,
        event_store=event_store,
        event_bus=event_bus,
        state_machine=state_machine,
        runtime_supervisor=runtime_supervisor,
        brain_manager=brain_manager,
        context_builder=context_builder,
    )


def _connect_daemon_db(path: Path) -> sqlite3.Connection:
    """Open the daemon-owned connection for the threaded local HTTP server."""

    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


__all__ = [
    "DaemonApp",
    "DaemonAppBusyError",
    "DaemonAppError",
    "DaemonAppNotStartedError",
    "JarvisDaemon",
    "JarvisDaemonApp",
    "create_daemon_app",
    "create_daemon_app_from_config",
]
