"""Prompt 01 scaffold contract checks."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

REQUIRED_DOCS = (
    "docs/PRODUCT.md",
    "docs/CONTRACTS.md",
    "docs/TURN_PIPELINE.md",
    "docs/AUDIO_RUNTIME.md",
    "docs/LAUNCH_SUPERVISION.md",
    "docs/SECURITY_MODEL.md",
    "docs/PANEL_CONTRACT.md",
    "docs/MACOS_OPERATOR_CONTRACT.md",
    "docs/MIGRATION_INVENTORY.md",
    "docs/LEGACY_RUNTIME_FINDINGS.md",
    "docs/DECISIONS.md",
    "docs/REVIEW_HANDOFF.md",
)

REQUIRED_DIRS = (
    "config",
    "config/persona",
    "jarvis",
    "jarvis/daemon",
    "jarvis/runtime",
    "jarvis/api",
    "jarvis/store",
    "jarvis/events",
    "jarvis/turns",
    "jarvis/brain",
    "jarvis/memory",
    "jarvis/audio",
    "jarvis/voice",
    "jarvis/tools",
    "jarvis/workers",
    "jarvis/panel",
    "jarvis/panel/assets",
    "scripts",
    "launchd",
    "tests",
)

REQUIRED_TOP_LEVEL_FILES = (
    "README.md",
    "pyproject.toml",
    ".gitignore",
    "config/jarvis.example.toml",
    "config/persona/jarvis.md",
    "jarvis/store/schema.sql",
    "jarvis/panel/assets/index.html",
    "jarvis/panel/assets/app.js",
    "jarvis/panel/assets/styles.css",
    "scripts/jarvisd",
    "scripts/jarvis-panel",
    "scripts/dev-reset-local-state.sh",
    "launchd/com.ozzy.jarvisd.plist.example",
)

FORBIDDEN_RUNTIME_SNIPPETS = (
    "/Users/n1_ozzy/Documents/dev/dan",
    "/tmp/dan",
    "afplay",
    "--dangerously-skip-permissions",
)


def test_required_docs_exist() -> None:
    missing = [path for path in REQUIRED_DOCS if not (ROOT / path).is_file()]
    assert missing == []


def test_review_handoff_contains_required_orientation() -> None:
    handoff = (ROOT / "docs/REVIEW_HANDOFF.md").read_text(encoding="utf-8")

    required_snippets = (
        "Jarvis v4.2 Reviewer Handoff",
        "JARVIS-V3-EXECUTION-ROADMAP.md is historical only",
        "read-only reference",
        "FAZY A–H",
        "docs/MASTER_PLAN.md",
        "jarvis-dan-report",
        "decree §7.8",
        "never auto-execute",
    )

    missing = [snippet for snippet in required_snippets if snippet not in handoff]
    assert missing == []


def test_macos_operator_contract_contains_required_orientation() -> None:
    contract = (ROOT / "docs/MACOS_OPERATOR_CONTRACT.md").read_text(encoding="utf-8")

    required_snippets = (
        "macOS Operator Contract",
        "Examples vs commitments",
        "not automatically implementation commitments",
        "promoted by a later scoped prompt",
        "local macOS operator",
        "If the user can do an action through the Mac UI",
        "model never operates the Mac directly",
        "Accessibility API",
        "ScreenCaptureKit",
        "Vision OCR",
        "OperatorSession",
        "external communication examples",
        "Prompt 19D",
    )

    missing = [snippet for snippet in required_snippets if snippet not in contract]
    assert missing == []


def test_required_directories_exist() -> None:
    missing = [path for path in REQUIRED_DIRS if not (ROOT / path).is_dir()]
    assert missing == []


def test_required_scaffold_files_exist() -> None:
    missing = [path for path in REQUIRED_TOP_LEVEL_FILES if not (ROOT / path).is_file()]
    assert missing == []


def test_runtime_scaffold_avoids_legacy_escape_hatches() -> None:
    scanned_roots = ("jarvis", "config", "scripts", "launchd", "README.md", "pyproject.toml")
    text_suffixes = {".py", ".sql", ".toml", ".md", ".sh", ".example", ".html", ".js", ".css", ""}
    offenders: list[tuple[str, str]] = []

    for relative_root in scanned_roots:
        root = ROOT / relative_root
        files = [root] if root.is_file() else [path for path in root.rglob("*") if path.is_file()]
        for path in files:
            if "__pycache__" in path.parts or path.suffix not in text_suffixes:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for snippet in FORBIDDEN_RUNTIME_SNIPPETS:
                if snippet in text:
                    offenders.append((str(path.relative_to(ROOT)), snippet))

    assert offenders == []


def test_ds_store_is_ignored_in_repo_gitignore() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".DS_Store" in gitignore.splitlines()
