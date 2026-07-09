"""Global push-to-talk hotkey: pure logic for the panel's native shell.

The panel watches a held key combo anywhere on the desktop and drives the
daemon's PTT endpoints (source "global_hotkey"). The NSEvent monitor that
feeds this is a few lines in `menubar_app.py`; everything decidable lives
here so it can be tested without a keyboard or Accessibility permission:

  parse_hotkey("left_cmd+left_shift") -> a macOS device-modifier bitmask
  HotkeyEdgeDetector(mask).update(flags) -> "down" | "up" | None
  PttHotkeyClient(base, token).dispatch(edge) -> POST /voice/ptt/{down,up}

The bit values are the IOKit device-dependent modifier masks that appear in
the low bits of `NSEvent.modifierFlags()` on a flagsChanged event — they
distinguish left from right, which the generic NSEventModifierFlag* masks do
not.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from jarvis.logging import get_logger

logger = get_logger(__name__)

# IOKit NX_DEVICE*KEYMASK — low bits of NSEvent.modifierFlags(), side-aware.
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


def _urllib_poster(url: str, *, data: bytes, headers: dict[str, str]) -> None:
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=3) as response:  # noqa: S310 - localhost
            response.read()
    except urllib.error.HTTPError as exc:
        _close_http_error(exc)
        raise


def _urllib_health_checker(base_url: str) -> bool:
    request = urllib.request.Request(f"{base_url.rstrip('/')}/health", method="GET")
    try:
        with urllib.request.urlopen(request, timeout=1) as response:  # noqa: S310 - localhost
            response.read()
            return 200 <= int(response.status) < 300
    except urllib.error.HTTPError as exc:
        _close_http_error(exc)
        return False
    except Exception:
        return False


def _urllib_settings_getter(base_url: str, token: str | None) -> Any:
    headers: dict[str, str] = {}
    if token:
        headers["X-Jarvis-Token"] = token
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/settings", headers=headers, method="GET"
    )
    with urllib.request.urlopen(request, timeout=2) as response:  # noqa: S310 - localhost
        return json.loads(response.read().decode("utf-8"))


def fetch_effective_hotkey(
    base_url: str,
    token: str | None,
    *,
    getter: Callable[[str, str | None], Any] = _urllib_settings_getter,
) -> str | None:
    """Return the live `voice.ptt_hotkey` the daemon actually holds, or None.

    The panel UI writes the hotkey to the daemon's DB-backed settings (GET
    /settings, source "api"), NOT to the static TOML the shell loads at boot.
    Reading only the TOML would bind the native monitor to a stale/default
    combo while the user pressed the one they configured — the button (a
    WebView→HTTP call, combo-agnostic) would still work, the global hotkey
    would silently never fire. So we ask the daemon for the effective value
    and fall back to the config only when it is unreachable, missing, or the
    stored value is not a usable string.
    """

    try:
        payload = getter(base_url, token)
    except Exception as exc:  # noqa: BLE001 - a dead daemon must not crash panel boot
        logger.warning("Could not read effective PTT hotkey from daemon: %s", exc)
        return None
    settings = payload.get("settings") if isinstance(payload, dict) else None
    if not isinstance(settings, dict):
        return None
    value = settings.get("voice.ptt_hotkey")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def accessibility_trust_state(
    *,
    checker: Callable[[], bool] | None = None,
) -> str:
    """Return "trusted" | "untrusted" | "unknown" for macOS Accessibility.

    A global NSEvent monitor for key/flagsChanged events only receives events
    when the running process is trusted for Accessibility. Without that trust
    the global hotkey monitor is still installed but its handler is *never*
    called — the exact "the PTT button works, the global hotkey stays silent"
    failure: the button is a WebView→HTTP call and needs no permission, the
    hotkey rides the OS event stream and does.

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
    except Exception:  # noqa: BLE001 - a probe failure must not crash panel boot
        return "unknown"


def _close_http_error(exc: urllib.error.HTTPError) -> None:
    close = getattr(exc, "close", None)
    if callable(close):
        close()
        return
    fp = getattr(exc, "fp", None)
    if fp is not None:
        fp.close()


class PttHotkeyClient:
    """Posts PTT edges to the daemon, tolerating a dead daemon.

    The key handler must never crash the panel, so every transport error is
    logged and swallowed — a missed lease is recoverable, a dead panel is not.
    """

    def __init__(
        self,
        base_url: str,
        token: str | None,
        *,
        poster: Callable[..., Any] = _urllib_poster,
        health_checker: Callable[[], bool] | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._token = token
        self._poster = poster
        self._health_checker = health_checker or (
            lambda: _urllib_health_checker(self._base)
        )
        self._last_backend_available: bool | None = None
        self._pending: bool = False
        self._lock = threading.Lock()

    def _post(self, path: str) -> None:
        # Client-side lock: ignore new requests while one is in flight
        if not self._lock.acquire(blocking=False):
            logger.debug("PTT request dropped: previous request still in flight")
            return
        try:
            if not self._backend_available():
                return
            headers = {"Content-Type": "application/json"}
            if self._token:
                headers["X-Jarvis-Token"] = self._token
            data = json.dumps({"source": PTT_SOURCE}).encode("utf-8")
            try:
                self._poster(f"{self._base}{path}", data=data, headers=headers)
            except Exception as exc:  # noqa: BLE001 - a transport hiccup must not kill the panel
                logger.warning("PTT hotkey request skipped after %s failed: %s", path, exc)
                self._last_backend_available = False
        finally:
            self._lock.release()

    def _backend_available(self) -> bool:
        ok = bool(self._health_checker())
        if not ok:
            if self._last_backend_available is not False:
                logger.warning("PTT hotkey disabled: Jarvis backend is offline or unhealthy.")
            self._last_backend_available = False
            return False
        self._last_backend_available = True
        return True

    def down(self) -> None:
        self._post("/voice/ptt/down")

    def up(self) -> None:
        self._post("/voice/ptt/up")

    def dispatch(self, edge: str | None) -> None:
        if edge == "down":
            self.down()
        elif edge == "up":
            self.up()


__all__ = [
    "HotkeyEdgeDetector",
    "HotkeySpecError",
    "PttHotkeyClient",
    "PTT_SOURCE",
    "accessibility_trust_state",
    "fetch_effective_hotkey",
    "parse_hotkey",
]
