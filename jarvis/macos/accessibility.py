"""Read-only Accessibility adapter (FAZA D1, docs/MASTER_PLAN.md).

Approved surfaces in D1 are deliberately narrow: the frontmost application
and its focused window. Nothing here enumerates other apps, other windows or
the full UI tree of the system.

Safety model (mirrors the file tools' defense in depth):
- PermissionPolicy decides first (`ui_read` row of the source matrix).
- Every backend result passes `sanitize_window_snapshot` at the tool layer:
  secure text field values are stripped, element counts and text lengths are
  clipped, output stays JSON-safe. A buggy backend cannot leak a password.
- Results flow into tool_runs/events where secret redaction applies as well.

Backends:
- `ax` — real AXUIElement reads via ctypes (jarvis/macos/ax_backend.py).
  Requires the Accessibility TCC grant; see docs/runbooks/ACCESSIBILITY_TCC.md.
- `fake` — deterministic fixture for tests and the smoke harness; announces
  itself in every payload via ``backend: "fake"``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

MAX_ELEMENTS = 120
MAX_TEXT_CHARS = 512

SECURE_ROLE = "AXSecureTextField"


class AccessibilityError(Exception):
    """Raised when a backend cannot observe the UI (no TCC grant, no window)."""


class AccessibilityReader:
    """Backend interface: raw, unsanitized snapshots of the approved surfaces."""

    backend = "abstract"

    def active_app(self) -> Mapping[str, Any]:
        raise NotImplementedError

    def focused_window(self) -> Mapping[str, Any]:
        raise NotImplementedError


_DEFAULT_FIXTURE_APP = {
    "app_name": "FakePad",
    "bundle_id": "com.jarvis.fakepad",
    "pid": 4242,
}

_DEFAULT_FIXTURE_WINDOW = {
    "app_name": "FakePad",
    "title": "Fake Window — Jarvis ui_read smoke",
    "elements": [
        {"role": "AXStaticText", "label": None, "value": "Hello from the fake UI"},
        {"role": "AXTextField", "label": "Login", "value": "ozzy"},
        {
            "role": "AXTextField",
            "subrole": SECURE_ROLE,
            "label": "Password",
            "value": "fake-secure-value",
            "secure": True,
        },
        {"role": "AXButton", "label": "Zaloguj", "value": None},
    ],
}


class FakeAccessibilityReader(AccessibilityReader):
    """Deterministic backend for tests and smoke runs.

    The default fixture intentionally contains a secure text field with a
    value, so every test/smoke run proves the sanitizer strips it.
    """

    backend = "fake"

    def __init__(
        self,
        active_app: Mapping[str, Any] | None = None,
        focused_window: Mapping[str, Any] | None = None,
    ):
        self._active_app = dict(active_app) if active_app is not None else dict(_DEFAULT_FIXTURE_APP)
        self._focused_window = (
            dict(focused_window) if focused_window is not None else dict(_DEFAULT_FIXTURE_WINDOW)
        )

    def active_app(self) -> Mapping[str, Any]:
        return dict(self._active_app)

    def focused_window(self) -> Mapping[str, Any]:
        return dict(self._focused_window)


def create_reader(backend: str) -> AccessibilityReader:
    """Build the configured backend; unknown names fail closed."""

    normalized = str(backend).strip().lower()
    if normalized == "fake":
        return FakeAccessibilityReader()
    if normalized == "ax":
        from jarvis.macos.ax_backend import AXAccessibilityReader

        return AXAccessibilityReader()
    raise AccessibilityError(f"Unknown ui_read backend: {backend!r}")


def sanitize_app_snapshot(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    data = raw if isinstance(raw, Mapping) else {}
    pid = data.get("pid")
    return {
        "app_name": _clip_text(data.get("app_name"))[0] or "",
        "bundle_id": _clip_text(data.get("bundle_id"))[0],
        "pid": pid if isinstance(pid, int) and not isinstance(pid, bool) else None,
    }


def sanitize_window_snapshot(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    """Normalize a backend window snapshot into a safe, bounded payload.

    Secure text fields keep their role/label but never their value; the
    element list and every text are clipped; anything non-primitive is
    stringified so the payload is always JSON-safe.
    """

    data = raw if isinstance(raw, Mapping) else {}
    truncated = False

    app_name, clipped = _clip_text(data.get("app_name"))
    truncated |= clipped
    title, clipped = _clip_text(data.get("title"))
    truncated |= clipped

    raw_elements = data.get("elements")
    elements_in = list(raw_elements) if isinstance(raw_elements, (list, tuple)) else []
    if len(elements_in) > MAX_ELEMENTS:
        elements_in = elements_in[:MAX_ELEMENTS]
        truncated = True

    elements: list[dict[str, Any]] = []
    for raw_element in elements_in:
        element, clipped = _sanitize_element(raw_element)
        truncated |= clipped
        elements.append(element)

    return {
        "app_name": app_name or "",
        "title": title or "",
        "elements": elements,
        "truncated": truncated,
    }


def _sanitize_element(raw: Any) -> tuple[dict[str, Any], bool]:
    data = raw if isinstance(raw, Mapping) else {}
    truncated = False

    role, clipped = _clip_text(data.get("role"))
    truncated |= clipped
    subrole, clipped = _clip_text(data.get("subrole"))
    truncated |= clipped
    label, clipped = _clip_text(data.get("label"))
    truncated |= clipped

    secure = bool(data.get("secure")) or role == SECURE_ROLE or subrole == SECURE_ROLE
    if secure:
        value: str | None = None
    else:
        value, clipped = _clip_text(data.get("value"))
        truncated |= clipped

    return (
        {
            "role": role,
            "subrole": subrole,
            "label": label,
            "value": value,
            "secure": secure,
        },
        truncated,
    )


def _clip_text(value: Any) -> tuple[str | None, bool]:
    if value is None:
        return None, False
    text = value if isinstance(value, str) else str(value)
    if len(text) > MAX_TEXT_CHARS:
        return text[:MAX_TEXT_CHARS], True
    return text, False


def _probe() -> int:
    """Manual TCC/onboarding probe: ``python -m jarvis.macos.accessibility``.

    Prints JSON with the trust status and, when trusted, sanitized snapshots
    of the approved surfaces. Exit codes: 0 read OK, 2 TCC missing/failed.
    """

    import json
    import sys

    report: dict[str, Any] = {"backend": "ax"}
    try:
        from jarvis.macos.ax_backend import AXAccessibilityReader, is_process_trusted

        report["trusted"] = is_process_trusted()
        if report["trusted"]:
            reader = AXAccessibilityReader()
            report["active_app"] = sanitize_app_snapshot(reader.active_app())
            report["window"] = sanitize_window_snapshot(reader.focused_window())
        else:
            report["hint"] = (
                "Grant Accessibility to the process hosting jarvisd: "
                "System Settings -> Privacy & Security -> Accessibility. "
                "See docs/runbooks/ACCESSIBILITY_TCC.md"
            )
    except AccessibilityError as exc:
        report["error"] = str(exc)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report.get("trusted") and "error" not in report:
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(_probe())


__all__ = [
    "AccessibilityError",
    "AccessibilityReader",
    "FakeAccessibilityReader",
    "MAX_ELEMENTS",
    "MAX_TEXT_CHARS",
    "SECURE_ROLE",
    "create_reader",
    "sanitize_app_snapshot",
    "sanitize_window_snapshot",
]
