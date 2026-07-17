"""Memory item activation repository and API tests."""

from __future__ import annotations

import json
import queue
import sqlite3
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from dan.daemon.app import DaemonApp, create_daemon_app
from dan.memory.evidence import MemoryEvidenceRepository
from dan.memory.inbox import MemoryCandidateRepository
from dan.memory.items import (
    MemoryItemConflict,
    MemoryItemNotFound,
    MemoryItemRepository,
    canonical_key_for_candidate,
)
from dan.store.db import close_quietly, connect_db, initialize_database
from dan.store.event_store import create_event_store
from tests.git_guards import assert_schema_and_migrations_unchanged
from tests.test_api_smoke import (
    request_json,
    request_raw,
    running_server,
    table_count,
    write_config,
)


ROOT = Path(__file__).resolve().parents[1]
RAW_QUOTE_SECRET = "sk-ant-activatequote123"


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = initialize_database(tmp_path / "dan.db")
    try:
        yield connection
    finally:
        close_quietly(connection)


@pytest.fixture
def candidate_repo(conn: sqlite3.Connection) -> MemoryCandidateRepository:
    return MemoryCandidateRepository(conn, now=lambda: "2026-07-04T12:00:00+00:00")


@pytest.fixture
def evidence_repo(conn: sqlite3.Connection) -> MemoryEvidenceRepository:
    return MemoryEvidenceRepository(conn, now=lambda: "2026-07-04T12:00:00+00:00")


@pytest.fixture
def repo(conn: sqlite3.Connection) -> MemoryItemRepository:
    return MemoryItemRepository(conn, now=lambda: "2026-07-04T12:01:00+00:00")


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    return write_config(tmp_path / "dan.toml", tmp_path / "home" / "dan.db")


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
        "namespace": "project/dan/memory",
        "claim": "Approved candidates activate into memory items.",
        "title": "Memory Activation",
        "recommended_action": "approve",
        "confidence": "high",
        "sensitivity": "low",
    }
    payload.update(overrides)
    return repo.create_candidate(**payload)  # type: ignore[arg-type]


def candidate_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "candidate_kind": "semantic",
        "scope": "project",
        "namespace": "project/dan/memory",
        "claim": "Approved API candidates activate into memory items.",
        "title": "Memory Activation API",
        "recommended_action": "approve",
        "confidence": "high",
        "sensitivity": "low",
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


def approved_candidate_with_evidence(
    candidate_repo: MemoryCandidateRepository,
    evidence_repo: MemoryEvidenceRepository,
    **candidate_overrides: object,
):
    candidate = create_candidate(candidate_repo, **candidate_overrides)
    evidence_repo.add_evidence(candidate.id, **evidence_payload())
    return candidate_repo.approve_candidate(candidate.id)


def activation_events(conn: sqlite3.Connection) -> list[dict[str, object]]:
    return [
        dict(event.payload)
        for event in create_event_store(conn).list_after(0, limit=100)
        if event.type == "memory.activated"
    ]


def test_approved_candidate_with_evidence_activates_into_memory_item(
    repo: MemoryItemRepository,
    candidate_repo: MemoryCandidateRepository,
    evidence_repo: MemoryEvidenceRepository,
) -> None:
    candidate = approved_candidate_with_evidence(candidate_repo, evidence_repo)

    item = repo.activate_candidate(candidate.id)

    assert item.kind == "semantic"
    assert item.scope == "project"
    assert item.namespace == "project/dan/memory"
    assert item.title == "Memory Activation"
    assert item.claim == "Approved candidates activate into memory items."
    assert item.content == "Approved candidates activate into memory items."
    assert item.status == "active"
    assert item.confidence == "high"
    assert item.sensitivity == "low"
    assert item.source_policy == "candidate_evidence"
    assert item.created_at == "2026-07-04T12:01:00+00:00"
    assert item.updated_at == "2026-07-04T12:01:00+00:00"


def test_needs_review_candidate_cannot_activate(
    repo: MemoryItemRepository,
    candidate_repo: MemoryCandidateRepository,
) -> None:
    candidate = create_candidate(candidate_repo)

    with pytest.raises(MemoryItemConflict):
        repo.activate_candidate(candidate.id)


def test_rejected_candidate_cannot_activate(
    repo: MemoryItemRepository,
    candidate_repo: MemoryCandidateRepository,
    evidence_repo: MemoryEvidenceRepository,
) -> None:
    candidate = create_candidate(candidate_repo)
    evidence_repo.add_evidence(candidate.id, **evidence_payload())
    rejected = candidate_repo.reject_candidate(candidate.id)

    with pytest.raises(MemoryItemConflict):
        repo.activate_candidate(rejected.id)


def test_missing_candidate_returns_not_found(repo: MemoryItemRepository) -> None:
    with pytest.raises(MemoryItemNotFound):
        repo.activate_candidate("missing")


def test_approved_candidate_without_evidence_cannot_activate(
    repo: MemoryItemRepository,
    candidate_repo: MemoryCandidateRepository,
) -> None:
    approved = candidate_repo.approve_candidate(create_candidate(candidate_repo).id)

    with pytest.raises(MemoryItemConflict):
        repo.activate_candidate(approved.id)


def test_activation_creates_exactly_one_memory_item(
    repo: MemoryItemRepository,
    candidate_repo: MemoryCandidateRepository,
    evidence_repo: MemoryEvidenceRepository,
    conn: sqlite3.Connection,
) -> None:
    candidate = approved_candidate_with_evidence(candidate_repo, evidence_repo)

    item = repo.activate_candidate(candidate.id)

    assert _table_count(conn, "memory_items") == 1
    stored_id = conn.execute("SELECT id FROM memory_items").fetchone()[0]
    assert stored_id == item.id


def test_duplicate_activation_returns_existing_item_without_duplicate_row(
    repo: MemoryItemRepository,
    candidate_repo: MemoryCandidateRepository,
    evidence_repo: MemoryEvidenceRepository,
    conn: sqlite3.Connection,
) -> None:
    candidate = approved_candidate_with_evidence(candidate_repo, evidence_repo)

    first = repo.activate_candidate(candidate.id)
    second = repo.activate_candidate(candidate.id)

    assert second == first
    assert _table_count(conn, "memory_items") == 1


def test_canonical_key_for_candidate_distinguishes_delimiter_collision() -> None:
    shared_parts = {
        "scope": "project",
        "namespace": "project/dan/memory",
        "kind": "semantic",
    }

    first_key = canonical_key_for_candidate(
        **shared_parts,
        title="a|b",
        claim="c",
    )
    second_key = canonical_key_for_candidate(
        **shared_parts,
        title="a",
        claim="b|c",
    )

    assert first_key != second_key
    assert first_key == canonical_key_for_candidate(
        **shared_parts,
        title="a|b",
        claim="c",
    )


def test_canonical_key_for_candidate_distinguishes_missing_title_marker() -> None:
    shared_parts = {
        "scope": "project",
        "namespace": "project/dan/memory",
        "kind": "semantic",
        "claim": "same claim",
    }

    missing_title_key = canonical_key_for_candidate(
        **shared_parts,
        title=None,
    )
    literal_title_key = canonical_key_for_candidate(
        **shared_parts,
        title="<no-title>",
    )

    assert missing_title_key != literal_title_key


def test_delimiter_collision_candidates_create_distinct_memory_items(
    repo: MemoryItemRepository,
    candidate_repo: MemoryCandidateRepository,
    evidence_repo: MemoryEvidenceRepository,
    conn: sqlite3.Connection,
) -> None:
    first_candidate = approved_candidate_with_evidence(
        candidate_repo,
        evidence_repo,
        title="a|b",
        claim="c",
    )
    second_candidate = approved_candidate_with_evidence(
        candidate_repo,
        evidence_repo,
        title="a",
        claim="b|c",
    )

    first_item = repo.activate_candidate(first_candidate.id)
    second_item = repo.activate_candidate(second_candidate.id)

    assert first_item.id != second_item.id
    assert first_item.canonical_key != second_item.canonical_key
    assert _table_count(conn, "memory_items") == 2
    linked_memory_by_candidate = {
        str(candidate_id): str(memory_id)
        for candidate_id, memory_id in conn.execute(
            """
            SELECT candidate_id, memory_id
            FROM memory_evidence
            WHERE candidate_id IN (?, ?)
            ORDER BY candidate_id ASC
            """,
            (first_candidate.id, second_candidate.id),
        ).fetchall()
    }
    assert linked_memory_by_candidate[first_candidate.id] == first_item.id
    assert linked_memory_by_candidate[second_candidate.id] == second_item.id
    assert linked_memory_by_candidate[second_candidate.id] != first_item.id


def test_same_exact_title_and_claim_dedupes_memory_items(
    repo: MemoryItemRepository,
    candidate_repo: MemoryCandidateRepository,
    evidence_repo: MemoryEvidenceRepository,
    conn: sqlite3.Connection,
) -> None:
    first_candidate = approved_candidate_with_evidence(
        candidate_repo,
        evidence_repo,
        title="Exact Shared Title",
        claim="Exact shared claim.",
    )
    second_candidate = approved_candidate_with_evidence(
        candidate_repo,
        evidence_repo,
        title="Exact Shared Title",
        claim="Exact shared claim.",
    )

    first_item = repo.activate_candidate(first_candidate.id)
    second_item = repo.activate_candidate(second_candidate.id)

    assert second_item == first_item
    assert _table_count(conn, "memory_items") == 1
    linked_memory_ids = [
        str(memory_id)
        for (memory_id,) in conn.execute(
            """
            SELECT memory_id
            FROM memory_evidence
            WHERE candidate_id IN (?, ?)
            ORDER BY candidate_id ASC
            """,
            (first_candidate.id, second_candidate.id),
        ).fetchall()
    ]
    assert linked_memory_ids == [first_item.id, first_item.id]


def test_same_title_different_claims_create_distinct_memory_items(
    repo: MemoryItemRepository,
    candidate_repo: MemoryCandidateRepository,
    evidence_repo: MemoryEvidenceRepository,
    conn: sqlite3.Connection,
) -> None:
    first_candidate = approved_candidate_with_evidence(
        candidate_repo,
        evidence_repo,
        title="Shared Title",
        claim="First approved claim.",
    )
    second_candidate = approved_candidate_with_evidence(
        candidate_repo,
        evidence_repo,
        title="Shared Title",
        claim="Second approved claim.",
    )

    first_item = repo.activate_candidate(first_candidate.id)
    second_item = repo.activate_candidate(second_candidate.id)

    assert first_item.id != second_item.id
    assert _table_count(conn, "memory_items") == 2
    linked_memory_by_candidate = {
        str(candidate_id): str(memory_id)
        for candidate_id, memory_id in conn.execute(
            """
            SELECT candidate_id, memory_id
            FROM memory_evidence
            WHERE candidate_id IN (?, ?)
            ORDER BY candidate_id ASC
            """,
            (first_candidate.id, second_candidate.id),
        ).fetchall()
    }
    assert linked_memory_by_candidate[first_candidate.id] == first_item.id
    assert linked_memory_by_candidate[second_candidate.id] == second_item.id
    assert linked_memory_by_candidate[second_candidate.id] != first_item.id


def test_concurrent_duplicate_activation_returns_one_item_and_event(
    candidate_repo: MemoryCandidateRepository,
    evidence_repo: MemoryEvidenceRepository,
    conn: sqlite3.Connection,
) -> None:
    candidate = approved_candidate_with_evidence(candidate_repo, evidence_repo)
    db_path = _database_path(conn)
    worker_count = 2
    start_barrier = threading.Barrier(worker_count)
    empty_key_barrier = threading.Barrier(worker_count, timeout=1.0)
    results: queue.Queue[str | BaseException] = queue.Queue()

    def worker() -> None:
        thread_conn = connect_db(db_path)
        try:
            repo = MemoryItemRepository(
                thread_conn,
                event_store=create_event_store(thread_conn),
                now=lambda: "2026-07-04T12:01:00+00:00",
            )
            original_item_by_canonical_key = repo._item_by_canonical_key

            def synchronized_empty_key_check(canonical_key: str):
                item = original_item_by_canonical_key(canonical_key)
                if item is None:
                    try:
                        empty_key_barrier.wait()
                    except threading.BrokenBarrierError:
                        pass
                return item

            repo._item_by_canonical_key = synchronized_empty_key_check  # type: ignore[method-assign]
            start_barrier.wait()
            results.put(repo.activate_candidate(candidate.id).id)
        except BaseException as exc:
            results.put(exc)
        finally:
            close_quietly(thread_conn)

    threads = [
        threading.Thread(target=worker, name=f"memory-activate-{index}")
        for index in range(worker_count)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert all(not thread.is_alive() for thread in threads)
    returned = [results.get_nowait() for _ in range(worker_count)]
    errors = [result for result in returned if isinstance(result, BaseException)]
    assert errors == []
    memory_ids = [str(result) for result in returned]
    assert len(set(memory_ids)) == 1
    assert _table_count(conn, "memory_items") == 1
    assert len(activation_events(conn)) == 1
    linked_memory_ids = [
        str(row[0])
        for row in conn.execute(
            "SELECT memory_id FROM memory_evidence WHERE candidate_id = ?",
            (candidate.id,),
        ).fetchall()
    ]
    assert linked_memory_ids == [memory_ids[0]]


def test_activation_links_evidence_to_memory_id_when_supported(
    repo: MemoryItemRepository,
    candidate_repo: MemoryCandidateRepository,
    evidence_repo: MemoryEvidenceRepository,
    conn: sqlite3.Connection,
) -> None:
    candidate = approved_candidate_with_evidence(candidate_repo, evidence_repo)

    item = repo.activate_candidate(candidate.id)

    columns = {row[1] for row in conn.execute("PRAGMA table_info(memory_evidence)")}
    assert "memory_id" in columns
    linked_ids = [
        row[0]
        for row in conn.execute(
            "SELECT memory_id FROM memory_evidence WHERE candidate_id = ?",
            (candidate.id,),
        ).fetchall()
    ]
    assert linked_ids == [item.id]


def test_activation_does_not_create_memory_blocks(
    repo: MemoryItemRepository,
    candidate_repo: MemoryCandidateRepository,
    evidence_repo: MemoryEvidenceRepository,
    conn: sqlite3.Connection,
) -> None:
    candidate = approved_candidate_with_evidence(candidate_repo, evidence_repo)

    repo.activate_candidate(candidate.id)

    assert _table_count(conn, "memory_blocks") == 0


def test_activation_emits_memory_activated(
    candidate_repo: MemoryCandidateRepository,
    evidence_repo: MemoryEvidenceRepository,
    conn: sqlite3.Connection,
) -> None:
    event_store = create_event_store(conn)
    repo = MemoryItemRepository(
        conn,
        event_store=event_store,
        now=lambda: "2026-07-04T12:01:00+00:00",
    )
    candidate = approved_candidate_with_evidence(candidate_repo, evidence_repo)

    item = repo.activate_candidate(candidate.id)

    assert activation_events(conn) == [
        {
            "candidate_id": candidate.id,
            "memory_id": item.id,
            "kind": "semantic",
            "scope": "project",
            "namespace": "project/dan/memory",
            "status": "active",
        }
    ]


def test_activation_event_payload_omits_claim_content_and_quote(
    candidate_repo: MemoryCandidateRepository,
    evidence_repo: MemoryEvidenceRepository,
    conn: sqlite3.Connection,
) -> None:
    event_store = create_event_store(conn)
    repo = MemoryItemRepository(
        conn,
        event_store=event_store,
        now=lambda: "2026-07-04T12:01:00+00:00",
    )
    candidate = approved_candidate_with_evidence(candidate_repo, evidence_repo)

    repo.activate_candidate(candidate.id)

    payload = activation_events(conn)[0]
    rendered_payload = json.dumps(payload, sort_keys=True)
    assert "claim" not in payload
    assert "content" not in payload
    assert "quote" not in payload
    assert RAW_QUOTE_SECRET not in rendered_payload


def test_duplicate_activation_emits_memory_activated_once(
    candidate_repo: MemoryCandidateRepository,
    evidence_repo: MemoryEvidenceRepository,
    conn: sqlite3.Connection,
) -> None:
    event_store = create_event_store(conn)
    repo = MemoryItemRepository(
        conn,
        event_store=event_store,
        now=lambda: "2026-07-04T12:01:00+00:00",
    )
    candidate = approved_candidate_with_evidence(candidate_repo, evidence_repo)

    repo.activate_candidate(candidate.id)
    repo.activate_candidate(candidate.id)

    assert len(activation_events(conn)) == 1


def test_list_and_get_memory_items(
    repo: MemoryItemRepository,
    candidate_repo: MemoryCandidateRepository,
    evidence_repo: MemoryEvidenceRepository,
) -> None:
    candidate = approved_candidate_with_evidence(candidate_repo, evidence_repo)
    item = repo.activate_candidate(candidate.id)

    assert repo.list_items() == [item]
    assert repo.get_item(item.id) == item
    assert repo.get_item("missing") is None


def test_post_activate_candidate_api_works_for_approved_candidate_with_evidence(
    app: DaemonApp,
) -> None:
    app.start()

    with running_server(app) as base_url:
        _, created = request_json("POST", f"{base_url}/memory/candidates", candidate_payload())
        request_json(
            "POST",
            f"{base_url}/memory/candidates/{created['candidate']['id']}/evidence",
            evidence_payload(),
        )
        request_json(
            "POST",
            f"{base_url}/memory/candidates/{created['candidate']['id']}/approve",
        )
        status, payload = request_json(
            "POST",
            f"{base_url}/memory/candidates/{created['candidate']['id']}/activate",
        )

    assert status == 200
    assert payload["ok"] is True
    item = payload["item"]
    assert isinstance(item, dict)
    assert item["status"] == "active"
    assert item["claim"] == "Approved API candidates activate into memory items."
    assert table_count(app, "memory_items") == 1
    assert table_count(app, "memory_blocks") == 0


@pytest.mark.parametrize("decision", ["needs_review", "rejected"])
def test_post_activate_candidate_api_returns_409_for_non_approved_candidate(
    app: DaemonApp,
    decision: str,
) -> None:
    app.start()

    with running_server(app) as base_url:
        _, created = request_json("POST", f"{base_url}/memory/candidates", candidate_payload())
        if decision == "rejected":
            request_json(
                "POST",
                f"{base_url}/memory/candidates/{created['candidate']['id']}/reject",
            )
        status, content_type, body = request_raw(
            "POST",
            f"{base_url}/memory/candidates/{created['candidate']['id']}/activate",
        )

    assert status == 409
    assert "application/json" in content_type
    assert "<html" not in body.lower()
    assert json.loads(body)["status"] == 409


def test_post_activate_missing_candidate_api_returns_json_404(app: DaemonApp) -> None:
    app.start()

    with running_server(app) as base_url:
        status, content_type, body = request_raw(
            "POST",
            f"{base_url}/memory/candidates/missing/activate",
        )

    assert status == 404
    assert "application/json" in content_type
    assert "<html" not in body.lower()
    assert json.loads(body)["status"] == 404


def test_post_activate_candidate_without_evidence_api_returns_json_409(
    app: DaemonApp,
) -> None:
    app.start()

    with running_server(app) as base_url:
        _, created = request_json("POST", f"{base_url}/memory/candidates", candidate_payload())
        request_json(
            "POST",
            f"{base_url}/memory/candidates/{created['candidate']['id']}/approve",
        )
        status, content_type, body = request_raw(
            "POST",
            f"{base_url}/memory/candidates/{created['candidate']['id']}/activate",
        )

    assert status == 409
    assert "application/json" in content_type
    assert "<html" not in body.lower()
    assert json.loads(body)["status"] == 409


def test_get_memory_items_api_lists_memory_items(app: DaemonApp) -> None:
    app.start()

    with running_server(app) as base_url:
        _, created = request_json("POST", f"{base_url}/memory/candidates", candidate_payload())
        request_json(
            "POST",
            f"{base_url}/memory/candidates/{created['candidate']['id']}/evidence",
            evidence_payload(),
        )
        request_json(
            "POST",
            f"{base_url}/memory/candidates/{created['candidate']['id']}/approve",
        )
        _, activated = request_json(
            "POST",
            f"{base_url}/memory/candidates/{created['candidate']['id']}/activate",
        )
        status, payload = request_json("GET", f"{base_url}/memory/items")

    assert status == 200
    assert payload["ok"] is True
    assert payload["items"] == [activated["item"]]


def test_get_memory_item_api_returns_memory_item(app: DaemonApp) -> None:
    app.start()

    with running_server(app) as base_url:
        _, created = request_json("POST", f"{base_url}/memory/candidates", candidate_payload())
        request_json(
            "POST",
            f"{base_url}/memory/candidates/{created['candidate']['id']}/evidence",
            evidence_payload(),
        )
        request_json(
            "POST",
            f"{base_url}/memory/candidates/{created['candidate']['id']}/approve",
        )
        _, activated = request_json(
            "POST",
            f"{base_url}/memory/candidates/{created['candidate']['id']}/activate",
        )
        status, payload = request_json(
            "GET",
            f"{base_url}/memory/items/{activated['item']['id']}",
        )

    assert status == 200
    assert payload["ok"] is True
    assert payload["item"] == activated["item"]


def test_get_missing_memory_item_api_returns_json_404(app: DaemonApp) -> None:
    app.start()

    with running_server(app) as base_url:
        status, content_type, body = request_raw(
            "GET",
            f"{base_url}/memory/items/missing",
        )

    assert status == 404
    assert "application/json" in content_type
    assert "<html" not in body.lower()
    assert json.loads(body)["status"] == 404


def test_duplicate_activate_candidate_api_returns_existing_item(app: DaemonApp) -> None:
    app.start()

    with running_server(app) as base_url:
        _, created = request_json("POST", f"{base_url}/memory/candidates", candidate_payload())
        request_json(
            "POST",
            f"{base_url}/memory/candidates/{created['candidate']['id']}/evidence",
            evidence_payload(),
        )
        request_json(
            "POST",
            f"{base_url}/memory/candidates/{created['candidate']['id']}/approve",
        )
        _, first = request_json(
            "POST",
            f"{base_url}/memory/candidates/{created['candidate']['id']}/activate",
        )
        status, second = request_json(
            "POST",
            f"{base_url}/memory/candidates/{created['candidate']['id']}/activate",
        )

    assert status == 200
    assert second["item"] == first["item"]
    assert table_count(app, "memory_items") == 1


def test_memory_items_keep_schema_and_migrations_unchanged() -> None:
    assert_schema_and_migrations_unchanged(ROOT)


def _table_count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _database_path(conn: sqlite3.Connection) -> Path:
    for _, name, path in conn.execute("PRAGMA database_list").fetchall():
        if name == "main":
            return Path(str(path))
    raise AssertionError("main SQLite database path not found")
