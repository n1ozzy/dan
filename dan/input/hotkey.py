"""Global push-to-talk hotkey: pure, OS-free decision logic.

Moved from `dan/panel/hotkey.py` (Release 1, Task 9): the daemon owns the one
global event tap, so the parsing/edge logic lives next to it. Everything
decidable is here so it can be tested without a keyboard or Accessibility
permission:

  parse_hotkey("left_cmd+left_shift") -> a macOS device-modifier bitmask
  HotkeyEdgeDetector(mask).update(flags) -> "down" | "up" | None

The bit values are the IOKit device-dependent modifier masks that appear in
the low bits of a flagsChanged event's modifier flags — they distinguish left
from right, which the generic NSEventModifierFlag* masks do not.
"""

from __future__ import annotations

from collections.abc import Callable

# IOKit NX_DEVICE*KEYMASK — low bits of the modifier flags, side-aware.
_TOKEN_BITS: dict[str, int] = {
    "left_ctrl": 0x00001,
    "right_ctrl": 0x02000,
    "left_shift": 0x00002,
    "right_shift": 0x00004,
    "left_cmd": 0x00008,
    "right_cmd": 0x00010,
    "left_option": 0x00020,
    "right_option": 0x00040,
    # aliases
    "left_alt": 0x00020,
    "right_alt": 0x00040,
}

PTT_SOURCE = "global_hotkey"

# Only the low 16 bits of the modifier flags carry the device-dependent
# (side-aware) masks; the high bits are the generic NSEventModifierFlag*.
DEVICE_MODIFIER_MASK = 0xFFFF


class HotkeySpecError(ValueError):
    """Raised when a hotkey spec names an unknown modifier token."""


def parse_hotkey(spec: str) -> int:
    """Turn "left_cmd+left_shift" into the OR of its device-modifier bits.

    Empty / whitespace-only spec returns 0, meaning "no global hotkey".
    """

    mask = 0
    for raw in spec.split("+"):
        token = raw.strip().lower()
        if not token:
            continue
        try:
            mask |= _TOKEN_BITS[token]
        except KeyError as exc:
            known = ", ".join(sorted(_TOKEN_BITS))
            raise HotkeySpecError(
                f"Unknown hotkey token {token!r}. Known: {known}."
            ) from exc
    return mask


class HotkeyEdgeDetector:
    """Edge-triggered detector over a stream of modifier-flag snapshots.

    `update(flags)` returns "down" the first poll where every required bit is
    present, "up" the first poll after any required bit drops, else None. A
    zero required-mask is disabled and never fires.

    Includes a cooldown after "up" to prevent stacking when tapping one key
    while holding another (e.g. hold cmd, tap shift repeatedly).
    """

    def __init__(self, required_mask: int) -> None:
        self._required = required_mask
        self._held = False
        self._last_down_time: float = 0.0
        self._last_up_time: float = 0.0
        # Minimum time between up and next down to avoid rapid re-trigger
        # when tapping a modifier while holding another.
        self._min_up_down_interval: float = 0.15  # 150ms

    def update(self, flags: int) -> str | None:
        if self._required == 0:
            return None
        import time
        now = time.monotonic()
        active = (flags & self._required) == self._required
        if active and not self._held:
            # Debounce: ignore duplicate down events within 150ms
            if now - self._last_down_time < 0.15:
                return None
            # Cooldown after up: prevent rapid down/up/down when tapping a key
            # while another required key is held.
            if now - self._last_up_time < self._min_up_down_interval:
                return None
            self._held = True
            self._last_down_time = now
            return "down"
        if not active and self._held:
            # No debounce on up — release immediately when keys drop
            self._held = False
            self._last_up_time = now
            return "up"
        return None


class PttActivationGate:
    """Grace window against accidental brushes while typing (Ozzy 2026-07-10).

    A "down" edge does NOT arm the mic immediately: a timer waits
    `grace_seconds`, and only if the combo is STILL held when it fires does
    `on_down` run. A release inside the grace cancels the pending timer — an
    accidental press+release never touches the mic. `on_up` runs only after a
    delivered `on_down`, so down/up stay paired. Lived in the panel before
    Task 9; now the daemon-owned monitor enforces it.
    """

    def __init__(
        self,
        *,
        grace_seconds: float,
        on_down: Callable[[], None],
        on_up: Callable[[], None],
        timer_factory: Callable[..., "object"] | None = None,
    ) -> None:
        import threading

        self._grace = max(0.0, float(grace_seconds))
        self._on_down = on_down
        self._on_up = on_up
        self._timer_factory = timer_factory or threading.Timer
        self._lock = threading.Lock()
        self._timer: object | None = None
        self._down_sent = False

    def edge(self, edge: str | None) -> None:
        if edge == "down":
            self._handle_down()
        elif edge == "up":
            self._handle_up()

    def _fire_down(self) -> None:
        with self._lock:
            self._timer = None
            self._down_sent = True
        self._on_down()

    def _handle_down(self) -> None:
        if self._grace <= 0:
            self._fire_down()
            return
        timer = self._timer_factory(self._grace, self._fire_down)
        daemon = getattr(timer, "daemon", None)
        if daemon is not None:
            timer.daemon = True
        with self._lock:
            self._down_sent = False
            self._timer = timer
        timer.start()

    def _handle_up(self) -> None:
        with self._lock:
            timer, self._timer = self._timer, None
            down_sent, self._down_sent = self._down_sent, False
        if timer is not None:
            timer.cancel()
        if down_sent:
            self._on_up()
        # released within the grace -> never armed -> send nothing


def accessibility_trust_state(
    *,
    checker: Callable[[], bool] | None = None,
) -> str:
    """Return "trusted" | "untrusted" | "unknown" for macOS Accessibility.

    A global event tap for key/flagsChanged events only receives events when
    the running process is trusted for Accessibility. Without that trust the
    tap is installed but its callback is *never* called — the exact "the PTT
    button works, the global hotkey stays silent" failure: the button is an
    HTTP call and needs no permission, the hotkey rides the OS event stream
    and does.

    We surface that state instead of hiding it. "unknown" means the AX API is
    unavailable (non-macOS, or the ApplicationServices framework isn't
    installed) so callers can print a generic hint rather than a false claim
    of being trusted or untrusted.
    """

    if checker is None:
        try:
            from ApplicationServices import AXIsProcessTrusted  # type: ignore
        except Exception:  # noqa: BLE001 - missing framework / non-macOS -> unknown
            return "unknown"
        checker = AXIsProcessTrusted
    try:
        return "trusted" if checker() else "untrusted"
    except Exception:  # noqa: BLE001 - a probe failure must not crash the caller
        return "unknown"


__all__ = [
    "DEVICE_MODIFIER_MASK",
    "HotkeyEdgeDetector",
    "HotkeySpecError",
    "PTT_SOURCE",
    "PttActivationGate",
    "accessibility_trust_state",
    "parse_hotkey",
]
