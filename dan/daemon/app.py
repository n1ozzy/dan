"""Minimal DAN daemon application wiring."""

from __future__ import annotations

import os
import json
import sqlite3
import threading
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dan.brain.base import BrainResponse
from dan.brain.context_builder import ContextBuilder
from dan.brain.manager import BrainManager, BrainManagerError
from dan.config import DANConfig, compiled_memory_operator_env_controls, load_config
from dan.config_registry import ConfigStore, validate_setting_updates
from dan.events.bus import EventBus
from dan.events.models import Event, utc_now_iso
from dan.events.types import EventType
from dan.logging import get_logger
from dan.memory import (
    CompiledMemoryContext,
    MemoryBlock,
    MemoryCandidate,
    MemoryCandidateConflict,
    MemoryCandidateError,
    MemoryCandidateNotFound,
    MemoryCandidateRepository,
    MemoryEvidence,
    MemoryEvidenceConflict,
    MemoryEvidenceError,
    MemoryEvidenceNotFound,
    MemoryEvidenceRepository,
    MemoryCompiler,
    MemoryCompilerConfig,
    MemoryCompilerRequest,
    MemoryItem,
    MemoryItemConflict,
    MemoryItemError,
    MemoryItemNotFound,
    MemoryItemRepository,
    MemoryError,
    MemoryManager,
)
from dan.memory.archive import MemoryArchive, memory_recall_to_dict
from dan.paths import RuntimePaths, ensure_runtime_dirs, resolve_runtime_paths
from dan.runtime.supervisor import RuntimeSupervisor
from dan.security.redaction import redact_secrets
from dan.security.transport import ensure_api_token
from dan.store.db import (
    ThreadLocalConnection,
    close_quietly,
    connect_db,
    DatabaseError,
    get_schema_version,
    initialize_database,
)
from dan.store.event_store import EventStore, create_event_store
from dan.tools import (
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
from dan.macos.accessibility import create_actor, create_reader
from dan.macos.screen import create_screen_reader
from dan.macos.terminal import create_terminal_bridge
from dan.tools.file_tool import FileReadTool, FileWriteTool
from dan.tools.memory_tool import MemorySaveTool
from dan.tools.memory_recall_tool import MemoryRecallTool
from dan.tools.registry import ToolRegistryError
from dan.tools.screen_tool import ScreenOcrRegionTool, ScreenReadWindowTool
from dan.tools.terminal_tool import TerminalPasteTool, TerminalReadScreenTool
from dan.tools.shell_tool import ShellReadTool
from dan.tools.web_tool import WebFetchTool
from dan.tools.ui_tool import (
    UiActiveAppTool,
    UiClickTool,
    UiFocusAppTool,
    UiReadWindowTool,
    UiTypeTool,
)
from dan.daemon.state_machine import RuntimeState, RuntimeStateMachine
from dan.daemon.intake import IntakeGate
from dan.turns.orchestrator import TextTurnResult, TurnOrchestrator
from dan.turns.models import Turn
from dan.turns.repository import ConversationRepository, TurnRepository
from dan.workers import (
    MockWorker,
    UnknownWorkerKindError,
    WorkerBroker,
    WorkerBrokerError,
)


# The persisted brain choice lives in the daemon-owned settings table, not in
# process memory: dand owns truth, so a restart restores the last switch.
BRAIN_ADAPTER_SETTING_KEY = "brain.current_adapter"
INTAKE_DRAIN_TIMEOUT_SECONDS = 30.0


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


class DaemonLifecycleError(DaemonAppError):
    """Raised when daemon owner shutdown cannot be proven complete."""


def _build_generation_registry() -> Any:
    from dan.voice.cancellation import GenerationRegistry

    return GenerationRegistry()


@dataclass
class DaemonApp:
    config: DANConfig
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
    memory_candidate_repository: MemoryCandidateRepository | None = None
    memory_evidence_repository: MemoryEvidenceRepository | None = None
    memory_item_repository: MemoryItemRepository | None = None
    memory_archive: MemoryArchive | None = None
    worker_broker: WorkerBroker | None = None
    voice_resolver: Any = None
    voice_service: Any = None
    voice_engine: Any = None
    voice_player: Any = None
    voice_recorder: Any = None
    voice_broker: Any = None
    voice_stt: Any = None
    voice_gateway: Any = None
    voice_cancellation: Any = None
    voice_lease_sweeper: Any = None
    # Voice catalog directory override (personas.toml + pronunciations.toml).
    # None means the repo's config/voice; tests point it at a tmp dir so the
    # persona endpoints never touch the real catalog.
    voice_catalog_dir: Any = None
    intake_gate: IntakeGate | None = None
    # Daemon-owned global PTT hotkey (Task 9): the ONE event tap on the
    # machine lives here, guarded by a file lock in the runtime dir. The
    # factory is injectable so tests drive a fake tap; None means "build the
    # real Quartz monitor when allowed".
    hotkey_monitor: Any = None
    hotkey_monitor_factory: Any = None
    # Visible health blocker (missing Accessibility, lock conflict, bad spec);
    # the rest of dand stays healthy.
    hotkey_error: str | None = None
    # Injectable timer for the PTT activation grace (accidental-brush guard).
    ptt_timer_factory: Any = None
    # Supervised children (Task 9): only dand starts `supertonic serve`.
    child_supervisor: Any = None
    # POST /runtime/restart drains through this coordinator (injectable).
    restart_coordinator: Any = None
    # Daemon-lifetime (not voice-lifetime): streaming adapters register kill
    # handles here (G4d), the cancellation coordinator fires them (leg 1).
    voice_generation_registry: Any = field(default_factory=_build_generation_registry)
    # Rolling voice conversation: consecutive spoken utterances continue the
    # same conversation instead of each minting a fresh session (so DAN
    # remembers the previous turn). None until the first voice turn creates one.
    _voice_conversation_id: str | None = None
    api_token: str | None = None
    _session_tokens_in: int = 0
    _session_tokens_out: int = 0
    _tokens_lock: Any = field(default_factory=threading.Lock)
    text_turn_lock: Any = field(default_factory=threading.Lock)
    tool_execution_lock: Any = field(default_factory=threading.Lock)
    # Worker job threads, tracked so stop() can drain them before the
    # daemon.stopped event (FIX-03 DoD).
    worker_threads: list[threading.Thread] = field(default_factory=list)
    _worker_threads_lock: Any = field(default_factory=threading.Lock)
    _lifecycle_lock: Any = field(default_factory=threading.Lock)
    _voice_owner_blocked: bool = False
    _last_child_containment: Any = None

    def reload_voice_catalog(self) -> None:
        """Rebuild the resolver from the current catalog files and hot-swap it.

        In-process replacement instead of a launchd restart: queued/playing
        audio keeps its already-resolved snapshots, only new submits see the
        new catalog. Raises when the rebuilt catalog is invalid, leaving the
        previous resolver in place (fail-closed).
        """

        from dan.voice.service import build_voice_resolver

        resolver = build_voice_resolver(
            self.config,
            voice_root=self.voice_catalog_dir,
        )
        # Hand the resolver to the service first: if that rejects it, the app
        # keeps the previous one instead of caching a resolver nothing uses.
        service = self.voice_service
        if service is not None:
            service.replace_resolver(resolver)
        self.voice_resolver = resolver

    def start(self) -> None:
        """Start app-level state without running the long-lived HTTP loop."""

        if self._voice_owner_blocked:
            raise DaemonLifecycleError(
                "previous shutdown did not release the voice owner"
            )
        if self.started:
            return

        with self._lifecycle_lock:
            if self._voice_owner_blocked:
                raise DaemonLifecycleError(
                    "previous shutdown did not release the voice owner"
                )
            if self.started:
                return
            if self.brain_manager is not None:
                self.brain_manager.start()
            event_store = self._require_event_store()
            state_machine = self._require_state_machine()

            on_capture = None
            voice_stt = None
            voice_gateway = None
            voice_cancellation = None
            voice_recorder = None
            voice_broker = None
            voice_service = None
            voice_engine = None
            voice_player = None
            voice_lease_sweeper = None

            # Build every dependency first; if startup fails, tear down any
            # partially-built pieces instead of leaving hot processes alive.
            try:
                from dan.audio.devices import AudioDeviceManager
                from dan.voice.recorder import build_recorder
                from dan.voice.listening import ListeningLeaseSweeper

                AudioDeviceManager(self._require_conn(), config=self.config.audio)

                # STT pipeline first (the recorder needs its capture sink). Building
                # the engine validates the name, so an unknown or unavailable STT
                # engine kills the daemon at startup (established rule). Transcripts
                # end as `input.voice.transcribed` events and flow to the gateway:
                # anti-echo gate -> mic-side barge-in -> the same TurnOrchestrator
                # as panel text (ADR-011). The gate sits BEFORE turn creation, so an
                # echo of DAN's own TTS can never become a turn by
                # construction.
                if self.config.voice.enabled:
                    from dan.voice.anti_echo import AntiEchoGate
                    from dan.voice.broker import VoiceBroker
                    from dan.voice.cancellation import CancellationCoordinator
                    from dan.voice.gateway import VoiceTurnGateway
                    from dan.voice.player import CoreAudioPlayer, MockAudioPlayer
                    from dan.voice.queue import VoiceQueue
                    from dan.voice.service import VoiceService, build_voice_resolver
                    from dan.voice.stt import build_stt_engine
                    from dan.voice.transcription import TranscriptionPipeline
                    from dan.voice.tts import build_tts_engine
                    from dan.turns.orchestrator import (
                        TurnCancelledError,
                        TurnOrchestratorBusyError,
                    )

                    # Supervised Supertonic serve (Task 9): only dand spawns
                    # it, through the ChildSupervisor, BEFORE the engine is
                    # built — the engine then reuses the warm server and its
                    # own serve-autostart stays off (one owner, one child).
                    if self.child_supervisor is None:
                        from dan.daemon.supervisor import ChildSupervisor

                        # The ledger path is what lets the NEXT dand recognise
                        # a child this one leaves behind; without it the
                        # supervisor can only refuse a port it already owns.
                        self.child_supervisor = ChildSupervisor(
                            ledger_path=(
                                self.paths.runtime_dir / "supervised-children.json"
                            )
                        )
                    supervised_serve = self._ensure_supertonic_serve_child()
                    voice_engine = build_tts_engine(
                        self.config.voice.default_tts,
                        config=self.config,
                        serve_autostart=False if supervised_serve else None,
                    )
                    voice_player = (
                        MockAudioPlayer()
                        if self.config.voice.default_tts == "mock"
                        else CoreAudioPlayer()
                    )
                    if self.voice_resolver is None:
                        self.voice_resolver = build_voice_resolver(
                            self.config,
                            voice_root=self.voice_catalog_dir,
                        )
                    voice_service = VoiceService(
                        VoiceQueue(self._require_conn(), event_store=event_store),
                        self.voice_resolver,
                        intake_gate=self._require_intake_gate(),
                    )
                    voice_broker = VoiceBroker(
                        self._connect_existing,
                        engine=voice_engine,
                        player=voice_player,
                    )

                    # The registry itself is daemon-lifetime (streaming adapters hold
                    # a reference from create_daemon_app); voice only wires the
                    # coordinator that fires it.
                    voice_cancellation = CancellationCoordinator(
                        self._connect_existing,
                        generation_registry=self.voice_generation_registry,
                        playback_owner=voice_broker,
                    )
                    voice_gateway = VoiceTurnGateway(
                        anti_echo=AntiEchoGate(self._connect_existing, config=self.config.voice),
                        cancellation=voice_cancellation,
                        turn_starter=self._start_voice_turn,
                        speech_active=self._voice_speech_active,
                        busy_exceptions=(DaemonAppBusyError, TurnOrchestratorBusyError),
                        cancelled_exceptions=(TurnCancelledError,),
                        retry_seconds=float(self.config.voice.transcript_turn_retry_seconds),
                    )

                    stt_engine = build_stt_engine(self.config.voice.default_stt, config=self.config)
                    voice_stt = TranscriptionPipeline(
                        self._connect_existing,
                        config=self.config.voice,
                        engine=stt_engine,
                        on_transcript=voice_gateway.handle_transcript,
                    )
                    on_capture = voice_stt.accept_capture

                # One stateful recorder for the whole daemon: leases decide when it
                # runs, so per-request lease managers must share it. Building it
                # validates the backend (a missing sox binary kills the daemon at
                # startup — established rule); the input device comes from audio
                # policy at every start (ADR-012). When voice is disabled, the
                # route layer rejects listening mutations before a lease can start it.
                voice_recorder = build_recorder(
                    self.config.voice.recorder,
                    config=self.config,
                    input_device_provider=self._resolve_recorder_input_device,
                    on_capture=on_capture,
                )

                # Daemon-side lease TTL enforcement (FIX-04b): a crashed panel that
                # never sends button-up must not leave the microphone hot until the
                # next API call happens to run _expire_stale.
                voice_lease_sweeper = ListeningLeaseSweeper(
                    self._sweep_listening_leases,
                    interval_seconds=float(
                        getattr(self.config.voice, "lease_sweep_interval_seconds", 5.0)
                    ),
                )

                self.voice_stt = voice_stt
                self.voice_gateway = voice_gateway
                self.voice_cancellation = voice_cancellation
                self.voice_recorder = voice_recorder
                self.voice_service = voice_service
                self.voice_engine = voice_engine
                self.voice_player = voice_player
                self.voice_broker = voice_broker
                self.voice_lease_sweeper = voice_lease_sweeper

                if voice_broker is not None:
                    voice_broker.start()
                if voice_lease_sweeper is not None:
                    voice_lease_sweeper.start()

                self._start_hotkey_monitor()

                event_store.append(EventType.DAEMON_STARTED, "daemon", {"service": "dand"})
                state_machine.transition(RuntimeState.IDLE, reason="daemon started")
                if self.intake_gate is not None:
                    intake = self.intake_gate.snapshot()
                    if intake.state == "closed" and intake.reopen_policy == "daemon":
                        if intake.operation_id is None:
                            raise DaemonLifecycleError(
                                "closed intake gate has no operation id"
                            )
                        self.intake_gate.reopen(operation_id=intake.operation_id)
                self.started = True
            except Exception as startup_error:
                self._stop_hotkey_monitor()
                containment = self._stop_supervised_children()
                owner_error: BaseException | None = None
                if voice_lease_sweeper is not None:
                    try:
                        voice_lease_sweeper.stop()
                    except Exception:
                        get_logger(__name__).exception("Voice lease sweeper startup stop failed.")
                if voice_broker is not None:
                    try:
                        self._quiesce_voice_broker(voice_broker)
                    except Exception as exc:
                        owner_error = exc
                if voice_recorder is not None:
                    try:
                        voice_recorder.stop()
                    except Exception:
                        get_logger(__name__).exception("Voice recorder startup stop failed.")
                if voice_stt is not None:
                    try:
                        voice_stt.stop()
                    except Exception:
                        get_logger(__name__).exception("Voice STT startup stop failed.")
                if voice_gateway is not None:
                    try:
                        voice_gateway.stop()
                    except Exception:
                        get_logger(__name__).exception("Voice gateway startup stop failed.")

                self.voice_stt = None
                self.voice_gateway = None
                self.voice_recorder = None
                self.voice_lease_sweeper = None
                self.started = False
                containment_incomplete = containment is None or not bool(
                    getattr(containment, "complete", False)
                )
                if owner_error is not None or containment_incomplete:
                    self._voice_owner_blocked = True
                    # Retain every voice-owner reference for a later stop()
                    # retry. Dropping a stopped broker or engine here would
                    # also drop the only evidence tying a surviving child
                    # process group/listener to this daemon instance.
                    self.voice_cancellation = voice_cancellation
                    self.voice_service = voice_service
                    self.voice_engine = voice_engine
                    self.voice_player = voice_player
                    self.voice_broker = voice_broker
                    if owner_error is not None:
                        raise owner_error from startup_error
                    details = "; ".join(
                        getattr(containment, "errors", ())
                    )
                    raise DaemonLifecycleError(
                        "startup cleanup containment was incomplete"
                        + (f": {details}" if details else "")
                    ) from startup_error
                self.voice_cancellation = None
                self.voice_service = None
                self.voice_engine = None
                self.voice_player = None
                self.voice_broker = None
                raise

    def stop(self, reason: str | None = None, *, emit_event: bool = True) -> None:
        with self._lifecycle_lock:
            self.close_intake(reason=reason or "daemon shutdown")
            self._require_intake_gate().wait_for_drain(INTAKE_DRAIN_TIMEOUT_SECONDS)
            event_store = self._require_event_store()
            state_machine = self._require_state_machine()
            intake_errors: list[BaseException] = []

            # Sweeper first: it pokes the recorder via _sync_recorder and must
            # not race the shutdown below.
            if self.voice_lease_sweeper is not None:
                try:
                    self.voice_lease_sweeper.stop()
                except Exception as exc:
                    intake_errors.append(exc)
                    get_logger(__name__).exception("Voice lease sweeper stop failed.")
                else:
                    self.voice_lease_sweeper = None

            # Recorder before STT (FIX-04a): stop() must never leave an
            # orphaned sox recording after an in-process restart (hot mic), and
            # stopping it first lets the final capture reach the STT pipeline
            # below.
            if self.voice_recorder is not None:
                try:
                    self.voice_recorder.stop()
                except Exception as exc:
                    intake_errors.append(exc)
                    get_logger(__name__).exception("Voice recorder stop failed during shutdown.")
                else:
                    self.voice_recorder = None

            # STT first (no new transcripts), then the gateway — its stop()
            # WAITS for the in-flight voice turn, which writes through the
            # shared daemon connection; the daemon.stopped event below must
            # never race it on that connection.
            if self.voice_stt is not None:
                try:
                    self.voice_stt.stop()
                except Exception as exc:
                    intake_errors.append(exc)
                    get_logger(__name__).exception("STT stop failed during shutdown.")
                else:
                    self.voice_stt = None

            if self.voice_gateway is not None:
                try:
                    self.voice_gateway.stop()
                except Exception as exc:
                    intake_errors.append(exc)
                    get_logger(__name__).exception("Voice gateway stop failed during shutdown.")
                else:
                    self.voice_gateway = None

            # The gateway waits for its in-flight turn, and that turn may
            # enqueue one final speech request. Cancel active/queued speech
            # only AFTER every producer was drained, while the broker/player
            # still exist to stop any current native buffer.
            if self.voice_cancellation is not None:
                try:
                    self.voice_cancellation.cancel_active_speech(
                        reason=reason or "daemon shutdown",
                        source="daemon_shutdown",
                    )
                except Exception as exc:
                    intake_errors.append(exc)
                    get_logger(__name__).exception(
                        "Voice shutdown cancellation failed."
                    )

            if self.voice_broker is not None:
                broker = self.voice_broker
                self._quiesce_voice_broker(broker)
            self.voice_broker = None
            if self.voice_engine is not None:
                close_engine = getattr(self.voice_engine, "close", None)
                if callable(close_engine):
                    try:
                        close_engine()
                    except Exception:
                        get_logger(__name__).exception("Voice TTS engine close failed.")
            self.voice_engine = None
            self.voice_player = None
            self.voice_service = None

            if intake_errors:
                self._voice_owner_blocked = True
                self._stop_supervised_children()
                raise DaemonLifecycleError(
                    "voice intake shutdown failed closed before ownership release"
                ) from intake_errors[0]

            # The generation registry stays: it is daemon-lifetime and shared
            # with the brain adapters built in create_daemon_app.
            self.voice_cancellation = None

            # Reverse of start(): intake and playback are already closed above;
            # now the supervised children (whole process groups), then the
            # hotkey monitor releases the machine-wide owner lock.
            containment = self._stop_supervised_children()
            if containment is None or not bool(getattr(containment, "complete", False)):
                self._voice_owner_blocked = True
                raise DaemonLifecycleError(
                    "supervised child containment did not release every owner"
                )
            self._stop_hotkey_monitor()

            # The Claude adapter owns one long-lived stream-json subprocess.
            # Close it before the daemon store and lifecycle disappear so no
            # orphan can retain stdin/stdout across shutdown.
            if self.brain_manager is not None:
                try:
                    self.brain_manager.close()
                except Exception:
                    get_logger(__name__).exception(
                        "Persistent brain session stop failed during shutdown."
                    )

            # Drain worker job threads before daemon.stopped: their writes go
            # through the daemon store and must land before the final event.
            with self._worker_threads_lock:
                pending_workers = [t for t in self.worker_threads if t.is_alive()]
                self.worker_threads = []
            for thread in pending_workers:
                thread.join(timeout=10)
                if thread.is_alive():
                    get_logger(__name__).warning(
                        "Worker thread %s did not finish before daemon stop.", thread.name
                    )

            was_started = self.started
            self.started = False
            self._voice_owner_blocked = False

            if emit_event and was_started:
                event_store.append(
                    EventType.DAEMON_STOPPED,
                    "daemon",
                    {"service": "dand", "reason": reason},
                )
            if state_machine.state is not RuntimeState.STOPPING:
                state_machine.transition(RuntimeState.STOPPING, reason=reason or "daemon stopped")

    def close_intake(
        self,
        *,
        reason: str,
        operation_id: str | None = None,
    ) -> str:
        gate = self._require_intake_gate()
        current = gate.snapshot()
        if current.state == "closed":
            if operation_id is not None and current.operation_id != operation_id:
                raise DaemonLifecycleError(
                    "intake is already closed by a different operation"
                )
            if current.operation_id is None:
                raise DaemonLifecycleError("closed intake gate has no operation id")
            return current.operation_id
        resolved_operation_id = operation_id or str(uuid.uuid4())
        gate.close(operation_id=resolved_operation_id, reason=reason)
        return resolved_operation_id

    def mark_failed(self, *, reason: str, errors: tuple[str, ...] = ()) -> None:
        """Record that this daemon is alive but no longer serving.

        Used when a restart could not exit safely: the process survives with
        intake closed, so snapshot_state must stop reporting ok. While it
        stayed green, /health and the panel vouched for a daemon that could not
        speak, and a dead PTT looked like a hotkey problem instead of a dead
        owner.

        KNOWN DEFECT (2026-07-21) — it does not make that outage visible yet,
        so do not read it as a guarantee. The except below leaves the state
        untouched, and the failure that breaks the append (event store locked
        or full) is the same class that most likely broke the drain — so it
        stays green exactly when it matters. RuntimeStateMachine.force_idle is
        the shape to copy: assign the state even when the append raises.
        ERROR -> IDLE is a permitted transition and worker threads are drained
        only further down in stop(), so a turn finishing after this call walks
        the flag back. The guard above covers ERROR but not STOPPING, the other
        state with no outgoing transitions, from which transition() raises. The
        panel renders it nowhere either: typewriter.js force-hides
        #activityStrip, the only element showing runtime state. The premise
        "intake closed" is also optimistic — the voice layer is torn down only
        at the two raise sites after teardown, while dan/daemon/restart.py
        calls this after a drain that usually failed earlier.
        docs/reviews/2026-07-21-restart-orphan-shell-review.md §8-§10.
        """

        machine = self.state_machine
        if machine is None:
            return
        if machine.state is RuntimeState.ERROR:
            return
        try:
            machine.transition(
                RuntimeState.ERROR,
                reason=reason,
                metadata={"errors": list(errors)} if errors else None,
            )
        except Exception:
            get_logger(__name__).exception("Could not record the daemon failure state.")

    def snapshot_state(self) -> dict[str, Any]:
        state = self.state_machine.state.value if self.state_machine is not None else RuntimeState.BOOTING.value
        schema_version, latest_event_id = self._db_snapshot()
        return {
            "service": "dand",
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
            "session_tokens_in": self._session_tokens_in,
            "session_tokens_out": self._session_tokens_out,
            # Task 9: missing Accessibility / lost hotkey lock is a visible
            # blocker here while the rest of dand stays healthy.
            "hotkey": self._hotkey_snapshot(),
            "children": self._children_snapshot(),
        }

    def _hotkey_snapshot(self) -> dict[str, Any]:
        monitor = self.hotkey_monitor
        if monitor is None:
            return {
                "running": False,
                "accessibility": None,
                "blocker": self.hotkey_error,
            }
        try:
            health = monitor.health()
            return {
                "running": bool(health.running),
                "accessibility": health.accessibility,
                "blocker": self.hotkey_error,
            }
        except Exception:  # noqa: BLE001 - a probe failure must not break /state
            get_logger(__name__).exception("Hotkey health probe failed.")
            return {
                "running": False,
                "accessibility": "unknown",
                "blocker": self.hotkey_error,
            }

    def _children_snapshot(self) -> dict[str, Any]:
        if self.child_supervisor is None:
            return {}
        try:
            return dict(self.child_supervisor.status())
        except Exception:  # noqa: BLE001 - a probe failure must not break /state
            get_logger(__name__).exception("Child supervisor status failed.")
            return {}

    def allowed_state_targets(self) -> list[str]:
        state_machine = self._require_state_machine()
        return sorted(state.value for state in state_machine.allowed_targets())

    def list_events_after(self, after_id: int, limit: int) -> list[Event]:
        conn = self._connect_existing()
        try:
            return create_event_store(conn).list_after(after_id, limit=limit)
        finally:
            close_quietly(conn)

    def list_latest_events(self, limit: int) -> list[Event]:
        conn = self._connect_existing()
        try:
            return create_event_store(conn).latest(limit=limit)
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
            dan_id = self._resolve_dan_conversation_id()
            rows = ConversationRepository(conn).list_recent_with_stats(
                limit=max(limit, 50),
                include_archived=True,
            )
            selected = [row for row in rows if row.get("id") == dan_id]
            return selected or [{"id": dan_id, "title": "DAN", "turn_count": 0, "latest_turn_at": None}]
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
                self._resolve_dan_conversation_id(),
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
        if not isinstance(updates, Mapping) or not updates:
            raise DaemonAppError("Settings update must be a non-empty mapping.")
        for key in updates:
            if not isinstance(key, str) or not key.strip():
                raise DaemonAppError("Setting keys must be non-empty strings.")
        installation_updates, runtime_updates = validate_setting_updates(updates)
        if installation_updates:
            ConfigStore(self.config.source_path).set_many(installation_updates)
            self.config = load_config(self.config.source_path)
            if self.context_builder is not None:
                self.context_builder._config = self.config

        conn = self._connect_existing()
        now = utc_now_iso()
        try:
            with conn:
                for key, value in runtime_updates.items():
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

    def _listening_manager(self, conn: sqlite3.Connection):
        from dan.voice.listening import ListeningLeaseManager

        return ListeningLeaseManager(
            conn,
            config=self.config.voice,
            recorder=self.voice_recorder,
            event_store=create_event_store(conn),
        )

    def acquire_listening_lease(self, *, mode: str, source: str):
        if mode == "hold" and self.voice_broker is not None:
            try:
                self.voice_broker.pause()
                self.voice_broker.stop_playback()
            except Exception:
                pass
        if not self.started:
            raise DaemonAppNotStartedError("Daemon app is not started.")
        conn = self._connect_existing()
        try:
            return self._listening_manager(conn).acquire(mode=mode, source=source)
        finally:
            close_quietly(conn)

    def release_listening_leases(self, *, mode: str):
        if mode == "hold" and self.voice_broker is not None:
            try:
                self.voice_broker.resume()
            except Exception:
                pass
        if not self.started:
            raise DaemonAppNotStartedError("Daemon app is not started.")
        conn = self._connect_existing()
        try:
            return self._listening_manager(conn).release(mode=mode)
        finally:
            close_quietly(conn)

    def active_listening_leases(self):
        if not self.started:
            raise DaemonAppNotStartedError("Daemon app is not started.")
        conn = self._connect_existing()
        try:
            return self._listening_manager(conn).active()
        finally:
            close_quietly(conn)

    def cancel_active_speech(
        self,
        *,
        reason: str,
        source: str | None = None,
    ) -> dict[str, Any]:
        if not self.started:
            raise DaemonAppNotStartedError("Daemon app is not started.")
        if self.voice_cancellation is None:
            return {
                "reason": reason,
                "cancellation_reason": reason,
                "interruption_reason": reason,
                "interrupted_previous_response": False,
                "cancelled_speech_id": None,
                "previous_turn_id": None,
                "new_turn_source": "PTT" if source == "ptt" else (source or "voice"),
                "generation_cancelled": 0,
                "queue_cancelled": 0,
                "playback_stopped": False,
                "tombstoned_turns": 0,
            }
        return self.voice_cancellation.cancel_active_speech(
            reason=reason,
            source=source,
        )

    def list_voice_queue(self, *, limit: int = 20) -> list[dict[str, Any]]:
        if not self.started:
            raise DaemonAppNotStartedError("Daemon app is not started.")
        bounded_limit = _bounded_voice_queue_limit(limit)
        conn = self._connect_existing()
        try:
            rows = conn.execute(
                """
                SELECT id, created_at, updated_at, turn_id, text, priority,
                       voice_id, interrupt_policy, status, error,
                       metadata_json, spoken_at, playback_confirmed
                FROM voice_queue
                ORDER BY rowid DESC
                LIMIT ?
                """,
                (bounded_limit,),
            ).fetchall()
        finally:
            close_quietly(conn)
        return [_voice_queue_row(row) for row in rows]

    def get_audio_devices(self):
        """Observe audio devices through the owning manager (CONTRACTS §9)."""

        if not self.started:
            raise DaemonAppNotStartedError("Daemon app is not started.")
        from dan.audio.devices import AudioDeviceManager

        conn = self._connect_existing()
        try:
            manager = AudioDeviceManager(
                conn,
                config=self.config.audio,
                event_store=create_event_store(conn),
            )
            return manager.current()
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
        happens before the in-memory switch so the settings table (dand's
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

    def recall_memory(self, query: Any, *, limit: Any = 10) -> dict[str, Any]:
        if not self.started:
            raise DaemonAppNotStartedError("Daemon app is not started.")
        return memory_recall_to_dict(self._require_memory_archive().recall(query, limit=limit))

    def create_memory_candidate(self, payload: Mapping[str, Any]) -> MemoryCandidate:
        if not self.started:
            raise DaemonAppNotStartedError("Daemon app is not started.")
        try:
            return self._require_memory_candidate_repository().create_candidate(
                **dict(payload)
            )
        except MemoryCandidateError as exc:
            raise DaemonAppError(str(exc)) from exc

    def list_memory_candidates(self, *, status: str | None = None) -> list[MemoryCandidate]:
        if not self.started:
            raise DaemonAppNotStartedError("Daemon app is not started.")
        try:
            return self._require_memory_candidate_repository().list_candidates(
                status=status
            )
        except MemoryCandidateError as exc:
            raise DaemonAppError(str(exc)) from exc

    def get_memory_candidate(self, candidate_id: str) -> MemoryCandidate:
        if not self.started:
            raise DaemonAppNotStartedError("Daemon app is not started.")
        normalized_id = _required_text(candidate_id, "candidate_id")
        try:
            candidate = self._require_memory_candidate_repository().get_candidate(
                normalized_id
            )
        except MemoryCandidateError as exc:
            raise DaemonAppError(str(exc)) from exc
        if candidate is None:
            raise DaemonAppNotFoundError(f"Memory candidate not found: {normalized_id}")
        return candidate

    def approve_memory_candidate(self, candidate_id: str) -> MemoryCandidate:
        return self._decide_memory_candidate(candidate_id, "approve")

    def reject_memory_candidate(self, candidate_id: str) -> MemoryCandidate:
        return self._decide_memory_candidate(candidate_id, "reject")

    def add_memory_candidate_evidence(
        self,
        candidate_id: str,
        payload: Mapping[str, Any],
    ) -> MemoryEvidence:
        if not self.started:
            raise DaemonAppNotStartedError("Daemon app is not started.")
        repository = self._require_memory_evidence_repository()
        try:
            return repository.add_evidence(candidate_id, **dict(payload))
        except MemoryEvidenceNotFound as exc:
            raise DaemonAppNotFoundError(str(exc)) from exc
        except MemoryEvidenceConflict as exc:
            raise DaemonAppConflictError(str(exc)) from exc
        except MemoryEvidenceError as exc:
            raise DaemonAppError(str(exc)) from exc

    def list_memory_candidate_evidence(
        self,
        candidate_id: str,
    ) -> list[MemoryEvidence]:
        if not self.started:
            raise DaemonAppNotStartedError("Daemon app is not started.")
        repository = self._require_memory_evidence_repository()
        try:
            return repository.list_evidence(candidate_id)
        except MemoryEvidenceNotFound as exc:
            raise DaemonAppNotFoundError(str(exc)) from exc
        except MemoryEvidenceError as exc:
            raise DaemonAppError(str(exc)) from exc

    def activate_memory_candidate(self, candidate_id: str) -> MemoryItem:
        if not self.started:
            raise DaemonAppNotStartedError("Daemon app is not started.")
        normalized_id = _required_text(candidate_id, "candidate_id")
        repository = self._require_memory_item_repository()
        try:
            return repository.activate_candidate(normalized_id)
        except MemoryItemNotFound as exc:
            raise DaemonAppNotFoundError(str(exc)) from exc
        except MemoryItemConflict as exc:
            raise DaemonAppConflictError(str(exc)) from exc
        except MemoryItemError as exc:
            raise DaemonAppError(str(exc)) from exc

    def list_memory_items(self) -> list[MemoryItem]:
        if not self.started:
            raise DaemonAppNotStartedError("Daemon app is not started.")
        try:
            return self._require_memory_item_repository().list_items()
        except MemoryItemError as exc:
            raise DaemonAppError(str(exc)) from exc

    def get_memory_item(self, memory_id: str) -> MemoryItem:
        if not self.started:
            raise DaemonAppNotStartedError("Daemon app is not started.")
        normalized_id = _required_text(memory_id, "memory_id")
        try:
            item = self._require_memory_item_repository().get_item(normalized_id)
        except MemoryItemError as exc:
            raise DaemonAppError(str(exc)) from exc
        if item is None:
            raise DaemonAppNotFoundError(f"Memory item not found: {normalized_id}")
        return item

    def compile_memory_preview(
        self,
        request: MemoryCompilerRequest,
    ) -> CompiledMemoryContext:
        if not self.started:
            raise DaemonAppNotStartedError("Daemon app is not started.")
        try:
            compiler = MemoryCompiler(self._require_memory_item_repository())
            return compiler.compile(request)
        except MemoryItemError as exc:
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

        thread = threading.Thread(
            target=_run, name=f"dan-worker-{job.id[:8]}", daemon=True
        )
        with self._worker_threads_lock:
            self.worker_threads = [t for t in self.worker_threads if t.is_alive()]
            self.worker_threads.append(thread)
        thread.start()
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
        # Owner-controlled local runtime: every registered request executes on
        # the one observable path. Tool policy remains descriptive metadata;
        # it cannot create a second, hidden approval workflow.
        del source
        recorder = self._require_tool_run_recorder()
        recorder.record_requested(
            run_id=request.id,
            tool_name=tool.name,
            risk=tool.risk,
            input=request.arguments,
            turn_id=turn_id,
            correlation_id=turn_id,
        )
        recorder.record_started(request.id, correlation_id=turn_id)
        result = self.tool_registry.execute_tool(request)
        if result.status == "finished":
            recorder.record_finished(
                request.id,
                output=result.output or {},
                correlation_id=turn_id,
            )
        else:
            recorder.record_failed(
                request.id,
                error=result.error or "Tool execution failed.",
                correlation_id=turn_id,
            )
        return result

    def _decide_memory_candidate(self, candidate_id: str, decision: str) -> MemoryCandidate:
        if not self.started:
            raise DaemonAppNotStartedError("Daemon app is not started.")
        normalized_id = _required_text(candidate_id, "candidate_id")
        repository = self._require_memory_candidate_repository()
        try:
            if decision == "approve":
                return repository.approve_candidate(normalized_id)
            if decision == "reject":
                return repository.reject_candidate(normalized_id)
        except MemoryCandidateNotFound as exc:
            raise DaemonAppNotFoundError(str(exc)) from exc
        except MemoryCandidateConflict as exc:
            raise DaemonAppConflictError(str(exc)) from exc
        except MemoryCandidateError as exc:
            raise DaemonAppError(str(exc)) from exc
        raise DaemonAppError(f"Unknown memory candidate decision: {decision}")

    def list_pending_approvals(self, limit: int = 50) -> list[dict[str, Any]]:
        if not self.started:
            raise DaemonAppNotStartedError("Daemon app is not started.")
        return self._require_approval_gate().list_pending(limit=limit)

    def list_actionable_approvals(self, limit: int = 50) -> list[dict[str, Any]]:
        """Everything the operator can still act on: pending decisions plus
        approvals that were approved but never executed (e.g. a prior execute
        failed). Approved rows that already ran are dropped — they are done,
        not silently discarded. This is the source of truth the panel renders,
        so nothing disappears just because a client forgot it."""

        if not self.started:
            raise DaemonAppNotStartedError("Daemon app is not started.")
        gate = self._require_approval_gate()
        recorder = self._require_tool_run_recorder()
        policy = self._live_tool_permission_policy()
        actionable: list[dict[str, Any]] = []
        for approval in gate.list_pending_and_approved(limit=limit):
            if approval.get("status") == "approved" and recorder.get_by_approval_id(
                str(approval.get("id"))
            ) is not None:
                continue
            # Runtime-lab (Ozzy 2026-07-10): the live policy auto-runs every ALLOW
            # tool right after the turn, so an ALLOW approval is NOT something the
            # operator has to click — surfacing it only causes a yellow flicker in
            # the panel before auto-run resolves it. Hide those. Anything the policy
            # would NOT allow outright (genuinely stuck) stays visible. Wrapped so a
            # single malformed approval can never break the whole panel projection.
            if approval.get("status") == "pending":
                try:
                    if self._approval_decision_is_allow(approval, policy):
                        continue
                except Exception:
                    get_logger(__name__).debug(
                        "Actionable-approval allow-check failed for %s; keeping it visible.",
                        approval.get("id"),
                        exc_info=True,
                    )
            actionable.append(approval)
        return actionable

    def approve_and_execute_tool(
        self, approval_id: str, *, reason: str | None = None
    ) -> dict[str, Any]:
        """One operator click: approve (if still pending) then execute, under a
        single lock so there is no half-approved window and no separate second
        step. A double-click conflicts on "already executed" instead of running
        twice; an approval that was approved earlier but never ran is retried."""

        if not self.started:
            raise DaemonAppNotStartedError("Daemon app is not started.")

        normalized_approval_id = _required_text(approval_id, "approval_id")
        with self.tool_execution_lock:
            gate = self._require_approval_gate()
            approval = gate.get_approval(normalized_approval_id)
            if approval is None:
                raise DaemonAppNotFoundError(f"Unknown approval: {normalized_approval_id}")
            status = approval.get("status")
            if status == "pending":
                gate.decide(normalized_approval_id, "approved", reason=reason)
            elif status != "approved":
                raise DaemonAppConflictError(
                    f"Approval is not actionable: {normalized_approval_id}"
                )
            return self._execute_approved_tool_locked(normalized_approval_id)

    def approve(self, approval_id: str, *, reason: str | None = None) -> dict[str, Any]:
        if not self.started:
            raise DaemonAppNotStartedError("Daemon app is not started.")
        return self._require_approval_gate().decide(approval_id, "approved", reason=reason)

    def reject(self, approval_id: str, *, reason: str | None = None) -> dict[str, Any]:
        if not self.started:
            raise DaemonAppNotStartedError("Daemon app is not started.")
        gate = self._require_approval_gate()
        approval = gate.get_approval(approval_id)
        rejected = gate.decide(approval_id, "rejected", reason=reason)
        self._reject_memory_save_candidate_for_approval(approval or rejected)
        return rejected

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

        with self._require_intake_gate().admit(f"text:{source}"):
            if not self.text_turn_lock.acquire(blocking=False):
                raise DaemonAppBusyError("Another text turn is already running.")

            try:
                orchestrator = self._create_turn_orchestrator()
                return orchestrator.handle_text(
                    text=text,
                    conversation_id=self._resolve_dan_conversation_id(),
                    metadata=metadata,
                    source=source,
                )
            finally:
                self.text_turn_lock.release()

    def _approval_decision_is_allow(
        self, approval: Mapping[str, Any], policy: ToolPermissionPolicy
    ) -> bool:
        """Would the live policy ALLOW this captured tool outright? Only ALLOW
        auto-runs; APPROVAL_REQUIRED (and destructive) stay pending for a human."""

        source = _request_source_from_approval(approval)
        if source is None:
            return False
        tool_request = _tool_request_from_approval(approval)
        try:
            tool = self.tool_registry.get(tool_request.tool_name)
        except ToolRegistryError:
            return False
        decision = policy.decide(
            tool.risk,
            source=source,
            tool_name=tool.name,
            payload=tool_request.arguments,
        ).decision
        return decision == ToolDecision.ALLOW

    def _start_voice_turn(self, text: str) -> TextTurnResult:
        """Gateway hook: an accepted transcript enters the SAME orchestrator
        as panel text (ADR-011), marked with the voice source. The HTTP layer
        refuses this source on purpose — only this internal path mints voice
        turns.

        Every utterance rolls into the ONE durable conversation persisted in
        settings (voice.conversation_id) — resolved up front, so cancelled or
        failed turns cannot lose it."""

        conversation_id = self._resolve_voice_conversation_id()
        # Log BEFORE the turn runs: a cancelled turn raises, and the cancelled
        # turns are exactly the ones the conversation-id diagnosis needs to see.
        get_logger(__name__).info("voice-turn conversation: id=%s", conversation_id)
        return self.handle_text_input(
            text=text,
            conversation_id=conversation_id,
            source="voice",
            metadata={"origin": "voice_transcript"},
        )

    _DAN_CONVERSATION_SETTING = "dan.conversation_id"
    _VOICE_CONVERSATION_SETTING = "voice.conversation_id"

    def _resolve_dan_conversation_id(self) -> str:
        """One durable DAN conversation for panel text and voice."""
        try:
            settings = self.get_settings()
        except Exception:
            settings = {}
        dan_id = settings.get(self._DAN_CONVERSATION_SETTING)
        voice_id = settings.get(self._VOICE_CONVERSATION_SETTING)
        resolved = next(
            (
                value.strip()
                for value in (dan_id, voice_id)
                if isinstance(value, str) and value.strip()
            ),
            str(uuid.uuid4()),
        )
        try:
            if dan_id != resolved or voice_id != resolved:
                self.update_settings(
                    {
                        self._DAN_CONVERSATION_SETTING: resolved,
                        self._VOICE_CONVERSATION_SETTING: resolved,
                    }
                )
        except Exception:
            get_logger(__name__).warning(
                "Could not persist DAN conversation id; using it for this run only.",
                exc_info=True,
            )
        return resolved

    def _resolve_voice_conversation_id(self) -> str:
        return self._resolve_dan_conversation_id()

    # -- daemon-owned global PTT hotkey (Task 9) --------------------------

    def _start_hotkey_monitor(self) -> None:
        """Install the one global hotkey tap, if configured and allowed.

        The default (real Quartz) monitor never starts in tests or with the
        mic disabled (DAN_TEST_MODE / DAN_DISABLE_MIC); an injected factory
        bypasses that env gate because injection means a hermetic fake.
        Failure to own the hotkey is a visible health blocker
        (`hotkey_error`), never a daemon crash — dand stays healthy while PTT
        is reported unavailable (no fallback to a panel listener).
        """

        from dan.input.hotkey import HotkeySpecError, parse_hotkey

        self.hotkey_error = None
        if not self.config.voice.enabled:
            return
        try:
            required_mask = parse_hotkey(self.config.voice.ptt_hotkey or "")
        except HotkeySpecError as exc:
            self.hotkey_error = str(exc)
            get_logger(__name__).warning("Global PTT hotkey disabled: %s", exc)
            return
        if required_mask == 0:
            return
        factory = self.hotkey_monitor_factory
        if factory is None:
            if os.environ.get("DAN_TEST_MODE") == "1" or os.environ.get(
                "DAN_DISABLE_MIC"
            ) == "1":
                return

            def factory(*, lock_path: Path, required_mask: int, on_edge: Any) -> Any:
                from dan.input.macos_event_tap import MacOSHotkeyMonitor

                return MacOSHotkeyMonitor(
                    lock_path=lock_path,
                    required_mask=required_mask,
                    on_edge=on_edge,
                )

        gate = self._build_ptt_activation_gate()
        monitor = factory(
            lock_path=self.paths.runtime_dir / "hotkey.lock",
            required_mask=required_mask,
            on_edge=gate.edge,
        )
        try:
            monitor.start()
        except Exception as exc:  # noqa: BLE001 - blocker, not a crash
            self.hotkey_error = str(exc)
            get_logger(__name__).warning(
                "Global PTT hotkey unavailable (dand stays up): %s", exc
            )
            return
        self.hotkey_monitor = monitor

    def _build_ptt_activation_gate(self) -> Any:
        """Accidental-brush guard (Ozzy): grace before the mic ever arms."""

        from dan.input.hotkey import PttActivationGate

        grace_ms = int(getattr(self.config.voice, "ptt_activation_grace_ms", 0) or 0)
        return PttActivationGate(
            grace_seconds=grace_ms / 1000.0,
            on_down=self._hotkey_ptt_down,
            on_up=self._hotkey_ptt_up,
            timer_factory=self.ptt_timer_factory,
        )

    def _stop_hotkey_monitor(self) -> None:
        monitor, self.hotkey_monitor = self.hotkey_monitor, None
        if monitor is None:
            return
        try:
            monitor.stop()
        except Exception:
            get_logger(__name__).exception("Hotkey monitor stop failed.")

    def _hotkey_ptt_down(self) -> None:
        """In-process PTT controller: the tap drives the SAME lease manager as
        POST /voice/ptt/down (source "global_hotkey") — never HTTP back into
        the daemon that hosts the tap."""

        try:
            self.acquire_listening_lease(mode="hold", source="global_hotkey")
            self._require_event_store().append(
                EventType.PTT_DOWN, "hotkey", {"source": "global_hotkey"}
            )
        except Exception:
            get_logger(__name__).exception("Hotkey PTT down failed.")

    def _hotkey_ptt_up(self) -> None:
        try:
            self.release_listening_leases(mode="hold")
            self._require_event_store().append(
                EventType.PTT_UP, "hotkey", {"source": "global_hotkey"}
            )
        except Exception:
            get_logger(__name__).exception("Hotkey PTT up failed.")

    # -- supervised children (Task 9) -------------------------------------

    def _ensure_supertonic_serve_child(self) -> bool:
        """Register + start `supertonic serve` under the ChildSupervisor.

        Returns True when the serve child is supervised (so the engine must
        not autostart its own). Configuration gate mirrors the engine's warm-
        serve contract: a serve URL plus autostart=true means dand owns the
        server; autostart=false keeps expecting an externally-managed one.
        """

        voice = self.config.voice
        if (
            self.child_supervisor is None
            or str(voice.default_tts or "").strip().lower() != "supertonic"
            or not str(voice.supertonic_serve_url or "").strip()
            or not bool(voice.supertonic_serve_autostart)
        ):
            return False
        from dan.daemon.supervisor import ChildSpec
        from dan.voice.tts import _resolve_supertonic_binary

        serve_url = str(voice.supertonic_serve_url).rstrip("/")
        port = serve_url.rsplit(":", 1)[-1]
        binary = _resolve_supertonic_binary(str(voice.supertonic_binary or ""))
        self.child_supervisor.register(
            ChildSpec(
                name="supertonic",
                argv=(
                    binary,
                    "serve",
                    "--model",
                    str(voice.supertonic_serve_model or "supertonic-3"),
                    "--port",
                    port,
                    "--log-level",
                    "warning",
                ),
                health_url=f"{serve_url}/v1/health",
                restart_limit=3,
                backoff_seconds=(0.5, 1.0, 2.0, 4.0, 8.0),
            )
        )
        self.child_supervisor.ensure_running("supertonic")
        start_watchdog = getattr(self.child_supervisor, "start_watchdog", None)
        if callable(start_watchdog):
            start_watchdog()
        return True

    def _stop_supervised_children(self) -> Any:
        from dan.daemon.supervisor import ChildContainmentResult

        if self.child_supervisor is None:
            result = ChildContainmentResult(True, True, True, True, (), ())
            self._last_child_containment = result
            return result
        try:
            result = self.child_supervisor.stop_all()
        except Exception as exc:
            get_logger(__name__).exception("Supervised child shutdown failed.")
            try:
                remaining = tuple(self.child_supervisor.child_pids())
            except Exception:
                remaining = ()
            result = ChildContainmentResult(
                watchdog_joined=False,
                children_reaped=False,
                process_groups_released=False,
                listeners_released=False,
                remaining_pids=remaining,
                errors=(str(exc),),
            )
        self._last_child_containment = result
        return result

    def _quiesce_voice_broker(self, broker: Any) -> None:
        from dan.voice.broker import VoiceBrokerShutdownTimeout

        try:
            broker.stop()
        except VoiceBrokerShutdownTimeout as first_error:
            self._voice_owner_blocked = True
            containment = self._stop_supervised_children()
            try:
                broker.stop()
            except VoiceBrokerShutdownTimeout:
                raise
            except Exception as retry_error:
                raise DaemonLifecycleError(
                    "voice owner shutdown failed after emergency containment"
                ) from retry_error
            if containment is None or not bool(
                getattr(containment, "complete", False)
            ):
                raise DaemonLifecycleError(
                    "voice owner quiesced but supervised containment failed"
                ) from first_error
            self._voice_owner_blocked = False
        except Exception as exc:
            self._voice_owner_blocked = True
            self._stop_supervised_children()
            raise DaemonLifecycleError(
                "voice owner shutdown failed before quiescence"
            ) from exc

    def emergency_contain_supervised_children(self) -> Any:
        return self._stop_supervised_children()

    def child_pids(self) -> list[int]:
        if self.child_supervisor is None:
            return []
        return list(self.child_supervisor.child_pids())

    def _sweep_listening_leases(self) -> None:
        """Sweeper tick: expire stale leases and sync the recorder."""

        if not self.started or self.voice_recorder is None:
            return
        conn = self._connect_existing()
        try:
            self._listening_manager(conn).active()
        finally:
            close_quietly(conn)

    def _voice_speech_active(self) -> bool:
        """Barge-in probe: is DAN speaking (queue) or generating (registry)?"""

        registry = self.voice_generation_registry
        if registry is not None and registry.active_count() > 0:
            return True
        from dan.voice.queue import VoiceQueue

        conn = self._connect_existing()
        try:
            return VoiceQueue(conn).pending_count() > 0
        finally:
            close_quietly(conn)

    def close(self) -> None:
        if self.started or self.voice_broker is not None or self._voice_owner_blocked:
            self.stop(reason="close")
        close_quietly(self.conn)
        self.conn = None
        self.event_store = None
        self.state_machine = None
        self.brain_manager = None
        self.context_builder = None
        self.memory_manager = None
        self.memory_candidate_repository = None
        self.memory_evidence_repository = None
        self.memory_item_repository = None
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

    def _require_intake_gate(self) -> IntakeGate:
        if self.intake_gate is None:
            raise DaemonAppError("Daemon app is not initialized with an intake gate.")
        return self.intake_gate

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

    def _require_memory_candidate_repository(self) -> MemoryCandidateRepository:
        if self.memory_candidate_repository is None:
            raise DaemonAppError(
                "Daemon app is not initialized with a memory candidate repository."
            )
        return self.memory_candidate_repository

    def _require_memory_evidence_repository(self) -> MemoryEvidenceRepository:
        if self.memory_evidence_repository is None:
            raise DaemonAppError(
                "Daemon app is not initialized with a memory evidence repository."
            )
        return self.memory_evidence_repository

    def _require_memory_item_repository(self) -> MemoryItemRepository:
        if self.memory_item_repository is None:
            raise DaemonAppError(
                "Daemon app is not initialized with a memory item repository."
            )
        return self.memory_item_repository

    def _require_memory_archive(self) -> MemoryArchive:
        if self.memory_archive is None:
            raise DaemonAppError("Daemon app is not initialized with a memory archive.")
        return self.memory_archive

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

    def _live_tool_permission_policy(self) -> ToolPermissionPolicy:
        """Tool-permission policy for the current turn: the startup policy (TOML
        seed) overlaid with the panel's live settings. Rebuilt per turn so panel
        changes take effect on the next turn without a restart."""

        try:
            settings = self.get_settings()
        except Exception:  # a settings read must never harden into a turn failure
            get_logger(__name__).warning(
                "Live settings read failed; using startup permission policy.",
                exc_info=True,
            )
            return self.tool_permission_policy
        return _policy_with_settings_overlay(self.tool_permission_policy, settings)

    def _create_turn_orchestrator(self) -> TurnOrchestrator:
        speech_pipeline = None
        if self.config.voice.enabled and self.config.voice.speak_responses:
            from dan.voice.speech import SpeechPipeline

            speech_pipeline = SpeechPipeline(
                config=self.config.voice,
                voice_service=self.voice_service,
            )
        return _MemorySaveAwareTurnOrchestrator(
            conn=self._require_conn(),
            event_store=self._require_event_store(),
            event_bus=self.event_bus,
            state_machine=self._require_state_machine(),
            brain_manager=self._require_brain_manager(),
            context_builder=self._require_context_builder(),
            tool_registry=self.tool_registry,
            speech_pipeline=speech_pipeline,
            on_response=self._accumulate_tokens,
        )

    def _accumulate_tokens(self, response: BrainResponse) -> None:
        usage = response.usage
        if usage is None:
            return
        with self._tokens_lock:
            self._session_tokens_in += usage.input_tokens or 0
            self._session_tokens_out += usage.output_tokens or 0

    def _approval_gate_for_tool_requests(
        self,
        *,
        source: RequestSource | str | None = None,
    ) -> Any:
        gate = self._require_approval_gate()
        try:
            tool = self.tool_registry.get("memory_save")
        except ToolRegistryError:
            return gate
        if isinstance(tool, MemorySaveTool):
            return _MemorySaveProposalApprovalGate(gate, tool, source=source)
        return gate

    def _reject_memory_save_candidate_for_approval(self, approval: Mapping[str, Any] | None) -> None:
        candidate_id = _memory_save_candidate_id_from_approval(approval)
        if candidate_id is None:
            return
        repository = self._require_memory_candidate_repository()
        try:
            repository.reject_candidate(candidate_id)
        except MemoryCandidateConflict as exc:
            candidate = repository.get_candidate(candidate_id)
            if candidate is not None and candidate.status == "rejected":
                return
            raise DaemonAppConflictError(str(exc)) from exc
        except MemoryCandidateNotFound as exc:
            raise DaemonAppNotFoundError(str(exc)) from exc
        except MemoryCandidateError as exc:
            raise DaemonAppError(str(exc)) from exc

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
        permission = self._live_tool_permission_policy().decide(
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
        db_path = self.paths.db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        if not db_path.is_file():
            get_logger(__name__).warning("Database missing in worker path; initializing on demand: %s", db_path)
            return initialize_database(db_path)
        try:
            return connect_db(db_path)
        except DatabaseError as exc:
            logger = get_logger(__name__)
            logger.warning(
                "DB connect failed; path=%s cwd=%s home=%s parent_exists=%s parent_writable=%s file_exists=%s file_writable=%s",
                db_path,
                os.getcwd(),
                os.getenv("HOME"),
                db_path.parent.exists(),
                os.access(db_path.parent, os.W_OK),
                db_path.exists(),
                os.access(db_path, os.W_OK),
            )
            raise

    def _resolve_recorder_input_device(self) -> str | None:
        """Audio policy decides which input the recorder uses (ADR-012).

        Resolved at every recorder start, not once at wiring time: devices
        come and go, and the snapshot side effect keeps the DB truthful.
        """

        from dan.audio.devices import AudioDeviceManager

        conn = self._connect_existing()
        try:
            manager = AudioDeviceManager(conn, config=self.config.audio)
            return manager.current().input_device
        finally:
            close_quietly(conn)

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

DANDaemonApp = DaemonApp
DANDaemon = DaemonApp


def _resolve_compiled_memory_enabled(
    config: DANConfig,
    *,
    explicit_enabled: bool | None,
    force_disabled: bool = False,
) -> bool:
    if not config.memory.enabled:
        return False
    if force_disabled:
        return False
    if explicit_enabled is not None:
        return bool(explicit_enabled)
    return bool(config.memory.compiled_context_enabled)


def _compiled_memory_config_from_config(config: DANConfig) -> MemoryCompilerConfig:
    return MemoryCompilerConfig(
        max_items=config.memory.compiled_context_max_items,
        max_chars=config.memory.compiled_context_max_chars,
        include_procedural=config.memory.compiled_context_include_procedural,
    )


def create_daemon_app(
    config_path: str | Path | None = None,
    *,
    initialize: bool = True,
    voice_resolver: Any = None,
    memory_compiler: Any | None = None,
    compiled_memory_enabled: bool | None = None,
    compiled_memory_enabled_session_profiles: Iterable[tuple[str, str]] | None = None,
    compiled_memory_force_disabled: bool = False,
    compiled_memory_config: MemoryCompilerConfig | None = None,
) -> DaemonApp:
    config = load_config(config_path)
    get_logger(__name__).warning("Loaded config from: %s", config.source_path)
    return create_daemon_app_from_config(
        config,
        initialize=initialize,
        voice_resolver=voice_resolver,
        memory_compiler=memory_compiler,
        compiled_memory_enabled=compiled_memory_enabled,
        compiled_memory_enabled_session_profiles=compiled_memory_enabled_session_profiles,
        compiled_memory_force_disabled=compiled_memory_force_disabled,
        compiled_memory_config=compiled_memory_config,
    )


def _setting_list(settings: Mapping[str, Any], key: str, default: Iterable[str]) -> tuple[str, ...]:
    value = settings.get(key)
    if isinstance(value, (list, tuple)):
        items = tuple(str(item).strip() for item in value if str(item).strip())
        return items or tuple(default)
    if isinstance(value, str) and value.strip():
        return (value.strip(),)
    return tuple(default)


def _policy_with_settings_overlay(
    base: ToolPermissionPolicy,
    settings: Mapping[str, Any],
) -> ToolPermissionPolicy:
    # Runtime-lab: panel/settings must not reintroduce approval gates. Keep the
    # API shape, but every turn gets an open execution policy.
    return ToolPermissionPolicy(
        destructive_tools_enabled=True,
        approved_roots=_setting_list(settings, "security.approved_roots", base.approved_roots),
        trusted_scopes=base.trusted_scopes,
        voice_auto_approve=True,
        auto_approve_mode="all",
        require_approval_for_shell=False,
        require_approval_for_file_write=False,
        require_approval_for_network=False,
        require_approval_for_ui=False,
        require_approval_for_terminal=False,
        require_approval_for_memory=False,
    )

def create_daemon_app_from_config(
    config: DANConfig,
    *,
    initialize: bool = True,
    voice_resolver: Any = None,
    memory_compiler: Any | None = None,
    compiled_memory_enabled: bool | None = None,
    compiled_memory_enabled_session_profiles: Iterable[tuple[str, str]] | None = None,
    compiled_memory_force_disabled: bool = False,
    compiled_memory_config: MemoryCompilerConfig | None = None,
) -> DaemonApp:
    paths = resolve_runtime_paths(config)
    event_bus = EventBus()
    runtime_supervisor = RuntimeSupervisor(home=paths.home)
    # One containment source feeds both the policy and the file tool so the
    # advisory check and the execution-time re-check can never disagree.
    # On this runtime branch, the repo itself is always in scope so DAN can
    # actually work on the project instead of being trapped under ~/.dan.
    repo_root = Path(__file__).resolve().parents[2]
    approved_roots = [str(root) for root in config.security.approved_roots] or [
        str(paths.home),
        str(repo_root),
    ]
    shell_read_whitelist = [str(cmd) for cmd in config.security.shell_read_whitelist] or None
    tool_registry = create_default_tool_registry()
    tool_registry.register(FileReadTool(approved_roots=approved_roots))
    tool_registry.register(FileWriteTool(approved_roots=approved_roots))
    tool_registry.register(
        ShellReadTool(
            whitelist=shell_read_whitelist,
            approved_roots=approved_roots,
            unrestricted=config.security.shell_read_unrestricted,
        )
    )
    tool_registry.register(WebFetchTool())
    ui_reader = create_reader(config.security.ui_read_backend)
    tool_registry.register(UiActiveAppTool(ui_reader))
    tool_registry.register(UiReadWindowTool(ui_reader))
    ui_actor = create_actor(
        config.security.ui_act_backend or config.security.ui_read_backend
    )
    tool_registry.register(UiClickTool(ui_actor))
    tool_registry.register(UiTypeTool(ui_actor))
    tool_registry.register(UiFocusAppTool(ui_actor))
    # Captures are transient artifacts under the dand-owned runtime dir;
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
    # The startup policy fully reflects the TOML config. It is the SEED: each
    # turn overlays the panel's settings on top (see _live_tool_permission_policy),
    # so what the operator sets in the panel wins on the next turn without a
    # restart — the same live-settings pattern the model/effort picker uses.
    tool_permission_policy = ToolPermissionPolicy(
        destructive_tools_enabled=True,
        approved_roots=approved_roots,
        trusted_scopes=config.security.trusted_scopes,
        voice_auto_approve=True,
        auto_approve_mode="all",
        require_approval_for_shell=False,
        require_approval_for_file_write=False,
        require_approval_for_network=False,
        require_approval_for_ui=False,
        require_approval_for_terminal=False,
        require_approval_for_memory=False,
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
            memory_candidate_repository=None,
            memory_evidence_repository=None,
            memory_item_repository=None,
            memory_archive=None,
            voice_resolver=voice_resolver,
        )

    ensure_runtime_dirs(paths)
    api_token = ensure_api_token(paths.runtime_dir)
    initialized_conn = initialize_database(paths.db_path)
    close_quietly(initialized_conn)
    conn = _connect_daemon_db(paths.db_path)
    event_store = create_event_store(conn)
    intake_gate = IntakeGate(conn)
    state_machine = RuntimeStateMachine(event_store, event_bus=event_bus)
    # One registry instance is shared between the streaming brain adapters
    # (which register subprocess kill handles per turn, G4d) and the voice
    # cancellation coordinator (which fires them on barge-in, G4c).
    generation_registry = _build_generation_registry()
    brain_manager = BrainManager.from_config(
        config,
        generation_registry=generation_registry,
        state_path=paths.runtime_dir / "claude-session.json",
    )
    _restore_persisted_brain_adapter(conn, brain_manager)
    memory_manager = MemoryManager(conn, event_store=event_store)
    memory_candidate_repository = MemoryCandidateRepository(conn, event_store=event_store)
    memory_evidence_repository = MemoryEvidenceRepository(conn, event_store=event_store)
    memory_item_repository = MemoryItemRepository(conn, event_store=event_store)
    memory_archive = MemoryArchive(conn)
    operator_env_controls = compiled_memory_operator_env_controls()
    runtime_compiled_memory_force_disabled = (
        bool(compiled_memory_force_disabled) or operator_env_controls.force_disabled
    )
    runtime_compiled_memory_explicit_enabled = (
        compiled_memory_enabled
        if compiled_memory_enabled is not None
        else operator_env_controls.enabled
    )
    scoped_allow_list_supplied = compiled_memory_enabled_session_profiles is not None
    runtime_compiled_memory_scope_pairs = (
        tuple(compiled_memory_enabled_session_profiles)
        if scoped_allow_list_supplied
        else ()
    )
    runtime_compiled_memory_gate_enabled = _resolve_compiled_memory_enabled(
        config,
        explicit_enabled=runtime_compiled_memory_explicit_enabled,
        force_disabled=runtime_compiled_memory_force_disabled,
    )
    runtime_compiled_memory_enabled = (
        runtime_compiled_memory_gate_enabled
        and not scoped_allow_list_supplied
    )
    runtime_compiled_memory_config = compiled_memory_config or _compiled_memory_config_from_config(
        config
    )
    runtime_memory_compiler = memory_compiler
    if runtime_compiled_memory_gate_enabled and runtime_memory_compiler is None:
        runtime_memory_compiler = MemoryCompiler(memory_item_repository)
    # Registered here, not with the other tools above: memory_save needs the
    # DB-backed Memory OS repositories, so the uninitialized (no-DB) registry
    # never offers it.
    tool_registry.register(
        MemorySaveTool(
            candidate_repository=memory_candidate_repository,
            evidence_repository=memory_evidence_repository,
            item_repository=memory_item_repository,
        )
    )
    tool_registry.register(MemoryRecallTool(memory_archive))
    context_builder = ContextBuilder(
        conn,
        config=config,
        event_store=event_store,
        memory_manager=memory_manager,
        memory_compiler=runtime_memory_compiler,
        compiled_memory_enabled=runtime_compiled_memory_enabled,
        compiled_memory_scope_gate_enabled=runtime_compiled_memory_gate_enabled,
        compiled_memory_enabled_session_profiles=runtime_compiled_memory_scope_pairs,
        compiled_memory_force_disabled=runtime_compiled_memory_force_disabled,
        compiled_memory_config=runtime_compiled_memory_config,
        tool_specs=tool_registry.list_specs,
    )
    approval_gate = ApprovalGate(conn, event_store=event_store)
    tool_run_recorder = ToolRunRecorder(conn, event_store=event_store)
    # E2: the mock worker is the only registered worker; real provider
    # workers (codex/claude CLI) arrive in their own stage behind config.
    worker_broker = None
    return DaemonApp(
        config=config,
        paths=paths,
        conn=conn,
        event_store=event_store,
        intake_gate=intake_gate,
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
        memory_candidate_repository=memory_candidate_repository,
        memory_evidence_repository=memory_evidence_repository,
        memory_item_repository=memory_item_repository,
        memory_archive=memory_archive,
        worker_broker=worker_broker,
        voice_resolver=voice_resolver,
        voice_generation_registry=generation_registry,
        api_token=api_token,
    )


class _MemorySaveAwareTurnOrchestrator(TurnOrchestrator):
    def _capture_model_tool_calls(
        self,
        *,
        response: BrainResponse,
        turn_id: str,
        conversation_id: str,
        event_ids: list[int],
        correlation_id: str,
        loop_guard: Any = None,
    ) -> Any:
        return self._capture_model_tool_calls_with_memory_validation(
            response=response,
            turn_id=turn_id,
            conversation_id=conversation_id,
            event_ids=event_ids,
            correlation_id=correlation_id,
            loop_guard=loop_guard,
        )

    def _capture_model_tool_calls_with_memory_validation(
        self,
        *,
        response: BrainResponse,
        turn_id: str,
        conversation_id: str,
        event_ids: list[int],
        correlation_id: str,
        loop_guard: Any = None,
    ) -> Any:
        if not response.tool_calls:
            return super()._capture_model_tool_calls(
                response=response,
                turn_id=turn_id,
                conversation_id=conversation_id,
                event_ids=event_ids,
                correlation_id=correlation_id,
                loop_guard=loop_guard,
            )

        validation_errors = [
            self._memory_save_proposal_validation_error(tool_call)
            for tool_call in response.tool_calls
        ]
        if all(error is None for error in validation_errors):
            return super()._capture_model_tool_calls(
                response=response,
                turn_id=turn_id,
                conversation_id=conversation_id,
                event_ids=event_ids,
                correlation_id=correlation_id,
                loop_guard=loop_guard,
            )

        result = super()._capture_model_tool_calls(
            response=_response_with_tool_calls(response, []),
            turn_id=turn_id,
            conversation_id=conversation_id,
            event_ids=event_ids,
            correlation_id=correlation_id,
            loop_guard=loop_guard,
        )
        for index, (tool_call, validation_error) in enumerate(
            zip(response.tool_calls, validation_errors, strict=True),
            start=1,
        ):
            if validation_error is not None:
                result.tool_calls.append(
                    self._record_model_tool_call_failure(
                        call_id=_model_tool_call_id(tool_call, index),
                        tool_name=_model_tool_call_name(tool_call),
                        status="failed",
                        error=validation_error,
                        turn_id=turn_id,
                        conversation_id=conversation_id,
                        event_ids=event_ids,
                        correlation_id=correlation_id,
                    )
                )
                continue

            capture = super()._capture_model_tool_calls(
                response=_response_with_tool_calls(
                    response,
                    [_StableModelToolCall(tool_call, index)],
                ),
                turn_id=turn_id,
                conversation_id=conversation_id,
                event_ids=event_ids,
                correlation_id=correlation_id,
                loop_guard=loop_guard,
            )
            result.tool_calls.extend(capture.tool_calls)
            if getattr(capture, "loop_blocked", None):
                result.loop_blocked = capture.loop_blocked
                break
        return result

    def _memory_save_proposal_validation_error(self, tool_call: Any) -> str | None:
        if _model_tool_call_name(tool_call) != "memory_save":
            return None
        if self._tool_registry is None:
            return None
        try:
            tool = self._tool_registry.get("memory_save")
        except ToolRegistryError:
            return None
        if not isinstance(tool, MemorySaveTool):
            return None
        try:
            arguments = _json_safe_model_tool_arguments(tool_call)
        except ValueError:
            return None
        candidate_error = _memory_save_model_candidate_id_error(arguments)
        if candidate_error is not None:
            return candidate_error
        try:
            tool.validate_proposal_arguments(arguments)
        except ValueError as exc:
            return str(exc)
        return None


class _StableModelToolCall:
    def __init__(self, tool_call: Any, index: int) -> None:
        self.id = _model_tool_call_id(tool_call, index)
        self.name = _model_tool_call_name(tool_call)
        self.arguments = getattr(tool_call, "arguments", {})
        self.risk = getattr(tool_call, "risk", "safe_read")


def _response_with_tool_calls(response: BrainResponse, tool_calls: list[Any]) -> BrainResponse:
    return BrainResponse(
        text=response.text,
        tool_calls=tool_calls,
        model=response.model,
        usage=response.usage,
        raw_metadata=dict(response.raw_metadata),
    )


def _model_tool_call_id(tool_call: Any, index: int) -> str:
    value = getattr(tool_call, "id", None)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return f"model-tool-call-{index}"


def _model_tool_call_name(tool_call: Any) -> str:
    value = getattr(tool_call, "name", None)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return "unknown"


def _json_safe_model_tool_arguments(tool_call: Any) -> dict[str, Any]:
    arguments = getattr(tool_call, "arguments", {})
    if not isinstance(arguments, Mapping):
        raise ValueError("tool arguments must be a JSON object")
    try:
        json.dumps(arguments, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("tool arguments must be JSON serializable") from exc
    return dict(arguments)


class _MemorySaveProposalValidationError(ValueError):
    pass


def _memory_save_model_candidate_id_error(arguments: Mapping[str, Any]) -> str | None:
    if "candidate_id" in arguments:
        return "memory_save model proposal must not include candidate_id"
    return None


def _is_model_originated_source(source: RequestSource | str | None) -> bool:
    if source is None:
        return False
    try:
        return RequestSource(source) == RequestSource.MODEL_ORIGINATED
    except (TypeError, ValueError):
        return False


class _MemorySaveProposalApprovalGate:
    def __init__(
        self,
        approval_gate: ApprovalGate,
        memory_save_tool: MemorySaveTool,
        *,
        conversation_id: str | None = None,
        source: RequestSource | str | None = None,
    ) -> None:
        self._approval_gate = approval_gate
        self._memory_save_tool = memory_save_tool
        self._conversation_id = conversation_id
        self._source = source

    def with_conversation_id(self, conversation_id: str) -> "_MemorySaveProposalApprovalGate":
        return _MemorySaveProposalApprovalGate(
            self._approval_gate,
            self._memory_save_tool,
            conversation_id=conversation_id,
            source=self._source,
        )

    def create_approval(
        self,
        risk: str,
        requested_by: str,
        action_type: str,
        payload: Mapping[str, Any],
        metadata: Mapping[str, Any] | None = None,
        turn_id: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        payload_dict = dict(payload)
        metadata_dict = dict(metadata or {})
        arguments = payload_dict.get("arguments")
        if (
            payload_dict.get("tool_name") == "memory_save"
            and isinstance(arguments, Mapping)
            and _is_model_originated_source(self._source)
        ):
            candidate_error = _memory_save_model_candidate_id_error(arguments)
            if candidate_error is not None:
                raise _MemorySaveProposalValidationError(candidate_error)

        if (
            payload_dict.get("tool_name") == "memory_save"
            and isinstance(arguments, Mapping)
            and "candidate_id" not in arguments
        ):
            proposal = self._memory_save_tool.propose(
                arguments,
                source_type="explicit_memory_save",
                source_id=_first_non_empty_text(
                    metadata_dict.get("tool_call_id"),
                    metadata_dict.get("tool_request_id"),
                    payload_dict.get("source_id"),
                ),
                conversation_id=_first_non_empty_text(
                    payload_dict.get("conversation_id"),
                    metadata_dict.get("conversation_id"),
                    self._conversation_id,
                ),
                turn_id=_first_non_empty_text(
                    payload_dict.get("turn_id"),
                    metadata_dict.get("turn_id"),
                    turn_id,
                ),
                event_id=_first_int(
                    payload_dict.get("event_id"),
                    metadata_dict.get("event_id"),
                ),
            )
            arguments_with_candidate = dict(arguments)
            arguments_with_candidate["candidate_id"] = proposal["candidate_id"]
            payload_dict["arguments"] = arguments_with_candidate
            metadata_dict["memory_candidate_id"] = proposal["candidate_id"]
            metadata_dict["memory_evidence_id"] = proposal["evidence_id"]

        return self._approval_gate.create_approval(
            risk=risk,
            requested_by=requested_by,
            action_type=action_type,
            payload=payload_dict,
            metadata=metadata_dict,
            turn_id=turn_id,
            correlation_id=correlation_id,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._approval_gate, name)


def _memory_save_candidate_id_from_approval(approval: Mapping[str, Any] | None) -> str | None:
    if approval is None:
        return None
    payload = approval.get("payload")
    if not isinstance(payload, Mapping):
        return None
    if payload.get("tool_name") != "memory_save":
        return None
    arguments = payload.get("arguments")
    if not isinstance(arguments, Mapping):
        return None
    return _first_non_empty_text(arguments.get("candidate_id"))


def _first_non_empty_text(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _first_int(*values: Any) -> int | None:
    for value in values:
        if type(value) is int:
            return value
    return None


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


def _connect_daemon_db(path: Path) -> ThreadLocalConnection:
    """Open the daemon-owned connection for the threaded local HTTP server.

    Per-thread connections behind one facade (FIX-03): a single shared
    connection would make concurrent HTTP/worker threads share one implicit
    transaction, so one thread's rollback could silently discard another's
    append-only event.
    """

    return ThreadLocalConnection(path)


def _request_source_from_approval(approval: Mapping[str, Any]) -> RequestSource | None:
    """Restore the original request source stored in the approval payload.

    Legacy helper: it serves the `/approvals` execute path, which model tool
    calls no longer take — they run straight through the registry. Returning
    None here blocks nothing in the live turn flow, so do not cite this as a
    fail-closed guard (the "unknown source => blocked" rule it used to
    implement was never built; see docs/MACOS_PERMISSION_MODEL.md, which is
    marked unimplemented design).
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


def _bounded_voice_queue_limit(limit: int) -> int:
    if type(limit) is not int or limit <= 0:
        raise DaemonAppError("limit must be a positive integer.")
    if limit > 100:
        raise DaemonAppError("limit must be at most 100.")
    return limit


def _voice_queue_row(row: sqlite3.Row | tuple[Any, ...]) -> dict[str, Any]:
    metadata = _safe_json_mapping(row[10])
    text = str(row[4] or "")
    preview = str(redact_secrets(text)).replace("\n", " ").strip()
    if len(preview) > 160:
        preview = f"{preview[:160]}..."
    seq = metadata.get("seq")
    try:
        normalized_seq = int(seq) if seq is not None else None
    except (TypeError, ValueError):
        normalized_seq = None
    return {
        "id": str(row[0]),
        "created_at": str(row[1]),
        "updated_at": str(row[2]),
        "turn_id": str(row[3]) if row[3] else None,
        "status": str(row[8]),
        "kind": str(metadata.get("kind") or "sentence"),
        "seq": normalized_seq,
        "priority": int(row[5]),
        "voice_id": str(row[6]) if row[6] else None,
        "interrupt_policy": str(row[7]),
        "spoken_at": str(row[11]) if row[11] else None,
        # Playback telemetry: True only after the player confirmed audible
        # output. The panel uses it to tell "synthesized" from "played"
        # honestly instead of guessing green from 'done' alone (Task 10).
        "playback_confirmed": bool(row[12]),
        "error": str(redact_secrets(row[9])) if row[9] else None,
        "text_length": len(text),
        "text_preview": preview,
    }


def _safe_json_mapping(value: Any) -> dict[str, Any]:
    try:
        decoded = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return dict(decoded) if isinstance(decoded, Mapping) else {}


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
    "DANDaemon",
    "DANDaemonApp",
    "create_daemon_app",
    "create_daemon_app_from_config",
]
