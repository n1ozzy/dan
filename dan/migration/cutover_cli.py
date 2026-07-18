"""CLI entrypoints for scripts/dan-cutover and scripts/dan-rollback.

Every command defaults to dry-run. ``apply`` demands ``--apply`` plus the
exact manifest SHA-256 and refuses: a dirty integration tree, missing backup
space, any unresolved manifest row, a stale manifest SHA or an unavailable
rollback destination. With ``--fixture`` the probe is an empty FakeProbe and
nothing on the live system is ever consulted.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from dan.migration.cutover import (
    CutoverBlocked,
    CutoverEngine,
    CutoverManifest,
)
from dan.migration.journal import PHASE_COMMITTED, Journal
from dan.migration.runtime_probe import FakeProbe, SystemProbe


def _load(manifest_path: Path, fixture_root: Path | None) -> CutoverManifest:
    root = fixture_root if fixture_root is not None else manifest_path.parent
    try:
        return CutoverManifest.load(manifest_path, root=root)
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise CutoverBlocked(
            f"{manifest_path} is not a cutover decision manifest "
            f"(Task 14 generates the real one): {exc}"
        ) from exc


def _engine(arguments: argparse.Namespace) -> CutoverEngine:
    fixture_root = Path(arguments.fixture) if arguments.fixture else None
    manifest = _load(Path(arguments.manifest), fixture_root)
    if fixture_root is not None:
        probe = FakeProbe()
    else:
        probe = SystemProbe(patterns=manifest.legacy_process_names)
    return CutoverEngine(
        manifest=manifest,
        home=manifest.home,
        probe=probe,
        resume_journal=Path(arguments.journal) if getattr(arguments, "journal", None) else None,
    )


def _print_plan(plan: dict) -> None:
    print(f"manifest:        {plan['manifest']}")
    print(f"manifest sha256: {plan['manifest_sha256']}")
    print(f"home:            {plan['home']}")
    print("\nproducers (decision required for every row):")
    for name, row in plan["producers"].items():
        print(f"  [{row['decision'] or 'UNRESOLVED'}] {name}")
    print("\nnon-DB file decisions:")
    for row in plan["files"]:
        print(f"  [{row['decision']}] {row['path']} — {row['reason']}")
    print("\npaths:")
    for key, value in plan["paths"].items():
        if key == "donors":
            for donor in value:
                state = "present" if donor["present"] else "MISSING"
                print(f"  donor       {donor['path']} ({state})")
        else:
            state = "present" if value["present"] else "MISSING"
            print(f"  {key:<11} {value['path']} ({state})")
    print("\nlaunch agents:")
    for agent in plan["launch_agents"]:
        state = "present" if agent["plist"]["present"] else "MISSING"
        print(f"  {agent['label']} -> {agent['plist']['path']} ({state})")
    print("\nobserved processes:")
    if plan["processes"]:
        for process in plan["processes"]:
            print(f"  pid {process['pid']}: {process['command']}")
    else:
        print("  (none)")
    print("\ndatabase table counts:")
    for database, counts in plan["db_counts"].items():
        if counts:
            described = ", ".join(f"{table}={count}" for table, count in counts.items())
        else:
            described = "missing or empty"
        print(f"  {database}: {described}")
    print("\npending destructive operations (dry-run, nothing executed):")
    for operation in plan["pending_destructive_operations"]:
        print(f"  - {operation}")
    if plan["precondition_failures"]:
        print("\nBLOCKERS:")
        for failure in plan["precondition_failures"]:
            print(f"  ! {failure}")


def _tree_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    if path.is_dir():
        for item in path.rglob("*"):
            if item.is_file():
                total += item.stat().st_size
    return total


def _nearest_existing(path: Path) -> Path:
    current = path
    while not current.exists():
        parent = current.parent
        if parent == current:
            break
        current = parent
    return current


def _apply_refusals(engine: CutoverEngine, arguments: argparse.Namespace) -> list[str]:
    manifest = engine.manifest
    refusals: list[str] = []
    if not arguments.apply:
        refusals.append("apply requires the explicit --apply flag (default is dry-run)")
    provided = arguments.manifest_sha256 or ""
    if provided != manifest.sha256:
        refusals.append(
            "stale or missing manifest SHA-256: pass --manifest-sha256 "
            f"{manifest.sha256}"
        )
    unresolved = [
        name
        for name, row in sorted(manifest.producers.items())
        if row.decision not in {"migrated", "disabled", "rejected"}
    ]
    if unresolved:
        refusals.append(f"unresolved manifest rows: {', '.join(unresolved)}")
    tree = manifest.old_jarvis
    if not (tree / ".git").exists():
        refusals.append(
            f"integration tree {tree} is not a git checkout — cannot prove it is clean"
        )
    else:
        status = subprocess.run(
            ["git", "-C", str(tree), "status", "--porcelain"],
            capture_output=True,
            check=False,
            text=True,
        )
        if status.returncode != 0 or status.stdout.strip():
            refusals.append(f"integration tree {tree} is dirty or unreadable")
    needed = _tree_size(manifest.old_dan) + sum(
        _tree_size(database) for database in manifest.databases
    )
    anchor = _nearest_existing(manifest.backup_root)
    free = shutil.disk_usage(anchor).free
    if free < needed * 2:
        refusals.append(
            f"insufficient backup space under {anchor}: need ~{needed * 2} bytes, "
            f"free {free}"
        )
    if not os.access(anchor, os.W_OK):
        refusals.append(f"rollback destination unavailable: {anchor} is not writable")
    return refusals


def cutover_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dan-cutover",
        description="Journaled DAN cutover tooling — every command is dry-run "
        "unless --apply plus the exact manifest SHA-256 are given.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("preflight", "plan", "apply"):
        sub = subparsers.add_parser(name)
        sub.add_argument("--manifest", required=True)
        sub.add_argument("--fixture", default=None, help="fixture root; implies no live probes")
        if name == "apply":
            sub.add_argument("--apply", action="store_true", dest="apply")
            sub.add_argument("--manifest-sha256", default=None)
            sub.add_argument("--cancel-in-flight", action="store_true")
            sub.add_argument("--journal", default=None, help="resume an interrupted journal")
    status = subparsers.add_parser("status")
    status.add_argument("--journal", required=True)
    arguments = parser.parse_args(argv)

    try:
        if arguments.command == "status":
            return _status(Path(arguments.journal))
        engine = _engine(arguments)
        if arguments.command == "plan":
            _print_plan(engine.plan())
            return 0
        if arguments.command == "preflight":
            failures = engine.precondition_failures()
            if failures:
                for failure in failures:
                    print(f"BLOCKED: {failure}")
                return 1
            print("preflight ok: quiescent, every manifest row decided")
            return 0
        # apply
        refusals = _apply_refusals(engine, arguments)
        if refusals:
            for refusal in refusals:
                print(f"REFUSED: {refusal}")
            return 1
        report = engine.apply(
            manifest_sha256=arguments.manifest_sha256,
            cancel_in_flight=arguments.cancel_in_flight,
        )
        print(f"cutover complete; journal: {report.journal}")
        return 0
    except CutoverBlocked as exc:
        print(f"BLOCKED: {exc}")
        return 1


def _status(journal_dir: Path) -> int:
    if journal_dir.name == Journal.FILENAME:
        journal_dir = journal_dir.parent
    journal = Journal.open(journal_dir)
    entries = journal.entries()
    committed = [entry.phase.value for entry in entries if entry.operation == PHASE_COMMITTED]
    print(f"journal: {journal.path}")
    print(f"entries: {len(entries)}")
    print(f"committed phases ({len(committed)}/11): {', '.join(committed) or '(none)'}")
    pending_mutations = [
        entry
        for entry in entries
        if entry.operation != PHASE_COMMITTED and entry.rollback_operation != "none"
    ]
    print(f"journaled mutations with inverses: {len(pending_mutations)}")
    if entries:
        last = entries[-1]
        print(f"last entry: [{last.phase.value}] {last.operation} {last.source or ''}")
    return 0


def rollback_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dan-rollback",
        description="Journal-driven cutover rollback; dry-run without --apply.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    sub = subparsers.add_parser("apply")
    sub.add_argument("--journal", required=True)
    sub.add_argument("--manifest", required=True)
    sub.add_argument("--fixture", default=None)
    sub.add_argument("--apply", action="store_true", dest="apply")
    arguments = parser.parse_args(argv)

    from dan.migration.rollback import RollbackBlocked, perform_rollback, rollback_plan

    journal_dir = Path(arguments.journal)
    if journal_dir.name == Journal.FILENAME:
        journal_dir = journal_dir.parent
    fixture_root = Path(arguments.fixture) if arguments.fixture else None
    try:
        manifest = _load(Path(arguments.manifest), fixture_root)
        if not arguments.apply:
            print("dry-run: inverse operations, newest first:")
            for operation in rollback_plan(journal_dir):
                print(f"  - {operation}")
            print("pass --apply to execute")
            return 0
        report = perform_rollback(
            journal_dir=journal_dir,
            manifest=manifest,
            home=manifest.home,
            apply_changes=True,
        )
        for line in report.undone:
            print(f"undone: {line}")
        print(f"old runtime start allowed: {report.old_runtime_start_allowed}")
        return 0
    except (CutoverBlocked, RollbackBlocked) as exc:
        print(f"BLOCKED: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(cutover_main())
