"""Safe file tool placeholders.

Prompt 13 intentionally does not implement file reading or file writing.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from jarvis.tools.registry import Tool


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


__all__ = ["FileReadPlaceholderTool", "FileTool", "FileWritePlaceholderTool"]
