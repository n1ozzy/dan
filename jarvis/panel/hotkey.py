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
    """

    def __init__(self, required_mask: int) -> None:
        self._required = required_mask
        self._held = False

    def update(self, flags: int) -> str | None:
        if self._required == 0:
            return None
        active = (flags & self._required) == self._required
        if active and not self._held:
            self._held = True
            return "down"
        if not active and self._held:
            self._held = False
            return "up"
        return None


def _urllib_poster(url: str, *, data: bytes, headers: dict[str, str]) -> None:
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=3) as response:  # noqa: S310 - localhost
        response.read()


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
    ) -> None:
        self._base = base_url.rstrip("/")
        self._token = token
        self._poster = poster

    def _post(self, path: str) -> None:
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["X-Jarvis-Token"] = self._token
        data = json.dumps({"source": PTT_SOURCE}).encode("utf-8")
        try:
            self._poster(f"{self._base}{path}", data=data, headers=headers)
        except Exception:  # noqa: BLE001 - a transport hiccup must not kill the panel
            logger.exception("PTT hotkey POST to %s failed; ignoring.", path)

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
    "parse_hotkey",
]
