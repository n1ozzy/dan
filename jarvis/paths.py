"""Runtime path resolution for Jarvis-owned local state."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from jarvis.config import JarvisConfig


@dataclass(frozen=True)
class RuntimePaths:
    home: Path
    db_path: Path
    logs_dir: Path
    runtime_dir: Path
    pid_file: Path
    log_file: Path

    def to_dict(self) -> dict[str, str]:
        return {key: str(value) for key, value in asdict(self).items()}


def expand_user_path(path: str) -> Path:
    """Expand a user-configured path into an absolute filesystem path."""

    expanded = Path(path).expanduser()
    if not expanded.is_absolute():
        expanded = Path.cwd() / expanded
    return expanded


def resolve_runtime_paths(config: JarvisConfig) -> RuntimePaths:
    """Resolve runtime paths without creating files or directories."""

    logs_dir = expand_user_path(config.runtime.logs_dir)
    return RuntimePaths(
        home=expand_user_path(config.runtime.home),
        db_path=expand_user_path(config.database.path),
        logs_dir=logs_dir,
        runtime_dir=expand_user_path(config.runtime.runtime_dir),
        pid_file=expand_user_path(config.runtime.pid_file),
        log_file=logs_dir / "jarvisd.log",
    )


def ensure_runtime_dirs(paths: RuntimePaths) -> None:
    """Create only the Jarvis runtime directories needed before use."""

    for directory in (paths.home, paths.logs_dir, paths.runtime_dir):
        directory.mkdir(parents=True, exist_ok=True)


def paths_to_jsonable(paths: RuntimePaths) -> dict[str, Any]:
    return paths.to_dict()
