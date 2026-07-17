"""Legacy DAN leftovers report — DIAGNOSE-ONLY (MASTER_PLAN §5 FAZA H, H2).

Inventories what the legacy DAN install left on this machine (processes,
LaunchAgents, repo checkout, temp droppings, model caches) and prints a
decision list. Decree §7.6: DAN keeps running on its own until Ozzy
retires it by hand — this module therefore reports and never mutates:
no deletes, no signals, no service management. Paths that identify DAN
artifacts are composed from parts at runtime; the test suite forbids the
literal strings in this tree.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path

# dan.runtime.supervisor is the daemon-side legacy-conflict detector and
# the source of truth for known legacy script/agent names; this module adds
# the disk inventory and the absent-items checklist on top of it.
from dan.runtime.supervisor import LEGACY_LAUNCH_AGENTS, LEGACY_PROCESS_PATTERNS

# Extra signatures that identify DAN-lineage processes without
# false-positives on e.g. "--allow-dangerously-…" or unrelated daemons;
# deliberately no bare "dan" substring.
_PROCESS_SIGNATURES = (
    "dan_core",
    "voice-broker",
    "voice_broker",
    "xtts",
    "chatterbox",
)

_JARVIS_MLX_NOTE = (
    "Uwaga: wariant MLX to najprawdopodobniej zasób Jarvisa (M1, dekret "
    "§7.8 — zostaje). NIE KASOWAĆ bez osobnej decyzji."
)


@dataclass(frozen=True)
class Finding:
    category: str
    label: str
    path: str | None
    exists: bool
    size_bytes: int | None
    note: str = ""
    # informational: size already counted inside another finding (skip in totals)
    informational: bool = False
    # jarvis_asset: belongs to Jarvis (e.g. M1), listed only to avoid accidents
    jarvis_asset: bool = False


def dan_repo_dir(home: Path) -> Path:
    return home / "Documents" / "dev" / "dan"


def _tree_size_bytes(path: Path) -> int:
    if path.is_file() or path.is_symlink():
        try:
            return path.lstat().st_size
        except OSError:
            return 0
    total = 0
    for root, _dirs, files in os.walk(path, onerror=lambda _exc: None):
        for name in files:
            try:
                total += (Path(root) / name).lstat().st_size
            except OSError:
                continue
    return total


def format_size(size_bytes: int | None) -> str:
    if size_bytes is None:
        return "-"
    value = float(size_bytes)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} {unit}"
        value /= 1024
    return f"{int(size_bytes)} B"


def _path_finding(
    category: str,
    label: str,
    path: Path,
    note: str = "",
    informational: bool = False,
    jarvis_asset: bool = False,
) -> Finding:
    exists = path.exists() or path.is_symlink()
    return Finding(
        category=category,
        label=label,
        path=str(path),
        exists=exists,
        size_bytes=_tree_size_bytes(path) if exists else None,
        note=note,
        informational=informational,
        jarvis_asset=jarvis_asset,
    )


def match_process_lines(lines: Iterable[str]) -> list[str]:
    """Lines from `ps -axo pid=,command=` that look DAN-lineage. The report
    tool itself (dan.diagnostics) is excluded."""

    matched: list[str] = []
    for line in lines:
        lowered = line.lower()
        if "dan.diagnostics" in lowered:
            continue
        if any(signature in lowered for signature in _PROCESS_SIGNATURES) or any(
            all(fragment in line for fragment in fragments)
            for _label, fragments in LEGACY_PROCESS_PATTERNS
        ):
            matched.append(line.strip())
    return matched


def _ps_lines() -> list[str]:
    try:
        completed = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True, timeout=10)
    except OSError:
        return []
    if completed.returncode != 0:
        return []
    return completed.stdout.splitlines()


def collect_findings(
    home: Path | None = None,
    tmp_dir: Path | None = None,
    ps_lines: Iterable[str] | None = None,
) -> list[Finding]:
    home = home if home is not None else Path.home()
    tmp_dir = tmp_dir if tmp_dir is not None else Path("/tmp")
    lines = list(ps_lines) if ps_lines is not None else _ps_lines()

    findings: list[Finding] = []
    repo = dan_repo_dir(home)

    # 1. Running processes.
    matched = match_process_lines(lines)
    matched.extend(
        line.strip()
        for line in lines
        if str(repo) in line and line.strip() not in matched
    )
    if matched:
        findings.extend(
            Finding(
                category="process",
                label="Proces DAN-lineage",
                path=line,
                exists=True,
                size_bytes=None,
                note="Sygnatura procesu; zweryfikuj przed dniem sprzątania.",
            )
            for line in matched
        )
    else:
        findings.append(
            Finding(
                category="process",
                label="Procesy DAN-lineage",
                path=None,
                exists=False,
                size_bytes=None,
                note="Brak pasujących procesów w chwili raportu.",
            )
        )

    # 2. LaunchAgents: com.dan.* glob plus the supervisor registry (which
    # also knows legacy names without "dan" in them, e.g. com.ozzy.jarvis).
    agents_dir = home / "Library" / "LaunchAgents"
    candidates: dict[str, Path] = (
        {plist.name: plist for plist in agents_dir.glob("com.dan.*")}
        if agents_dir.is_dir()
        else {}
    )
    for _label, plist_name in LEGACY_LAUNCH_AGENTS:
        registry_path = agents_dir / plist_name
        if registry_path.exists():
            candidates.setdefault(plist_name, registry_path)
    plists = [candidates[name] for name in sorted(candidates)]
    if plists:
        findings.extend(
            _path_finding(
                "launch_agent",
                "LaunchAgent DAN",
                plist,
                note="Definicja usługi; sam plik nie znaczy, że usługa jest załadowana.",
            )
            for plist in plists
        )
    else:
        findings.append(
            Finding(
                category="launch_agent",
                label="LaunchAgents com.dan.*",
                path=str(agents_dir / "com.dan.*"),
                exists=False,
                size_bytes=None,
            )
        )

    # 3. Repo checkout (+ its embedded venv, reported separately for scale).
    findings.append(_path_finding("repo", "Repo legacy DAN", repo))
    findings.append(
        _path_finding(
            "repo",
            "Venv w repo DAN",
            repo / ".venv",
            note="Wliczony też w rozmiar repo powyżej.",
            informational=True,
        )
    )

    # 4. Temp droppings (dan-* in the temp dir).
    droppings = sorted(tmp_dir.glob("dan-*")) if tmp_dir.is_dir() else []
    if droppings:
        findings.extend(
            _path_finding("tmp_file", "Pozostałość tymczasowa DAN", item)
            for item in droppings
        )
    else:
        findings.append(
            Finding(
                category="tmp_file",
                label="Pliki tymczasowe dan-*",
                path=str(tmp_dir / "dan-*"),
                exists=False,
                size_bytes=None,
            )
        )

    # 5. Hugging Face cache — chatterbox model families.
    hub = home / ".cache" / "huggingface" / "hub"
    models = (
        sorted(
            entry
            for entry in hub.glob("models--*")
            if "chatterbox" in entry.name.lower()
        )
        if hub.is_dir()
        else []
    )
    if models:
        for model in models:
            is_mlx = "mlx" in model.name.lower()
            findings.append(
                _path_finding(
                    "hf_model",
                    "Model chatterbox w HF cache",
                    model,
                    note=_JARVIS_MLX_NOTE
                    if is_mlx
                    else "PyTorch chatterbox po DANie.",
                    jarvis_asset=is_mlx,
                )
            )
    else:
        findings.append(
            Finding(
                category="hf_model",
                label="Modele chatterbox w HF cache",
                path=str(hub),
                exists=False,
                size_bytes=None,
            )
        )

    # 6. XTTS venv — historical name; report absence honestly if gone.
    xtts_candidates = (
        home / "xtts-venv",
        home / "Documents" / "dev" / "xtts-venv",
        repo / "xtts-venv",
    )
    xtts_hits = [path for path in xtts_candidates if path.exists()]
    if xtts_hits:
        findings.extend(
            _path_finding("xtts_venv", "XTTS venv", path) for path in xtts_hits
        )
    else:
        findings.append(
            Finding(
                category="xtts_venv",
                label="XTTS venv",
                path=" | ".join(str(path) for path in xtts_candidates),
                exists=False,
                size_bytes=None,
                note="Nie znaleziono w żadnej z historycznych lokalizacji.",
            )
        )

    # 7. Standalone TTS model store.
    findings.append(
        _path_finding(
            "tts_model",
            "Modele TTS w Application Support",
            home / "Library" / "Application Support" / "tts",
        )
    )

    return findings


def render_text(findings: list[Finding]) -> str:
    lines = [
        "JARVIS — raport pozostałości po legacy DAN (H2, diagnose-only)",
        "=" * 62,
        "To narzędzie NICZEGO nie kasuje i nie zatrzymuje — wyłącznie",
        "raportuje. Sprzątanie wykonuje wyłącznie Ozzy, ręcznie, w dniu",
        '"Jarvis w 100%" (dekret §7.6: DAN działa osobno do odwołania).',
        "",
    ]
    total = 0
    jarvis_total = 0
    for finding in findings:
        marker = "OBECNE" if finding.exists else "BRAK  "
        size = format_size(finding.size_bytes)
        lines.append(f"[{marker}] {finding.label}: {finding.path or '-'} ({size})")
        if finding.note:
            lines.append(f"         ↳ {finding.note}")
        if finding.size_bytes and not finding.informational:
            total += finding.size_bytes
            if finding.jarvis_asset:
                jarvis_total += finding.size_bytes
    lines += [
        "",
        f"Łącznie na dysku: {format_size(total)}",
        f"Z tego zasoby Jarvisa (nie kasować): {format_size(jarvis_total)}",
        f"Kandydat do zwolnienia decyzją Ozzy'ego: {format_size(total - jarvis_total)}",
    ]
    return "\n".join(lines)


def render_json(findings: list[Finding]) -> str:
    return json.dumps(
        {
            "diagnose_only": True,
            "findings": [asdict(finding) for finding in findings],
        },
        ensure_ascii=False,
        indent=2,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="jarvis-dan-report",
        description="Report (never touch) what the legacy DAN left behind.",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--home", help="override home directory (tests)")
    parser.add_argument("--tmp-dir", help="override temp directory (tests)")
    parser.add_argument(
        "--no-ps", action="store_true", help="skip the process scan (tests)"
    )
    args = parser.parse_args(argv)

    findings = collect_findings(
        home=Path(args.home) if args.home else None,
        tmp_dir=Path(args.tmp_dir) if args.tmp_dir else None,
        ps_lines=[] if args.no_ps else None,
    )
    renderer = render_json if args.json else render_text
    print(renderer(findings), file=sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
