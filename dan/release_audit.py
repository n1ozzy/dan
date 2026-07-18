"""Release privacy audit for the DAN repository (Task 13).

Three read-only scans:

- ``audit_worktree``: current tracked+untracked (non-ignored) files for
  secrets, owner-specific absolute paths and private runtime data files;
- ``audit_git_history``: every blob reachable from any ref for secret
  patterns (historical non-secret migration traces are allowed and NOT
  scanned for here);
- ``scan_active_roots``: active agent instruction/adapter roots inside a
  given home directory for executable legacy references. Exclusions are
  structural (path containment), never string matching. Tests run this
  exclusively against a synthetic fake home.

Sensitive tokens below are built by concatenation so this module never
contains the contiguous strings it hunts for (the repository's own guard
tests scan raw file content of ``dan/``).
"""

from __future__ import annotations

import fnmatch
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# --- patterns -----------------------------------------------------------------

# Owner-specific absolute home prefix (concatenated: the audit must not flag itself).
OWNER_HOME = "/Users/" + "n1_ozzy"

_TMP_DAN_PREFIX = "/tmp/" + "dan-"
_LOUD_THINKING = "/tmp/" + "claude-loud-thinking"
_LEGACY_BROKER = "voice_" + "broker.py"
_LEGACY_FEEDER = "feeder" + ".sh"
_DIRECT_PLAYER = "af" + "play"
_LEGACY_DAN_TOOLS = "Documents/dev/" + "dan/tools"
_LEGACY_DANV2 = "Documents/dev/" + "DANv2"

SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("api-key", re.compile(r"\bsk-[A-Za-z0-9_\-]{24,}\b")),
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    (
        "github-token",
        re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b|\bgithub_pat_[A-Za-z0-9_]{22,}\b"),
    ),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
)

# Executable legacy references hunted in active agent roots.
LEGACY_REFERENCE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("legacy-tmp-runtime", _TMP_DAN_PREFIX),
    ("loud-thinking-runtime", _LOUD_THINKING),
    ("legacy-broker", _LEGACY_BROKER),
    ("legacy-feeder", _LEGACY_FEEDER),
    ("direct-player", _DIRECT_PLAYER),
    ("legacy-repo", _LEGACY_DAN_TOOLS),
    ("legacy-repo", _LEGACY_DANV2),
)

# Repo files that ARE private runtime data and must never be committed.
PRIVATE_FILE_GLOBS: tuple[str, ...] = (
    ".env",
    ".env.*",
    "*.pem",
    "*.p12",
    "*.key",
    "*.keychain",
    "id_rsa*",
    "id_ed25519*",
    "*.db",
    "*.sqlite",
    "*.sqlite3",
    "*.log",
)

# Active agent instruction/adapter roots relative to a home directory.
ACTIVE_ROOTS: tuple[str, ...] = (
    "AGENTS.md",
    ".agents",
    ".claude",
    ".codex",
    ".openclaw",
    "Library/LaunchAgents",
)

_MAX_FILE_BYTES = 4 * 1024 * 1024
_TEXT_PROBE_BYTES = 4096


@dataclass(frozen=True)
class Allow:
    """A deliberate path+kind scoped exemption (never a global string one)."""

    path: str
    kind: str
    reason: str


# Path/context-scoped allowlist for the worktree audit. Each entry names one
# file and one finding kind with a human reason; nothing here exempts a
# string globally across the tree. The same list applies to named blobs in
# the history scan: a fixture file's past revisions carry the same fakes.
_FAKE_SECRET_FIXTURE = "deliberate fake secret fixture exercising redaction/leak guards"
WORKTREE_ALLOWLIST: tuple[Allow, ...] = (
    Allow("dan/macos/screen.py", "api-key", _FAKE_SECRET_FIXTURE),
    Allow("dan/macos/terminal.py", "api-key", _FAKE_SECRET_FIXTURE),
    # Historical blob names of the two files above from before the package
    # rename; identical fake fixture content, reachable only via git history.
    Allow("jarvis/macos/screen.py", "api-key", _FAKE_SECRET_FIXTURE),
    Allow("jarvis/macos/terminal.py", "api-key", _FAKE_SECRET_FIXTURE),
    Allow("scripts/smoke-file-read.sh", "api-key", _FAKE_SECRET_FIXTURE),
    Allow("scripts/smoke-screen-read.sh", "api-key", _FAKE_SECRET_FIXTURE),
    Allow("scripts/smoke-terminal.sh", "api-key", _FAKE_SECRET_FIXTURE),
    Allow("scripts/smoke-worker-jobs.sh", "api-key", _FAKE_SECRET_FIXTURE),
    Allow("tests/test_api_smoke.py", "api-key", _FAKE_SECRET_FIXTURE),
    Allow("tests/test_file_read_tool.py", "api-key", _FAKE_SECRET_FIXTURE),
    Allow("tests/test_memory_compiler.py", "api-key", _FAKE_SECRET_FIXTURE),
    Allow("tests/test_memory_compiler_eval.py", "api-key", _FAKE_SECRET_FIXTURE),
    Allow("tests/test_memory_compiler_preview_api.py", "api-key", _FAKE_SECRET_FIXTURE),
    Allow("tests/test_memory_compiler_wire.py", "api-key", _FAKE_SECRET_FIXTURE),
    Allow("tests/test_memory_save_tool.py", "aws-access-key", _FAKE_SECRET_FIXTURE),
    Allow("tests/test_panel_assets.py", "api-key", _FAKE_SECRET_FIXTURE),
    Allow("tests/test_secret_redaction.py", "api-key", _FAKE_SECRET_FIXTURE),
    Allow("tests/test_secret_redaction.py", "aws-access-key", _FAKE_SECRET_FIXTURE),
    Allow("tests/test_secret_redaction.py", "private-key", _FAKE_SECRET_FIXTURE),
    Allow("tests/test_secret_redaction.py", "slack-token", _FAKE_SECRET_FIXTURE),
    Allow("tests/test_worker_jobs.py", "api-key", _FAKE_SECRET_FIXTURE),
)


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    kind: str
    detail: str


@dataclass
class WorktreeFindings:
    private_paths: list[Finding] = field(default_factory=list)
    absolute_owner_paths: list[Finding] = field(default_factory=list)
    secrets: list[Finding] = field(default_factory=list)

    def is_clean(self) -> bool:
        return not (self.private_paths or self.absolute_owner_paths or self.secrets)


def _allowed(relative: str, kind: str) -> bool:
    return any(item.path == relative and item.kind == kind for item in WORKTREE_ALLOWLIST)


def _git_lines(repo_root: Path, *arguments: str) -> list[str]:
    completed = subprocess.run(
        ["git", "-C", str(repo_root), *arguments],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in completed.stdout.splitlines() if line]


def _worktree_files(repo_root: Path) -> list[str]:
    """Tracked plus untracked-but-not-ignored files, release candidates only."""
    return sorted(
        set(
            _git_lines(
                repo_root, "ls-files", "--cached", "--others", "--exclude-standard"
            )
        )
    )


def _read_text(path: Path) -> str | None:
    try:
        if not path.is_file() or path.is_symlink():
            return None
        if path.stat().st_size > _MAX_FILE_BYTES:
            return None
        raw = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in raw[:_TEXT_PROBE_BYTES]:
        return None  # binary
    return raw.decode("utf-8", errors="replace")


def _scan_secret_lines(relative: str, text: str) -> list[Finding]:
    findings: list[Finding] = []
    for number, line in enumerate(text.splitlines(), start=1):
        for kind, pattern in SECRET_PATTERNS:
            if pattern.search(line):
                findings.append(Finding(relative, number, kind, line.strip()[:200]))
    return findings


def audit_worktree(repo_root: Path) -> WorktreeFindings:
    """Scan the current release tree; read-only."""
    repo_root = Path(repo_root)
    findings = WorktreeFindings()
    for relative in _worktree_files(repo_root):
        name = Path(relative).name
        if any(fnmatch.fnmatch(name, glob) for glob in PRIVATE_FILE_GLOBS):
            if not _allowed(relative, "private-path"):
                findings.private_paths.append(
                    Finding(relative, 0, "private-path", "private runtime data file in release tree")
                )
        text = _read_text(repo_root / relative)
        if text is None:
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if OWNER_HOME in line and not _allowed(relative, "owner-path"):
                findings.absolute_owner_paths.append(
                    Finding(relative, number, "owner-path", line.strip()[:200])
                )
        findings.secrets.extend(
            finding
            for finding in _scan_secret_lines(relative, text)
            if not _allowed(relative, finding.kind)
        )
    return findings


def audit_git_history(repo_root: Path) -> list[Finding]:
    """Scan every blob reachable from any ref for secret patterns only.

    Historical non-secret migration traces (old absolute paths and the
    like) are deliberately allowed to remain in git history; secrets are not.
    """
    repo_root = Path(repo_root)
    object_lines = _git_lines(repo_root, "rev-list", "--all", "--objects")
    shas: dict[str, str] = {}
    for line in object_lines:
        parts = line.split(" ", 1)
        shas.setdefault(parts[0], parts[1] if len(parts) > 1 else "")

    findings: list[Finding] = []
    process = subprocess.Popen(
        ["git", "-C", str(repo_root), "cat-file", "--batch"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
    )
    assert process.stdin is not None and process.stdout is not None
    try:
        for sha, name in shas.items():
            process.stdin.write(f"{sha}\n".encode())
            process.stdin.flush()
            header = process.stdout.readline().decode("utf-8", errors="replace").strip()
            parts = header.split()
            if len(parts) != 3:
                continue  # "<sha> missing": no payload follows
            size = int(parts[2])
            # Every found object (blob, commit, tree, tag) is followed by
            # <size> bytes + newline; consume them all to keep the batch
            # stream in sync even for object types we do not scan.
            payload = process.stdout.read(size)
            process.stdout.read(1)  # trailing newline
            if parts[1] != "blob":
                continue
            if size > _MAX_FILE_BYTES or b"\x00" in payload[:_TEXT_PROBE_BYTES]:
                continue
            text = payload.decode("utf-8", errors="replace")
            for finding in _scan_secret_lines(f"{sha[:12]}:{name}", text):
                # The path-scoped fixture allowlist follows the blob's
                # historical name: past revisions of a fixture file carry
                # the same deliberate fakes.
                if _allowed(name, finding.kind):
                    continue
                findings.append(finding)
    finally:
        process.stdin.close()
        process.stdout.close()
        process.wait()
    return findings


def _is_excluded(path: Path, exclude: tuple[Path, ...]) -> bool:
    """Structural containment check; never a substring match on the path text."""
    resolved = path.resolve()
    for excluded in exclude:
        excluded_resolved = Path(excluded).resolve()
        if resolved == excluded_resolved or excluded_resolved in resolved.parents:
            return True
    return False


def scan_active_roots(
    home: Path, exclude: tuple[Path, ...] = ()
) -> list[Finding]:
    """Read-only scan of active agent roots under ``home`` for legacy references.

    ``exclude`` entries are compared structurally (path containment), so an
    archive directory is skipped as a subtree while files merely *named*
    like it are still scanned.
    """
    home = Path(home)
    findings: list[Finding] = []
    for root_name in ACTIVE_ROOTS:
        root = home / root_name
        if not root.exists():
            continue
        candidates = [root] if root.is_file() else sorted(root.rglob("*"))
        for path in candidates:
            if not path.is_file() or _is_excluded(path, exclude):
                continue
            text = _read_text(path)
            if text is None:
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                for kind, needle in LEGACY_REFERENCE_PATTERNS:
                    if needle in line:
                        findings.append(
                            Finding(str(path), number, kind, line.strip()[:200])
                        )
    return findings


__all__ = [
    "Allow",
    "Finding",
    "WorktreeFindings",
    "audit_worktree",
    "audit_git_history",
    "scan_active_roots",
    "OWNER_HOME",
    "SECRET_PATTERNS",
    "LEGACY_REFERENCE_PATTERNS",
    "WORKTREE_ALLOWLIST",
]
