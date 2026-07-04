# Jarvis Documentation Package Index

This package contains new documentation for branch `rescue/audt-gpt5.5pro-limit-cdn`, reviewed against local HEAD `bd18d3b`, git history, public GitHub reference, and the Memory OS task history from the active sessions.

## Files

- `JARVIS_CURRENT_STATE.md` — current branch state, completed work, active workstream, next steps.
- `JARVIS_ARCHITECTURE.md` — whole-system technical architecture.
- `MEMORY_OS_ARCHITECTURE.md` — detailed Memory OS architecture and prompt-safety contract.
- `JARVIS_PROJECT_RULES.md` — engineering rules for future Codex/ChatGPT work.
- `JARVIS_CHANGE_GUARDS.md` — reusable guard commands and task boundaries.
- `JARVIS_ROADMAP.md` — Done / Now / Next / Later / Do not do yet.
- `JARVIS_HISTORY.md` — condensed history from first commit to current branch.
- `JARVIS_DO_NOT_TOUCH.md` — high-risk boundaries.
- `JARVIS_DOCS_PACKAGE_INDEX.md` — package index and suggested docs-only commit scope.

## Recommended placement

Files live in the repo `docs/` directory.

Do not overwrite existing authoritative docs blindly. Review conflicts with:

- `AGENTS.md`
- `docs/PROJECT_RULES.md`
- `docs/STATUS.md`
- `docs/DOCS_INDEX.md`
- `docs/MEMORY_CONTRACT.md`
- `docs/MEMORY_COMPILER.md`

## Suggested commit

```sh
git add docs/JARVIS_CURRENT_STATE.md   docs/JARVIS_ARCHITECTURE.md   docs/MEMORY_OS_ARCHITECTURE.md   docs/JARVIS_PROJECT_RULES.md   docs/JARVIS_CHANGE_GUARDS.md   docs/JARVIS_ROADMAP.md   docs/JARVIS_HISTORY.md   docs/JARVIS_DO_NOT_TOUCH.md   docs/JARVIS_DOCS_PACKAGE_INDEX.md

git commit -m "docs: document Jarvis architecture and project rules"
```
