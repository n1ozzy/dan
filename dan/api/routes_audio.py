"""Audio device route payloads (G1, CONTRACTS §9)."""

from __future__ import annotations

from typing import Any

from dan.daemon.app import DaemonApp


ROUTE_GROUP = "audio"


def get_audio_devices(app: DaemonApp) -> dict[str, Any]:
    state = app.get_audio_devices()
    return {
        "audio": {
            "enabled": bool(app.config.audio.enabled),
            "backend": app.config.audio.backend,
            "input_device": state.input_device,
            "output_device": state.output_device,
            "input_transport": state.input_transport,
            "output_transport": state.output_transport,
            "preferred_input": state.preferred_input,
            "output_policy": app.config.audio.output_policy,
            "allow_bluetooth_microphone": bool(
                app.config.audio.allow_bluetooth_microphone
            ),
            "warnings": list(state.warnings),
            "ts": state.ts,
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
    }


def register_routes(app: object) -> None:
    return None


__all__ = ["ROUTE_GROUP", "get_audio_devices", "register_routes"]
