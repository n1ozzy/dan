"""Command-line entrypoint placeholder for jarvisd."""

from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jarvis")
    subcommands = parser.add_subparsers(dest="command")
    daemon = subcommands.add_parser("daemon")
    daemon.add_argument("action", choices=("run", "status", "stop", "restart"))
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    parser.parse_args(argv)
    print("jarvisd not implemented yet")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
