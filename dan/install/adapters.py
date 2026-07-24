"""Host adapter specs, the one speak contract and the decision manifest.

Every host adapter — Claude skill, Codex skill, OpenClaw skill, gpt-say,
standup and the MessageDisplay hook — invokes exactly the same CLI:

    dan speak --json --as <persona> --session <session> --source <host> --stdin

with UTF-8 text on stdin. Adapters carry no persona text, no voice maps, no
engine choice, no mastering and no fallback logic.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
INTEGRATIONS_ROOT = REPO_ROOT / "integrations"
MANIFEST_PATH = INTEGRATIONS_ROOT / "manifest.toml"
INVENTORY_SNAPSHOT_PATH = INTEGRATIONS_ROOT / "inventory-producers.json"

COMPLETE_STATUSES = frozenset({"migrated", "disabled", "rejected"})
_REQUIRED_ROW_FIELDS = (
    "id",
    "host",
    "old_format",
    "behavior",
    "destination",
    "status",
    "test",
    "reason",
)


class AdapterError(RuntimeError):
    """An adapter or manifest contract was violated."""


@dataclass(frozen=True)
class AdapterInvocation:
    argv: list[str]
    stdin: bytes
    stdin_encoding: str
    persona: str
    session: str
    source: str


@dataclass(frozen=True)
class AdapterSpec:
    host: str
    persona: str
    session: str
    template: str  # repo-relative adapter file
    destination: str  # home-relative install destination

    @property
    def template_path(self) -> Path:
        return REPO_ROOT / self.template

    @property
    def command_line(self) -> str:
        return (
            f"dan speak --json --as {self.persona} --session {self.session} "
            f"--source {self.host} --stdin"
        )

    def invoke(self, text: str) -> AdapterInvocation:
        return AdapterInvocation(
            argv=[
                "dan",
                "speak",
                "--json",
                "--as",
                self.persona,
                "--session",
                self.session,
                "--source",
                self.host,
                "--stdin",
            ],
            stdin=text.encode("utf-8"),
            stdin_encoding="utf-8",
            persona=self.persona,
            session=self.session,
            source=self.host,
        )


ADAPTERS: dict[str, AdapterSpec] = {
    "claude": AdapterSpec(
        host="claude",
        persona="dan",
        session="claude",
        template="integrations/claude/skills/dan-persona/SKILL.md",
        destination=".claude/skills/dan-persona/SKILL.md",
    ),
    "codex": AdapterSpec(
        host="codex",
        persona="dan",
        session="codex",
        template="integrations/codex/skills/dan-persona/SKILL.md",
        destination=".codex/skills/dan-persona/SKILL.md",
    ),
    "openclaw": AdapterSpec(
        host="openclaw",
        persona="dan",
        session="openclaw",
        template="integrations/openclaw/skills/dan/SKILL.md",
        destination=".openclaw/skills/dan/SKILL.md",
    ),
    "gpt-say": AdapterSpec(
        host="gpt-say",
        persona="dan",
        session="gpt-say",
        template="integrations/shared/skills/gpt-say/SKILL.md",
        destination=".agents/skills/gpt-say/SKILL.md",
    ),
    "standup": AdapterSpec(
        host="standup",
        persona="dan",
        session="standup",
        template="integrations/shared/skills/standup/SKILL.md",
        destination=".agents/skills/standup/SKILL.md",
    ),
    "hook": AdapterSpec(
        host="hook",
        persona="dan",
        session="claude-hook",
        template="integrations/claude/hooks/tts-message-display.sh",
        destination=".claude/hooks/tts-message-display.sh",
    ),
}

SHARED_SKILLS = (
    "gadanie",
    "dobranocka",
    "trio-live",
    "danusia-live",
    "gpt-say",
    "voice-report",
    "standup",
    "screen-control",
)


def installed_adapter(host: str) -> AdapterSpec:
    try:
        return ADAPTERS[host]
    except KeyError:
        raise AdapterError(f"unknown adapter host: {host!r}") from None


@dataclass(frozen=True)
class ManifestRow:
    id: str
    host: str
    old_format: str
    behavior: str
    destination: str
    status: str
    test: str
    reason: str


@dataclass(frozen=True)
class Manifest:
    rows: tuple[ManifestRow, ...]
    notes: dict[str, str]
    pending: tuple[str, ...] = field(default_factory=tuple)

    @property
    def producer_ids(self) -> tuple[str, ...]:
        return tuple(row.id for row in self.rows)

    def row(self, producer_id: str) -> ManifestRow:
        for row in self.rows:
            if row.id == producer_id:
                return row
        raise AdapterError(f"no manifest row for producer: {producer_id!r}")


@dataclass(frozen=True)
class InventorySnapshot:
    producer_ids: tuple[str, ...]
    source: str


def load_manifest(path: Path = MANIFEST_PATH) -> Manifest:
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise AdapterError(f"could not load manifest {path}: {exc}") from exc

    rows: list[ManifestRow] = []
    pending: list[str] = []
    for raw in data.get("producer", []):
        missing = [name for name in _REQUIRED_ROW_FIELDS if not str(raw.get(name, "")).strip()]
        status = str(raw.get("status", ""))
        if missing or status not in COMPLETE_STATUSES:
            pending.append(str(raw.get("id", "<missing id>")))
            continue
        rows.append(ManifestRow(**{name: str(raw[name]) for name in _REQUIRED_ROW_FIELDS}))
    notes = {key: str(value) for key, value in (data.get("notes") or {}).items()}
    return Manifest(rows=tuple(rows), notes=notes, pending=tuple(pending))


def load_inventory(path: Path = INVENTORY_SNAPSHOT_PATH) -> InventorySnapshot:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AdapterError(f"could not load inventory snapshot {path}: {exc}") from exc
    ids = data.get("producer_ids")
    if not isinstance(ids, list) or not ids:
        raise AdapterError(f"inventory snapshot has no producer_ids: {path}")
    return InventorySnapshot(producer_ids=tuple(ids), source=str(data.get("source", "")))


def adapter_install_items() -> list[tuple[str, Path, int]]:
    """(home-relative destination, template path, mode) for every adapter file."""

    items: list[tuple[str, Path, int]] = []
    seen: set[str] = set()
    for spec in ADAPTERS.values():
        if spec.destination in seen:
            continue
        seen.add(spec.destination)
        mode = 0o755 if spec.destination.endswith(".sh") else 0o644
        items.append((spec.destination, spec.template_path, mode))
    for name in SHARED_SKILLS:
        destination = f".agents/skills/{name}/SKILL.md"
        if destination in seen:
            continue
        seen.add(destination)
        template = INTEGRATIONS_ROOT / "shared" / "skills" / name / "SKILL.md"
        items.append((destination, template, 0o644))
    return items


__all__ = [
    "ADAPTERS",
    "AdapterError",
    "AdapterInvocation",
    "AdapterSpec",
    "INVENTORY_SNAPSHOT_PATH",
    "INTEGRATIONS_ROOT",
    "MANIFEST_PATH",
    "Manifest",
    "ManifestRow",
    "SHARED_SKILLS",
    "adapter_install_items",
    "installed_adapter",
    "load_inventory",
    "load_manifest",
]
