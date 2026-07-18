"""Installer entrypoint used by scripts/install.sh.

    python -m dan.install --stage-only [--home H]
    python -m dan.install --apply [--home H] [--no-launchd]

--stage-only renders and verifies into a temporary staging root and changes
no active home path. --apply runs the five-phase plan with a backup root
under HOME/.dan/backups/.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

from dan.install import InstallError, InstallPlan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m dan.install")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--stage-only", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--home", help="Target HOME (defaults to $HOME)")
    parser.add_argument("--no-launchd", dest="launchd", action="store_false")
    args = parser.parse_args(argv)

    home = Path(args.home) if args.home else Path(os.environ["HOME"])
    plan = InstallPlan(home=home, include_launchd=args.launchd)

    preflight = plan.preflight()
    if not preflight.ok:
        print(json.dumps(preflight.to_dict(), ensure_ascii=False, sort_keys=True))
        return 1

    try:
        if args.stage_only:
            with tempfile.TemporaryDirectory(prefix="dan-install-staging-") as staging:
                plan.render(Path(staging))
                plan.verify(Path(staging))
            print(
                json.dumps(
                    {
                        "ok": True,
                        "mode": "stage-only",
                        "home": str(home),
                        "items": [item.relpath for item in plan.items],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0

        stamp = time.strftime("%Y%m%d-%H%M%S")
        staging = home / ".dan" / f"staging-{stamp}"
        backup_root = home / ".dan" / "backups" / f"install-{stamp}"
        plan.render(staging)
        plan.verify(staging)
        report = plan.apply(backup_root=backup_root)
        shutil.rmtree(staging, ignore_errors=True)
        print(
            json.dumps(
                {"ok": True, "mode": "apply", **report.to_dict()},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    except InstallError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
