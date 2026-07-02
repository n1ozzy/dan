"""Real AXUIElement backend for the D1 ui_read adapter (ctypes, no pyobjc).

The project has zero runtime dependencies, so this talks to
ApplicationServices/CoreFoundation directly via ctypes. The surface is the
D1 contract only: frontmost application and its focused window.

Secure text fields: elements whose role/subrole is AXSecureTextField never
have their value copied here (defense at the source); the tool-layer
sanitizer strips them again regardless.

Requires the Accessibility TCC grant for the process hosting jarvisd —
see docs/runbooks/ACCESSIBILITY_TCC.md. Without the grant every read raises
AccessibilityError; nothing is retried or escalated automatically.
"""

from __future__ import annotations

import ctypes
from collections.abc import Mapping
from typing import Any

from jarvis.macos.accessibility import (
    MAX_ELEMENTS,
    SECURE_ROLE,
    AccessibilityActor,
    AccessibilityError,
    AccessibilityReader,
)

_APPLICATION_SERVICES = (
    "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
)
_CORE_FOUNDATION = "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"

_UTF8 = 0x08000100  # kCFStringEncodingUTF8
_AX_SUCCESS = 0  # kAXErrorSuccess
_NUMBER_AS_INT64 = 4  # kCFNumberSInt64Type
_MAX_WALK_DEPTH = 5


class _Frameworks:
    """Lazily loaded and typed CoreFoundation/ApplicationServices handles."""

    def __init__(self) -> None:
        try:
            self.cf = ctypes.CDLL(_CORE_FOUNDATION)
            self.ax = ctypes.CDLL(_APPLICATION_SERVICES)
        except OSError as exc:
            raise AccessibilityError(f"macOS frameworks unavailable: {exc}") from exc

        cf, ax = self.cf, self.ax
        ref = ctypes.c_void_p

        cf.CFRelease.argtypes = [ref]
        cf.CFRelease.restype = None
        cf.CFGetTypeID.argtypes = [ref]
        cf.CFGetTypeID.restype = ctypes.c_ulong
        for getter in ("CFStringGetTypeID", "CFArrayGetTypeID", "CFBooleanGetTypeID",
                       "CFNumberGetTypeID"):
            fn = getattr(cf, getter)
            fn.argtypes = []
            fn.restype = ctypes.c_ulong
        cf.CFStringCreateWithCString.argtypes = [ref, ctypes.c_char_p, ctypes.c_uint32]
        cf.CFStringCreateWithCString.restype = ref
        cf.CFStringGetLength.argtypes = [ref]
        cf.CFStringGetLength.restype = ctypes.c_long
        cf.CFStringGetMaximumSizeForEncoding.argtypes = [ctypes.c_long, ctypes.c_uint32]
        cf.CFStringGetMaximumSizeForEncoding.restype = ctypes.c_long
        cf.CFStringGetCString.argtypes = [ref, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
        cf.CFStringGetCString.restype = ctypes.c_bool
        cf.CFArrayGetCount.argtypes = [ref]
        cf.CFArrayGetCount.restype = ctypes.c_long
        cf.CFArrayGetValueAtIndex.argtypes = [ref, ctypes.c_long]
        cf.CFArrayGetValueAtIndex.restype = ref
        cf.CFBooleanGetValue.argtypes = [ref]
        cf.CFBooleanGetValue.restype = ctypes.c_bool
        cf.CFNumberGetValue.argtypes = [ref, ctypes.c_int, ctypes.c_void_p]
        cf.CFNumberGetValue.restype = ctypes.c_bool

        cf.CFRetain.argtypes = [ref]
        cf.CFRetain.restype = ref
        cf.CFDictionaryGetValue.argtypes = [ref, ref]
        cf.CFDictionaryGetValue.restype = ref

        ax.AXIsProcessTrusted.argtypes = []
        ax.AXIsProcessTrusted.restype = ctypes.c_bool
        ax.AXUIElementCreateSystemWide.argtypes = []
        ax.AXUIElementCreateSystemWide.restype = ref
        ax.AXUIElementCreateApplication.argtypes = [ctypes.c_int]
        ax.AXUIElementCreateApplication.restype = ref
        ax.AXUIElementCopyAttributeValue.argtypes = [ref, ref, ctypes.POINTER(ref)]
        ax.AXUIElementCopyAttributeValue.restype = ctypes.c_int
        ax.AXUIElementSetAttributeValue.argtypes = [ref, ref, ref]
        ax.AXUIElementSetAttributeValue.restype = ctypes.c_int
        ax.AXUIElementPerformAction.argtypes = [ref, ref]
        ax.AXUIElementPerformAction.restype = ctypes.c_int
        ax.AXUIElementGetPid.argtypes = [ref, ctypes.POINTER(ctypes.c_int)]
        ax.AXUIElementGetPid.restype = ctypes.c_int
        # CoreGraphics is an ApplicationServices subframework; the window
        # list (owner names + pids, no TCC needed) backs ui_focus_app.
        ax.CGWindowListCopyWindowInfo.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
        ax.CGWindowListCopyWindowInfo.restype = ref


_frameworks: _Frameworks | None = None


def _fw() -> _Frameworks:
    global _frameworks
    if _frameworks is None:
        _frameworks = _Frameworks()
    return _frameworks


def is_process_trusted() -> bool:
    """True when this process holds the Accessibility TCC grant."""

    return bool(_fw().ax.AXIsProcessTrusted())


class AXAccessibilityReader(AccessibilityReader):
    backend = "ax"

    def __init__(self) -> None:
        _fw()  # fail fast if frameworks cannot load

    def active_app(self) -> Mapping[str, Any]:
        app = self._focused_application()
        try:
            return {
                "app_name": _copy_string_attribute(app, "AXTitle"),
                "bundle_id": None,  # not exposed via AX; D1 reports pid instead
                "pid": _element_pid(app),
            }
        finally:
            _release(app)

    def focused_window(self) -> Mapping[str, Any]:
        app = self._focused_application()
        try:
            app_name = _copy_string_attribute(app, "AXTitle")
            window = _copy_attribute(app, "AXFocusedWindow")
            if window is None:
                raise AccessibilityError("frontmost application has no focused window")
            try:
                title = _copy_string_attribute(window, "AXTitle")
                elements: list[dict[str, Any]] = []
                _walk_children(window, elements, depth=0)
            finally:
                _release(window)
        finally:
            _release(app)
        return {
            "app_name": app_name,
            "title": title,
            "elements": elements,
        }

    def _focused_application(self) -> ctypes.c_void_p:
        return _copy_focused_application()


class AXAccessibilityActor(AccessibilityActor):
    """UI actions via AX APIs only: AXPress for clicks, AXValue for typing,
    AXFrontmost for focus. No CGEvent synthetic input in D2 (ADR-018)."""

    backend = "ax"

    def __init__(self) -> None:
        _fw()  # fail fast if frameworks cannot load

    def click(self, *, label: str, role: str | None = None) -> Mapping[str, Any]:
        app = _copy_focused_application()
        try:
            window = _copy_attribute(app, "AXFocusedWindow")
            if window is None:
                raise AccessibilityError("frontmost application has no focused window")
            try:
                element = _find_element(window, label=label, role=role)
                if element is None:
                    raise AccessibilityError(
                        f"no clickable element labelled {label!r} in the focused window"
                    )
                try:
                    _perform_action(element, "AXPress")
                finally:
                    _release(element)
            finally:
                _release(window)
        finally:
            _release(app)
        return {"clicked": True, "label": label}

    def type_text(self, text: str) -> Mapping[str, Any]:
        if "\x00" in text:
            raise AccessibilityError("ui_type text may not contain NUL characters")
        app = _copy_focused_application()
        try:
            element = _copy_attribute(app, "AXFocusedUIElement")
            if element is None:
                raise AccessibilityError("frontmost application has no focused element")
            try:
                role = _copy_string_attribute(element, "AXRole")
                subrole = _copy_string_attribute(element, "AXSubrole")
                if role == SECURE_ROLE or subrole == SECURE_ROLE:
                    raise AccessibilityError(
                        "focused element is a secure text field; typing refused"
                    )
                _set_string_attribute(element, "AXValue", text)
            finally:
                _release(element)
        finally:
            _release(app)
        return {"typed": True, "chars_typed": len(text)}

    def focus_app(self, app_name: str) -> Mapping[str, Any]:
        fw = _fw()
        if not fw.ax.AXIsProcessTrusted():
            raise AccessibilityError(
                "Accessibility (TCC) is not granted to this process; "
                "see docs/runbooks/ACCESSIBILITY_TCC.md"
            )
        pid = _pid_for_app_name(app_name)
        if pid is None:
            raise AccessibilityError(f"no running application named {app_name!r}")
        app = fw.ax.AXUIElementCreateApplication(pid)
        if not app:
            raise AccessibilityError(f"cannot create AX element for pid {pid}")
        try:
            _set_boolean_attribute(app, "AXFrontmost", True)
        finally:
            _release(app)
        return {"focused": True, "app_name": app_name, "pid": pid}


def _copy_focused_application() -> ctypes.c_void_p:
    fw = _fw()
    if not fw.ax.AXIsProcessTrusted():
        raise AccessibilityError(
            "Accessibility (TCC) is not granted to this process; "
            "see docs/runbooks/ACCESSIBILITY_TCC.md"
        )
    system_wide = fw.ax.AXUIElementCreateSystemWide()
    if not system_wide:
        raise AccessibilityError("AXUIElementCreateSystemWide returned NULL")
    try:
        app = _copy_attribute(system_wide, "AXFocusedApplication")
    finally:
        _release(system_wide)
    if app is not None:
        return app
    # Live finding (2026-07-02): the system-wide focus query fails with
    # kAXErrorCannotComplete (-25204) when the process is not a GUI app
    # (terminal/launchd-hosted jarvisd). Resolve the frontmost pid from the
    # on-screen window list instead and target that app directly.
    pid = _frontmost_pid()
    if pid is None:
        raise AccessibilityError("no focused application")
    app = fw.ax.AXUIElementCreateApplication(pid)
    if not app:
        raise AccessibilityError(f"cannot create AX element for pid {pid}")
    return ctypes.c_void_p(app)


def _find_element(
    window: ctypes.c_void_p,
    *,
    label: str,
    role: str | None,
    depth: int = 0,
) -> ctypes.c_void_p | None:
    """Find a matching element; the returned ref is CFRetain-ed (caller
    releases). Match: AXTitle or AXDescription equals label, optional role."""

    if depth >= _MAX_WALK_DEPTH:
        return None
    fw = _fw()
    children = _copy_attribute(window, "AXChildren")
    if children is None:
        return None
    try:
        if fw.cf.CFGetTypeID(children) != fw.cf.CFArrayGetTypeID():
            return None
        count = fw.cf.CFArrayGetCount(children)
        for index in range(count):
            child = fw.cf.CFArrayGetValueAtIndex(children, index)
            if not child:
                continue
            child_label = _copy_string_attribute(child, "AXTitle") or _copy_string_attribute(
                child, "AXDescription"
            )
            if child_label == label:
                child_role = _copy_string_attribute(child, "AXRole")
                if role is None or child_role == role:
                    return ctypes.c_void_p(fw.cf.CFRetain(child))
            found = _find_element(child, label=label, role=role, depth=depth + 1)
            if found is not None:
                return found
    finally:
        _release(children)
    return None


def _perform_action(element: ctypes.c_void_p, action: str) -> None:
    fw = _fw()
    name = fw.cf.CFStringCreateWithCString(None, action.encode("utf-8"), _UTF8)
    if not name:
        raise AccessibilityError(f"cannot create CFString for {action}")
    try:
        status = fw.ax.AXUIElementPerformAction(element, name)
    finally:
        fw.cf.CFRelease(name)
    if status != _AX_SUCCESS:
        raise AccessibilityError(f"AX action {action} failed with status {status}")


def _set_string_attribute(element: ctypes.c_void_p, attribute: str, text: str) -> None:
    fw = _fw()
    name = fw.cf.CFStringCreateWithCString(None, attribute.encode("utf-8"), _UTF8)
    value = fw.cf.CFStringCreateWithCString(None, text.encode("utf-8"), _UTF8)
    if not name or not value:
        _release(name)
        _release(value)
        raise AccessibilityError(f"cannot create CFStrings for {attribute}")
    try:
        status = fw.ax.AXUIElementSetAttributeValue(element, name, value)
    finally:
        fw.cf.CFRelease(name)
        fw.cf.CFRelease(value)
    if status != _AX_SUCCESS:
        raise AccessibilityError(
            f"setting {attribute} failed with status {status}; "
            "the focused element may not accept text"
        )


def _set_boolean_attribute(element: ctypes.c_void_p, attribute: str, value: bool) -> None:
    fw = _fw()
    name = fw.cf.CFStringCreateWithCString(None, attribute.encode("utf-8"), _UTF8)
    if not name:
        raise AccessibilityError(f"cannot create CFString for {attribute}")
    boolean = ctypes.c_void_p.in_dll(fw.cf, "kCFBooleanTrue" if value else "kCFBooleanFalse")
    try:
        status = fw.ax.AXUIElementSetAttributeValue(element, name, boolean)
    finally:
        fw.cf.CFRelease(name)
    if status != _AX_SUCCESS:
        raise AccessibilityError(f"setting {attribute} failed with status {status}")


_CG_ON_SCREEN_ONLY = 1  # kCGWindowListOptionOnScreenOnly
_CG_NULL_WINDOW_ID = 0  # kCGNullWindowID


def _window_owner_entries() -> list[tuple[str | None, int | None, int | None]]:
    """(owner_name, owner_pid, layer) for on-screen windows, front to back.

    Only owner names, pids and layers are read — never window contents;
    ADR-018 keeps observation of other apps' UI out of scope."""

    fw = _fw()
    windows = fw.ax.CGWindowListCopyWindowInfo(_CG_ON_SCREEN_ONLY, _CG_NULL_WINDOW_ID)
    if not windows:
        return []
    owner_name_key = fw.cf.CFStringCreateWithCString(None, b"kCGWindowOwnerName", _UTF8)
    owner_pid_key = fw.cf.CFStringCreateWithCString(None, b"kCGWindowOwnerPID", _UTF8)
    layer_key = fw.cf.CFStringCreateWithCString(None, b"kCGWindowLayer", _UTF8)
    entries: list[tuple[str | None, int | None, int | None]] = []
    try:
        count = fw.cf.CFArrayGetCount(windows)
        for index in range(count):
            entry = fw.cf.CFArrayGetValueAtIndex(windows, index)
            if not entry:
                continue
            name_ref = fw.cf.CFDictionaryGetValue(entry, owner_name_key)
            name = _cfstring_to_str(ctypes.c_void_p(name_ref)) if name_ref else None
            pid_ref = fw.cf.CFDictionaryGetValue(entry, owner_pid_key)
            pid = _cfnumber_to_int(pid_ref)
            layer = _cfnumber_to_int(fw.cf.CFDictionaryGetValue(entry, layer_key))
            entries.append((name, pid, layer))
    finally:
        fw.cf.CFRelease(owner_name_key)
        fw.cf.CFRelease(owner_pid_key)
        fw.cf.CFRelease(layer_key)
        fw.cf.CFRelease(windows)
    return entries


def _cfnumber_to_int(ref: int | None) -> int | None:
    if not ref:
        return None
    holder = ctypes.c_int64()
    if _fw().cf.CFNumberGetValue(ctypes.c_void_p(ref), _NUMBER_AS_INT64, ctypes.byref(holder)):
        return int(holder.value)
    return None


def _frontmost_pid() -> int | None:
    """Pid of the frontmost regular app: first layer-0 on-screen window."""

    for _name, pid, layer in _window_owner_entries():
        if layer == 0 and pid is not None:
            return pid
    return None


def _pid_for_app_name(app_name: str) -> int | None:
    for name, pid, _layer in _window_owner_entries():
        if name == app_name and pid is not None:
            return pid
    return None


def _walk_children(element: ctypes.c_void_p, out: list[dict[str, Any]], *, depth: int) -> None:
    """Depth-first, bounded walk; child refs are only used while their owning
    CFArray is alive (CF Get rule), so no retains are needed."""

    if depth >= _MAX_WALK_DEPTH or len(out) >= MAX_ELEMENTS:
        return
    children = _copy_attribute(element, "AXChildren")
    if children is None:
        return
    fw = _fw()
    try:
        if fw.cf.CFGetTypeID(children) != fw.cf.CFArrayGetTypeID():
            return
        count = fw.cf.CFArrayGetCount(children)
        for index in range(count):
            if len(out) >= MAX_ELEMENTS:
                return
            child = fw.cf.CFArrayGetValueAtIndex(children, index)
            if not child:
                continue
            out.append(_describe_element(child))
            _walk_children(child, out, depth=depth + 1)
    finally:
        _release(children)


def _describe_element(element: ctypes.c_void_p) -> dict[str, Any]:
    role = _copy_string_attribute(element, "AXRole")
    subrole = _copy_string_attribute(element, "AXSubrole")
    label = _copy_string_attribute(element, "AXTitle") or _copy_string_attribute(
        element, "AXDescription"
    )
    secure = role == SECURE_ROLE or subrole == SECURE_ROLE
    value: Any = None
    if not secure:
        value = _copy_scalar_attribute(element, "AXValue")
    return {
        "role": role,
        "subrole": subrole,
        "label": label,
        "value": value,
        "secure": secure,
    }


def _copy_attribute(element: ctypes.c_void_p, attribute: str) -> ctypes.c_void_p | None:
    fw = _fw()
    name = fw.cf.CFStringCreateWithCString(None, attribute.encode("utf-8"), _UTF8)
    if not name:
        raise AccessibilityError(f"cannot create CFString for {attribute}")
    value = ctypes.c_void_p()
    try:
        status = fw.ax.AXUIElementCopyAttributeValue(element, name, ctypes.byref(value))
    finally:
        fw.cf.CFRelease(name)
    if status != _AX_SUCCESS or not value:
        return None
    return value


def _copy_string_attribute(element: ctypes.c_void_p, attribute: str) -> str | None:
    value = _copy_attribute(element, attribute)
    if value is None:
        return None
    try:
        return _cfstring_to_str(value)
    finally:
        _release(value)


def _copy_scalar_attribute(element: ctypes.c_void_p, attribute: str) -> Any:
    fw = _fw()
    value = _copy_attribute(element, attribute)
    if value is None:
        return None
    try:
        type_id = fw.cf.CFGetTypeID(value)
        if type_id == fw.cf.CFStringGetTypeID():
            return _cfstring_to_str(value)
        if type_id == fw.cf.CFBooleanGetTypeID():
            return bool(fw.cf.CFBooleanGetValue(value))
        if type_id == fw.cf.CFNumberGetTypeID():
            holder = ctypes.c_int64()
            if fw.cf.CFNumberGetValue(value, _NUMBER_AS_INT64, ctypes.byref(holder)):
                return int(holder.value)
        return None
    finally:
        _release(value)


def _cfstring_to_str(value: ctypes.c_void_p) -> str | None:
    fw = _fw()
    if fw.cf.CFGetTypeID(value) != fw.cf.CFStringGetTypeID():
        return None
    length = fw.cf.CFStringGetLength(value)
    buffer_size = fw.cf.CFStringGetMaximumSizeForEncoding(length, _UTF8) + 1
    buffer = ctypes.create_string_buffer(buffer_size)
    if not fw.cf.CFStringGetCString(value, buffer, buffer_size, _UTF8):
        return None
    return buffer.value.decode("utf-8", errors="replace")


def _element_pid(element: ctypes.c_void_p) -> int | None:
    fw = _fw()
    pid = ctypes.c_int()
    if fw.ax.AXUIElementGetPid(element, ctypes.byref(pid)) != _AX_SUCCESS:
        return None
    return int(pid.value)


def _release(value: ctypes.c_void_p | None) -> None:
    if value:
        _fw().cf.CFRelease(value)


__all__ = ["AXAccessibilityActor", "AXAccessibilityReader", "is_process_trusted"]
