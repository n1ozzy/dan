"""Memory candidate evidence ledger repository and API tests."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from jarvis.daemon.app import DaemonApp, create_daemon_app
from jarvis.memory.evidence import (
    MemoryEvidenceConflict,
    MemoryEvidenceNotFound,
    MemoryEvidenceRepository,
    MemoryEvidenceValidationError,
)
from jarvis.memory.inbox import MemoryCandidateRepository
from jarvis.security.redaction import REDACTION_PLACEHOLDER
from jarvis.store.db import close_quietly, initialize_database
from jarvis.store.event_store import create_event_store
from tests.git_guards import assert_schema_and_migrations_unchanged
from tests.test_api_smoke import (
    request_json,
    request_raw,
    running_server,
    table_count,
    write_config,
)


ROOT = Path(__file__).resolve().parents[1]
RAW_QUOTE_SECRET = "sk-ant-evidencequote123"


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = initialize_database(tmp_path / "jarvis.db")
    try:
        yield connection
    finally:
        close_quietly(connection)


@pytest.fixture
def candidate_repo(conn: sqlite3.Connection) -> MemoryCandidateRepository:
    return MemoryCandidateRepository(conn, now=lambda: "2026-07-04T12:00:00+00:00")


@pytest.fixture
def repo(conn: sqlite3.Connection) -> MemoryEvidenceRepository:
    return MemoryEvidenceRepository(conn, now=lambda: "2026-07-04T12:00:00+00:00")


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    return write_config(tmp_path / "jarvis.toml", tmp_path / "home" / "jarvis.db")


@pytest.fixture
def app(config_path: Path) -> Iterator[DaemonApp]:
    daemon_app = create_daemon_app(config_path)
    try:
        yield daemon_app
    finally:
        daemon_app.close()


def create_candidate(repo: MemoryCandidateRepository, **overrides: object):
    payload: dict[str, object] = {
        "candidate_kind": "semantic",
        "scope": "project",
        "namespace": "project/jarvis/memory",
        "claim": "Candidate evidence stays provenance only.",
        "recommended_action": "approve",
    }
    payload.update(overrides)
    return repo.create_candidate(**payload)  # type: ignore[arg-type]


def candidate_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "candidate_kind": "semantic",
        "scope": "project",
        "namespace": "project/jarvis/memory",
        "claim": "Candidate evidence is review provenance.",
        "recommended_action": "approve",
    }
    payload.update(overrides)
    return payload


def evidence_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "source_type": "conversation_turn",
        "source_id": "source-1",
        "conversation_id": "conversation-1",
        "turn_id": "turn-1",
        "event_id": 7,
        "quote": f"Quote includes {RAW_QUOTE_SECRET}",
        "weight": 0.75,
    }
    payload.update(overrides)
    return payload


def evidence_events(app: DaemonApp) -> list[dict[str, object]]:
    assert app.event_store is not None
    return [
        dict(event.payload)
        for event in app.event_store.list_after(0, limit=100)
        if event.type == "memory.evidence.created"
    ]


def test_add_evidence_for_needs_review_candidate(
    repo: MemoryEvidenceRepository,
    candidate_repo: MemoryCandidateRepository,
) -> None:
    candidate = create_candidate(candidate_repo)

    evidence = repo.add_evidence(candidate.id, **evidence_payload())

    assert evidence.candidate_id == candidate.id
    assert evidence.source_type == "conversation_turn"
    assert evidence.source_id == "source-1"
    assert evidence.conversation_id == "conversation-1"
    assert evidence.turn_id == "turn-1"
    assert evidence.event_id == 7
    assert evidence.weight == 0.75
    assert evidence.created_at == "2026-07-04T12:00:00+00:00"


def test_list_evidence_for_candidate(
    repo: MemoryEvidenceRepository,
    candidate_repo: MemoryCandidateRepository,
) -> None:
    first_candidate = create_candidate(candidate_repo, claim="First candidate")
    second_candidate = create_candidate(candidate_repo, claim="Second candidate")
    first = repo.add_evidence(first_candidate.id, **evidence_payload(source_id="source-a"))
    second = repo.add_evidence(first_candidate.id, **evidence_payload(source_id="source-b"))
    repo.add_evidence(second_candidate.id, **evidence_payload(source_id="source-c"))

    assert repo.list_evidence(first_candidate.id) == [first, second]


def test_evidence_quote_is_redacted_before_persistence(
    repo: MemoryEvidenceRepository,
    candidate_repo: MemoryCandidateRepository,
    conn: sqlite3.Connection,
) -> None:
    candidate = create_candidate(candidate_repo)

    evidence = repo.add_evidence(candidate.id, **evidence_payload())

    stored_quote = conn.execute(
        "SELECT quote FROM memory_evidence WHERE id = ?",
        (evidence.id,),
    ).fetchone()[0]
    stored_observation = conn.execute(
        "SELECT observed_text FROM memory_observations WHERE id = ?",
        (evidence.observation_id,),
    ).fetchone()[0]
    for text in (evidence.quote, stored_quote, stored_observation):
        assert text is not None
        assert RAW_QUOTE_SECRET not in text
        assert REDACTION_PLACEHOLDER in text


def test_evidence_requires_existing_candidate(repo: MemoryEvidenceRepository) -> None:
    with pytest.raises(MemoryEvidenceNotFound):
        repo.add_evidence("missing", **evidence_payload())


def test_evidence_requires_candidate_status_needs_review(
    repo: MemoryEvidenceRepository,
    candidate_repo: MemoryCandidateRepository,
) -> None:
    candidate = create_candidate(candidate_repo)
    candidate_repo.approve_candidate(candidate.id)

    with pytest.raises(MemoryEvidenceConflict):
        repo.add_evidence(candidate.id, **evidence_payload())


def test_evidence_cannot_be_added_to_approved_candidate(
    repo: MemoryEvidenceRepository,
    candidate_repo: MemoryCandidateRepository,
) -> None:
    approved = candidate_repo.approve_candidate(create_candidate(candidate_repo).id)

    with pytest.raises(MemoryEvidenceConflict):
        repo.add_evidence(approved.id, **evidence_payload())


def test_evidence_cannot_be_added_to_rejected_candidate(
    repo: MemoryEvidenceRepository,
    candidate_repo: MemoryCandidateRepository,
) -> None:
    rejected = candidate_repo.reject_candidate(create_candidate(candidate_repo).id)

    with pytest.raises(MemoryEvidenceConflict):
        repo.add_evidence(rejected.id, **evidence_payload())


def test_evidence_requires_at_least_one_locator(
    repo: MemoryEvidenceRepository,
    candidate_repo: MemoryCandidateRepository,
) -> None:
    candidate = create_candidate(candidate_repo)

    with pytest.raises(MemoryEvidenceValidationError):
        repo.add_evidence(
            candidate.id,
            **evidence_payload(
                source_id=None,
                conversation_id=None,
                turn_id=None,
                event_id=None,
                quote=None,
            ),
        )


@pytest.mark.parametrize("weight", [0, -0.1, 1.1, "heavy", True])
def test_invalid_weight_is_rejected(
    repo: MemoryEvidenceRepository,
    candidate_repo: MemoryCandidateRepository,
    weight: object,
) -> None:
    candidate = create_candidate(candidate_repo)

    with pytest.raises(MemoryEvidenceValidationError):
        repo.add_evidence(candidate.id, **evidence_payload(weight=weight))


def test_evidence_does_not_create_memory_items(
    repo: MemoryEvidenceRepository,
    candidate_repo: MemoryCandidateRepository,
    conn: sqlite3.Connection,
) -> None:
    candidate = create_candidate(candidate_repo)

    repo.add_evidence(candidate.id, **evidence_payload())

    assert _table_count(conn, "memory_items") == 0


def test_evidence_does_not_create_memory_blocks(
    repo: MemoryEvidenceRepository,
    candidate_repo: MemoryCandidateRepository,
    conn: sqlite3.Connection,
) -> None:
    candidate = create_candidate(candidate_repo)

    repo.add_evidence(candidate.id, **evidence_payload())

    assert _table_count(conn, "memory_blocks") == 0


def test_evidence_create_emits_concise_event_without_quote_or_secret(
    conn: sqlite3.Connection,
    candidate_repo: MemoryCandidateRepository,
) -> None:
    event_store = create_event_store(conn)
    repo = MemoryEvidenceRepository(
        conn,
        event_store=event_store,
        now=lambda: "2026-07-04T12:00:00+00:00",
    )
    candidate = create_candidate(candidate_repo)
    observation_count = _table_count(conn, "memory_observations")
    evidence_count = _table_count(conn, "memory_evidence")

    evidence = repo.add_evidence(candidate.id, **evidence_payload())

    assert _table_count(conn, "memory_observations") == observation_count + 1
    assert _table_count(conn, "memory_evidence") == evidence_count + 1
    events = event_store.list_after(0, limit=10)
    assert [event.type for event in events] == ["memory.evidence.created"]
    assert events[0].payload == {
        "evidence_id": evidence.id,
        "candidate_id": candidate.id,
        "source_type": "conversation_turn",
        "has_quote": True,
        "has_conversation_id": True,
        "has_turn_id": True,
        "has_event_id": True,
        "weight": 0.75,
    }
    rendered_payload = json.dumps(events[0].payload, sort_keys=True)
    assert "quote" not in events[0].payload
    assert RAW_QUOTE_SECRET not in rendered_payload
    assert "claim" not in events[0].payload
    assert "title" not in events[0].payload
    assert "reason" not in events[0].payload


@pytest.mark.parametrize(
    ("decide_method", "final_status"),
    [
        ("approve_candidate", "approved"),
        ("reject_candidate", "rejected"),
    ],
)
def test_add_evidence_rechecks_candidate_status_at_write_time(
    conn: sqlite3.Connection,
    candidate_repo: MemoryCandidateRepository,
    decide_method: str,
    final_status: str,
) -> None:
    event_store = create_event_store(conn)
    repo = MemoryEvidenceRepository(
        conn,
        event_store=event_store,
        now=lambda: "2026-07-04T12:00:00+00:00",
    )
    candidate = create_candidate(candidate_repo)
    original_candidate_status = repo._candidate_status
    decided = False

    def status_then_decide(candidate_id: str) -> str | None:
        nonlocal decided
        status = original_candidate_status(candidate_id)
        if candidate_id == candidate.id and not decided:
            decided = True
            getattr(candidate_repo, decide_method)(candidate.id)
        return status

    repo._candidate_status = status_then_decide  # type: ignore[method-assign]
    observation_count = _table_count(conn, "memory_observations")
    evidence_count = _table_count(conn, "memory_evidence")

    with pytest.raises(MemoryEvidenceConflict):
        repo.add_evidence(candidate.id, **evidence_payload())

    decided_candidate = candidate_repo.get_candidate(candidate.id)
    assert decided is True
    assert decided_candidate is not None
    assert decided_candidate.status == final_status
    assert _table_count(conn, "memory_observations") == observation_count
    assert _table_count(conn, "memory_evidence") == evidence_count
    assert [
        event.type for event in event_store.list_after(0, limit=10)
    ] == []


def test_post_memory_candidate_evidence_creates_evidence(app: DaemonApp) -> None:
    app.start()

    with running_server(app) as base_url:
        _, created = request_json("POST", f"{base_url}/memory/candidates", candidate_payload())
        status, payload = request_json(
            "POST",
            f"{base_url}/memory/candidates/{created['candidate']['id']}/evidence",
            evidence_payload(),
        )

    assert status == 201
    assert payload["ok"] is True
    evidence = payload["evidence"]
    assert evidence["candidate_id"] == created["candidate"]["id"]
    assert evidence["source_type"] == "conversation_turn"
    assert RAW_QUOTE_SECRET not in evidence["quote"]
    assert REDACTION_PLACEHOLDER in evidence["quote"]
    assert table_count(app, "memory_items") == 0
    assert table_count(app, "memory_blocks") == 0


def test_get_memory_candidate_evidence_lists_evidence(app: DaemonApp) -> None:
    app.start()

    with running_server(app) as base_url:
        _, created = request_json("POST", f"{base_url}/memory/candidates", candidate_payload())
        _, first = request_json(
            "POST",
            f"{base_url}/memory/candidates/{created['candidate']['id']}/evidence",
            evidence_payload(source_id="source-a"),
        )
        _, second = request_json(
            "POST",
            f"{base_url}/memory/candidates/{created['candidate']['id']}/evidence",
            evidence_payload(source_id="source-b"),
        )
        status, payload = request_json(
            "GET",
            f"{base_url}/memory/candidates/{created['candidate']['id']}/evidence",
        )

    assert status == 200
    assert payload["ok"] is True
    assert payload["candidate_id"] == created["candidate"]["id"]
    assert payload["evidence"] == [first["evidence"], second["evidence"]]


def test_missing_candidate_evidence_returns_json_404(app: DaemonApp) -> None:
    app.start()

    with running_server(app) as base_url:
        status, content_type, body = request_raw(
            "POST",
            f"{base_url}/memory/candidates/missing/evidence",
            evidence_payload(),
        )

    assert status == 404
    assert "application/json" in content_type
    assert "<html" not in body.lower()
    assert json.loads(body)["status"] == 404


def test_invalid_evidence_payload_returns_json_400(app: DaemonApp) -> None:
    app.start()

    with running_server(app) as base_url:
        _, created = request_json("POST", f"{base_url}/memory/candidates", candidate_payload())
        status, content_type, body = request_raw(
            "POST",
            f"{base_url}/memory/candidates/{created['candidate']['id']}/evidence",
            {"source_type": "", "quote": 123, "weight": 2},
        )

    assert status == 400
    assert "application/json" in content_type
    assert "<html" not in body.lower()
    assert json.loads(body)["status"] == 400


def test_decided_candidate_evidence_returns_json_409(app: DaemonApp) -> None:
    app.start()

    with running_server(app) as base_url:
        _, created = request_json("POST", f"{base_url}/memory/candidates", candidate_payload())
        request_json(
            "POST",
            f"{base_url}/memory/candidates/{created['candidate']['id']}/approve",
        )
        status, content_type, body = request_raw(
            "POST",
            f"{base_url}/memory/candidates/{created['candidate']['id']}/evidence",
            evidence_payload(),
        )

    assert status == 409
    assert "application/json" in content_type
    assert "<html" not in body.lower()
    assert json.loads(body)["status"] == 409


def test_get_missing_candidate_evidence_returns_json_404(app: DaemonApp) -> None:
    app.start()

    with running_server(app) as base_url:
        status, content_type, body = request_raw(
            "GET",
            f"{base_url}/memory/candidates/missing/evidence",
        )

    assert status == 404
    assert "application/json" in content_type
    assert "<html" not in body.lower()
    assert json.loads(body)["status"] == 404


def test_evidence_api_emits_created_event_without_quote_or_secret(app: DaemonApp) -> None:
    app.start()

    with running_server(app) as base_url:
        _, created = request_json("POST", f"{base_url}/memory/candidates", candidate_payload())
        _, posted = request_json(
            "POST",
            f"{base_url}/memory/candidates/{created['candidate']['id']}/evidence",
            evidence_payload(),
        )

    payloads = evidence_events(app)
    assert payloads == [
        {
            "evidence_id": posted["evidence"]["id"],
            "candidate_id": created["candidate"]["id"],
            "source_type": "conversation_turn",
            "has_quote": True,
            "has_conversation_id": True,
            "has_turn_id": True,
            "has_event_id": True,
            "weight": 0.75,
        }
    ]
    rendered_payload = json.dumps(payloads, sort_keys=True)
    assert RAW_QUOTE_SECRET not in rendered_payload
    assert "quote" not in payloads[0]


def test_memory_evidence_keeps_schema_and_migrations_unchanged() -> None:
    assert_schema_and_migrations_unchanged(ROOT)


def _table_count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
