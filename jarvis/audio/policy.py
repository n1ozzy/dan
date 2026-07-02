"""Audio device policy (CONTRACTS §9, ADR-012).

Pure functions: devices in, policy-applied state out. The policy never
mutates system audio settings — it decides which devices Jarvis will USE
(recording input) and describes the output it will play to.
"""

from __future__ import annotations

from typing import Any

from jarvis.audio.models import AudioDevice, AudioDeviceState


PREFERRED_INPUT = "Mikrofon (MacBook Air)"
BLUETOOTH_MICROPHONE_POLICY = "warn"


def apply_policy(
    devices: tuple[AudioDevice, ...] | list[AudioDevice],
    config: Any,
) -> AudioDeviceState:
    preferred_input = getattr(config, "preferred_input", PREFERRED_INPUT)
    allow_bt_mic = bool(getattr(config, "allow_bluetooth_microphone", False))
    warnings: list[str] = []

    inputs = [device for device in devices if device.is_input]
    outputs = [device for device in devices if device.is_output]

    # Input: pin_builtin_mic pins Jarvis recording to the preferred input
    # when it exists; the system default is only a fallback.
    selected_input: AudioDevice | None = None
    preferred = next((d for d in inputs if d.name == preferred_input), None)
    if preferred is not None:
        selected_input = preferred
    else:
        if inputs:
            warnings.append(
                f"Preferred input {preferred_input!r} not present; "
                "falling back to the system default input."
            )
        fallback = next((d for d in inputs if d.default_input), None)
        if fallback is None and inputs:
            fallback = inputs[0]
        selected_input = fallback

    if selected_input is not None and selected_input.transport == "bluetooth":
        if allow_bt_mic:
            warnings.append(
                f"Bluetooth microphone {selected_input.name!r} in use; "
                "expect degraded capture quality."
            )
        else:
            warnings.append(
                f"Bluetooth microphone {selected_input.name!r} is disabled by "
                "policy (allow_bluetooth_microphone = false); no usable input."
            )
            selected_input = None

    # Output follows the system default; a bluetooth speaker is fine.
    selected_output = next((d for d in outputs if d.default_output), None)
    if selected_output is None and outputs:
        selected_output = outputs[0]

    return AudioDeviceState(
        input_device=selected_input.name if selected_input else None,
        output_device=selected_output.name if selected_output else None,
        preferred_input=preferred_input,
        warnings=tuple(warnings),
        input_transport=selected_input.transport if selected_input else None,
        output_transport=selected_output.transport if selected_output else None,
        devices=tuple(devices),
    )


__all__ = ["BLUETOOTH_MICROPHONE_POLICY", "PREFERRED_INPUT", "apply_policy"]
