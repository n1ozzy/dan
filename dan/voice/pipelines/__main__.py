"""Explicit CLI for offline pipeline renders: python -m dan.voice.pipelines."""

from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from pathlib import Path

from dan.voice.pipelines import render_offline


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m dan.voice.pipelines",
        description="Render text offline through a persona's catalog pipeline.",
    )
    parser.add_argument("--persona", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--json", action="store_true", dest="as_json")
    text_source = parser.add_mutually_exclusive_group(required=True)
    text_source.add_argument("--stdin", action="store_true")
    text_source.add_argument("text", nargs="?", default=None)
    args = parser.parse_args(argv)

    if args.stdin:
        raw = sys.stdin.buffer.read()
        try:
            text = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            print(f"stdin is not valid UTF-8: {exc}", file=sys.stderr)
            return 2
    else:
        text = str(args.text or "")
    text = unicodedata.normalize("NFC", text)

    try:
        artifact = render_offline(args.persona, text, args.output)
    except Exception as exc:  # noqa: BLE001 — one CLI boundary, explicit nonzero exit
        if args.as_json:
            print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        else:
            print(f"offline render failed: {exc}", file=sys.stderr)
        return 1

    if args.as_json:
        payload = {
            "status": "rendered",
            "output": str(getattr(artifact, "path", args.output)),
            "sha256": getattr(artifact, "sha256", None),
            "seed": getattr(artifact, "seed", None),
            "score": getattr(artifact, "acceptance_score", None),
        }
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(f"rendered: {getattr(artifact, 'path', args.output)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
