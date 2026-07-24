"""Atomic, backup-first, reversible installer for the DAN host surface.

`InstallPlan` exposes exactly five phases:

    preflight() -> PreflightReport
    render(staging: Path) -> None
    verify(staging: Path) -> None
    apply(backup_root: Path) -> InstallReport
    rollback(report: InstallReport) -> None

`apply()` runs only after the very same staging tree passed `verify()`.
Every replaced path is backed up first; every write lands via `os.replace`
from a sibling temp file; a destination symlink is replaced, never followed;
`~/.claude/archive` is excluded structurally — an item that even targets it
is refused before anything is written.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from dan.install.adapters import adapter_install_items
from dan.install.launchd import render_plist

MANAGED_BLOCK_BEGIN = ""
MANAGED_BLOCK_END = ""
INSTALL_MANIFEST_RELPATH = ".dan/install-manifest.json"

_CLAUDE_MD_BLOCK = """DAN runtime is installed. Voice/persona rules:
- DAN speech goes ONLY through `dan speak --json --as dan --session <s> --source claude --stdin`
  or the same command with `--as danusia`. These are the only public voice routes.
- Persona canon: `dan persona context` (config/persona/DAN.md, DAN_CANON_VERSION: 1) — fail-closed.
- MessageDisplay hook: ~/.claude/hooks/tts-message-display.sh speaks only [[GŁOS]] segments;
  switch with `dan voice hook on|off|status` (voice.hook_enabled), session override DAN_VOICE_HOOK=off.
- Diagnostics: `dan doctor --json`. Queue: `dan queue list --json`."""

_AGENTS_MD_BLOCK = """DAN runtime is installed. Any agent that speaks does so through
`dan speak --json --as dan --session <session> --source <host> --stdin` or the same
command with `--as danusia` (UTF-8 stdin; these are the only public voice routes).
Persona canon: `dan persona context`.
Skills live in ~/.agents/skills/ (gadanie, dobranocka, trio-live, danusia-live,
gpt-say, voice-report, standup, screen-control)."""


class InstallError(RuntimeError):
    """A phase contract of the installer was violated."""


@dataclass
class InstallItem:
    relpath: str
    content: bytes | None = None
    source: Path | None = None
    mode: int = 0o644
    managed_block: str | None = None


@dataclass(frozen=True)
class PreflightCheck:
    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class PreflightReport:
    home: str
    repo_root: str
    include_launchd: bool
    checks: tuple[PreflightCheck, ...]

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)

    def to_dict(self) -> dict[str, object]:
        return {
            "home": self.home,
            "repo_root": self.repo_root,
            "include_launchd": self.include_launchd,
            "ok": self.ok,
            "checks": [
                {"name": c.name, "ok": c.ok, "detail": c.detail} for c in self.checks
            ],
        }


@dataclass(frozen=True)
class InstallEntry:
    path: str
    backup: str | None
    sha_before: str | None
    sha_after: str
    operation: str  # "create" | "replace"
    inverse: str  # "remove" | "restore-backup"


@dataclass
class InstallReport:
    home: str
    backup_root: str
    entries: list[InstallEntry] = field(default_factory=list)
    dirs_created: list[str] = field(default_factory=list)
    manifest_path: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "home": self.home,
            "backup_root": self.backup_root,
            "entries": [
                {
                    "path": e.path,
                    "backup": e.backup,
                    "sha_before": e.sha_before,
                    "sha_after": e.sha_after,
                    "operation": e.operation,
                    "inverse": e.inverse,
                }
                for e in self.entries
            ],
            "dirs_created": self.dirs_created,
        }


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _assert_destination_allowed(relpath: str) -> PurePosixPath:
    pure = PurePosixPath(relpath)
    if pure.is_absolute():
        raise InstallError(f"destination must be home-relative: {relpath!r}")
    parts = pure.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise InstallError(f"unsafe destination path: {relpath!r}")
    # ~/.claude/archive is holy ground: excluded structurally, not by habit.
    if len(parts) >= 2 and parts[0] == ".claude" and parts[1] == "archive":
        raise InstallError(f"destination inside ~/.claude/archive is forbidden: {relpath!r}")
    return pure


def _merge_managed_block(existing: str | None, block_body: str) -> str:
    block = f"{MANAGED_BLOCK_BEGIN}\n{block_body.rstrip()}\n{MANAGED_BLOCK_END}\n"
    if existing is None or not existing.strip():
        return block
    if MANAGED_BLOCK_BEGIN in existing:
        head, _, rest = existing.partition(MANAGED_BLOCK_BEGIN)
        _, marker, tail = rest.partition(MANAGED_BLOCK_END)
        if not marker:
            raise InstallError("managed block begin marker without end marker")
        tail = tail.lstrip("\n")
        merged = head.rstrip("\n")
        merged = (merged + "\n\n" if merged else "") + block
        if tail:
            merged = merged + "\n" + tail
        return merged
    return existing.rstrip("\n") + "\n\n" + block


class InstallPlan:
    """Five-phase install into one HOME. Never touches paths outside it."""

    def __init__(self, home: Path, *, include_launchd: bool = True) -> None:
        self.home = Path(home)
        self.include_launchd = include_launchd
        self.items: list[InstallItem] = self._default_items()
        self._rendered: dict[str, str] | None = None
        self._verified_staging: Path | None = None

    # ------------------------------------------------------------ items
    def _default_items(self) -> list[InstallItem]:
        items = [
            InstallItem(relpath=relpath, source=source, mode=mode)
            for relpath, source, mode in adapter_install_items()
        ]
        if self.include_launchd:
            items.append(
                InstallItem(
                    relpath="Library/LaunchAgents/com.dan.dand.plist",
                    content=render_plist(self.home),
                    mode=0o644,
                )
            )
        items.append(
            InstallItem(relpath=".claude/CLAUDE.md", managed_block=_CLAUDE_MD_BLOCK)
        )
        items.append(InstallItem(relpath="AGENTS.md", managed_block=_AGENTS_MD_BLOCK))
        return items

    # ------------------------------------------------------------ phases
    def preflight(self) -> PreflightReport:
        checks: list[PreflightCheck] = []
        home_ok = self.home.is_dir() and os.access(self.home, os.W_OK)
        checks.append(
            PreflightCheck("home_writable", home_ok, str(self.home))
        )
        archive = self.home / ".claude" / "archive"
        checks.append(
            PreflightCheck(
                "claude_archive_excluded",
                True,
                f"{archive} is structurally excluded"
                + (" (present)" if archive.exists() else " (absent)"),
            )
        )
        seen: set[str] = set()
        duplicates = [
            item.relpath
            for item in self.items
            if item.relpath in seen or seen.add(item.relpath)
        ]
        checks.append(
            PreflightCheck("unique_destinations", not duplicates, ", ".join(duplicates) or "ok")
        )
        missing_sources = [
            str(item.source)
            for item in self.items
            if item.source is not None and not item.source.is_file()
        ]
        checks.append(
            PreflightCheck(
                "templates_present", not missing_sources, ", ".join(missing_sources) or "ok"
            )
        )
        symlinked = [
            item.relpath
            for item in self.items
            if self._symlinked_parent(item.relpath) is not None
        ]
        checks.append(
            PreflightCheck(
                "no_symlinked_parent_dirs", not symlinked, ", ".join(symlinked) or "ok"
            )
        )
        return PreflightReport(
            home=str(self.home),
            repo_root=str(Path(__file__).resolve().parents[2]),
            include_launchd=self.include_launchd,
            checks=tuple(checks),
        )

    def render(self, staging: Path) -> None:
        staging = Path(staging)
        rendered: dict[str, str] = {}
        payloads: dict[str, tuple[bytes, int]] = {}
        for item in self.items:
            _assert_destination_allowed(item.relpath)
            payload = self._item_payload(item)
            payloads[item.relpath] = (payload, item.mode)
            rendered[item.relpath] = _sha256_bytes(payload)
        staging.mkdir(parents=True, exist_ok=True)
        for relpath, (payload, mode) in payloads.items():
            target = staging / relpath
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
            os.chmod(target, mode)
        self._rendered = rendered
        self._verified_staging = None

    def verify(self, staging: Path) -> None:
        staging = Path(staging)
        if self._rendered is None:
            raise InstallError("verify() requires a prior render() in this plan")
        for item in self.items:
            _assert_destination_allowed(item.relpath)
            staged = staging / item.relpath
            if not staged.is_file():
                raise InstallError(f"staged file missing: {staged}")
            digest = _sha256_bytes(staged.read_bytes())
            if digest != self._rendered[item.relpath]:
                raise InstallError(f"staged file does not match render: {staged}")
        self._verified_staging = staging.resolve()

    def apply(self, backup_root: Path) -> InstallReport:
        if self._verified_staging is None or self._rendered is None:
            raise InstallError("apply() requires verify() on the same staging first")
        staging = self._verified_staging
        # Re-check the tree right now: apply refuses staging tampered after verify.
        for item in self.items:
            staged = staging / item.relpath
            if not staged.is_file() or _sha256_bytes(staged.read_bytes()) != self._rendered[item.relpath]:
                raise InstallError(f"staging changed after verify: {staged}")
            if self._symlinked_parent(item.relpath) is not None:
                raise InstallError(
                    f"refusing to traverse symlinked parent for: {item.relpath}"
                )

        backup_root = Path(backup_root)
        report = InstallReport(home=str(self.home), backup_root=str(backup_root))
        for item in self.items:
            staged = staging / item.relpath
            dest = self.home / item.relpath
            self._ensure_parents(dest.parent, report)
            payload = staged.read_bytes()
            sha_before: str | None = None
            backup_path: str | None = None
            operation = "create"
            if dest.is_symlink() or dest.exists():
                operation = "replace"
                sha_before, backup_path = self._backup(dest, backup_root)
            tmp = dest.parent / f".{dest.name}.dan-install-tmp"
            tmp.write_bytes(payload)
            os.chmod(tmp, item.mode)
            os.replace(tmp, dest)  # atomic; replaces a symlink, never follows it
            report.entries.append(
                InstallEntry(
                    path=str(dest),
                    backup=backup_path,
                    sha_before=sha_before,
                    sha_after=_sha256_bytes(payload),
                    operation=operation,
                    inverse="restore-backup" if backup_path else "remove",
                )
            )
        self._write_install_manifest(report)
        return report

    def rollback(self, report: InstallReport) -> None:
        for entry in reversed(report.entries):
            dest = Path(entry.path)
            if entry.inverse == "restore-backup" and entry.backup:
                backup = Path(entry.backup)
                if backup.is_symlink():
                    if dest.is_symlink() or dest.exists():
                        dest.unlink()
                    os.symlink(os.readlink(backup), dest)
                else:
                    shutil.copy2(backup, dest)
            else:
                if dest.is_symlink() or dest.exists():
                    dest.unlink()
        if report.manifest_path:
            manifest = Path(report.manifest_path)
            if manifest.exists():
                manifest.unlink()
        for raw in sorted(report.dirs_created, key=lambda value: -len(Path(value).parts)):
            directory = Path(raw)
            if directory.is_dir() and not any(directory.iterdir()):
                directory.rmdir()

    # ------------------------------------------------------------ helpers
    def _item_payload(self, item: InstallItem) -> bytes:
        if item.managed_block is not None:
            dest = self.home / item.relpath
            existing = None
            if dest.is_file():
                existing = dest.read_text(encoding="utf-8")
            return _merge_managed_block(existing, item.managed_block).encode("utf-8")
        if item.content is not None:
            return item.content
        if item.source is not None:
            try:
                return item.source.read_bytes()
            except OSError as exc:
                raise InstallError(f"could not read template {item.source}: {exc}") from exc
        raise InstallError(f"item has no payload: {item.relpath}")

    def _symlinked_parent(self, relpath: str) -> Path | None:
        current = self.home
        for part in PurePosixPath(relpath).parts[:-1]:
            current = current / part
            if current.is_symlink():
                return current
        return None

    def _ensure_parents(self, directory: Path, report: InstallReport) -> None:
        missing: list[Path] = []
        current = directory
        while not current.exists():
            missing.append(current)
            current = current.parent
        for path in reversed(missing):
            path.mkdir()
            report.dirs_created.append(str(path))

    def _backup(self, dest: Path, backup_root: Path) -> tuple[str, str]:
        relative = dest.relative_to(self.home)
        backup_path = backup_root / relative
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        if dest.is_symlink():
            target = os.readlink(dest)
            if backup_path.is_symlink() or backup_path.exists():
                backup_path.unlink()
            os.symlink(target, backup_path)
            sha_before = _sha256_bytes(b"symlink:" + target.encode("utf-8"))
        else:
            shutil.copy2(dest, backup_path)
            sha_before = _sha256_bytes(dest.read_bytes())
        return sha_before, str(backup_path)

    def _write_install_manifest(self, report: InstallReport) -> None:
        manifest_path = self.home / INSTALL_MANIFEST_RELPATH
        self._ensure_parents(manifest_path.parent, report)
        payload = json.dumps(report.to_dict(), ensure_ascii=False, indent=1, sort_keys=True)
        tmp = manifest_path.parent / f".{manifest_path.name}.dan-install-tmp"
        tmp.write_text(payload + "\n", encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, manifest_path)
        report.manifest_path = str(manifest_path)


__all__ = [
    "INSTALL_MANIFEST_RELPATH",
    "InstallEntry",
    "InstallError",
    "InstallItem",
    "InstallPlan",
    "InstallReport",
    "MANAGED_BLOCK_BEGIN",
    "MANAGED_BLOCK_END",
    "PreflightCheck",
    "PreflightReport",
]
