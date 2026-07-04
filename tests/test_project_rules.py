"""Project guardrails for docs ownership, scope, and CI safety."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = (
    "AGENTS.md",
    "docs/PROJECT_RULES.md",
    "docs/DOCS_INDEX.md",
    "docs/STATUS.md",
    "docs/AGENT_PROMPT_TEMPLATE.md",
)

ARCHITECTURE_LAWS = (
    "jarvisd owns truth",
    "panel is only a client",
    "brain adapters are stateless",
    "provider sessions are not jarvis memory",
    "one task = one scope = one commit = stop for review",
    "no schema/migrations changes without explicit task scope",
    "no live voice/mic/speaker/launchctl/provider/network in automated ci",
    "regression test first",
    "authoritative, current, runbook, historical, or archived",
    "old roadmap/handoff files cannot override current project_rules/status",
    "examples are not roadmap commitments",
    "voice claims must say whether they are mock/smoke/live/manual",
    "no broad cleanup/refactor mixed with feature/fix work",
)

PROMPT_TEMPLATE_SECTIONS = (
    "Task",
    "Scope",
    "Allowed files",
    "Forbidden files",
    "Required failing test first",
    "Verification",
    "Stop condition",
)

FORBIDDEN_WORKFLOW_SNIPPETS = (
    "smoke-voice-turn.sh",
    "smoke-voice-listening.sh",
    "smoke-voice-recorder.sh",
    "smoke-voice-stt.sh",
    "smoke-voice-speech.sh",
    "launchctl",
    "smoke-claude-cli-brain.sh",
    "smoke-provider",
    "provider_smoke",
    "openai api",
    "groq",
    "ollama",
)

FORBIDDEN_DOC_AUTHORITY_CLAIMS = (
    "JARVIS-V3-EXECUTION-ROADMAP.md is the source of truth",
    "JARVIS-V3-EXECUTION-ROADMAP.md is authoritative",
    "docs/REVIEW_HANDOFF.md is the source of truth",
    "docs/REVIEW_HANDOFF.md is authoritative",
    "docs/JARVIS_FIX_TASKS_HANDOFF.md is the source of truth",
    "docs/JARVIS_FIX_TASKS_HANDOFF.md is authoritative",
    "supersedes docs/PROJECT_RULES.md",
    "supersedes docs/STATUS.md",
)


def read_project_file(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_required_guardrail_files_exist() -> None:
    missing = [path for path in REQUIRED_FILES if not (ROOT / path).is_file()]

    assert missing == []


def test_project_rules_contains_architecture_laws() -> None:
    text = read_project_file("docs/PROJECT_RULES.md").casefold()

    missing = [snippet for snippet in ARCHITECTURE_LAWS if snippet not in text]

    assert missing == []


def test_agent_prompt_template_contains_required_sections() -> None:
    text = read_project_file("docs/AGENT_PROMPT_TEMPLATE.md")

    missing = [section for section in PROMPT_TEMPLATE_SECTIONS if section not in text]

    assert missing == []


def test_docs_index_declares_conflict_precedence() -> None:
    text = read_project_file("docs/DOCS_INDEX.md").casefold()

    required = (
        "agents.md",
        "docs/project_rules.md",
        "docs/status.md",
        "win over old handoffs/roadmaps",
    )
    missing = [snippet for snippet in required if snippet not in text]

    assert missing == []


def test_old_roadmaps_and_handoffs_do_not_claim_current_authority() -> None:
    docs = [path for path in (ROOT / "docs").rglob("*.md") if path.is_file()]
    offenders: list[tuple[str, str]] = []

    for path in docs:
        text = path.read_text(encoding="utf-8", errors="replace")
        for snippet in FORBIDDEN_DOC_AUTHORITY_CLAIMS:
            if snippet in text:
                offenders.append((str(path.relative_to(ROOT)), snippet))

    assert offenders == []


def test_workflows_do_not_run_live_voice_launchctl_or_real_providers() -> None:
    workflows_dir = ROOT / ".github" / "workflows"
    if not workflows_dir.exists():
        return

    workflow_files = [
        path
        for path in workflows_dir.rglob("*")
        if path.is_file() and path.suffix in {".yml", ".yaml"}
    ]
    offenders: list[tuple[str, str]] = []

    for path in workflow_files:
        text = path.read_text(encoding="utf-8", errors="replace").casefold()
        for snippet in FORBIDDEN_WORKFLOW_SNIPPETS:
            if snippet in text:
                offenders.append((str(path.relative_to(ROOT)), snippet))

    assert offenders == []
