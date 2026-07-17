"""Read-only conversation and turn history route payloads."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from dan.daemon.app import DaemonApp
from dan.turns.models import Turn


ROUTE_GROUP = "history"


def get_conversations(
    app: DaemonApp,
    *,
    limit: int = 50,
    include_archived: bool = False,
) -> dict[str, Any]:
    return {
        "conversations": app.list_conversations(
            limit=limit,
            include_archived=include_archived,
        ),
        "limit": limit,
        "include_archived": include_archived,
    }


def get_turns(
    app: DaemonApp,
    *,
    conversation_id: str,
    limit: int = 50,
    newest_first: bool = False,
) -> dict[str, Any]:
    turns = app.list_turns(
        conversation_id=conversation_id,
        limit=limit,
        newest_first=newest_first,
    )
    effective_id = turns[0].conversation_id if turns else conversation_id
    return {
        "conversation_id": effective_id,
        "turns": [turn_to_dict(turn) for turn in turns],
        "limit": limit,
        "newest_first": newest_first,
    }


def turn_to_dict(turn: Turn) -> dict[str, Any]:
    return asdict(turn)


def register_routes(app: object) -> None:
    return None


__all__ = [
    "ROUTE_GROUP",
    "get_conversations",
    "get_turns",
    "register_routes",
    "turn_to_dict",
]
