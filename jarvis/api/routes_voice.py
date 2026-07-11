"""Voice listening/PTT route payloads (G2, CONTRACTS §8, PANEL_CONTRACT §2)."""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jarvis.api.routes_runtime import CANONICAL_PTT_MODES
from jarvis.daemon.app import DaemonApp
from jarvis.security.redaction import redact_secrets
from jarvis.voice.listening import ALLOWED_SOURCES
from jarvis.voice.models import ListeningLease
from jarvis.voice.tts import BANNED_ENGINES, RESERVED_ENGINES


ROUTE_GROUP = "voice"
READINESS_OK = "ok"
READINESS_MISSING = "missing"
READINESS_INVALID = "invalid"
READINESS_UNKNOWN = "unknown"
_READINESS_ORDER = {
    READINESS_INVALID: 0,
    READINESS_MISSING: 1,
    READINESS_UNKNOWN: 2,
    READINESS_OK: 3,
}


class VoiceDisabledError(Exception):
    """Raised when a voice mutation arrives while voice is disabled."""


class VoiceRequestValidationError(ValueError):
    """Raised when a voice API payload is invalid."""


def _lease_payload(lease: ListeningLease) -> dict[str, Any]:
    return {
        "id": lease.id,
        "mode": lease.mode,
        "source": lease.source,
        "status": lease.status,
        "created_at": lease.created_at,
        "expires_at": lease.expires_at,
        "released_at": lease.released_at,
    }


def _require_voice_enabled(app: DaemonApp) -> None:
    if not app.config.voice.enabled:
        raise VoiceDisabledError("Voice is disabled ([voice].enabled = false).")


def _source_from_request(payload: Any, default: str) -> str:
    if payload is None:
        return default
    if not isinstance(payload, Mapping):
        raise VoiceRequestValidationError("Request JSON must be an object.")
    source = payload.get("source", default)
    if not isinstance(source, str) or not source.strip():
        raise VoiceRequestValidationError("source must be a non-empty string.")
    return source.strip()


def _validated_source_from_request(payload: Any, default: str) -> str:
    source = _source_from_request(payload, default)
    if source not in ALLOWED_SOURCES:
        raise VoiceRequestValidationError(f"Unknown listening source {source!r}.")
    return source


def post_ptt_down(app: DaemonApp, request_payload: Any) -> dict[str, Any]:
    _require_voice_enabled(app)
    source = _validated_source_from_request(request_payload, "ptt")
    lease = app.acquire_listening_lease(mode="hold", source=source)
    return {"ok": True, "lease": _lease_payload(lease)}


def post_ptt_up(app: DaemonApp, request_payload: Any) -> dict[str, Any]:
    _require_voice_enabled(app)
    released = app.release_listening_leases(mode="hold")
    return {"ok": True, "released": len(released)}


def post_listen_lock(app: DaemonApp, request_payload: Any) -> dict[str, Any]:
    _require_voice_enabled(app)
    source = _source_from_request(request_payload, "lock")
    lease = app.acquire_listening_lease(mode="locked", source=source)
    return {"ok": True, "lease": _lease_payload(lease)}


def post_listen_unlock(app: DaemonApp, request_payload: Any) -> dict[str, Any]:
    _require_voice_enabled(app)
    released = app.release_listening_leases(mode="locked")
    return {"ok": True, "released": len(released)}


def get_listening(app: DaemonApp) -> dict[str, Any]:
    leases = app.active_listening_leases()
    return {
        "listening": bool(leases),
        "voice_enabled": bool(app.config.voice.enabled),
        "leases": [_lease_payload(lease) for lease in leases],
    }


def get_voice_queue(app: DaemonApp, *, limit: int = 20) -> dict[str, Any]:
    if type(limit) is not int or limit <= 0 or limit > 100:
        raise VoiceRequestValidationError("limit must be an integer between 1 and 100.")
    return {"voice_queue": app.list_voice_queue(limit=limit), "limit": limit}


def get_voice_runtime(app: DaemonApp) -> dict[str, Any]:
    """Read-only, safe Voice Runtime projection for the panel.

    This deliberately probes only cheap local facts: configured names, already
    constructed daemon objects, PATH/package/path existence, recent events and
    queue rows. It never loads models, starts the microphone/speaker, calls a
    provider, or prints credential values.
    """

    audio_state = _safe_audio_state(app)
    queue_rows = _safe_voice_queue(app)
    latest_events = _safe_latest_events(app)
    voice_error = _latest_safe_error(latest_events, ("voice", "audio", "speech", "tts", "stt"))
    groups = _voice_runtime_groups(app, audio_state, queue_rows, voice_error)
    warnings = _voice_runtime_warnings(app, groups)
    return {
        "voice_runtime": {
            "read_only": True,
            "voice_enabled": bool(app.config.voice.enabled),
            "default_tts": _safe_value(app.config.voice.default_tts),
            "default_stt": _safe_value(app.config.voice.default_stt),
            "groups": groups,
            "warnings": warnings,
            "latest_safe_error": voice_error,
            "cannot_probe_safely": [
                "no network/internet check without an existing network tool surface",
                "no model loading for STT/TTS readiness",
                "no microphone activation or recorder start",
                "no speaker playback",
                "no API credential value exposure",
                "macOS TCC permission is reported only through existing audio state if present",
            ],
        }
    }


def _voice_runtime_groups(
    app: DaemonApp,
    audio_state: Any,
    queue_rows: list[dict[str, Any]],
    voice_error: str | None,
) -> dict[str, dict[str, Any]]:
    voice = app.config.voice
    audio = app.config.audio
    tts_probe = _tts_readiness(app)
    stt_probe = _stt_readiness(app)
    recorder_probe = _recorder_readiness(app)
    playback_probe = _playback_readiness(app)
    active_leases = app.active_listening_leases()
    queue_summary = _queue_summary(queue_rows)
    cancellation_reason = _latest_queue_value(
        queue_rows,
        ("cancellation_reason", "cancel_reason", "interruption_reason"),
    )

    return {
        "capture_input": _runtime_group(
            "Capture/Input",
            configured={
                "recorder": voice.recorder,
                "recorder_binary": voice.recorder_binary or "PATH:sox",
                "audio_backend": audio.backend,
                "input_policy": audio.input_policy,
                "preferred_input": audio.preferred_input,
                "allow_bluetooth_microphone": bool(audio.allow_bluetooth_microphone),
            },
            effective={
                "recorder": _component_name(app.voice_recorder),
                "listening": bool(active_leases),
                "input_device": getattr(audio_state, "input_device", None),
                "input_transport": getattr(audio_state, "input_transport", None),
            },
            readiness=_worst_readiness(
                recorder_probe["readiness"],
                _input_device_readiness(app, audio_state),
            ),
            dependency_status=_join_status(
                recorder_probe["dependency_status"],
                _input_device_dependency(app, audio_state),
            ),
            latest_safe_error=voice_error,
            warnings=_capture_warnings(app, audio_state, recorder_probe),
        ),
        "stt_transcription": _runtime_group(
            "STT/Transcription",
            configured={
                "default_stt": voice.default_stt,
                "model": voice.stt_model,
                "language": voice.stt_language,
                "timeout_seconds": voice.stt_timeout_seconds,
            },
            effective={
                "engine": _component_engine_name(app.voice_stt),
                "pipeline": _component_name(app.voice_stt),
            },
            readiness=stt_probe["readiness"],
            dependency_status=stt_probe["dependency_status"],
            latest_safe_error=voice_error,
            warnings=stt_probe["warnings"],
        ),
        "endpointing_vad_ptt": _runtime_group(
            "Endpointing/VAD/PTT",
            configured={
                "ptt_mode": voice.ptt_mode,
                "ptt_hotkey": "configured" if voice.ptt_hotkey else "not configured",
                "stt_min_rms": voice.stt_min_rms,
                "stt_min_voiced_seconds": voice.stt_min_voiced_seconds,
                "stt_min_voiced_ratio": voice.stt_min_voiced_ratio,
                "lease_ttl_seconds": voice.listen_lock_ttl_seconds,
            },
            effective={
                "active_leases": len(active_leases),
                "lease_modes": sorted({lease.mode for lease in active_leases}),
                "lease_sources": sorted({lease.source for lease in active_leases}),
            },
            readiness=READINESS_OK if voice.ptt_mode in CANONICAL_PTT_MODES else READINESS_INVALID,
            dependency_status="daemon lease state only; no microphone activation",
            latest_safe_error=voice_error,
            warnings=[] if voice.ptt_mode in CANONICAL_PTT_MODES else ["invalid PTT mode"],
        ),
        "turn_manager": _runtime_group(
            "Turn Manager",
            configured={
                "transcript_turn_retry_seconds": voice.transcript_turn_retry_seconds,
                "anti_echo_window_seconds": voice.anti_echo_window_seconds,
                "anti_echo_overlap_threshold": voice.anti_echo_overlap_threshold,
            },
            effective={
                "voice_gateway": _component_name(app.voice_gateway),
                "current_voice_conversation_id": getattr(app, "_voice_conversation_id", None),
            },
            readiness=READINESS_OK if (not voice.enabled or app.voice_gateway is not None) else READINESS_MISSING,
            dependency_status="turn gateway present" if app.voice_gateway is not None else "not built",
            latest_safe_error=voice_error,
            warnings=[] if (not voice.enabled or app.voice_gateway is not None) else ["voice enabled but turn gateway missing"],
        ),
        "tts_voice_model": _runtime_group(
            "TTS/Voice Model",
            configured={
                "default_tts": voice.default_tts,
                "voice_id": voice.supertonic_voice,
                "voice_model": voice.supertonic_voice,
                "voice_profile": voice.supertonic_voice,
                "language": voice.supertonic_lang,
                "speed": voice.supertonic_speed,
                "steps": voice.supertonic_steps,
                "speak_responses": bool(voice.speak_responses),
            },
            effective={
                "engine": _effective_tts_engine(app),
                "voice_id": voice.supertonic_voice if _effective_tts_engine(app) else None,
            },
            readiness=tts_probe["readiness"],
            dependency_status=tts_probe["dependency_status"],
            latest_safe_error=voice_error,
            warnings=tts_probe["warnings"],
        ),
        "playback": _runtime_group(
            "Playback",
            configured={
                "playback_binary": voice.playback_binary,
                "output_policy": audio.output_policy,
                "broker_enabled": bool(voice.broker_enabled),
                "speak_responses": bool(voice.speak_responses),
            },
            effective={
                "broker": _component_name(app.voice_broker),
                "output_device": getattr(audio_state, "output_device", None),
                "output_transport": getattr(audio_state, "output_transport", None),
            },
            readiness=playback_probe["readiness"],
            dependency_status=playback_probe["dependency_status"],
            latest_safe_error=voice_error,
            warnings=playback_probe["warnings"],
        ),
        "queue_barge_in": _runtime_group(
            "Queue/Barge-in",
            configured={
                "queue_persisted": bool(voice.queue_persisted),
                "broker_enabled": bool(voice.broker_enabled),
                "speak_responses": bool(voice.speak_responses),
            },
            effective={
                "queue_counts": queue_summary,
                "cancellation_reason": cancellation_reason,
                "cancellation_coordinator": _component_name(app.voice_cancellation),
            },
            readiness=READINESS_OK,
            dependency_status="queue readable; cancellation coordinator "
            + ("present" if app.voice_cancellation is not None else "not built"),
            latest_safe_error=voice_error,
            warnings=_queue_warnings(app, queue_rows),
        ),
        "voice_errors": _runtime_group(
            "Voice Errors",
            configured={
                "voice_enabled": bool(voice.enabled),
                "default_tts": voice.default_tts,
                "default_stt": voice.default_stt,
            },
            effective={
                "latest_safe_error": voice_error,
            },
            readiness=READINESS_INVALID if voice_error else READINESS_OK,
            dependency_status="latest events inspected",
            latest_safe_error=voice_error,
            warnings=[voice_error] if voice_error else [],
        ),
    }


def _runtime_group(
    label: str,
    *,
    configured: Mapping[str, Any],
    effective: Mapping[str, Any],
    readiness: str,
    dependency_status: str,
    latest_safe_error: str | None,
    warnings: list[str],
) -> dict[str, Any]:
    return {
        "label": label,
        "configured": _safe_mapping(configured),
        "effective": _safe_mapping(effective),
        "readiness": readiness if readiness in _READINESS_ORDER else READINESS_UNKNOWN,
        "dependency_status": _safe_value(dependency_status),
        "latest_safe_error": _safe_value(latest_safe_error),
        "warnings": [_safe_value(item) for item in warnings if item],
    }


def _tts_readiness(app: DaemonApp) -> dict[str, Any]:
    voice = app.config.voice
    engine = str(voice.default_tts or "").strip().lower()
    warnings: list[str] = []
    if not engine:
        return _probe(READINESS_MISSING, "TTS engine not configured", warnings)
    if engine in BANNED_ENGINES:
        return _probe(READINESS_INVALID, f"TTS engine {engine!r} is banned", [f"TTS engine {engine} is invalid"])
    if engine in RESERVED_ENGINES:
        return _probe(READINESS_MISSING, f"TTS engine {engine!r} is reserved/not implemented", [RESERVED_ENGINES[engine]])
    if engine == "mock":
        return _probe(READINESS_OK, "mock TTS is built in", warnings)
    if engine == "supertonic":
        binary = _probe_executable(
            explicit=str(voice.supertonic_binary or ""),
            fallbacks=(str(Path(sys.executable).parent / "supertonic"), "supertonic"),
            label="supertonic binary",
        )
        playback = _playback_readiness(app)
        readiness = _worst_readiness(binary["readiness"], playback["readiness"])
        if not voice.supertonic_voice:
            readiness = _worst_readiness(readiness, READINESS_MISSING)
            warnings.append("TTS selected but voice_id/model missing")
        if app.config.voice.enabled and readiness != READINESS_OK:
            warnings.append("voice enabled but TTS engine unavailable")
        return _probe(
            readiness,
            _join_status(binary["dependency_status"], playback["dependency_status"]),
            warnings,
        )
    return _probe(READINESS_INVALID, f"unknown TTS engine {engine!r}", [f"unknown TTS engine {engine}"])


def _stt_readiness(app: DaemonApp) -> dict[str, Any]:
    voice = app.config.voice
    engine = str(voice.default_stt or "").strip().lower().replace("-", "_")
    warnings: list[str] = []
    if not engine:
        return _probe(READINESS_MISSING, "STT engine not configured", warnings)
    if engine == "mock":
        return _probe(READINESS_OK, "mock STT is built in", warnings)
    if engine == "mlx_whisper":
        package = _probe_python_package("mlx_whisper")
        model = _probe_model_reference(str(voice.stt_model or ""))
        readiness = _worst_readiness(package["readiness"], model["readiness"])
        if model["readiness"] == READINESS_MISSING:
            warnings.append("STT selected but local model/runtime path missing")
        if app.config.voice.enabled and readiness != READINESS_OK:
            warnings.append("voice enabled but STT engine unavailable")
        return _probe(
            readiness,
            _join_status(package["dependency_status"], model["dependency_status"]),
            warnings,
        )
    return _probe(READINESS_INVALID, f"unknown STT engine {engine!r}", [f"unknown STT engine {engine}"])


def _recorder_readiness(app: DaemonApp) -> dict[str, Any]:
    backend = str(app.config.voice.recorder or "").strip().lower()
    if not backend:
        return _probe(READINESS_MISSING, "recorder backend not configured", [])
    if backend == "mock":
        return _probe(READINESS_OK, "mock recorder is built in", [])
    if backend == "sox":
        probe = _probe_executable(
            explicit=str(app.config.voice.recorder_binary or ""),
            fallbacks=("sox",),
            label="sox recorder binary",
        )
        warnings = []
        if app.config.voice.enabled and probe["readiness"] != READINESS_OK:
            warnings.append("voice enabled but recorder command unavailable")
        return _probe(probe["readiness"], probe["dependency_status"], warnings)
    return _probe(READINESS_INVALID, f"unknown recorder backend {backend!r}", [f"unknown recorder backend {backend}"])


def _playback_readiness(app: DaemonApp) -> dict[str, Any]:
    playback_binary = str(app.config.voice.playback_binary or "").strip()
    probe = _probe_executable(
        explicit=playback_binary,
        fallbacks=(),
        label="playback binary",
    )
    warnings = []
    if app.config.voice.enabled and app.config.voice.speak_responses and probe["readiness"] != READINESS_OK:
        warnings.append("voice enabled but playback engine unavailable")
    return _probe(probe["readiness"], probe["dependency_status"], warnings)


def _probe(readiness: str, dependency_status: str, warnings: list[str]) -> dict[str, Any]:
    return {
        "readiness": readiness,
        "dependency_status": dependency_status,
        "warnings": warnings,
    }


def _probe_executable(*, explicit: str, fallbacks: tuple[str, ...], label: str) -> dict[str, str]:
    candidates = (explicit,) if explicit else fallbacks
    if not candidates:
        return {"readiness": READINESS_UNKNOWN, "dependency_status": f"{label} not configured"}
    for candidate in candidates:
        if not candidate:
            continue
        resolved = _resolve_executable(candidate)
        if resolved is not None:
            return {"readiness": READINESS_OK, "dependency_status": f"{label} present"}
    return {"readiness": READINESS_MISSING, "dependency_status": f"{label} missing"}


def _resolve_executable(candidate: str) -> Path | None:
    text = str(candidate or "").strip()
    if not text:
        return None
    if "/" not in text:
        found = shutil.which(text)
        return Path(found) if found else None
    path = Path(os.path.expanduser(text))
    return path if path.is_file() and os.access(path, os.X_OK) else None


def _probe_python_package(package: str) -> dict[str, str]:
    return {
        "readiness": READINESS_OK if importlib.util.find_spec(package) is not None else READINESS_MISSING,
        "dependency_status": f"python package {package} "
        + ("present" if importlib.util.find_spec(package) is not None else "missing"),
    }


def _probe_model_reference(model: str) -> dict[str, str]:
    if not model:
        return {"readiness": READINESS_MISSING, "dependency_status": "model not configured"}
    if _looks_like_local_path(model):
        path = Path(os.path.expanduser(model))
        return {
            "readiness": READINESS_OK if path.exists() else READINESS_MISSING,
            "dependency_status": "local model path present" if path.exists() else "local model path missing",
        }
    return {
        "readiness": READINESS_UNKNOWN,
        "dependency_status": "model reference is not a local path; not loaded/probed",
    }


def _looks_like_local_path(value: str) -> bool:
    return value.startswith(("/", "~", ".")) or "/" in value and not value.count("/") == 1


def _input_device_readiness(app: DaemonApp, audio_state: Any) -> str:
    if str(app.config.voice.recorder or "").strip().lower() == "mock":
        return READINESS_OK
    if getattr(audio_state, "input_device", None):
        return READINESS_OK
    return READINESS_MISSING if app.config.voice.enabled else READINESS_UNKNOWN


def _input_device_dependency(app: DaemonApp, audio_state: Any) -> str:
    if str(app.config.voice.recorder or "").strip().lower() == "mock":
        return "input device not required by mock recorder"
    if getattr(audio_state, "input_device", None):
        return "input device selected by audio policy"
    return "input device unavailable through current audio state"


def _capture_warnings(app: DaemonApp, audio_state: Any, recorder_probe: Mapping[str, Any]) -> list[str]:
    warnings = list(recorder_probe.get("warnings") or [])
    if app.config.voice.enabled and not getattr(audio_state, "input_device", None) and app.config.voice.recorder != "mock":
        warnings.append("voice enabled but no usable input device exposed")
    for warning in getattr(audio_state, "warnings", ()) or ():
        warnings.append(str(warning))
    return warnings


def _queue_warnings(app: DaemonApp, queue_rows: list[dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    if app.config.voice.speak_responses and not app.config.voice.broker_enabled:
        warnings.append("speak responses enabled but voice broker disabled")
    stuck = [
        row for row in queue_rows
        if str(row.get("status", "")).lower() in {"pending", "playing", "active"}
    ]
    if len(stuck) > 20:
        warnings.append("voice queue has many active/pending rows")
    return warnings


def _voice_runtime_warnings(app: DaemonApp, groups: Mapping[str, Mapping[str, Any]]) -> list[str]:
    warnings: list[str] = []
    if not app.config.voice.enabled:
        return warnings
    for group in groups.values():
        for warning in group.get("warnings", []) or []:
            if warning not in warnings:
                warnings.append(str(warning))
        readiness = group.get("readiness")
        label = group.get("label", "voice runtime")
        if readiness in {READINESS_MISSING, READINESS_INVALID}:
            message = f"{label}: readiness {readiness}"
            if message not in warnings:
                warnings.append(message)
    return warnings


def _worst_readiness(*values: str) -> str:
    normalized = [
        value if value in _READINESS_ORDER else READINESS_UNKNOWN
        for value in values
        if value
    ]
    if not normalized:
        return READINESS_UNKNOWN
    return min(normalized, key=lambda item: _READINESS_ORDER[item])


def _join_status(*parts: str | None) -> str:
    return "; ".join(str(part) for part in parts if part)


def _component_name(component: Any) -> str | None:
    if component is None:
        return None
    return str(getattr(component, "name", None) or component.__class__.__name__)


def _component_engine_name(component: Any) -> str | None:
    if component is None:
        return None
    for attr in ("engine", "_engine", "tts_engine", "_tts_engine", "stt_engine", "_stt_engine"):
        child = getattr(component, attr, None)
        if child is not None:
            return _component_name(child)
    return _component_name(component)


def _effective_tts_engine(app: DaemonApp) -> str | None:
    return (
        _component_engine_name(app.voice_broker)
        or _component_engine_name(app.voice_cancellation)
        or _component_name(app.voice_broker)
        or _component_name(app.voice_cancellation)
    )


def _safe_audio_state(app: DaemonApp) -> Any:
    try:
        return app.get_audio_devices()
    except Exception:  # noqa: BLE001 - diagnostic projection must not break boot/API
        return None


def _safe_voice_queue(app: DaemonApp) -> list[dict[str, Any]]:
    try:
        rows = app.list_voice_queue(limit=100)
    except Exception:  # noqa: BLE001
        return []
    return [dict(row) for row in rows if isinstance(row, Mapping)]


def _safe_latest_events(app: DaemonApp) -> list[Mapping[str, Any]]:
    try:
        events = app.list_latest_events(limit=50)
    except Exception:  # noqa: BLE001
        return []
    normalized: list[Mapping[str, Any]] = []
    for event in events:
        if isinstance(event, Mapping):
            normalized.append(event)
            continue
        normalized.append(
            {
                "id": getattr(event, "id", 0),
                "type": getattr(event, "type", ""),
                "payload": getattr(event, "payload", {}),
            }
        )
    return normalized


def _latest_safe_error(events: list[Mapping[str, Any]], families: tuple[str, ...]) -> str | None:
    for event in sorted(events, key=lambda item: int(item.get("id", 0) or 0), reverse=True):
        event_type = str(event.get("type") or "").lower()
        if not any(family in event_type for family in families):
            continue
        payload = event.get("payload")
        payload = payload if isinstance(payload, Mapping) else {}
        status = str(payload.get("status") or "").lower()
        error = payload.get("error") or payload.get("message") or payload.get("reason")
        if "error" in event_type or "failed" in event_type or status == "failed" or error:
            summary = f"{event.get('type')}: {error or status or 'failed'}"
            return _safe_value(summary)
    return None


def _queue_summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        status = str(row.get("status") or "unknown")
        kind = str(row.get("kind") or "sentence")
        key = f"{kind}/{status}"
        counts[key] = counts.get(key, 0) + 1
    return counts


def _latest_queue_value(rows: list[dict[str, Any]], keys: tuple[str, ...]) -> str | None:
    for row in rows:
        for key in keys:
            value = row.get(key)
            if value not in (None, ""):
                return _safe_value(value)
    return None


def _safe_mapping(values: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): _safe_value(value) for key, value in values.items()}


def _safe_value(value: Any) -> Any:
    if value is None or value == "":
        return None
    if isinstance(value, Mapping):
        return _safe_mapping(value)
    if isinstance(value, (list, tuple, set)):
        return [_safe_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)):
        return redact_secrets(value) if isinstance(value, str) else value
    return str(type(value).__name__)


def register_routes(app: object) -> None:
    return None


__all__ = [
    "ROUTE_GROUP",
    "VoiceDisabledError",
    "VoiceRequestValidationError",
    "get_listening",
    "get_voice_queue",
    "get_voice_runtime",
    "post_listen_lock",
    "post_listen_unlock",
    "post_ptt_down",
    "post_ptt_up",
    "register_routes",
]
