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

        ax.AXIsProcessTrusted.argtypes = []
        ax.AXIsProcessTrusted.restype = ctypes.c_bool
        ax.AXUIElementCreateSystemWide.argtypes = []
        ax.AXUIElementCreateSystemWide.restype = ref
        ax.AXUIElementCopyAttributeValue.argtypes = [ref, ref, ctypes.POINTER(ref)]
        ax.AXUIElementCopyAttributeValue.restype = ctypes.c_int
        ax.AXUIElementGetPid.argtypes = [ref, ctypes.POINTER(ctypes.c_int)]
        ax.AXUIElementGetPid.restype = ctypes.c_int


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
        if app is None:
            raise AccessibilityError("no focused application")
        return app


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


__all__ = ["AXAccessibilityReader", "is_process_trusted"]
