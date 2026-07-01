"""Minimal argparse CLI for Jarvis v4.1 configuration inspection."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from jarvis.config import ConfigError, JarvisConfig, load_config
from jarvis.paths import RuntimePaths, resolve_runtime_paths


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

    subcommands.add_parser("doctor")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
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

    parser.error("unknown command")
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


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
