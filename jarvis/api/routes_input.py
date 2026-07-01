"""Input route placeholders for future turn orchestration."""

from __future__ import annotations


ROUTE_GROUP = "input"


def text_input_not_implemented() -> dict[str, object]:
    return {"error": "Text turn pipeline is not implemented yet.", "status": 501}


def register_routes(app: object) -> None:
    return None


__all__ = ["ROUTE_GROUP", "register_routes", "text_input_not_implemented"]
