"""Minimal argparse CLI for Jarvis v4.1 configuration inspection."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from jarvis.config import ConfigError, JarvisConfig, load_config
from jarvis.paths import RuntimePaths, resolve_runtime_paths
from jarvis.store.db import (
    DatabaseError,
    close_quietly,
    connect_db,
    get_schema_version,
    initialize_database,
    table_names,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jarvis")
    parser.add_argument("--config", help="Path to a Jarvis TOML config file")

    subcommands = parser.add_subparsers(dest="command", required=True)

    config_parser = subcommands.add_parser("config")
    config_commands = config_parser.add_subparsers(dest="config_command", required=True)
    config_commands.add_parser("show")

    paths_parser = subcommands.add_parser("paths")
    paths_commands = paths_parser.add_subparsers(dest="paths_command", required=True)
    paths_commands.add_parser("show")

    db_parser = subcommands.add_parser("db")
    db_commands = db_parser.add_subparsers(dest="db_command", required=True)
    db_status = db_commands.add_parser("status")
    db_status.add_argument("--config", dest="db_config", help="Path to a Jarvis TOML config file")
    db_init = db_commands.add_parser("init")
    db_init.add_argument("--config", dest="db_config", help="Path to a Jarvis TOML config file")

    subcommands.add_parser("doctor")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        config = load_config(_config_path_from_args(args))
        paths = resolve_runtime_paths(config)
    except ConfigError as exc:
        print(f"ConfigError: {exc}", file=sys.stderr)
        return 2

    if args.command == "config" and args.config_command == "show":
        _print_json(config.to_dict())
        return 0

    if args.command == "paths" and args.paths_command == "show":
        _print_json(paths.to_dict())
        return 0

    if args.command == "doctor":
        _print_json(_doctor_payload(config, paths))
        return 0

    if args.command == "db":
        return _handle_db_command(args.db_command, paths)

    parser.error("unknown command")
    return 2


def _config_path_from_args(args: argparse.Namespace) -> str | None:
    return getattr(args, "db_config", None) or args.config


def _handle_db_command(command: str, paths: RuntimePaths) -> int:
    try:
        if command == "status":
            _print_json(_db_status_payload(paths))
            return 0

        if command == "init":
            conn = initialize_database(paths.db_path)
            try:
                _print_json(_db_status_payload(paths, conn=conn))
            finally:
                close_quietly(conn)
            return 0
    except DatabaseError as exc:
        print(f"DatabaseError: {exc}", file=sys.stderr)
        return 2

    print(f"unknown db command: {command}", file=sys.stderr)
    return 2


def _doctor_payload(config: JarvisConfig, paths: RuntimePaths) -> dict[str, Any]:
    return {
        "config_ok": True,
        "runtime_home": str(paths.home),
        "db_path": str(paths.db_path),
        "logs_dir": str(paths.logs_dir),
        "runtime_dir": str(paths.runtime_dir),
        "launchd_label": config.launchd.label,
        "voice_enabled": config.voice.enabled,
        "brain_adapter": config.brain.default_adapter,
        "daemon_host": config.daemon.host,
        "daemon_port": config.daemon.port,
    }


def _db_status_payload(
    paths: RuntimePaths, conn: object | None = None
) -> dict[str, Any]:
    db_exists = paths.db_path.is_file()
    owns_conn = False
    db_conn = conn

    if db_exists and db_conn is None:
        db_conn = connect_db(paths.db_path)
        owns_conn = True

    try:
        schema_version = get_schema_version(db_conn) if db_conn is not None else 0
        tables = sorted(table_names(db_conn)) if db_conn is not None else []
    finally:
        if owns_conn:
            close_quietly(db_conn)

    return {
        "db_path": str(paths.db_path),
        "db_exists": db_exists or paths.db_path.is_file(),
        "schema_version": schema_version,
        "tables": tables,
    }


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
