"""Read-only install preflight. `python -m dan.install.preflight --json`.

Reports what an install into $HOME would do and whether it can proceed.
Changes nothing.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dan.install import InstallPlan


def build_report(home: Path | None = None, *, include_launchd: bool = True):
    resolved = Path(home) if home is not None else Path(os.environ.get("HOME", "~")).expanduser()
    plan = InstallPlan(home=resolved, include_launchd=include_launchd)
    return plan.preflight()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dan-install-preflight")
    parser.add_argument("--json", dest="json_output", action="store_true")
    parser.add_argument("--home", help="Target HOME (defaults to $HOME)")
    parser.add_argument("--no-launchd", dest="launchd", action="store_false")
    args = parser.parse_args(argv)

    report = build_report(Path(args.home) if args.home else None, include_launchd=args.launchd)
    payload = report.to_dict()
    if args.json_output:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
