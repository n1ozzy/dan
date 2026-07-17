"""Parity contracts for memory_recall transports."""

from __future__ import annotations

import io
import json
from importlib import import_module
from pathlib import Path

import pytest

from dan import cli as dan_cli
from dan.memory.archive import ArchiveDocument, MemoryArchive, memory_recall_to_dict
from dan.daemon.app import create_daemon_app
from dan.store.db import initialize_database
from dan.tools.registry import ToolRegistry, ToolRequest
from tests.test_api_smoke import request_json, running_server, write_config
from tests.test_cli_memory import config_args, memory_server


def _seed_archive(tmp_path: Path) -> tuple[MemoryArchive, dict[str, object]]:
    conn = initialize_database(tmp_path / "dan.db")
    archive = MemoryArchive(conn, now=lambda: "2026-07-16T12:00:00Z")
    archive.upsert(
        ArchiveDocument(
            source_type="gpt_transcript",
            source_uri="gpt:local:canonical",
            source_item_id="document",
            content="one shared recall result",
        )
    )
    expected = memory_recall_to_dict(archive.recall("shared recall", limit=5))
    return archive, expected


def test_memory_recall_tool_returns_canonical_payload(tmp_path: Path) -> None:
    tool_module = import_module("dan.tools.memory_recall_tool")
    archive, expected = _seed_archive(tmp_path)
    registry = ToolRegistry()
    registry.register(tool_module.MemoryRecallTool(archive))

    result = registry.execute_tool(
        ToolRequest(
            id="recall-1",
            tool_name="memory_recall",
            arguments={"query": "shared recall", "limit": 5},
            requested_by="model",
        )
    )

    assert result.status == "finished"
    assert result.output == expected


def test_memory_recall_rejects_unknown_arguments_through_registry(tmp_path: Path) -> None:
    tool_module = import_module("dan.tools.memory_recall_tool")
    archive, _expected = _seed_archive(tmp_path)
    registry = ToolRegistry()
    registry.register(tool_module.MemoryRecallTool(archive))

    result = registry.execute_tool(
        ToolRequest(
            id="recall-typo",
            tool_name="memory_recall",
            arguments={"query": "shared recall", "limt": 1},
            requested_by="model",
        )
    )

    assert result.status == "failed"
    assert "unexpected" in (result.error or "")


def test_memory_recall_api_returns_canonical_payload(tmp_path: Path) -> None:
    config_path = write_config(tmp_path / "dan.toml", tmp_path / "home" / "dan.db")
    app = create_daemon_app(config_path)
    try:
        assert app.conn is not None
        archive = MemoryArchive(app.conn, now=lambda: "2026-07-16T12:00:00Z")
        archive.upsert(
            ArchiveDocument(
                source_type="gpt_transcript",
                source_uri="gpt:local:canonical",
                source_item_id="document",
                content="one shared recall result",
            )
        )
        expected = memory_recall_to_dict(archive.recall("shared recall", limit=5))
        assert app.tool_registry.get("memory_recall").name == "memory_recall"
        app.start()

        with running_server(app) as base_url:
            status, payload = request_json(
                "POST",
                f"{base_url}/memory/recall",
                {"query": "shared recall", "limit": 5},
            )

        assert status == 200
        assert payload == expected
    finally:
        app.close()


def test_memory_recall_cli_returns_canonical_payload(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _archive, expected = _seed_archive(tmp_path)
    with memory_server(response_payload=expected) as (base_url, records):
        exit_code = dan_cli.main(
            [
                *config_args(),
                "memory",
                "recall",
                "shared recall",
                "--limit",
                "5",
                "--url",
                base_url,
            ]
        )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert json.loads(captured.out) == expected
    assert records == [
        {
            "method": "POST",
            "path": "/memory/recall",
            "headers": records[0]["headers"],
            "json": {"query": "shared recall", "limit": 5},
        }
    ]


def test_memory_recall_local_mcp_returns_canonical_structured_content(tmp_path: Path) -> None:
    mcp_module = import_module("dan.mcp.memory_server")
    tool_module = import_module("dan.tools.memory_recall_tool")
    archive, expected = _seed_archive(tmp_path)
    requests = io.StringIO(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18"},
            }
        )
        + "\n"
        + json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "memory_recall",
                    "arguments": {"query": "shared recall", "limit": 5},
                },
            }
        )
        + "\n"
    )
    responses = io.StringIO()

    mcp_module.serve(requests, responses, tool_module.MemoryRecallTool(archive))

    messages = [json.loads(line) for line in responses.getvalue().splitlines()]
    assert messages[0]["result"]["serverInfo"]["name"] == "dan-memory"
    assert messages[1]["result"]["structuredContent"] == expected
    assert messages[1]["result"]["isError"] is False


def test_memory_mcp_rejects_unsupported_protocol_version(tmp_path: Path) -> None:
    mcp_module = import_module("dan.mcp.memory_server")
    tool_module = import_module("dan.tools.memory_recall_tool")
    archive, _expected = _seed_archive(tmp_path)
    requests = io.StringIO(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "bogus"},
            }
        )
        + "\n"
    )
    responses = io.StringIO()

    mcp_module.serve(requests, responses, tool_module.MemoryRecallTool(archive))

    response = json.loads(responses.getvalue())
    assert response["error"]["code"] == -32602
