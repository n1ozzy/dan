"""AudioDeviceManager: read-only device observation + policy + snapshots.

CONTRACTS §9 / ADR-012: this manager OWNS device state — voice/STT
components never pick devices directly. It never mutates system audio:
"pin builtin mic" pins what DAN records FROM, "output follows system
default" describes what the broker will play TO. Backends follow the
established pattern: "native" reads real devices (system_profiler — an
Apple tool in a subprocess, same shape as the ADR-020 screencapture
bridge), "fake" is a deterministic fixture whose default input is a
BLUETOOTH microphone so every test and smoke proves the warning path.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import uuid
from collections.abc import Callable
from typing import Any

from dan.audio.models import AudioDevice, AudioDeviceState
from dan.audio.policy import apply_policy
from dan.events.types import EventType
from dan.store.repositories import utc_now_iso


SYSTEM_PROFILER = "/usr/sbin/system_profiler"
SYSTEM_PROFILER_TIMEOUT_SECONDS = 15

_TRANSPORT_MAP = {
    "coreaudio_device_type_builtin": "builtin",
    "coreaudio_device_type_bluetooth": "bluetooth",
    "coreaudio_device_type_usb": "usb",
}

# The fake fixture deliberately has ONLY a bluetooth default microphone, so
# every test and smoke that touches it proves the warning/fallback path.
FAKE_DEVICES: tuple[AudioDevice, ...] = (
    AudioDevice(
        name="Fake BT Headset",
        transport="bluetooth",
        is_input=True,
        default_input=True,
    ),
    AudioDevice(
        name="Głośniki (MacBook Air)",
        transport="builtin",
        is_output=True,
        default_output=True,
    ),
)


class AudioBackendError(Exception):
    """Raised when the audio backend is unknown or fails to observe."""


def parse_system_profiler_payload(payload: Any) -> tuple[AudioDevice, ...]:
    """Parse `system_profiler SPAudioDataType -json` output."""

    if not isinstance(payload, dict):
        return ()
    devices: list[AudioDevice] = []
    for section in payload.get("SPAudioDataType") or []:
        if not isinstance(section, dict):
            continue
        for item in section.get("_items") or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("_name") or "").strip()
            if not name:
                continue
            transport = _TRANSPORT_MAP.get(
                str(item.get("coreaudio_device_transport") or ""), "unknown"
            )
            is_input = bool(item.get("coreaudio_device_input"))
            is_output = bool(item.get("coreaudio_device_output"))
            if not is_input and not is_output:
                continue
            devices.append(
                AudioDevice(
                    name=name,
                    transport=transport,
                    is_input=is_input,
                    is_output=is_output,
                    default_input=item.get("coreaudio_default_audio_input_device")
                    == "spaudio_yes",
                    default_output=item.get("coreaudio_default_audio_output_device")
                    == "spaudio_yes",
                )
            )
    return tuple(devices)


class AudioDeviceManager:
    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        config: Any,
        event_store: Any | None = None,
        now: Callable[[], str] | None = None,
    ) -> None:
        backend = str(getattr(config, "backend", "native") or "native")
        if backend not in {"native", "fake"}:
            raise AudioBackendError(
                f"Unknown audio backend {backend!r}; expected 'native' or 'fake'."
            )
        self._conn = conn
        self._config = config
        self._backend = backend
        self._event_store = event_store
        self._now = now or utc_now_iso

    def current(self) -> AudioDeviceState:
        """Observe devices, apply policy, persist the snapshot on change."""

        devices = self._observe()
        state = apply_policy(devices, self._config)
        state = AudioDeviceState(
            input_device=state.input_device,
            output_device=state.output_device,
            preferred_input=state.preferred_input,
            warnings=state.warnings,
            ts=self._now(),
            input_transport=state.input_transport,
            output_transport=state.output_transport,
            devices=state.devices,
        )
        self._persist_if_changed(state)
        return state

    def _observe(self) -> tuple[AudioDevice, ...]:
        if self._backend == "fake":
            return FAKE_DEVICES
        try:
            completed = subprocess.run(
                [SYSTEM_PROFILER, "SPAudioDataType", "-json"],
                capture_output=True,
                timeout=SYSTEM_PROFILER_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AudioBackendError(f"system_profiler failed: {exc}") from exc
        if completed.returncode != 0:
            raise AudioBackendError(
                f"system_profiler exited {completed.returncode}: "
                f"{completed.stderr.decode('utf-8', 'replace')[:200]}"
            )
        try:
            payload = json.loads(completed.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AudioBackendError(f"system_profiler output unreadable: {exc}") from exc
        return parse_system_profiler_payload(payload)

    def _persist_if_changed(self, state: AudioDeviceState) -> None:
        fingerprint = {
            "input_device": state.input_device,
            "output_device": state.output_device,
            "preferred_input": state.preferred_input,
            "warnings": list(state.warnings),
            "input_transport": state.input_transport,
            "output_transport": state.output_transport,
        }
        last = self._conn.execute(
            "SELECT raw_json FROM audio_device_snapshots ORDER BY created_at DESC, id DESC LIMIT 1"
        ).fetchone()
        if last is not None:
            try:
                previous = json.loads(str(last[0])).get("fingerprint")
            except json.JSONDecodeError:
                previous = None
            if previous == fingerprint:
                return

        raw = {
            "fingerprint": fingerprint,
            "devices": [
                {
                    "name": device.name,
                    "transport": device.transport,
                    "is_input": device.is_input,
                    "is_output": device.is_output,
                    "default_input": device.default_input,
                    "default_output": device.default_output,
                }
                for device in state.devices
            ],
        }
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO audio_device_snapshots (
                  id, created_at, input_device_name, input_device_uid,
                  output_device_name, output_device_uid, preferred_input,
                  output_policy, bluetooth_microphone_allowed, warning, raw_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uuid.uuid4().hex,
                    state.ts or self._now(),
                    state.input_device,
                    None,
                    state.output_device,
                    None,
                    state.preferred_input,
                    str(getattr(self._config, "output_policy", "follow_system_default")),
                    1 if getattr(self._config, "allow_bluetooth_microphone", False) else 0,
                    "; ".join(state.warnings) or None,
                    json.dumps(raw, ensure_ascii=False, sort_keys=True),
                ),
            )
        if self._event_store is not None:
            self._event_store.append(
                EventType.AUDIO_DEVICES_SNAPSHOT,
                "audio",
                {
                    "input_device": state.input_device,
                    "output_device": state.output_device,
                    "warnings": list(state.warnings),
                },
            )


__all__ = [
    "AudioBackendError",
    "AudioDeviceManager",
    "FAKE_DEVICES",
    "parse_system_profiler_payload",
]
