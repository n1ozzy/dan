"""Sanitized, serializable evidence for database migration operators."""

from __future__ import annotations

from typing import Any

from dan.migration.legacy_data import DatabaseMigrationReport


def render_database_migration_report(report: DatabaseMigrationReport) -> dict[str, Any]:
    """Return counts and integrity evidence, never paths or row content."""

    backup: dict[str, Any] | None = None
    if report.backup is not None:
        backup = {
            "checkpoint": list(report.backup.checkpoint),
            "integrity": report.backup.integrity,
            "source_table_counts": dict(report.backup.source_counts),
            "destination_table_counts": dict(report.backup.destination_counts),
            "sha256": report.backup.sha256,
        }
    return {
        "backup": backup,
        "jarvis_rows_preserved": report.jarvis_rows_preserved,
        "memory": {
            "imported": report.memory.imported,
            "merged": report.memory.merged,
            "rejected": report.memory.rejected,
            "classes": [
                {
                    "source_table": outcome.source_table,
                    "outcome": outcome.outcome,
                    "reason": outcome.reason,
                    "count": outcome.count,
                }
                for outcome in report.memory.classes
            ],
        },
    }
