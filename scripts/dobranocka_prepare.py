#!/usr/bin/env python3
"""Validate DAN/Danusia story text without inventing artistic direction."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from dan.voice.policy import OWNER_VOICE_PERSONAS


def _utterances(source: Path) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for line_number, raw in enumerate(
        source.read_text(encoding="utf-8", errors="strict").splitlines(),
        start=1,
    ):
        normalized = raw.strip()
        if not normalized or normalized.startswith("#"):
            continue
        if "|" not in normalized:
            raise ValueError(f"{source}:{line_number}: oczekiwano persona|tekst")
        persona, text = (part.strip() for part in normalized.split("|", 1))
        if persona not in OWNER_VOICE_PERSONAS:
            raise ValueError(
                f"{source}:{line_number}: persona musi być dan albo danusia"
            )
        if not text:
            raise ValueError(f"{source}:{line_number}: pusta wypowiedź")
        rows.append((persona, text))
    if not rows:
        raise ValueError(f"brak wypowiedzi dan|/danusia| w {source}")
    return rows


def compile_story(source: Path, *, mode: str = "radio") -> tuple[list[str], dict]:
    if mode not in {"story", "radio"}:
        raise ValueError("mode musi być story albo radio")
    utterances = _utterances(source)
    personas = sorted({persona for persona, _ in utterances})
    if mode == "story" and len(personas) != 1:
        raise ValueError("jedna historia wymaga dokładnie jednego czytającego")
    lines = [f"{persona}|{text}" for persona, text in utterances]
    manifest = {
        "source": source.name,
        "mode": mode,
        "personas": personas,
        "utterances": len(utterances),
        "word_count": sum(len(text.split()) for _, text in utterances),
        "direction": "owner-listening-required",
    }
    return lines, manifest


def write_story(
    source: Path,
    output_dir: Path,
    *,
    mode: str = "story",
) -> tuple[Path, Path]:
    lines, manifest = compile_story(source, mode=mode)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = source.stem
    scene = output_dir / f"{stem}.scene.txt"
    audit = output_dir / f"{stem}.scene.json"
    scene.write_text("\n".join(lines) + "\n", encoding="utf-8")
    audit.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return scene, audit


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--mode", choices=("story", "radio"), default="story")
    args = parser.parse_args()
    sources = sorted(args.source_dir.glob("0[1-7]-*.txt"))
    if len(sources) != 7:
        parser.error(f"oczekiwano 7 historii, znaleziono {len(sources)}")
    for source in sources:
        scene, audit = write_story(source, args.output_dir, mode=args.mode)
        print(f"{scene.name}\t{audit.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
