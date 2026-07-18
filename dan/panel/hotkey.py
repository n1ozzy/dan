"""Panel-side hotkey helpers: display state only, never observe keys.

Task 9 moved the global PTT hotkey into the daemon (`dan/input/hotkey.py` +
`dan/input/macos_event_tap.py`): dand owns the one CGEventTap on the machine.
The panel no longer installs any NSEvent global monitor and no longer posts
PTT edges itself — it may only *display* hotkey state (spec, Accessibility
trust) and let the operator press the in-panel PTT button, which POSTs a
manual PTT intent from the web UI.

The pure parsing/edge logic is re-exported from its new home so existing
imports (config registry, settings validation) keep working.
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable
from typing import Any

from dan.input.hotkey import (  # noqa: F401 - re-exported for panel-side callers
    HotkeyEdgeDetector,
    HotkeySpecError,
    PTT_SOURCE,
    accessibility_trust_state,
    parse_hotkey,
)
from dan.logging import get_logger

logger = get_logger(__name__)


def _urllib_settings_getter(base_url: str, token: str | None) -> Any:
    headers: dict[str, str] = {}
    if token:
        headers["X-DAN-Token"] = token
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
    The panel only *shows* this value now — the daemon's own monitor binds to
    it — but showing a stale combo would still mislead the operator, so we ask
    the daemon for the effective value and fall back to the config only when
    it is unreachable, missing, or the stored value is not a usable string.
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


__all__ = [
    "HotkeyEdgeDetector",
    "HotkeySpecError",
    "PTT_SOURCE",
    "accessibility_trust_state",
    "fetch_effective_hotkey",
    "parse_hotkey",
]
