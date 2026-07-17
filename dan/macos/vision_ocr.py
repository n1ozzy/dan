"""Vision `VNRecognizeTextRequest` driven through ctypes (no pyobjc).

Vision has no C API, so this module speaks to the Objective-C runtime
directly: `objc_msgSend` cast to an exact prototype per call (arm64 uses a
single entry point for register-class signatures). The surface is kept as
small as possible — request defaults only, no completion blocks, no
dispatch queues — because every extra message is ABI risk.

That risk is why callers never import this in the daemon process:
`NativeScreenReader` runs it via ``python -m dan.macos.screen --ocr`` in
a short-lived subprocess, so a segfault costs one tool run, not dand.

Observations are returned in Vision's natural order (top-to-bottom in
practice); D4 does no geometric re-sorting.
"""

from __future__ import annotations

import ctypes
from pathlib import Path


_LIBOBJC = "/usr/lib/libobjc.A.dylib"
_FOUNDATION = "/System/Library/Frameworks/Foundation.framework/Foundation"
_VISION = "/System/Library/Frameworks/Vision.framework/Vision"


class VisionOCRError(Exception):
    """Raised when the Vision bridge cannot produce OCR text."""


class _ObjC:
    """Lazily loaded Objective-C runtime with the frameworks Vision needs."""

    def __init__(self) -> None:
        try:
            self.objc = ctypes.CDLL(_LIBOBJC)
            # Loading Foundation and Vision registers their classes with the
            # runtime; the handles themselves are not used directly.
            ctypes.CDLL(_FOUNDATION)
            ctypes.CDLL(_VISION)
        except OSError as exc:
            raise VisionOCRError(f"macOS frameworks unavailable: {exc}") from exc

        self.objc.objc_getClass.argtypes = [ctypes.c_char_p]
        self.objc.objc_getClass.restype = ctypes.c_void_p
        self.objc.sel_registerName.argtypes = [ctypes.c_char_p]
        self.objc.sel_registerName.restype = ctypes.c_void_p
        self.objc.objc_autoreleasePoolPush.argtypes = []
        self.objc.objc_autoreleasePoolPush.restype = ctypes.c_void_p
        self.objc.objc_autoreleasePoolPop.argtypes = [ctypes.c_void_p]
        self.objc.objc_autoreleasePoolPop.restype = None

    def cls(self, name: str) -> ctypes.c_void_p:
        pointer = self.objc.objc_getClass(name.encode("ascii"))
        if not pointer:
            raise VisionOCRError(f"Objective-C class not found: {name}")
        return ctypes.c_void_p(pointer)

    def send(
        self,
        receiver: ctypes.c_void_p | int,
        selector: str,
        *args: object,
        restype: object = ctypes.c_void_p,
        argtypes: tuple[object, ...] = (),
    ) -> object:
        prototype = ctypes.CFUNCTYPE(restype, ctypes.c_void_p, ctypes.c_void_p, *argtypes)
        message = ctypes.cast(self.objc.objc_msgSend, prototype)
        return message(receiver, self.objc.sel_registerName(selector.encode("ascii")), *args)


_runtime: _ObjC | None = None


def _objc() -> _ObjC:
    global _runtime
    if _runtime is None:
        _runtime = _ObjC()
    return _runtime


def recognize_text(image_path: str | Path) -> list[str]:
    """OCR one image file on-device; returns recognized lines of text."""

    path = Path(image_path)
    if not path.is_file():
        raise VisionOCRError(f"Image file does not exist: {path}")

    rt = _objc()
    pool = rt.objc.objc_autoreleasePoolPush()
    handler = None
    request = None
    try:
        ns_path = rt.send(
            rt.cls("NSString"),
            "stringWithUTF8String:",
            str(path).encode("utf-8"),
            argtypes=(ctypes.c_char_p,),
        )
        url = rt.send(rt.cls("NSURL"), "fileURLWithPath:", ns_path, argtypes=(ctypes.c_void_p,))
        if not url:
            raise VisionOCRError(f"Cannot build a file URL for: {path}")

        options = rt.send(rt.cls("NSDictionary"), "dictionary")
        handler = rt.send(
            rt.send(rt.cls("VNImageRequestHandler"), "alloc"),
            "initWithURL:options:",
            url,
            options,
            argtypes=(ctypes.c_void_p, ctypes.c_void_p),
        )
        if not handler:
            raise VisionOCRError("VNImageRequestHandler could not open the capture.")

        # Request defaults: accurate recognition with language correction.
        request = rt.send(rt.send(rt.cls("VNRecognizeTextRequest"), "alloc"), "init")
        if not request:
            raise VisionOCRError("VNRecognizeTextRequest could not be created.")

        requests = rt.send(
            rt.cls("NSArray"), "arrayWithObject:", request, argtypes=(ctypes.c_void_p,)
        )
        error = ctypes.c_void_p(0)
        performed = rt.send(
            handler,
            "performRequests:error:",
            requests,
            ctypes.byref(error),
            restype=ctypes.c_bool,
            argtypes=(ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)),
        )
        if not performed:
            raise VisionOCRError(f"Vision perform failed: {_describe(rt, error)}")

        results = rt.send(request, "results")
        if not results:
            return []
        count = int(rt.send(results, "count", restype=ctypes.c_ulong))
        lines: list[str] = []
        for index in range(count):
            observation = rt.send(
                results, "objectAtIndex:", index, argtypes=(ctypes.c_ulong,)
            )
            candidates = rt.send(
                observation, "topCandidates:", 1, argtypes=(ctypes.c_ulong,)
            )
            if not candidates:
                continue
            if int(rt.send(candidates, "count", restype=ctypes.c_ulong)) == 0:
                continue
            candidate = rt.send(candidates, "objectAtIndex:", 0, argtypes=(ctypes.c_ulong,))
            text = _nsstring_to_str(rt, rt.send(candidate, "string"))
            if text:
                lines.append(text)
        return lines
    finally:
        for owned in (request, handler):
            if owned:
                rt.send(owned, "release", restype=None)
        rt.objc.objc_autoreleasePoolPop(pool)


def _nsstring_to_str(rt: _ObjC, ns_string: object) -> str | None:
    if not ns_string:
        return None
    raw = rt.send(ns_string, "UTF8String", restype=ctypes.c_char_p)
    if raw is None:
        return None
    return raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)


def _describe(rt: _ObjC, error: ctypes.c_void_p) -> str:
    if not error or not error.value:
        return "unknown Vision error"
    description = rt.send(ctypes.c_void_p(error.value), "localizedDescription")
    return _nsstring_to_str(rt, description) or "unknown Vision error"


__all__ = ["VisionOCRError", "recognize_text"]
