"""Prompt 01 scaffold contract checks."""

from __future__ import annotations

from pathlib import Path
import re


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
    "dan",
    "dan/daemon",
    "dan/runtime",
    "dan/api",
    "dan/store",
    "dan/events",
    "dan/turns",
    "dan/brain",
    "dan/memory",
    "dan/audio",
    "dan/voice",
    "dan/tools",
    "dan/workers",
    "dan/panel",
    "dan/panel/assets",
    "scripts",
    "launchd",
    "tests",
)

REQUIRED_TOP_LEVEL_FILES = (
    "README.md",
    "pyproject.toml",
    ".gitignore",
    "config/dan.example.toml",
    "dan/store/schema.sql",
    "dan/panel/assets/index.html",
    "dan/panel/assets/app.js",
    "dan/panel/assets/styles.css",
    "scripts/dand",
    "scripts/dan-panel",
    "scripts/dev-reset-local-state.sh",
    "launchd/com.dan.dand.plist.example",
)

FORBIDDEN_RUNTIME_SNIPPETS = (
    "/Users/n1_ozzy/Documents/dev/dan",
    "/tmp/dan",
    "afplay",
    "--dangerously-skip-permissions",
)

ALLOWED_RUNTIME_SNIPPETS = {
    ("README.md", "/Users/n1_ozzy/Documents/dev/dan"),
    ("dan/brain/context_builder.py", "/Users/n1_ozzy/Documents/dev/dan"),
    # The test gate detects these literals in source; it never executes them.
    ("dan/migration/test_safety.py", "/tmp/dan"),
    ("dan/migration/test_safety.py", "afplay"),
    ("dan/voice/shared_broker.py", "/tmp/dan"),
}


def test_required_docs_exist() -> None:
    missing = [path for path in REQUIRED_DOCS if not (ROOT / path).is_file()]
    assert missing == []


def test_active_docs_use_final_dan_product_names() -> None:
    active_docs = [ROOT / "README.md"]
    active_docs.extend(sorted((ROOT / "docs" / "runbooks").glob("*.md")))
    legacy = re.compile(r"\b(?:jarvis|Jarvis|JARVIS|DANv2)\b")
    external_operator_source = "~/Desktop/Jarvis/JARVIS-NEXT-STEPS-FOR-OZZY.md"

    matches = {
        str(path.relative_to(ROOT)): sorted(
            set(legacy.findall(path.read_text(encoding="utf-8").replace(external_operator_source, "")))
        )
        for path in active_docs
        if legacy.search(path.read_text(encoding="utf-8").replace(external_operator_source, ""))
    }

    assert matches == {}
    gate = (ROOT / "docs" / "runbooks" / "G4_LIVE_GATE.md").read_text(encoding="utf-8")
    assert "~/Desktop/Jarvis/JARVIS-NEXT-STEPS-FOR-OZZY.md" in gate


def test_claude_agent_contract_uses_final_runtime_names() -> None:
    contract = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")

    assert "# CLAUDE.md — dan-runtime" in contract
    assert "`dan-runtime` v4.2.0a0" in contract
    assert "`dand` (`dan.cli:daemon_main`)" in contract
    assert "`com.dan.dand` → `~/.dan/bin/dand`" in contract
    assert "`jarvis-runtime`" not in contract
    assert "`com.ozzy.jarvisd`" not in contract


def test_review_handoff_contains_required_orientation() -> None:
    handoff = (ROOT / "docs/REVIEW_HANDOFF.md").read_text(encoding="utf-8")

    required_snippets = (
        # Historical review provenance remains intentionally named Jarvis.
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
    scanned_roots = ("dan", "config", "scripts", "launchd", "README.md", "pyproject.toml")
    text_suffixes = {".py", ".sql", ".toml", ".md", ".sh", ".example", ".html", ".js", ".css", ""}
    offenders: list[tuple[str, str]] = []

    for relative_root in scanned_roots:
        root = ROOT / relative_root
        files = [root] if root.is_file() else [path for path in root.rglob("*") if path.is_file()]
        for path in files:
            if "__pycache__" in path.parts or path.suffix not in text_suffixes:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            relative = str(path.relative_to(ROOT))
            for snippet in FORBIDDEN_RUNTIME_SNIPPETS:
                if (relative, snippet) in ALLOWED_RUNTIME_SNIPPETS:
                    continue
                if snippet in text:
                    offenders.append((relative, snippet))

    assert offenders == []


def test_ds_store_is_ignored_in_repo_gitignore() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".DS_Store" in gitignore.splitlines()
