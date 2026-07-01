"""Minimal argparse CLI for Jarvis v4.1 local runtime inspection."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from jarvis.config import ConfigError, JarvisConfig, load_config
from jarvis.daemon.app import DaemonAppError, create_daemon_app
from jarvis.daemon.lifecycle import DaemonServerError, serve_forever
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

    daemon_parser = subcommands.add_parser("daemon")
    daemon_commands = daemon_parser.add_subparsers(dest="daemon_command", required=True)
    daemon_commands.add_parser("run")

    input_parser = subcommands.add_parser("input")
    input_commands = input_parser.add_subparsers(dest="input_command", required=True)
    input_text = input_commands.add_parser("text")
    input_text.add_argument("message")
    input_text.add_argument("--conversation-id")
    input_text.add_argument("--metadata-json")
    input_text.add_argument("--url", help="Base URL for a running jarvisd")
    input_text.add_argument("--timeout", type=_positive_timeout, default=5.0)

    health_parser = subcommands.add_parser("health")
    health_parser.add_argument("--url", help="Base URL for a running jarvisd")

    state_parser = subcommands.add_parser("state")
    state_parser.add_argument("--url", help="Base URL for a running jarvisd")

    events_parser = subcommands.add_parser("events")
    events_commands = events_parser.add_subparsers(dest="events_command", required=True)
    events_after = events_commands.add_parser("after")
    events_after.add_argument("--id", dest="after_id", type=int, default=0)
    events_after.add_argument("--limit", type=int, default=100)
    events_after.add_argument("--url", help="Base URL for a running jarvisd")

    runtime_parser = subcommands.add_parser("runtime")
    runtime_commands = runtime_parser.add_subparsers(dest="runtime_command", required=True)
    runtime_processes = runtime_commands.add_parser("processes")
    runtime_processes.add_argument("--url", help="Base URL for a running jarvisd")
    runtime_startup = runtime_commands.add_parser("startup")
    runtime_startup.add_argument("--url", help="Base URL for a running jarvisd")
    runtime_legacy = runtime_commands.add_parser("legacy")
    runtime_legacy.add_argument("--url", help="Base URL for a running jarvisd")

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

    if args.command == "daemon" and args.daemon_command == "run":
        return _handle_daemon_run(_config_path_from_args(args))

    if args.command == "input" and args.input_command == "text":
        return _handle_input_text(args, _base_url(args, config))

    if args.command == "health":
        return _handle_remote_json(_base_url(args, config), "/health")

    if args.command == "state":
        return _handle_remote_json(_base_url(args, config), "/state")

    if args.command == "events" and args.events_command == "after":
        query = urlencode({"after_id": args.after_id, "limit": args.limit})
        return _handle_remote_json(_base_url(args, config), f"/events?{query}")

    if args.command == "runtime":
        return _handle_remote_json(_base_url(args, config), f"/runtime/{args.runtime_command}")

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


def _handle_daemon_run(config_path: str | None) -> int:
    app = None
    try:
        app = create_daemon_app(config_path, initialize=True)
        app.start()
        serve_forever(app, app.config.daemon.host, app.config.daemon.port)
        return 0
    except KeyboardInterrupt:
        if app is not None:
            app.stop(reason="keyboard interrupt")
        return 0
    except (ConfigError, DatabaseError, DaemonAppError, DaemonServerError) as exc:
        print(f"DaemonError: {exc}", file=sys.stderr)
        return 2
    finally:
        if app is not None:
            app.close()


def _handle_input_text(args: argparse.Namespace, base_url: str) -> int:
    metadata = None
    if args.metadata_json is not None:
        try:
            metadata = json.loads(args.metadata_json)
        except json.JSONDecodeError as exc:
            _print_json_error(
                {
                    "error": "invalid_metadata_json",
                    "message": "--metadata-json must be a JSON object.",
                    "detail": exc.msg,
                }
            )
            return 2
        if not isinstance(metadata, dict):
            _print_json_error(
                {
                    "error": "invalid_metadata_json",
                    "message": "--metadata-json must be a JSON object.",
                }
            )
            return 2

    payload: dict[str, Any] = {
        "text": args.message,
        "source": "cli",
    }
    if args.conversation_id is not None:
        payload["conversation_id"] = args.conversation_id
    if metadata is not None:
        payload["metadata"] = metadata

    return _handle_remote_json(
        base_url,
        "/input/text",
        method="POST",
        payload=payload,
        timeout=args.timeout,
    )


def _handle_remote_json(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout: float = 5.0,
) -> int:
    try:
        response_payload = _request_json(base_url, path, method=method, payload=payload, timeout=timeout)
    except HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = {"error": str(exc), "status": exc.code}
        _print_json(body)
        return 2
    except (URLError, TimeoutError, OSError) as exc:
        _print_json_error({"error": "daemon_unreachable", "message": str(exc)})
        return 3

    _print_json(response_payload)
    return 0


def _request_json(
    base_url: str,
    path: str,
    *,
    method: str,
    payload: dict[str, Any] | None,
    timeout: float,
) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}{path}"
    headers = {"Accept": "application/json"}
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers, method=method)
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _base_url(args: argparse.Namespace, config: JarvisConfig) -> str:
    override = getattr(args, "url", None)
    if override:
        return override
    return f"http://{config.daemon.host}:{config.daemon.port}"


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


def _print_json_error(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), file=sys.stderr)


def _positive_timeout(value: str) -> float:
    try:
        timeout = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timeout must be a number") from exc
    if timeout <= 0:
        raise argparse.ArgumentTypeError("timeout must be greater than 0")
    return timeout


if __name__ == "__main__":
    raise SystemExit(main())
