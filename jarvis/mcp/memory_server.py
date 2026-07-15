"""Single-tool local MCP server for shared memory recall over stdio."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from typing import Any, TextIO

from jarvis.config import load_config
from jarvis.memory.archive import MemoryArchive
from jarvis.paths import resolve_runtime_paths
from jarvis.store.db import close_quietly, initialize_database
from jarvis.tools.memory_recall_tool import MemoryRecallTool


SERVER_NAME = "jarvis-memory"
SERVER_VERSION = "1.0.0"
PROTOCOL_VERSION = "2025-06-18"


def serve(input_stream: TextIO, output_stream: TextIO, tool: MemoryRecallTool) -> None:
    for line in input_stream:
        if not line.strip():
            continue
        request_id: Any = None
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("JSON-RPC request must be an object")
            request_id = request.get("id")
            response = _dispatch(request, tool)
        except (json.JSONDecodeError, ValueError) as exc:
            response = _error(request_id, -32600, str(exc))
        if response is not None:
            output_stream.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")))
            output_stream.write("\n")
            output_stream.flush()


def _dispatch(request: Mapping[str, Any], tool: MemoryRecallTool) -> dict[str, Any] | None:
    if request.get("jsonrpc") != "2.0":
        raise ValueError("jsonrpc must be '2.0'")
    method = request.get("method")
    request_id = request.get("id")
    if request_id is None:
        return None
    if method == "initialize":
        params = request.get("params")
        requested_version = params.get("protocolVersion") if isinstance(params, Mapping) else None
        if requested_version not in {None, PROTOCOL_VERSION}:
            return _error(request_id, -32602, "Unsupported protocolVersion")
        return _result(
            request_id,
            {
                "protocolVersion": requested_version or PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        )
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(
            request_id,
            {
                "tools": [
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "inputSchema": tool.input_schema,
                    }
                ]
            },
        )
    if method == "tools/call":
        params = request.get("params")
        if not isinstance(params, Mapping) or params.get("name") != tool.name:
            return _result(request_id, _tool_error("unknown tool"))
        arguments = params.get("arguments", {})
        if not isinstance(arguments, Mapping):
            return _result(request_id, _tool_error("tool arguments must be an object"))
        try:
            payload = dict(tool.run(arguments))
        except Exception as exc:
            return _result(request_id, _tool_error(str(exc)))
        return _result(
            request_id,
            {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    }
                ],
                "structuredContent": payload,
                "isError": False,
            },
        )
    return _error(request_id, -32601, f"Method not found: {method}")


def _tool_error(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "isError": True}


def _result(request_id: Any, result: Mapping[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": dict(result)}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jarvis-memory-mcp")
    parser.add_argument("--config")
    args = parser.parse_args(argv)
    config = load_config(args.config)
    paths = resolve_runtime_paths(config)
    conn = initialize_database(paths.db_path)
    try:
        serve(sys.stdin, sys.stdout, MemoryRecallTool(MemoryArchive(conn)))
    finally:
        close_quietly(conn)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main", "serve"]
