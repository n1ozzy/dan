"""File tools.

FAZA C3 (docs/MASTER_PLAN.md) implements the real read-only file tool.
File writing remains a placeholder until FAZA C4.

Safety model for reads:
- PermissionPolicy decides first (fail-closed approved roots, source matrix).
- The tool re-checks containment at execution time (defense in depth against
  symlink swaps between the policy check and the execute step).
- Size-limited, UTF-8 text only; binary content is refused, never returned.
- Results flow into tool_runs/events where secret redaction applies.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from typing import Any

from jarvis.tools.registry import Tool, ToolExecutionError


DEFAULT_MAX_BYTES = 262_144
HARD_MAX_BYTES = 1_048_576


class FileReadTool(Tool):
    name = "file_read"
    description = "Read a UTF-8 text file located under the approved roots."
    risk = "file_read"
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute or ~-relative file path."},
            "max_bytes": {
                "type": "integer",
                "description": f"Optional byte budget (default {DEFAULT_MAX_BYTES}, max {HARD_MAX_BYTES}).",
            },
        },
        "required": ["path"],
    }

    def __init__(self, approved_roots: Iterable[str]):
        self.approved_roots = tuple(_normalize_path(root) for root in approved_roots)

    def run(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        path = _required_path_argument(arguments)
        max_bytes = _max_bytes_argument(arguments)

        resolved = _normalize_path(path)
        if not self._is_within_approved_roots(resolved):
            raise ToolExecutionError(f"file_read path is outside approved roots: {resolved}")

        if not os.path.isfile(resolved):
            raise ToolExecutionError(f"file_read target is not a regular file: {resolved}")

        size_bytes = os.path.getsize(resolved)
        try:
            with open(resolved, "rb") as handle:
                chunk = handle.read(max_bytes + 1)
        except OSError as exc:
            raise ToolExecutionError(f"file_read cannot read file: {exc}") from exc

        if b"\x00" in chunk:
            raise ToolExecutionError("file_read refuses binary content (NUL byte found).")

        truncated = len(chunk) > max_bytes
        content = _decode_utf8_prefix(chunk[:max_bytes])

        return {
            "ok": True,
            "path": resolved,
            "size_bytes": size_bytes,
            "returned_bytes": min(len(chunk), max_bytes),
            "truncated": truncated,
            "content": content,
        }

    def _is_within_approved_roots(self, resolved: str) -> bool:
        if not self.approved_roots:
            return False
        return any(_is_within_root(resolved, root) for root in self.approved_roots)


class FileWriteTool(Tool):
    name = "file_write"
    description = "Write a UTF-8 text file under the approved roots (approval-gated)."
    risk = "file_write"
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute or ~-relative file path."},
            "content": {"type": "string", "description": "UTF-8 text content to write."},
            "overwrite": {
                "type": "boolean",
                "description": "Must be true to replace an existing file (default false).",
            },
        },
        "required": ["path", "content"],
    }

    def __init__(self, approved_roots: Iterable[str]):
        self.approved_roots = tuple(_normalize_path(root) for root in approved_roots)

    def run(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        path = _required_path_argument(arguments)
        content = arguments.get("content")
        if not isinstance(content, str):
            raise ToolExecutionError("file_write requires string content.")
        overwrite = arguments.get("overwrite", False)
        if not isinstance(overwrite, bool):
            raise ToolExecutionError("file_write overwrite must be a boolean.")

        encoded = content.encode("utf-8")
        if len(encoded) > HARD_MAX_BYTES:
            raise ToolExecutionError(
                f"file_write content exceeds {HARD_MAX_BYTES} bytes."
            )

        resolved = _normalize_path(path)
        if not self._is_within_approved_roots(resolved):
            raise ToolExecutionError(f"file_write path is outside approved roots: {resolved}")

        parent = os.path.dirname(resolved)
        if not os.path.isdir(parent):
            raise ToolExecutionError(f"file_write parent directory does not exist: {parent}")

        existed = os.path.lexists(resolved)
        if existed and not overwrite:
            raise ToolExecutionError(
                f"file_write target exists; pass overwrite=true to replace: {resolved}"
            )
        if existed and not os.path.isfile(resolved):
            raise ToolExecutionError(
                f"file_write target exists and is not a regular file: {resolved}"
            )

        temp_path = f"{resolved}.jarvis-write-{os.getpid()}.tmp"
        try:
            with open(temp_path, "wb") as handle:
                handle.write(encoded)
            os.replace(temp_path, resolved)
        except OSError as exc:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            raise ToolExecutionError(f"file_write cannot write file: {exc}") from exc

        return {
            "ok": True,
            "path": resolved,
            "bytes_written": len(encoded),
            "replaced_existing": existed,
        }

    def _is_within_approved_roots(self, resolved: str) -> bool:
        if not self.approved_roots:
            return False
        return any(_is_within_root(resolved, root) for root in self.approved_roots)


class FileReadPlaceholderTool(Tool):
    name = "file_read_placeholder"
    description = "Placeholder for future approved file reads; does not read files."
    risk = "file_read"
    input_schema = {"type": "object"}

    def run(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"ok": False, "message": "File reading is not implemented."}


class FileWritePlaceholderTool(Tool):
    name = "file_write_placeholder"
    description = "Placeholder for future approved file writes; does not write files."
    risk = "file_write"
    input_schema = {"type": "object"}

    def run(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"ok": False, "message": "File writing is not implemented."}


class FileTool(FileReadPlaceholderTool):
    """Backward-compatible placeholder name for the initial scaffold."""


def _required_path_argument(arguments: Mapping[str, Any]) -> str:
    raw_path = arguments.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ToolExecutionError("file_read requires a non-empty path argument.")
    return raw_path.strip()


def _max_bytes_argument(arguments: Mapping[str, Any]) -> int:
    raw_value = arguments.get("max_bytes", DEFAULT_MAX_BYTES)
    if isinstance(raw_value, bool) or not isinstance(raw_value, int):
        raise ToolExecutionError("file_read max_bytes must be an integer.")
    if raw_value <= 0 or raw_value > HARD_MAX_BYTES:
        raise ToolExecutionError(
            f"file_read max_bytes must be between 1 and {HARD_MAX_BYTES}."
        )
    return raw_value


def _decode_utf8_prefix(chunk: bytes) -> str:
    """Decode a byte prefix as UTF-8, tolerating a multi-byte cut at the end."""

    for trim in range(4):
        candidate = chunk[: len(chunk) - trim] if trim else chunk
        try:
            return candidate.decode("utf-8")
        except UnicodeDecodeError as exc:
            if trim == 3 or exc.start < len(candidate) - 3:
                break
    raise ToolExecutionError("file_read refuses non-UTF-8 content.")


def _normalize_path(path: str) -> str:
    return os.path.realpath(os.path.abspath(os.path.expanduser(path)))


def _is_within_root(candidate: str, approved_root: str) -> bool:
    try:
        return os.path.commonpath([candidate, approved_root]) == approved_root
    except ValueError:
        return False


__all__ = [
    "DEFAULT_MAX_BYTES",
    "FileReadPlaceholderTool",
    "FileReadTool",
    "FileTool",
    "FileWritePlaceholderTool",
    "FileWriteTool",
    "HARD_MAX_BYTES",
]
