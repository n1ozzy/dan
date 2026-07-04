"""Memory Inbox candidate repository and API tests."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from jarvis.daemon.app import DaemonApp, create_daemon_app
from jarvis.memory.inbox import (
    MemoryCandidateConflict,
    MemoryCandidateNotFound,
    MemoryCandidateRepository,
)
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


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = initialize_database(tmp_path / "jarvis.db")
    try:
        yield connection
    finally:
        close_quietly(connection)


@pytest.fixture
def repo(conn: sqlite3.Connection) -> MemoryCandidateRepository:
    return MemoryCandidateRepository(conn, now=lambda: "2026-07-04T12:00:00+00:00")


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


def create_candidate(
    repo: MemoryCandidateRepository,
    **overrides: object,
):
    payload: dict[str, object] = {
        "candidate_kind": "semantic",
        "scope": "project",
        "namespace": "project/jarvis/memory",
        "claim": "Memory Inbox candidates are review queue items.",
        "recommended_action": "approve",
    }
    payload.update(overrides)
    return repo.create_candidate(**payload)  # type: ignore[arg-type]


def candidate_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "candidate_kind": "semantic",
        "scope": "project",
        "namespace": "project/jarvis/memory",
        "claim": "Candidate review does not activate memory.",
        "recommended_action": "approve",
    }
    payload.update(overrides)
    return payload


def candidate_event_payloads(app: DaemonApp) -> list[dict[str, object]]:
    assert app.event_store is not None
    return [
        dict(event.payload)
        for event in app.event_store.list_after(0, limit=100)
        if event.type.startswith("memory.candidate.")
    ]


def test_create_candidate_defaults_to_needs_review(repo: MemoryCandidateRepository) -> None:
    candidate = create_candidate(repo)

    assert candidate.status == "needs_review"
    assert candidate.confidence == "unknown"
    assert candidate.sensitivity == "unknown"
    assert candidate.reviewed_at is None


def test_create_candidate_stores_required_and_optional_fields(
    repo: MemoryCandidateRepository,
) -> None:
    candidate = create_candidate(
        repo,
        title="Memory Inbox",
        reason="User asked to remember a project rule.",
        confidence="high",
        sensitivity="low",
        target_memory_id="memory-old",
        extra_ignored="not persisted",
    )

    assert candidate.candidate_kind == "semantic"
    assert candidate.scope == "project"
    assert candidate.namespace == "project/jarvis/memory"
    assert candidate.claim == "Memory Inbox candidates are review queue items."
    assert candidate.title == "Memory Inbox"
    assert candidate.reason == "User asked to remember a project rule."
    assert candidate.confidence == "high"
    assert candidate.sensitivity == "low"
    assert candidate.recommended_action == "approve"
    assert candidate.target_memory_id == "memory-old"
    assert not hasattr(candidate, "extra_ignored")


def test_create_candidate_redacts_secret_text_fields_before_persistence(
    repo: MemoryCandidateRepository,
    conn: sqlite3.Connection,
) -> None:
    raw_claim_secret = "sk-ant-memorycandidate123"
    raw_title_secret = "title-secret-abc.def"
    raw_reason_secret = "reasonsecret123"

    candidate = create_candidate(
        repo,
        claim=f"Candidate includes {raw_claim_secret}",
        title=f"Authorization: Bearer {raw_title_secret}",
        reason=f"api_key={raw_reason_secret}",
    )

    stored = conn.execute(
        "SELECT claim, title, reason FROM memory_candidates WHERE id = ?",
        (candidate.id,),
    ).fetchone()
    assert stored is not None
    for text in (candidate.claim, candidate.title, candidate.reason, *stored):
        assert text is not None
        assert raw_claim_secret not in text
        assert raw_title_secret not in text
        assert raw_reason_secret not in text
        assert REDACTION_PLACEHOLDER in text


def test_list_candidates_returns_all(repo: MemoryCandidateRepository) -> None:
    first = create_candidate(repo, claim="First candidate")
    second = create_candidate(repo, claim="Second candidate")

    assert [candidate.id for candidate in repo.list_candidates()] == [first.id, second.id]


def test_list_candidates_filters_by_status(repo: MemoryCandidateRepository) -> None:
    pending = create_candidate(repo, claim="Pending candidate")
    approved = repo.approve_candidate(create_candidate(repo, claim="Approved candidate").id)

    assert [candidate.id for candidate in repo.list_candidates(status="needs_review")] == [
        pending.id
    ]
    assert [candidate.id for candidate in repo.list_candidates(status="approved")] == [
        approved.id
    ]


def test_get_candidate_by_id_works(repo: MemoryCandidateRepository) -> None:
    candidate = create_candidate(repo)

    assert repo.get_candidate(candidate.id) == candidate


def test_missing_candidate_returns_none(repo: MemoryCandidateRepository) -> None:
    assert repo.get_candidate("missing") is None


def test_approve_candidate_sets_status_approved_and_reviewed_at(
    repo: MemoryCandidateRepository,
) -> None:
    candidate = create_candidate(repo)

    approved = repo.approve_candidate(candidate.id)

    assert approved.status == "approved"
    assert approved.reviewed_at == "2026-07-04T12:00:00+00:00"


def test_reject_candidate_sets_status_rejected_and_reviewed_at(
    repo: MemoryCandidateRepository,
) -> None:
    candidate = create_candidate(repo)

    rejected = repo.reject_candidate(candidate.id)

    assert rejected.status == "rejected"
    assert rejected.reviewed_at == "2026-07-04T12:00:00+00:00"


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_duplicate_approve_or_reject_returns_conflict(
    repo: MemoryCandidateRepository,
    decision: str,
) -> None:
    candidate = create_candidate(repo)
    decided = (
        repo.approve_candidate(candidate.id)
        if decision == "approve"
        else repo.reject_candidate(candidate.id)
    )

    with pytest.raises(MemoryCandidateConflict):
        if decision == "approve":
            repo.approve_candidate(decided.id)
        else:
            repo.reject_candidate(decided.id)


def test_approve_rejected_and_reject_approved_return_conflict(
    repo: MemoryCandidateRepository,
) -> None:
    approved = repo.approve_candidate(create_candidate(repo, claim="Approve first").id)
    rejected = repo.reject_candidate(create_candidate(repo, claim="Reject first").id)

    with pytest.raises(MemoryCandidateConflict):
        repo.reject_candidate(approved.id)
    with pytest.raises(MemoryCandidateConflict):
        repo.approve_candidate(rejected.id)


def test_deciding_missing_candidate_raises_not_found(repo: MemoryCandidateRepository) -> None:
    with pytest.raises(MemoryCandidateNotFound):
        repo.approve_candidate("missing")
    with pytest.raises(MemoryCandidateNotFound):
        repo.reject_candidate("missing")


def test_approve_does_not_create_memory_items_or_memory_blocks(
    repo: MemoryCandidateRepository,
    conn: sqlite3.Connection,
) -> None:
    repo.approve_candidate(create_candidate(repo).id)

    assert _table_count(conn, "memory_items") == 0
    assert _table_count(conn, "memory_blocks") == 0


def test_reject_does_not_create_memory_items_or_memory_blocks(
    repo: MemoryCandidateRepository,
    conn: sqlite3.Connection,
) -> None:
    repo.reject_candidate(create_candidate(repo).id)

    assert _table_count(conn, "memory_items") == 0
    assert _table_count(conn, "memory_blocks") == 0


def test_candidate_create_approve_reject_emit_concise_events(conn: sqlite3.Connection) -> None:
    event_store = create_event_store(conn)
    repo = MemoryCandidateRepository(
        conn,
        event_store=event_store,
        now=lambda: "2026-07-04T12:00:00+00:00",
    )

    created = create_candidate(repo, target_memory_id="memory-old")
    approved = repo.approve_candidate(created.id)
    rejected = repo.reject_candidate(create_candidate(repo, claim="Reject event").id)

    events = event_store.list_after(0, limit=10)

    assert [event.type for event in events] == [
        "memory.candidate.created",
        "memory.candidate.approved",
        "memory.candidate.created",
        "memory.candidate.rejected",
    ]
    assert events[0].payload == {
        "candidate_id": created.id,
        "candidate_kind": "semantic",
        "scope": "project",
        "namespace": "project/jarvis/memory",
        "status": "needs_review",
        "target_memory_id": "memory-old",
    }
    assert events[1].payload["candidate_id"] == approved.id
    assert events[1].payload["status"] == "approved"
    assert events[3].payload["candidate_id"] == rejected.id
    assert events[3].payload["status"] == "rejected"
    assert [event.type for event in events].count("memory.candidate.approved") == 1
    assert [event.type for event in events].count("memory.candidate.rejected") == 1
    assert "claim" not in events[0].payload


def test_decision_conflict_after_guarded_update_affects_zero_rows_emits_no_event(
    conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event_store = create_event_store(conn)
    repo = MemoryCandidateRepository(
        conn,
        event_store=event_store,
        now=lambda: "2026-07-04T12:00:00+00:00",
    )
    candidate = create_candidate(repo)
    original_get_candidate = repo.get_candidate
    calls = 0

    def get_candidate_then_competing_decision(candidate_id: str):
        nonlocal calls
        result = original_get_candidate(candidate_id)
        calls += 1
        if calls == 1:
            with conn:
                conn.execute(
                    """
                    UPDATE memory_candidates
                    SET status = ?, reviewed_at = ?
                    WHERE id = ?
                    """,
                    ("rejected", "2026-07-04T12:01:00+00:00", candidate_id),
                )
        return result

    monkeypatch.setattr(repo, "get_candidate", get_candidate_then_competing_decision)

    with pytest.raises(MemoryCandidateConflict):
        repo.approve_candidate(candidate.id)

    assert [event.type for event in event_store.list_after(0, limit=10)] == [
        "memory.candidate.created"
    ]


def test_post_memory_candidates_creates_candidate(app: DaemonApp) -> None:
    app.start()

    with running_server(app) as base_url:
        status, payload = request_json("POST", f"{base_url}/memory/candidates", candidate_payload())

    assert status == 201
    assert payload["ok"] is True
    candidate = payload["candidate"]
    assert candidate["id"]
    assert candidate["status"] == "needs_review"
    assert table_count(app, "memory_items") == 0
    assert table_count(app, "memory_blocks") == 0


def test_post_memory_candidates_redacts_secret_claim_before_persistence(
    app: DaemonApp,
) -> None:
    raw_secret = "sk-ant-candidateapi123"
    app.start()

    with running_server(app) as base_url:
        status, payload = request_json(
            "POST",
            f"{base_url}/memory/candidates",
            candidate_payload(claim=f"Candidate says {raw_secret}"),
        )

    assert status == 201
    candidate = payload["candidate"]
    assert isinstance(candidate, dict)
    assert raw_secret not in candidate["claim"]
    assert REDACTION_PLACEHOLDER in candidate["claim"]
    assert app.conn is not None
    stored_claim = app.conn.execute(
        "SELECT claim FROM memory_candidates WHERE id = ?",
        (candidate["id"],),
    ).fetchone()[0]
    assert raw_secret not in stored_claim
    assert REDACTION_PLACEHOLDER in stored_claim
    for event_payload in candidate_event_payloads(app):
        assert "claim" not in event_payload
        assert "title" not in event_payload
        assert "reason" not in event_payload


def test_get_memory_candidates_lists_candidates(app: DaemonApp) -> None:
    app.start()

    with running_server(app) as base_url:
        _, created = request_json("POST", f"{base_url}/memory/candidates", candidate_payload())
        status, payload = request_json("GET", f"{base_url}/memory/candidates")

    assert status == 200
    assert payload["ok"] is True
    assert payload["candidates"] == [created["candidate"]]


def test_get_memory_candidates_filters_by_status(app: DaemonApp) -> None:
    app.start()

    with running_server(app) as base_url:
        _, pending = request_json(
            "POST",
            f"{base_url}/memory/candidates",
            candidate_payload(claim="Pending"),
        )
        _, rejected = request_json(
            "POST",
            f"{base_url}/memory/candidates",
            candidate_payload(claim="Rejected"),
        )
        request_json("POST", f"{base_url}/memory/candidates/{rejected['candidate']['id']}/reject")
        status, payload = request_json("GET", f"{base_url}/memory/candidates?status=needs_review")

    assert status == 200
    assert payload["ok"] is True
    assert [candidate["id"] for candidate in payload["candidates"]] == [
        pending["candidate"]["id"]
    ]


def test_get_memory_candidate_by_id_returns_candidate(app: DaemonApp) -> None:
    app.start()

    with running_server(app) as base_url:
        _, created = request_json("POST", f"{base_url}/memory/candidates", candidate_payload())
        status, payload = request_json(
            "GET",
            f"{base_url}/memory/candidates/{created['candidate']['id']}",
        )

    assert status == 200
    assert payload["ok"] is True
    assert payload["candidate"] == created["candidate"]


def test_post_approve_changes_status_to_approved(app: DaemonApp) -> None:
    app.start()

    with running_server(app) as base_url:
        _, created = request_json("POST", f"{base_url}/memory/candidates", candidate_payload())
        status, payload = request_json(
            "POST",
            f"{base_url}/memory/candidates/{created['candidate']['id']}/approve",
        )

    assert status == 200
    assert payload["ok"] is True
    assert payload["candidate"]["status"] == "approved"
    assert payload["candidate"]["reviewed_at"] is not None
    assert table_count(app, "memory_items") == 0
    assert table_count(app, "memory_blocks") == 0


def test_post_reject_changes_status_to_rejected(app: DaemonApp) -> None:
    app.start()

    with running_server(app) as base_url:
        _, created = request_json("POST", f"{base_url}/memory/candidates", candidate_payload())
        status, payload = request_json(
            "POST",
            f"{base_url}/memory/candidates/{created['candidate']['id']}/reject",
        )

    assert status == 200
    assert payload["ok"] is True
    assert payload["candidate"]["status"] == "rejected"
    assert payload["candidate"]["reviewed_at"] is not None
    assert table_count(app, "memory_items") == 0
    assert table_count(app, "memory_blocks") == 0


def test_missing_candidate_returns_json_404(app: DaemonApp) -> None:
    app.start()

    with running_server(app) as base_url:
        status, content_type, body = request_raw(
            "GET",
            f"{base_url}/memory/candidates/missing",
        )

    assert status == 404
    assert "application/json" in content_type
    assert "<html" not in body.lower()
    assert json.loads(body)["status"] == 404


@pytest.mark.parametrize(
    "payload",
    [
        ["not-object"],
        {"candidate_kind": "", "scope": "project", "namespace": "n", "claim": "c", "recommended_action": "approve"},
        {"candidate_kind": "semantic", "scope": "project", "namespace": "n", "claim": "c"},
        {"candidate_kind": "semantic", "scope": "project", "namespace": "n", "claim": "c", "recommended_action": "approve", "title": 123},
    ],
)
def test_invalid_candidate_payload_returns_json_400(
    app: DaemonApp,
    payload: object,
) -> None:
    app.start()

    with running_server(app) as base_url:
        status, content_type, body = request_raw(
            "POST",
            f"{base_url}/memory/candidates",
            payload,
        )

    assert status == 400
    assert "application/json" in content_type
    assert "<html" not in body.lower()
    assert json.loads(body)["status"] == 400


@pytest.mark.parametrize(
    ("first_action", "second_action"),
    [
        ("approve", "approve"),
        ("approve", "reject"),
        ("reject", "reject"),
        ("reject", "approve"),
    ],
)
def test_duplicate_or_cross_decision_returns_json_409(
    app: DaemonApp,
    first_action: str,
    second_action: str,
) -> None:
    app.start()

    with running_server(app) as base_url:
        _, created = request_json("POST", f"{base_url}/memory/candidates", candidate_payload())
        request_json(
            "POST",
            f"{base_url}/memory/candidates/{created['candidate']['id']}/{first_action}",
        )
        status, content_type, body = request_raw(
            "POST",
            f"{base_url}/memory/candidates/{created['candidate']['id']}/{second_action}",
        )

    assert status == 409
    assert "application/json" in content_type
    assert "<html" not in body.lower()
    assert json.loads(body)["status"] == 409


def test_candidate_api_emits_created_approved_and_rejected_events(app: DaemonApp) -> None:
    app.start()

    with running_server(app) as base_url:
        _, approved = request_json(
            "POST",
            f"{base_url}/memory/candidates",
            candidate_payload(target_memory_id="memory-old"),
        )
        _, rejected = request_json(
            "POST",
            f"{base_url}/memory/candidates",
            candidate_payload(claim="Reject through API"),
        )
        request_json("POST", f"{base_url}/memory/candidates/{approved['candidate']['id']}/approve")
        request_json("POST", f"{base_url}/memory/candidates/{rejected['candidate']['id']}/reject")

    payloads = candidate_event_payloads(app)
    assert [payload["status"] for payload in payloads] == [
        "needs_review",
        "needs_review",
        "approved",
        "rejected",
    ]
    assert payloads[0]["candidate_id"] == approved["candidate"]["id"]
    assert payloads[0]["target_memory_id"] == "memory-old"
    assert "claim" not in payloads[0]


def test_memory_inbox_keeps_schema_and_migrations_unchanged() -> None:
    assert_schema_and_migrations_unchanged(ROOT)


def _table_count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
