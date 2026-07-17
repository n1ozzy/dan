"""Screen capture + OCR adapter (FAZA D4, risk class screen_read).

D4 implements the *narrow* shape of `screen_read` only: the frontmost
window or an explicitly named region. There is no full-display and no
continuous capture — that is the broad shape and needs its own ADR
(docs/DECISIONS.md ADR-020).

Safety model (mirrors ui_read, docs/MACOS_CAPABILITIES.md §1):
- PermissionPolicy decides first (`screen_read` row of the source matrix).
- Captures are transient artifacts: PNG files under a dand-owned work
  directory (0600), deleted right after OCR, never persisted to the DB.
- Only OCR **text** leaves this adapter. It passes `sanitize_ocr_snapshot`
  at the tool layer (line/length clipping) and then the usual
  tool_runs/EventStore redaction. The D3 stream never carries it
  (ADR-019 omits bulk tool output).

Backends:
- `native` — capture via Apple's `/usr/sbin/screencapture` (itself built on
  ScreenCaptureKit; keeps the zero-dependency rule — no pyobjc), OCR via
  Vision `VNRecognizeTextRequest` driven through ctypes in a **short-lived
  subprocess** (`python -m dan.macos.screen --ocr <png>`), so an ABI
  mistake in the ObjC bridge can never take the daemon down. Requires the
  Screen Recording TCC grant; see docs/runbooks/SCREEN_RECORDING_TCC.md.
- `fake` — deterministic fixture for tests and the smoke harness; announces
  itself in every payload via ``backend: "fake"``.
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any


MAX_OCR_LINES = 240
MAX_OCR_LINE_CHARS = 512

MAX_REGION_ORIGIN = 20000
MAX_REGION_SIZE = 10000

_SCREENCAPTURE_BINARY = "/usr/sbin/screencapture"
_CAPTURE_TIMEOUT_SECONDS = 15
_OCR_TIMEOUT_SECONDS = 60
_APPLICATION_SERVICES = (
    "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
)


class ScreenReadError(Exception):
    """Raised when a backend cannot capture or OCR (no TCC grant, no window)."""


class ScreenReader:
    """Backend interface: capture an approved surface and return OCR lines."""

    backend = "abstract"

    def read_window(self) -> Mapping[str, Any]:
        raise NotImplementedError

    def read_region(self, *, x: int, y: int, width: int, height: int) -> Mapping[str, Any]:
        raise NotImplementedError


# The fixture intentionally contains a secret-looking token, so every
# test/smoke run proves OCR output is redacted before it persists.
_DEFAULT_FIXTURE_LINES = [
    "DAN FAKE SCREEN — D4 smoke fixture",
    "login: ozzy",
    "token: sk-fakescreensecret1234567890",
    "Build finished in 4.2s (fake terminal output)",
]


class FakeScreenReader(ScreenReader):
    """Deterministic backend for tests and smoke runs."""

    backend = "fake"

    def __init__(self, lines: list[str] | None = None):
        self._lines = list(lines) if lines is not None else list(_DEFAULT_FIXTURE_LINES)

    def read_window(self) -> Mapping[str, Any]:
        return {
            "source": "window",
            "app_name": "FakePad",
            "pid": 4242,
            "window_id": 777,
            "lines": list(self._lines),
        }

    def read_region(self, *, x: int, y: int, width: int, height: int) -> Mapping[str, Any]:
        return {
            "source": "region",
            "region": {"x": x, "y": y, "width": width, "height": height},
            "lines": list(self._lines),
        }


class NativeScreenReader(ScreenReader):
    """screencapture + Vision OCR backend (Screen Recording TCC required)."""

    backend = "native"

    def __init__(self, *, work_dir: Path | str):
        self._work_dir = Path(work_dir)

    def read_window(self) -> Mapping[str, Any]:
        self._require_screen_recording()
        from dan.macos.ax_backend import frontmost_window_summary

        summary = frontmost_window_summary()
        if summary is None:
            raise ScreenReadError("No on-screen layer-0 window to capture.")
        capture = self._capture(["-l", str(summary["window_id"])])
        lines = self._ocr_and_discard(capture)
        return {
            "source": "window",
            "app_name": summary.get("app_name"),
            "pid": summary.get("pid"),
            "window_id": summary.get("window_id"),
            "lines": lines,
        }

    def read_region(self, *, x: int, y: int, width: int, height: int) -> Mapping[str, Any]:
        self._require_screen_recording()
        capture = self._capture(["-R", f"{x},{y},{width},{height}"])
        lines = self._ocr_and_discard(capture)
        return {
            "source": "region",
            "region": {"x": x, "y": y, "width": width, "height": height},
            "lines": lines,
        }

    def _require_screen_recording(self) -> None:
        if not preflight_screen_capture_access():
            raise ScreenReadError(
                "Screen Recording TCC grant is missing for the process hosting "
                "dand. See docs/runbooks/SCREEN_RECORDING_TCC.md."
            )

    def _capture(self, target_args: list[str]) -> Path:
        self._work_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        capture_path = self._work_dir / f"capture-{uuid.uuid4().hex}.png"
        command = [_SCREENCAPTURE_BINARY, "-x", "-o", "-t", "png", *target_args, str(capture_path)]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                timeout=_CAPTURE_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            _unlink_quietly(capture_path)
            raise ScreenReadError(f"screencapture did not run: {exc}") from exc

        if completed.returncode != 0 or not capture_path.is_file() or capture_path.stat().st_size == 0:
            stderr = completed.stderr.decode("utf-8", errors="replace")[:300]
            _unlink_quietly(capture_path)
            raise ScreenReadError(
                f"screencapture failed (rc={completed.returncode}): {stderr or 'no output file'}"
            )
        os.chmod(capture_path, 0o600)
        return capture_path

    def _ocr_and_discard(self, capture_path: Path) -> list[str]:
        try:
            return _ocr_in_subprocess(capture_path)
        finally:
            # Captures are transient artifacts; the pixels never outlive OCR.
            _unlink_quietly(capture_path)


def _ocr_in_subprocess(capture_path: Path) -> list[str]:
    """Run Vision OCR in a short-lived interpreter for crash isolation."""

    package_root = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(package_root) + (os.pathsep + existing if existing else "")
    command = [sys.executable, "-m", "dan.macos.screen", "--ocr", str(capture_path)]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            timeout=_OCR_TIMEOUT_SECONDS,
            check=False,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ScreenReadError(f"Vision OCR subprocess did not run: {exc}") from exc

    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace")[:300]
        raise ScreenReadError(f"Vision OCR failed (rc={completed.returncode}): {stderr}")
    try:
        payload = json.loads(completed.stdout.decode("utf-8"))
        lines = payload["lines"]
    except (ValueError, KeyError, UnicodeDecodeError) as exc:
        raise ScreenReadError(f"Vision OCR returned malformed output: {exc}") from exc
    if not isinstance(lines, list):
        raise ScreenReadError("Vision OCR returned malformed output: lines is not a list.")
    return [str(line) for line in lines]


def create_screen_reader(backend: str, *, work_dir: Path | str) -> ScreenReader:
    """Build the configured backend; unknown names fail closed."""

    normalized = str(backend).strip().lower()
    if normalized == "fake":
        return FakeScreenReader()
    if normalized == "native":
        return NativeScreenReader(work_dir=work_dir)
    raise ScreenReadError(f"Unknown screen_read backend: {backend!r}")


def preflight_screen_capture_access() -> bool:
    """True when this process holds the Screen Recording TCC grant."""

    try:
        lib = ctypes.CDLL(_APPLICATION_SERVICES)
        preflight = lib.CGPreflightScreenCaptureAccess
    except (OSError, AttributeError) as exc:
        raise ScreenReadError(f"Screen Recording preflight unavailable: {exc}") from exc
    preflight.argtypes = []
    preflight.restype = ctypes.c_bool
    return bool(preflight())


def sanitize_ocr_snapshot(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    """Clip OCR output at the tool layer: bounded line count and length.

    Sanitization here is structural (JSON-safe strings, hard caps); secret
    redaction happens where it always does — ToolRunRecorder/EventStore.
    """

    snapshot = raw if isinstance(raw, Mapping) else {}
    raw_lines = snapshot.get("lines")
    source_lines = list(raw_lines) if isinstance(raw_lines, (list, tuple)) else []

    truncated = len(source_lines) > MAX_OCR_LINES
    lines: list[str] = []
    for value in source_lines[:MAX_OCR_LINES]:
        text = value if isinstance(value, str) else str(value)
        if len(text) > MAX_OCR_LINE_CHARS:
            text = text[:MAX_OCR_LINE_CHARS]
            truncated = True
        lines.append(text)

    sanitized: dict[str, Any] = {
        "source": str(snapshot.get("source") or "unknown"),
        "lines": lines,
        "line_count": len(lines),
        "truncated": truncated,
    }
    for key in ("app_name", "pid", "window_id"):
        if key in snapshot:
            value = snapshot[key]
            sanitized[key] = value if isinstance(value, (str, int)) or value is None else str(value)
    region = snapshot.get("region")
    if isinstance(region, Mapping):
        sanitized["region"] = {
            key: int(region[key])
            for key in ("x", "y", "width", "height")
            if isinstance(region.get(key), int)
        }
    return sanitized


def _unlink_quietly(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _run_ocr_cli(path: str) -> int:
    """``python -m dan.macos.screen --ocr <png>``: OCR one file to JSON."""

    from dan.macos.vision_ocr import VisionOCRError, recognize_text

    try:
        lines = recognize_text(path)
    except VisionOCRError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps({"lines": lines}, ensure_ascii=False))
    return 0


def _probe() -> int:
    """Manual TCC/onboarding probe: ``python -m dan.macos.screen``.

    Prints JSON with the Screen Recording grant status and, when granted, a
    sanitized OCR snapshot of the frontmost window. Exit codes: 0 capture
    and OCR OK, 2 TCC missing/failed.
    """

    import tempfile

    report: dict[str, Any] = {"backend": "native"}
    try:
        report["screen_recording"] = preflight_screen_capture_access()
        if report["screen_recording"]:
            reader = NativeScreenReader(
                work_dir=Path(tempfile.mkdtemp(prefix="dan-screen-probe-"))
            )
            snapshot = sanitize_ocr_snapshot(reader.read_window())
            report["window"] = {
                "app_name": snapshot.get("app_name"),
                "window_id": snapshot.get("window_id"),
                "line_count": snapshot["line_count"],
                "truncated": snapshot["truncated"],
                "lines_preview": snapshot["lines"][:5],
            }
        else:
            report["hint"] = (
                "Grant Screen Recording to the process hosting dand: "
                "System Settings -> Privacy & Security -> Screen Recording. "
                "See docs/runbooks/SCREEN_RECORDING_TCC.md"
            )
    except ScreenReadError as exc:
        report["error"] = str(exc)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report.get("screen_recording") and "error" not in report:
        return 0
    return 2


if __name__ == "__main__":
    # `python -m` executes this file as __main__ while the OCR subprocess
    # re-imports it as dan.macos.screen — two distinct ScreenReadError
    # classes otherwise (the D2 probe lesson). Delegate to the canonical
    # module instance.
    from dan.macos import screen as _canonical

    if len(sys.argv) >= 3 and sys.argv[1] == "--ocr":
        raise SystemExit(_canonical._run_ocr_cli(sys.argv[2]))
    raise SystemExit(_canonical._probe())


__all__ = [
    "MAX_OCR_LINES",
    "MAX_OCR_LINE_CHARS",
    "MAX_REGION_ORIGIN",
    "MAX_REGION_SIZE",
    "FakeScreenReader",
    "NativeScreenReader",
    "ScreenReadError",
    "ScreenReader",
    "create_screen_reader",
    "preflight_screen_capture_access",
    "sanitize_ocr_snapshot",
]
