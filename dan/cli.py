"""Minimal argparse CLI for DAN v4.1 local runtime inspection."""

from __future__ import annotations

import argparse
import json
import signal
import sys
from dataclasses import asdict
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from dan.config import ConfigError, DANConfig, load_config
from dan.daemon.app import DaemonAppError, create_daemon_app, create_daemon_app_from_config
from dan.daemon.lifecycle import DaemonServerError, serve_forever
from dan.logging import configure_logging
from dan.memory.archive import MemoryArchive
from dan.memory.sync import MemorySourceSynchronizer
from dan.paths import RuntimePaths, resolve_runtime_paths
from dan.security.transport import API_TOKEN_HEADER, TransportTokenError, load_api_token
from dan.store.db import (
    DatabaseError,
    close_quietly,
    connect_db,
    get_schema_version,
    initialize_database,
    table_names,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dan")
    parser.add_argument("--config", help="Path to a DAN TOML config file")

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
    db_status.add_argument("--config", dest="db_config", help="Path to a DAN TOML config file")
    db_init = db_commands.add_parser("init")
    db_init.add_argument("--config", dest="db_config", help="Path to a DAN TOML config file")

    daemon_parser = subcommands.add_parser("daemon")
    daemon_commands = daemon_parser.add_subparsers(dest="daemon_command", required=True)
    daemon_commands.add_parser("run")

    input_parser = subcommands.add_parser("input")
    input_commands = input_parser.add_subparsers(dest="input_command", required=True)
    input_text = input_commands.add_parser("text")
    input_text.add_argument("message")
    input_text.add_argument("--conversation-id")
    input_text.add_argument("--metadata-json")
    input_text.add_argument("--url", help="Base URL for a running dand")
    input_text.add_argument("--timeout", type=_positive_timeout, default=5.0)

    conversations_parser = subcommands.add_parser("conversations")
    conversations_commands = conversations_parser.add_subparsers(
        dest="conversations_command",
        required=True,
    )
    conversations_list = conversations_commands.add_parser("list")
    conversations_list.add_argument("--limit", type=int)
    conversations_list.add_argument("--include-archived", action="store_true")
    conversations_list.add_argument("--url", help="Base URL for a running dand")
    conversations_list.add_argument("--timeout", type=_positive_timeout, default=5.0)

    turns_parser = subcommands.add_parser("turns")
    turns_commands = turns_parser.add_subparsers(dest="turns_command", required=True)
    turns_list = turns_commands.add_parser("list")
    turns_list.add_argument("--conversation-id", required=True)
    turns_list.add_argument("--limit", type=int)
    turns_list.add_argument("--newest-first", action="store_true")
    turns_list.add_argument("--url", help="Base URL for a running dand")
    turns_list.add_argument("--timeout", type=_positive_timeout, default=5.0)

    memory_parser = subcommands.add_parser("memory")
    memory_commands = memory_parser.add_subparsers(dest="memory_command", required=True)
    memory_list = memory_commands.add_parser("list")
    memory_list.add_argument("--active-only", action="store_true")
    memory_list.add_argument("--kind", action="append")
    memory_list.add_argument("--limit", type=int)
    memory_list.add_argument("--url", help="Base URL for a running dand")
    memory_list.add_argument("--timeout", type=_positive_timeout, default=5.0)

    memory_recall = memory_commands.add_parser("recall")
    memory_recall.add_argument("query")
    memory_recall.add_argument("--limit", type=int, default=10)
    memory_recall.add_argument("--url", help="Base URL for a running dand")
    memory_recall.add_argument("--timeout", type=_positive_timeout, default=5.0)

    memory_sync = memory_commands.add_parser("sync")
    memory_sync.add_argument(
        "source_type",
        choices=[
            "claude_jsonl",
            "claude_memory",
            "codex_session",
            "codex_memory",
            "gpt_transcript",
            "dan_turns",
        ],
    )
    memory_sync.add_argument("path", nargs="?")

    memory_create = memory_commands.add_parser("create")
    memory_create.add_argument("--kind", required=True)
    memory_create.add_argument("--title", required=True)
    memory_create.add_argument("--body", required=True)
    memory_create.add_argument("--priority", type=int, default=0)
    memory_create.add_argument("--metadata-json")
    memory_create.add_argument("--url", help="Base URL for a running dand")
    memory_create.add_argument("--timeout", type=_positive_timeout, default=5.0)

    memory_show = memory_commands.add_parser("show")
    memory_show.add_argument("--id", required=True)
    memory_show.add_argument("--url", help="Base URL for a running dand")
    memory_show.add_argument("--timeout", type=_positive_timeout, default=5.0)

    memory_update = memory_commands.add_parser("update")
    memory_update.add_argument("--id", required=True)
    memory_update.add_argument("--title")
    memory_update.add_argument("--body")
    memory_update.add_argument("--priority", type=int)
    memory_update.add_argument("--active", type=_bool_arg)
    memory_update.add_argument("--metadata-json")
    memory_update.add_argument("--url", help="Base URL for a running dand")
    memory_update.add_argument("--timeout", type=_positive_timeout, default=5.0)

    memory_disable = memory_commands.add_parser("disable")
    memory_disable.add_argument("--id", required=True)
    memory_disable.add_argument("--url", help="Base URL for a running dand")
    memory_disable.add_argument("--timeout", type=_positive_timeout, default=5.0)

    health_parser = subcommands.add_parser("health")
    health_parser.add_argument("--url", help="Base URL for a running dand")

    state_parser = subcommands.add_parser("state")
    state_parser.add_argument("--url", help="Base URL for a running dand")

    events_parser = subcommands.add_parser("events")
    events_commands = events_parser.add_subparsers(dest="events_command", required=True)
    events_after = events_commands.add_parser("after")
    events_after.add_argument("--id", dest="after_id", type=int, default=0)
    events_after.add_argument("--limit", type=int, default=100)
    events_after.add_argument("--url", help="Base URL for a running dand")

    runtime_parser = subcommands.add_parser("runtime")
    runtime_commands = runtime_parser.add_subparsers(dest="runtime_command", required=True)
    runtime_processes = runtime_commands.add_parser("processes")
    runtime_processes.add_argument("--url", help="Base URL for a running dand")
    runtime_startup = runtime_commands.add_parser("startup")
    runtime_startup.add_argument("--url", help="Base URL for a running dand")
    runtime_legacy = runtime_commands.add_parser("legacy")
    runtime_legacy.add_argument("--url", help="Base URL for a running dand")

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

    _configure_transport_token(paths)

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

    if args.command == "conversations" and args.conversations_command == "list":
        return _handle_conversations_list(args, _base_url(args, config))

    if args.command == "turns" and args.turns_command == "list":
        return _handle_turns_list(args, _base_url(args, config))

    if args.command == "memory":
        if args.memory_command == "sync":
            return _handle_memory_sync(args, paths)
        return _handle_memory_command(args, _base_url(args, config))

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


def daemon_main(argv: list[str] | None = None) -> int:
    """Run the daemon entrypoint without exposing the command hierarchy."""

    daemon_args = sys.argv[1:] if argv is None else argv
    return main([*daemon_args, "daemon", "run"])


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


class _TerminationSignal(KeyboardInterrupt):
    """SIGTERM turned into an exception so the graceful-stop path runs."""


def _raise_termination(signum: int, frame: object) -> None:
    raise _TerminationSignal(signal.Signals(signum).name)


def _handle_daemon_run(config_path: str | None) -> int:
    app = None
    # launchd stops the daemon with SIGTERM; without this handler Python
    # dies mid-loop and events keep a daemon.started with no matching
    # daemon.stopped.
    previous_sigterm = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, _raise_termination)
    try:
        config = load_config(config_path)
        # Logging must exist before the app initializes: the voice.* logger
        # diagnostics are the calibration channel for the live gate.
        configure_logging(config)
        app = create_daemon_app_from_config(config, initialize=True)
        app.start()
        serve_forever(app, app.config.daemon.host, app.config.daemon.port)
        return 0
    except _TerminationSignal as exc:
        if app is not None:
            app.stop(reason=str(exc))
        return 0
    except KeyboardInterrupt:
        if app is not None:
            app.stop(reason="keyboard interrupt")
        return 0
    except (ConfigError, DatabaseError, DaemonAppError, DaemonServerError) as exc:
        print(f"DaemonError: {exc}", file=sys.stderr)
        return 2
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)
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


def _handle_conversations_list(args: argparse.Namespace, base_url: str) -> int:
    query: dict[str, object] = {}
    if args.limit is not None:
        query["limit"] = args.limit
    if args.include_archived:
        query["include_archived"] = "true"

    path = _path_with_query("/conversations", query)
    return _handle_remote_json(base_url, path, timeout=args.timeout)


def _handle_turns_list(args: argparse.Namespace, base_url: str) -> int:
    query: dict[str, object] = {"conversation_id": args.conversation_id}
    if args.limit is not None:
        query["limit"] = args.limit
    if args.newest_first:
        query["newest_first"] = "true"

    path = _path_with_query("/turns", query)
    return _handle_remote_json(base_url, path, timeout=args.timeout)


def _handle_memory_command(args: argparse.Namespace, base_url: str) -> int:
    command = args.memory_command
    if command == "recall":
        return _handle_remote_json(
            base_url,
            "/memory/recall",
            method="POST",
            payload={"query": args.query, "limit": args.limit},
            timeout=args.timeout,
        )

    if command == "list":
        query: dict[str, object] = {}
        if args.active_only:
            query["active_only"] = "true"
        if args.kind:
            query["kind"] = ",".join(args.kind)
        if args.limit is not None:
            query["limit"] = args.limit
        return _handle_remote_json(
            base_url,
            _path_with_query("/memory", query),
            timeout=args.timeout,
        )

    if command == "create":
        metadata, error = _metadata_json_arg(args.metadata_json)
        if error is not None:
            _print_json_error(error)
            return 2
        payload: dict[str, Any] = {
            "kind": args.kind,
            "title": args.title,
            "body": args.body,
            "priority": args.priority,
        }
        if metadata is not None:
            payload["metadata"] = metadata
        return _handle_remote_json(
            base_url,
            "/memory",
            method="POST",
            payload=payload,
            timeout=args.timeout,
        )

    if command == "show":
        return _handle_remote_json(
            base_url,
            _memory_id_path(args.id),
            timeout=args.timeout,
        )

    if command == "update":
        metadata, error = _metadata_json_arg(args.metadata_json)
        if error is not None:
            _print_json_error(error)
            return 2
        payload: dict[str, Any] = {}
        if args.title is not None:
            payload["title"] = args.title
        if args.body is not None:
            payload["body"] = args.body
        if args.priority is not None:
            payload["priority"] = args.priority
        if args.active is not None:
            payload["active"] = args.active
        if metadata is not None:
            payload["metadata"] = metadata
        return _handle_remote_json(
            base_url,
            _memory_id_path(args.id),
            method="PATCH",
            payload=payload,
            timeout=args.timeout,
        )

    if command == "disable":
        return _handle_remote_json(
            base_url,
            _memory_id_path(args.id),
            method="DELETE",
            timeout=args.timeout,
        )

    print(f"unknown memory command: {command}", file=sys.stderr)
    return 2


def _handle_memory_sync(args: argparse.Namespace, paths: RuntimePaths) -> int:
    conn = None
    try:
        conn = initialize_database(paths.db_path)
        synchronizer = MemorySourceSynchronizer(MemoryArchive(conn), conn)
        if args.source_type == "dan_turns":
            if args.path is not None:
                raise ValueError("dan_turns sync does not accept a path")
            result = synchronizer.sync_dan_turns()
        else:
            if args.path is None:
                raise ValueError(f"{args.source_type} sync requires a path")
            result = synchronizer.sync_path(args.source_type, args.path)
        _print_json(asdict(result))
        return 0
    except (DatabaseError, OSError, ValueError) as exc:
        _print_json_error({"error": "memory_sync_failed", "message": str(exc)})
        return 2
    finally:
        close_quietly(conn)


def _path_with_query(path: str, query: dict[str, object]) -> str:
    if not query:
        return path
    return f"{path}?{urlencode(query)}"


def _memory_id_path(memory_id: str) -> str:
    return f"/memory/{quote(memory_id, safe='')}"


def _metadata_json_arg(raw_value: str | None) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if raw_value is None:
        return None, None
    try:
        value = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        return None, {
            "error": "invalid_metadata_json",
            "message": "--metadata-json must be a JSON object.",
            "detail": exc.msg,
        }
    if not isinstance(value, dict):
        return None, {
            "error": "invalid_metadata_json",
            "message": "--metadata-json must be a JSON object.",
        }
    return value, None


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


_transport_token: str | None = None


def _configure_transport_token(paths: RuntimePaths) -> None:
    """Load the local API token so mutating CLI requests can authenticate."""

    global _transport_token
    try:
        _transport_token = load_api_token(paths.runtime_dir)
    except TransportTokenError:
        _transport_token = None


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
    # Private-data reads (conversations, turns, memory) need the token too, not
    # just mutations (FIX-06 follow-up); harmless on the still-open reads.
    if _transport_token is not None:
        headers[API_TOKEN_HEADER] = _transport_token
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers, method=method)
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _base_url(args: argparse.Namespace, config: DANConfig) -> str:
    override = getattr(args, "url", None)
    if override:
        return override
    return f"http://{config.daemon.host}:{config.daemon.port}"


def _doctor_payload(config: DANConfig, paths: RuntimePaths) -> dict[str, Any]:
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


def _bool_arg(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    raise argparse.ArgumentTypeError("value must be true or false")


if __name__ == "__main__":
    raise SystemExit(main())
