"""Task 8: `dan queue` list/cancel/flush contract against a real daemon API."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from dan import cli as dan_cli
from dan.daemon.app import DaemonApp

from tests.test_api_smoke import running_server
from tests.test_voice_api_contract import make_voice_app, queue_rows
from tests.voice_helpers import speech_intent


@pytest.fixture
def voice_app(tmp_path: Path) -> Iterator[DaemonApp]:
    app = make_voice_app(tmp_path)
    try:
        yield app
    finally:
        app.close()


def run_cli(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, str]:
    rc = dan_cli.main(list(args))
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


def cli_args(app: DaemonApp, base_url: str, *rest: str) -> list[str]:
    return ["--config", str(app.config.source_path), *rest, "--url", base_url]


def assert_single_json_object(out: str) -> dict[str, object]:
    stripped = out.strip()
    assert stripped
    assert "\n" not in stripped, "JSON mode must print exactly one object"
    payload = json.loads(stripped)
    assert isinstance(payload, dict)
    return payload


def test_queue_list_json_prints_single_object(
    voice_app: DaemonApp, capsys: pytest.CaptureFixture[str]
) -> None:
    assert voice_app.voice_service is not None
    request = voice_app.voice_service.submit(speech_intent("hello", session="listing"))

    with running_server(voice_app) as base_url:
        rc, out, _err = run_cli(
            capsys, *cli_args(voice_app, base_url, "queue", "list", "--json")
        )

    payload = assert_single_json_object(out)
    assert rc == 0
    rows = payload["voice_queue"]
    assert isinstance(rows, list)
    assert request.id in {row["id"] for row in rows}
    assert all("text" not in row for row in rows)


def test_queue_flush_is_session_scoped(
    voice_app: DaemonApp, capsys: pytest.CaptureFixture[str]
) -> None:
    assert voice_app.voice_service is not None
    radio = voice_app.voice_service.submit(speech_intent("radio", session="radio"))
    standup = voice_app.voice_service.submit(
        speech_intent("standup", session="standup")
    )

    with running_server(voice_app) as base_url:
        rc, out, _err = run_cli(
            capsys,
            *cli_args(
                voice_app, base_url, "queue", "flush", "--session", "radio", "--json"
            ),
        )

    payload = assert_single_json_object(out)
    assert rc == 0
    assert payload["session"] == "radio"
    statuses = {row["id"]: row["status"] for row in queue_rows(voice_app)}
    assert statuses[radio.id] == "cancelled"
    assert statuses[standup.id] == "queued"


def test_queue_flush_requires_session_flag(
    voice_app: DaemonApp, capsys: pytest.CaptureFixture[str]
) -> None:
    with running_server(voice_app) as base_url:
        with pytest.raises(SystemExit):
            run_cli(capsys, *cli_args(voice_app, base_url, "queue", "flush"))


def test_queue_cancel_request(
    voice_app: DaemonApp, capsys: pytest.CaptureFixture[str]
) -> None:
    assert voice_app.voice_service is not None
    first = voice_app.voice_service.submit(speech_intent("first", session="c"))
    second = voice_app.voice_service.submit(
        speech_intent("second", session="c", utterance_index=1)
    )

    with running_server(voice_app) as base_url:
        rc, out, _err = run_cli(
            capsys,
            *cli_args(voice_app, base_url, "queue", "cancel", first.id, "--json"),
        )

    payload = assert_single_json_object(out)
    assert rc == 0
    assert payload["request_id"] == first.id
    statuses = {row["id"]: row["status"] for row in queue_rows(voice_app)}
    assert statuses[first.id] == "cancelled"
    assert statuses[second.id] == "queued"


def test_queue_cancel_unknown_request_is_nonzero(
    voice_app: DaemonApp, capsys: pytest.CaptureFixture[str]
) -> None:
    with running_server(voice_app) as base_url:
        rc, out, _err = run_cli(
            capsys,
            *cli_args(voice_app, base_url, "queue", "cancel", "no-such-id", "--json"),
        )

    payload = assert_single_json_object(out)
    assert rc != 0
    assert "error" in payload
