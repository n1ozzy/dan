"""Minimal Jarvis daemon application wiring."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jarvis.brain.context_builder import ContextBuilder
from jarvis.brain.manager import BrainManager, BrainManagerError
from jarvis.config import JarvisConfig, load_config
from jarvis.events.bus import EventBus
from jarvis.events.models import Event, utc_now_iso
from jarvis.events.types import EventType
from jarvis.logging import get_logger
from jarvis.memory import MemoryBlock, MemoryError, MemoryManager
from jarvis.paths import RuntimePaths, ensure_runtime_dirs, resolve_runtime_paths
from jarvis.runtime.supervisor import RuntimeSupervisor
from jarvis.security.transport import ensure_api_token
from jarvis.store.db import (
    close_quietly,
    connect_db,
    get_schema_version,
    initialize_database,
)
from jarvis.store.event_store import EventStore, create_event_store
from jarvis.tools import (
    ApprovalGate,
    RequestSource,
    ToolDecision,
    ToolPermissionPolicy,
    ToolRegistry,
    ToolRequest,
    ToolResult,
    ToolRunRecorder,
    ToolSpec,
    create_default_tool_registry,
)
from jarvis.macos.accessibility import create_actor, create_reader
from jarvis.macos.screen import create_screen_reader
from jarvis.macos.terminal import create_terminal_bridge
from jarvis.tools.file_tool import FileReadTool, FileWriteTool
from jarvis.tools.registry import ApprovalProbeTool
from jarvis.tools.screen_tool import ScreenOcrRegionTool, ScreenReadWindowTool
from jarvis.tools.terminal_tool import TerminalPasteTool, TerminalReadScreenTool
from jarvis.tools.shell_tool import ShellReadTool
from jarvis.tools.ui_tool import (
    UiActiveAppTool,
    UiClickTool,
    UiFocusAppTool,
    UiReadWindowTool,
    UiTypeTool,
)
from jarvis.daemon.state_machine import RuntimeState, RuntimeStateMachine
from jarvis.turns.orchestrator import TextTurnResult, TurnOrchestrator
from jarvis.turns.models import Turn
from jarvis.turns.repository import ConversationRepository, TurnRepository
from jarvis.workers import (
    MockWorker,
    UnknownWorkerKindError,
    WorkerBroker,
    WorkerBrokerError,
)


# The persisted brain choice lives in the daemon-owned settings table, not in
# process memory: jarvisd owns truth, so a restart restores the last switch.
BRAIN_ADAPTER_SETTING_KEY = "brain.current_adapter"


class DaemonAppError(Exception):
    """Raised when the daemon app cannot be created or used safely."""


class DaemonAppNotStartedError(DaemonAppError):
    """Raised when a request needs a started daemon app."""


class DaemonAppNotFoundError(DaemonAppError):
    """Raised when a requested daemon resource does not exist."""


class DaemonAppConflictError(DaemonAppError):
    """Raised when a request conflicts with current durable state."""


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
    tool_registry: ToolRegistry
    tool_permission_policy: ToolPermissionPolicy
    approval_gate: ApprovalGate | None
    tool_run_recorder: ToolRunRecorder | None
    started: bool = False
    brain_manager: BrainManager | None = None
    context_builder: ContextBuilder | None = None
    memory_manager: MemoryManager | None = None
    worker_broker: WorkerBroker | None = None
    api_token: str | None = None
    text_turn_lock: Any = field(default_factory=threading.Lock)
    tool_execution_lock: Any = field(default_factory=threading.Lock)

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
            "brain_adapter": (
                self.brain_manager.current_adapter_name
                if self.brain_manager is not None
                else self.config.brain.default_adapter
            ),
            "launchd_label": self.config.launchd.label,
            "pending_approval_count": self._pending_approval_count(),
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

    def list_conversations(
        self,
        limit: int = 50,
        include_archived: bool = False,
    ) -> list[dict[str, Any]]:
        if not self.started:
            raise DaemonAppNotStartedError("Daemon app is not started.")

        conn = self._connect_existing()
        try:
            return ConversationRepository(conn).list_recent_with_stats(
                limit=limit,
                include_archived=include_archived,
            )
        finally:
            close_quietly(conn)

    def list_turns(
        self,
        conversation_id: str,
        limit: int = 50,
        newest_first: bool = False,
    ) -> list[Turn]:
        if not self.started:
            raise DaemonAppNotStartedError("Daemon app is not started.")

        conn = self._connect_existing()
        try:
            return TurnRepository(conn).list_for_conversation(
                conversation_id,
                limit=limit,
                newest_first=newest_first,
            )
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

    def list_brain_adapters(self) -> list[dict[str, Any]]:
        if not self.started:
            raise DaemonAppNotStartedError("Daemon app is not started.")
        manager = self._require_brain_manager()
        adapters: list[dict[str, Any]] = []
        for name in manager.adapter_names():
            adapter = manager.get_adapter(name)
            models = getattr(adapter, "available_models", None)
            adapters.append(
                {
                    "name": name,
                    "models": list(models()) if callable(models) else [],
                    "current": name == manager.current_adapter_name,
                }
            )
        return adapters

    def current_brain_adapter(self) -> str:
        if not self.started:
            raise DaemonAppNotStartedError("Daemon app is not started.")
        return self._require_brain_manager().current_adapter_name

    def switch_brain(self, adapter_name: str) -> dict[str, Any]:
        """Switch the active brain adapter and persist the choice.

        The switch only ever changes which stateless adapter answers the next
        turn; conversation history lives in SQLite and is untouched. Persisting
        happens before the in-memory switch so the settings table (jarvisd's
        truth) can never lag behind a switch that already took effect.
        """

        if not self.started:
            raise DaemonAppNotStartedError("Daemon app is not started.")
        name = _required_text(adapter_name, "adapter")
        manager = self._require_brain_manager()
        previous = manager.current_adapter_name
        try:
            manager.get_adapter(name)
        except BrainManagerError as exc:
            raise DaemonAppNotFoundError(str(exc)) from exc

        self.update_settings({BRAIN_ADAPTER_SETTING_KEY: name})
        manager.switch_adapter(name)
        changed = previous != name
        if changed:
            self._require_event_store().append(
                EventType.BRAIN_SWITCHED,
                "api",
                {"from": previous, "to": name, "persisted": True},
            )
        return {
            "ok": True,
            "adapter": name,
            "previous": previous,
            "changed": changed,
        }

    def list_memory(
        self,
        *,
        active_only: bool = False,
        kinds: list[str] | tuple[str, ...] | None = None,
        limit: int = 100,
    ) -> list[MemoryBlock]:
        if not self.started:
            raise DaemonAppNotStartedError("Daemon app is not started.")
        return self._require_memory_manager().list_blocks(
            active_only=active_only,
            kinds=kinds,
            limit=limit,
        )

    def create_memory(
        self,
        *,
        kind: str,
        title: str,
        body: str,
        priority: int = 0,
        active: bool = True,
        metadata: Mapping[str, Any] | None = None,
    ) -> MemoryBlock:
        if not self.started:
            raise DaemonAppNotStartedError("Daemon app is not started.")
        return self._require_memory_manager().create_block(
            kind,
            title,
            body,
            priority=priority,
            active=active,
            metadata=metadata,
        )

    def get_memory(self, memory_id: str) -> MemoryBlock:
        if not self.started:
            raise DaemonAppNotStartedError("Daemon app is not started.")
        normalized_id = _required_text(memory_id, "memory_id")
        block = self._require_memory_manager().get_block(normalized_id)
        if block is None:
            raise DaemonAppNotFoundError(f"Memory block not found: {normalized_id}")
        return block

    def update_memory(
        self,
        memory_id: str,
        *,
        title: str | None = None,
        body: str | None = None,
        priority: int | None = None,
        active: bool | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> MemoryBlock:
        if not self.started:
            raise DaemonAppNotStartedError("Daemon app is not started.")
        normalized_id = _required_text(memory_id, "memory_id")
        manager = self._require_memory_manager()
        if manager.get_block(normalized_id) is None:
            raise DaemonAppNotFoundError(f"Memory block not found: {normalized_id}")
        try:
            return manager.update_block(
                normalized_id,
                title=title,
                body=body,
                priority=priority,
                active=active,
                metadata=metadata,
            )
        except MemoryError as exc:
            raise DaemonAppError(str(exc)) from exc

    def disable_memory(self, memory_id: str) -> MemoryBlock:
        if not self.started:
            raise DaemonAppNotStartedError("Daemon app is not started.")
        normalized_id = _required_text(memory_id, "memory_id")
        manager = self._require_memory_manager()
        if manager.get_block(normalized_id) is None:
            raise DaemonAppNotFoundError(f"Memory block not found: {normalized_id}")
        try:
            return manager.disable_block(normalized_id)
        except MemoryError as exc:
            raise DaemonAppError(str(exc)) from exc

    def create_worker_job(
        self,
        *,
        worker_kind: str,
        prompt: str,
        requested_by: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Enqueue a worker job and run it in a background thread.

        The response is the queued job; callers observe progress via
        GET /workers/jobs/<id> and the worker.job.* event stream.
        """

        if not self.started:
            raise DaemonAppNotStartedError("Daemon app is not started.")
        broker = self._require_worker_broker()
        try:
            job = broker.enqueue(
                worker_kind=worker_kind,
                prompt=prompt,
                requested_by=requested_by,
                metadata=metadata,
            )
        except UnknownWorkerKindError as exc:
            raise DaemonAppNotFoundError(str(exc)) from exc

        def _run() -> None:
            try:
                broker.execute(job.id)
            except WorkerBrokerError:
                # Worker failures are already persisted by execute(); this
                # only catches broker-level races (e.g. job cancelled between
                # enqueue and thread start). The job row stays authoritative.
                get_logger(__name__).exception("Worker job execution failed: %s", job.id)

        threading.Thread(
            target=_run, name=f"jarvis-worker-{job.id[:8]}", daemon=True
        ).start()
        return job.to_dict()

    def list_worker_jobs(
        self, *, limit: int = 50, status: str | None = None
    ) -> list[dict[str, Any]]:
        if not self.started:
            raise DaemonAppNotStartedError("Daemon app is not started.")
        jobs = self._require_worker_broker().list_jobs(limit=limit, status=status)
        return [job.to_dict() for job in jobs]

    def get_worker_job(self, job_id: str) -> dict[str, Any]:
        if not self.started:
            raise DaemonAppNotStartedError("Daemon app is not started.")
        normalized_id = _required_text(job_id, "job_id")
        job = self._require_worker_broker().get_job(normalized_id)
        if job is None:
            raise DaemonAppNotFoundError(f"Unknown worker job: {normalized_id}")
        return job.to_dict()

    def list_tool_specs(self) -> list[ToolSpec]:
        if not self.started:
            raise DaemonAppNotStartedError("Daemon app is not started.")
        return self.tool_registry.list_specs()

    def request_tool(
        self,
        *,
        tool_name: str,
        arguments: Mapping[str, Any],
        requested_by: str,
        source: RequestSource | str,
        turn_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> ToolResult:
        if not self.started:
            raise DaemonAppNotStartedError("Daemon app is not started.")

        request = ToolRequest(
            id=str(uuid.uuid4()),
            tool_name=tool_name,
            arguments=dict(arguments),
            requested_by=requested_by,
            turn_id=turn_id,
            metadata=dict(metadata or {}),
        )
        tool = self.tool_registry.get(tool_name)
        permission = self.tool_permission_policy.decide(
            tool.risk,
            source=source,
            tool_name=tool.name,
            payload=request.arguments,
        )

        if permission.decision == "allow":
            recorder = self._require_tool_run_recorder()
            recorder.record_requested(
                run_id=request.id,
                tool_name=tool.name,
                risk=tool.risk,
                input=request.arguments,
                turn_id=turn_id,
            )
            result = self.tool_registry.request_tool(
                request,
                permission_policy=self.tool_permission_policy,
                source=source,
                approval_gate=self.approval_gate,
            )
            if result.status == "finished":
                recorder.record_finished(request.id, output=result.output or {})
            elif result.status == "failed":
                recorder.record_failed(request.id, error=result.error or "Tool execution failed.")
            return result

        return self.tool_registry.request_tool(
            request,
            permission_policy=self.tool_permission_policy,
            source=source,
            approval_gate=self.approval_gate,
        )

    def list_pending_approvals(self, limit: int = 50) -> list[dict[str, Any]]:
        if not self.started:
            raise DaemonAppNotStartedError("Daemon app is not started.")
        return self._require_approval_gate().list_pending(limit=limit)

    def approve(self, approval_id: str, *, reason: str | None = None) -> dict[str, Any]:
        if not self.started:
            raise DaemonAppNotStartedError("Daemon app is not started.")
        return self._require_approval_gate().decide(approval_id, "approved", reason=reason)

    def reject(self, approval_id: str, *, reason: str | None = None) -> dict[str, Any]:
        if not self.started:
            raise DaemonAppNotStartedError("Daemon app is not started.")
        return self._require_approval_gate().decide(approval_id, "rejected", reason=reason)

    def execute_approved_tool(self, approval_id: str) -> dict[str, Any]:
        if not self.started:
            raise DaemonAppNotStartedError("Daemon app is not started.")

        with self.tool_execution_lock:
            return self._execute_approved_tool_locked(approval_id)

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
        self.memory_manager = None
        self.worker_broker = None
        self.approval_gate = None
        self.tool_run_recorder = None
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

    def _require_memory_manager(self) -> MemoryManager:
        if self.memory_manager is None:
            raise DaemonAppError("Daemon app is not initialized with a memory manager.")
        return self.memory_manager

    def _require_worker_broker(self) -> WorkerBroker:
        if self.worker_broker is None:
            raise DaemonAppError("Daemon app is not initialized with a worker broker.")
        return self.worker_broker

    def _require_approval_gate(self) -> ApprovalGate:
        if self.approval_gate is None:
            raise DaemonAppError("Daemon app is not initialized with an approval gate.")
        return self.approval_gate

    def _require_tool_run_recorder(self) -> ToolRunRecorder:
        if self.tool_run_recorder is None:
            raise DaemonAppError("Daemon app is not initialized with a tool run recorder.")
        return self.tool_run_recorder

    def _create_turn_orchestrator(self) -> TurnOrchestrator:
        return TurnOrchestrator(
            conn=self._require_conn(),
            event_store=self._require_event_store(),
            event_bus=self.event_bus,
            state_machine=self._require_state_machine(),
            brain_manager=self._require_brain_manager(),
            context_builder=self._require_context_builder(),
            tool_registry=self.tool_registry,
            approval_gate=self._require_approval_gate(),
            tool_permission_policy=self.tool_permission_policy,
        )

    def _execute_approved_tool_locked(self, approval_id: str) -> dict[str, Any]:
        normalized_approval_id = _required_text(approval_id, "approval_id")
        gate = self._require_approval_gate()
        recorder = self._require_tool_run_recorder()
        approval = gate.get_approval(normalized_approval_id)
        if approval is None:
            raise DaemonAppNotFoundError(f"Unknown approval: {normalized_approval_id}")
        if approval["status"] != "approved":
            raise DaemonAppConflictError(f"Approval is not approved: {normalized_approval_id}")

        existing_run = recorder.get_by_approval_id(normalized_approval_id)
        if existing_run is not None:
            raise DaemonAppConflictError(f"Approval already executed: {normalized_approval_id}")

        tool_request = _tool_request_from_approval(approval)
        request_source = _request_source_from_approval(approval)
        if request_source is None:
            return {
                "ok": False,
                "approval_id": normalized_approval_id,
                "status": "blocked",
                "error": "Approval payload has no valid request source; execution is blocked.",
            }
        tool = self.tool_registry.get(tool_request.tool_name)
        permission = self.tool_permission_policy.decide(
            tool.risk,
            source=request_source,
            tool_name=tool.name,
            payload=tool_request.arguments,
        )
        if permission.decision == ToolDecision.BLOCKED:
            return {
                "ok": False,
                "approval_id": normalized_approval_id,
                "status": "blocked",
                "error": permission.reason,
            }

        recorder.record_requested(
            run_id=tool_request.id,
            tool_name=tool.name,
            risk=tool.risk,
            input=tool_request.arguments,
            turn_id=tool_request.turn_id,
            approval_id=normalized_approval_id,
        )
        recorder.record_started(tool_request.id)
        result = self.tool_registry.execute_tool(
            tool_request,
            approval_id=normalized_approval_id,
        )
        if result.status == "finished":
            tool_run = recorder.record_finished(tool_request.id, output=result.output or {})
            response = {
                "ok": True,
                "approval_id": normalized_approval_id,
                "tool_run": tool_run,
                "result": result.output or {},
            }
            continuation = self._create_turn_orchestrator().continue_after_tool_result(
                approval_id=normalized_approval_id,
                tool_request=tool_request,
                tool_result=result,
                tool_run=tool_run,
            )
            if continuation is not None:
                response["continuation"] = continuation.to_dict()
            return response

        tool_run = recorder.record_failed(
            tool_request.id,
            error=result.error or "Tool execution failed.",
        )
        return {
            "ok": False,
            "approval_id": normalized_approval_id,
            "tool_run": tool_run,
            "result": {
                "status": "failed",
                "error": result.error or "Tool execution failed.",
            },
            "error": result.error or "Tool execution failed.",
        }

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

    def _pending_approval_count(self) -> int:
        if self.conn is not None:
            try:
                row = self.conn.execute(
                    "SELECT COUNT(*) FROM approvals WHERE status = 'pending'"
                ).fetchone()
                return 0 if row is None else int(row[0])
            except sqlite3.Error:
                return 0

        if not self.paths.db_path.is_file():
            return 0
        conn = connect_db(self.paths.db_path)
        try:
            row = conn.execute("SELECT COUNT(*) FROM approvals WHERE status = 'pending'").fetchone()
            return 0 if row is None else int(row[0])
        except sqlite3.Error:
            return 0
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
    # One containment source feeds both the policy and the file tool so the
    # advisory check and the execution-time re-check can never disagree.
    approved_roots = [str(root) for root in config.security.approved_roots] or [str(paths.home)]
    shell_read_whitelist = [str(cmd) for cmd in config.security.shell_read_whitelist] or None
    tool_registry = create_default_tool_registry()
    tool_registry.register(ApprovalProbeTool())
    tool_registry.register(FileReadTool(approved_roots=approved_roots))
    tool_registry.register(FileWriteTool(approved_roots=approved_roots))
    tool_registry.register(
        ShellReadTool(whitelist=shell_read_whitelist, approved_roots=approved_roots)
    )
    ui_reader = create_reader(config.security.ui_read_backend)
    tool_registry.register(UiActiveAppTool(ui_reader))
    tool_registry.register(UiReadWindowTool(ui_reader))
    ui_actor = create_actor(
        config.security.ui_act_backend or config.security.ui_read_backend
    )
    tool_registry.register(UiClickTool(ui_actor))
    tool_registry.register(UiTypeTool(ui_actor))
    tool_registry.register(UiFocusAppTool(ui_actor))
    # Captures are transient artifacts under the jarvisd-owned runtime dir;
    # the backend deletes them right after OCR (ADR-020).
    screen_reader = create_screen_reader(
        config.security.screen_read_backend,
        work_dir=paths.runtime_dir / "screen",
    )
    tool_registry.register(ScreenReadWindowTool(screen_reader))
    tool_registry.register(ScreenOcrRegionTool(screen_reader))
    # One bridge feeds both terminal tools, but read and paste keep their
    # own risk classes (terminal_read / terminal_write, ADR-021).
    terminal_bridge = create_terminal_bridge(config.security.terminal_backend)
    tool_registry.register(TerminalReadScreenTool(terminal_bridge))
    tool_registry.register(TerminalPasteTool(terminal_bridge))
    tool_permission_policy = ToolPermissionPolicy(
        destructive_tools_enabled=config.security.destructive_tools_enabled,
        approved_roots=approved_roots,
    )

    if not initialize:
        return DaemonApp(
            config=config,
            paths=paths,
            conn=None,
            event_store=None,
            event_bus=event_bus,
            state_machine=None,
            runtime_supervisor=runtime_supervisor,
            tool_registry=tool_registry,
            tool_permission_policy=tool_permission_policy,
            approval_gate=None,
            tool_run_recorder=None,
            memory_manager=None,
        )

    ensure_runtime_dirs(paths)
    api_token = ensure_api_token(paths.runtime_dir)
    initialized_conn = initialize_database(paths.db_path)
    close_quietly(initialized_conn)
    conn = _connect_daemon_db(paths.db_path)
    event_store = create_event_store(conn)
    state_machine = RuntimeStateMachine(event_store, event_bus=event_bus)
    brain_manager = BrainManager.from_config(config)
    _restore_persisted_brain_adapter(conn, brain_manager)
    memory_manager = MemoryManager(conn, event_store=event_store)
    context_builder = ContextBuilder(
        conn,
        config=config,
        event_store=event_store,
        memory_manager=memory_manager,
    )
    approval_gate = ApprovalGate(conn, event_store=event_store)
    tool_run_recorder = ToolRunRecorder(conn, event_store=event_store)
    # E2: the mock worker is the only registered worker; real provider
    # workers (codex/claude CLI) arrive in their own stage behind config.
    worker_broker = WorkerBroker(
        conn,
        event_store=event_store,
        memory_manager=memory_manager,
        workers=[MockWorker()],
        require_candidate_promotion=config.memory.worker_candidates_require_promotion,
    )
    return DaemonApp(
        config=config,
        paths=paths,
        conn=conn,
        event_store=event_store,
        event_bus=event_bus,
        state_machine=state_machine,
        runtime_supervisor=runtime_supervisor,
        tool_registry=tool_registry,
        tool_permission_policy=tool_permission_policy,
        approval_gate=approval_gate,
        tool_run_recorder=tool_run_recorder,
        brain_manager=brain_manager,
        context_builder=context_builder,
        memory_manager=memory_manager,
        worker_broker=worker_broker,
        api_token=api_token,
    )


def _restore_persisted_brain_adapter(
    conn: sqlite3.Connection, brain_manager: BrainManager
) -> None:
    """Restore the last persisted brain switch (settings table owns truth).

    A stale persisted choice (adapter no longer registered by config) must not
    brick the daemon: fall back to the config default and log the mismatch.
    """

    row = conn.execute(
        "SELECT value_json FROM settings WHERE key = ?",
        (BRAIN_ADAPTER_SETTING_KEY,),
    ).fetchone()
    if row is None:
        return
    try:
        persisted = json.loads(str(row[0]))
    except json.JSONDecodeError:
        persisted = None
    if not isinstance(persisted, str) or not persisted.strip():
        get_logger(__name__).warning(
            "Ignoring malformed persisted brain adapter setting %r", row[0]
        )
        return
    try:
        brain_manager.switch_adapter(persisted.strip())
    except BrainManagerError:
        get_logger(__name__).warning(
            "Persisted brain adapter %r is not registered; keeping default %r",
            persisted,
            brain_manager.current_adapter_name,
        )


def _connect_daemon_db(path: Path) -> sqlite3.Connection:
    """Open the daemon-owned connection for the threaded local HTTP server."""

    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def _request_source_from_approval(approval: Mapping[str, Any]) -> RequestSource | None:
    """Restore the original request source stored in the approval payload.

    Fail closed: approvals without a recognizable source never execute
    (docs/MACOS_PERMISSION_MODEL.md §1: unknown source => blocked).
    """

    payload = approval.get("payload")
    if not isinstance(payload, Mapping):
        return None
    raw_source = payload.get("source")
    if not isinstance(raw_source, str):
        return None
    try:
        return RequestSource(raw_source)
    except ValueError:
        return None


def _tool_request_from_approval(approval: Mapping[str, Any]) -> ToolRequest:
    payload = approval.get("payload")
    if not isinstance(payload, Mapping):
        raise DaemonAppError("Approval payload must be a JSON object.")

    raw_tool_name = payload.get("tool_name")
    if not isinstance(raw_tool_name, str) or not raw_tool_name.strip():
        raise DaemonAppError("Approval payload tool_name must be a non-empty string.")

    raw_arguments = payload.get("arguments")
    if not isinstance(raw_arguments, Mapping):
        raise DaemonAppError("Approval payload arguments must be a JSON object.")

    raw_requested_by = payload.get("requested_by")
    if not isinstance(raw_requested_by, str) or not raw_requested_by.strip():
        raise DaemonAppError("Approval payload requested_by must be a non-empty string.")

    raw_turn_id = payload.get("turn_id")
    turn_id: str | None
    if raw_turn_id is None:
        turn_id = None
    elif isinstance(raw_turn_id, str) and raw_turn_id.strip():
        turn_id = raw_turn_id.strip()
    else:
        raise DaemonAppError("Approval payload turn_id must be null or a non-empty string.")

    return ToolRequest(
        id=str(uuid.uuid4()),
        tool_name=raw_tool_name.strip(),
        arguments=dict(raw_arguments),
        requested_by=raw_requested_by.strip(),
        turn_id=turn_id,
        metadata={"approval_id": str(approval["id"])},
    )


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise DaemonAppError(f"{label} must be a string.")
    normalized = value.strip()
    if not normalized:
        raise DaemonAppError(f"{label} must be a non-empty string.")
    return normalized


__all__ = [
    "BRAIN_ADAPTER_SETTING_KEY",
    "DaemonApp",
    "DaemonAppBusyError",
    "DaemonAppConflictError",
    "DaemonAppError",
    "DaemonAppNotFoundError",
    "DaemonAppNotStartedError",
    "JarvisDaemon",
    "JarvisDaemonApp",
    "create_daemon_app",
    "create_daemon_app_from_config",
]
