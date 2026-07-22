#!/usr/bin/env python3
"""Kompiluj czytelne historie DAN/Danusia do playlist z reżyserią prozodii.

Źródło pozostaje czystym tekstem `dan|...` / `danusia|...`. Wynik używa
wstecznie zgodnego formatu feedera:
`persona;speed=...;profile=...;pause=...|tekst`.

Danusia zachowuje bieżące bazowe F4/clean z personas.toml, dlatego kompilator
nie emituje dla niej override'ow speed/profile. Filtry emocji nie zastępują
aktorstwa: gritty/krzyk nie są używane, a szept jest co najwyżej finałem DAN-a.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path


SPEAKER_RE = re.compile(r"^(dan|danusia)\|(.*)$")
FORMAT_WORDS = (
    "telefon", "roast battle", "teleturniej", "losowani", "reklama",
    "gorącą linię", "sąd ostateczny", "chemiczną ruletkę", "szczera gadka",
)
SUSPENSE_WORDS = (
    "cisza", "cicho", "ciemno", "szept", "sekret", "zmar", "potwór",
    "głos", "lasu", "nie oddycha", "zniknął", "puste", "strach", "anomalia",
)


def _utterances(source: Path) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for raw in source.read_text(encoding="utf-8").splitlines():
        match = SPEAKER_RE.match(raw.strip())
        if match and match.group(2).strip():
            rows.append((match.group(1), match.group(2).strip()))
    return rows


def _target_speed(index: int, total: int, speaker: str, text: str) -> float:
    if total <= 1:
        return 1.24
    frac = index / (total - 1)
    low = text.lower()
    if index == total - 1:
        return 1.24
    if frac < 0.14:
        target = 1.28
    elif frac < 0.42:
        target = 1.31
    elif frac < 0.64:
        target = 1.27
    elif frac < 0.79:
        target = 1.33
    elif frac < 0.90:
        target = 1.29
    else:
        target = 1.25
    if any(word in low for word in FORMAT_WORDS) and frac < 0.82:
        target = min(1.34, target + 0.03)
    return target


def _smooth_speed(target: float, previous: float | None) -> float:
    if previous is None:
        return target
    return max(previous - 0.07, min(previous + 0.07, target))


def compile_story(source: Path, *, mode: str = "radio") -> tuple[list[str], dict]:
    delivery_mode = mode
    if delivery_mode not in {"story", "radio"}:
        raise ValueError("mode musi być story albo radio")
    utterances = _utterances(source)
    total = len(utterances)
    if not total:
        raise ValueError(f"brak wypowiedzi dan|/danusia| w {source}")
    readers = {speaker for speaker, _ in utterances}
    if delivery_mode == "story" and len(readers) != 1:
        raise ValueError("jedna historia wymaga dokładnie jednego czytającego")

    output: list[str] = []
    modes: Counter[str] = Counter()
    previous_dan_speed: float | None = None
    last_speed = 0.0
    last_pause = 0.0

    for index, (speaker, text) in enumerate(utterances):
        frac = index / max(1, total - 1)
        low = text.lower()
        is_format = any(word in low for word in FORMAT_WORDS)
        is_suspense = any(word in low for word in SUSPENSE_WORDS)
        final = index == total - 1

        target = _target_speed(index, total, speaker, text)
        speed = target if final and total < 6 else _smooth_speed(target, previous_dan_speed)
        if speaker == "dan":
            previous_dan_speed = speed

        profile = "raw" if speaker == "dan" else "clean"
        if frac < 0.18:
            mode = "warm"
        elif frac < 0.42:
            mode = "wonder"
        elif frac < 0.64:
            mode = "suspense"
        elif frac < 0.79:
            mode = "relief"
        elif frac < 0.94:
            mode = "tender"
        else:
            mode = "sleepy"
        if is_format:
            mode = "live"
        if is_suspense and 0.38 <= frac < 0.92:
            mode = "suspense"

        pause = 0.18
        if text.endswith("?"):
            pause = 0.26
        if is_suspense:
            pause = max(pause, 0.32)
        if is_format:
            pause = max(pause, 0.34)
        if index == total - 3:
            pause = max(pause, 0.40)
        elif index == total - 2:
            pause = max(pause, 0.48)
        elif final:
            pause = 0.68
            profile = "szept" if speaker == "dan" and ("cisz" in low or "dobranoc" in low) else profile
            mode = "sleepy"

        speed = round(speed, 2)
        pause = round(pause, 2)
        if speaker == "danusia":
            output.append(f"{speaker};pause={pause:.2f}|{text}")
        else:
            output.append(
                f"{speaker};speed={speed:.2f};profile={profile};pause={pause:.2f}|{text}"
            )
        modes[mode] += 1
        if speaker == "dan":
            last_speed = speed
        last_pause = pause

    manifest = {
        "source": source.name,
        "mode": delivery_mode,
        "reader": next(iter(readers)) if len(readers) == 1 else None,
        "utterances": total,
        "word_count": sum(len(text.split()) for _, text in utterances),
        "final_speed": last_speed,
        "final_pause": last_pause,
        "whisper_count": sum(";profile=szept;" in line for line in output),
        "profiles": dict(Counter(
            line.split(";profile=", 1)[1].split(";", 1)[0]
            if ";profile=" in line else "persona-default"
            for line in output
        )),
        "modes": dict(modes),
    }
    return output, manifest


def write_story(source: Path, output_dir: Path, *, mode: str = "story") -> tuple[Path, Path]:
    lines, manifest = compile_story(source, mode=mode)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = source.stem
    playlist = output_dir / f"{stem}.playlist.txt"
    audit = output_dir / f"{stem}.prosody.json"
    playlist.write_text("\n".join(lines) + "\n", encoding="utf-8")
    audit.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return playlist, audit


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
        playlist, audit = write_story(source, args.output_dir, mode=args.mode)
        print(f"{playlist.name}\t{audit.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
