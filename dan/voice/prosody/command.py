"""Command-line boundary for the offline prosody renderer."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from .planning import DEFAULT_SEED_POOL, DirectorSettings


def add_prosody_subcommands(parser: argparse.ArgumentParser) -> None:
    commands = parser.add_subparsers(dest="prosody_command", required=True)
    render = commands.add_parser(
        "render",
        help="render an offline/storytelling scene with deterministic take selection",
    )
    _add_render_arguments(render)
    replay = commands.add_parser(
        "replay",
        help="reproduce a previous render from its manifest and verify the final hash",
    )
    _add_replay_arguments(replay)


def handle_prosody_command(args: argparse.Namespace, config: Any) -> int:
    try:
        if args.prosody_command == "render":
            payload = _handle_render(args, config)
        elif args.prosody_command == "replay":
            payload = _handle_replay(args, config)
        else:
            raise ValueError(f"unknown prosody command: {args.prosody_command}")
        _emit(payload, json_output=bool(args.json_output))
        return 0
    except Exception as exc:
        # The command reports success only after a complete final WAV and
        # manifest exist. Partial candidates remain on disk for diagnosis.
        payload = {
            "error": "prosody_command_failed",
            "type": type(exc).__name__,
            "message": str(exc),
        }
        if getattr(args, "json_output", False):
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        else:
            print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2


def build_standalone_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m dan.voice.prosody")
    parser.add_argument("--config", help="Path to DAN TOML config")
    commands = parser.add_subparsers(dest="prosody_command", required=True)
    render = commands.add_parser("render")
    _add_render_arguments(render)
    replay = commands.add_parser("replay")
    _add_replay_arguments(replay)
    return parser


def standalone_main(argv: list[str] | None = None) -> int:
    parser = build_standalone_parser()
    args = parser.parse_args(argv)
    from dan.config import ConfigError, load_config

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"ConfigError: {exc}", file=sys.stderr)
        return 2
    return handle_prosody_command(args, config)


def _handle_render(args: argparse.Namespace, config: Any) -> dict[str, Any]:
    seed_pool = args.seeds or DEFAULT_SEED_POOL
    hard_max_chars = int(
        getattr(config.voice, "supertonic_serve_max_chunk_length", 400)
    )
    settings = DirectorSettings(
        hard_max_chars=hard_max_chars,
        default_take_count=min(args.takes, len(seed_pool)),
        seed_pool=seed_pool,
    )
    manual = _manual_selections(args.select)
    from .renderer import OfflineProsodyRenderer, default_output_dir

    renderer = OfflineProsodyRenderer(
        config=config,
        repo_root=args.repo_root,
        voice_root=args.voice_root,
        settings=settings,
        ffmpeg_binary=args.ffmpeg,
    )
    output = args.out or default_output_dir(
        args.scene,
        runtime_dir=config.runtime.runtime_dir,
    )
    if args.plan_only:
        plan = renderer.plan_file(args.scene)
        output.mkdir(parents=True, exist_ok=True)
        plan_path = output / "plan.json"
        plan_path.write_text(
            json.dumps(plan.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return {
            "status": "planned",
            "plan": str(plan_path),
            "utterances": len(plan.utterances),
            "segments": sum(len(item.segments) for item in plan.utterances),
        }

    result = renderer.render_file(
        args.scene,
        output_dir=output,
        manual_selections=manual,
        overwrite=args.overwrite,
    )
    return _result_payload(result, status="rendered")


def _handle_replay(args: argparse.Namespace, config: Any) -> dict[str, Any]:
    from .renderer import OfflineProsodyRenderer

    renderer = OfflineProsodyRenderer(
        config=config,
        repo_root=args.repo_root,
        voice_root=args.voice_root,
        ffmpeg_binary=args.ffmpeg,
    )
    output = args.out
    if output is None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        output = (
            Path(config.runtime.runtime_dir).expanduser()
            / "prosody"
            / f"replay-{args.manifest.stem}-{stamp}"
        )
    result = renderer.replay_manifest(
        args.manifest,
        output_dir=output,
        overwrite=args.overwrite,
    )
    return _result_payload(result, status="replayed_and_verified")


def _result_payload(result: Any, *, status: str) -> dict[str, Any]:
    return {
        "status": status,
        "output_dir": str(result.output_dir),
        "final_wav": str(result.final_wav_path),
        "final_sha256": result.final_wav_sha256,
        "plan": str(result.plan_path),
        "manifest": str(result.manifest_path),
        "utterances": [str(path) for path in result.utterance_paths],
    }


def _add_render_arguments(render: argparse.ArgumentParser) -> None:
    render.add_argument(
        "scene",
        type=Path,
        help="UTF-8 directed scene file (persona;controls|spoken text)",
    )
    render.add_argument("--out", type=Path, help="output directory")
    render.add_argument("--takes", type=_positive_int, default=6)
    render.add_argument(
        "--seeds",
        type=_seed_list,
        default=None,
        help="comma-separated deterministic seed pool",
    )
    render.add_argument(
        "--select",
        action="append",
        default=[],
        metavar="SEGMENT=SEED",
        help="manual deterministic take selection; repeat per segment",
    )
    render.add_argument("--ffmpeg", default=None, help="ffmpeg binary/path override")
    render.add_argument("--repo-root", type=Path, default=None)
    render.add_argument("--voice-root", type=Path, default=None)
    render.add_argument("--plan-only", action="store_true")
    render.add_argument("--overwrite", action="store_true")
    render.add_argument("--json", dest="json_output", action="store_true")


def _add_replay_arguments(replay: argparse.ArgumentParser) -> None:
    replay.add_argument("manifest", type=Path)
    replay.add_argument("--out", type=Path)
    replay.add_argument("--ffmpeg", default=None)
    replay.add_argument("--repo-root", type=Path, default=None)
    replay.add_argument("--voice-root", type=Path, default=None)
    replay.add_argument("--overwrite", action="store_true")
    replay.add_argument("--json", dest="json_output", action="store_true")


def _emit(payload: dict[str, Any], *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return
    for key, value in payload.items():
        if isinstance(value, list):
            print(f"{key}:")
            for item in value:
                print(f"  {item}")
        else:
            print(f"{key}: {value}")


def _manual_selections(raw_values: list[str]) -> dict[str, int]:
    result: dict[str, int] = {}
    for raw in raw_values:
        if "=" not in raw:
            raise argparse.ArgumentTypeError("--select must be SEGMENT=SEED")
        segment, seed_raw = (item.strip() for item in raw.split("=", 1))
        if not segment:
            raise argparse.ArgumentTypeError("--select segment id is empty")
        try:
            seed = int(seed_raw, 10)
        except ValueError as exc:
            raise argparse.ArgumentTypeError("--select seed must be an integer") from exc
        if not 0 <= seed <= (2**32) - 1:
            raise argparse.ArgumentTypeError("--select seed is outside uint32")
        if segment in result:
            raise argparse.ArgumentTypeError(f"duplicate --select for {segment}")
        result[segment] = seed
    return result


def _seed_list(raw: str) -> tuple[int, ...]:
    values: list[int] = []
    seen: set[int] = set()
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            value = int(token, 10)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"invalid seed: {token!r}") from exc
        if not 0 <= value <= (2**32) - 1:
            raise argparse.ArgumentTypeError(f"seed is outside uint32: {value}")
        if value not in seen:
            seen.add(value)
            values.append(value)
    if not values:
        raise argparse.ArgumentTypeError("seed list is empty")
    return tuple(values)


def _positive_int(raw: str) -> int:
    try:
        value = int(raw, 10)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be an integer") from exc
    if value <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return value


__all__ = [
    "add_prosody_subcommands",
    "build_standalone_parser",
    "handle_prosody_command",
    "standalone_main",
]
