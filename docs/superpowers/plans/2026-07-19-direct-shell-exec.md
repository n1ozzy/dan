# Direct Shell Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a real direct `shell_exec` tool that executes model-requested shell commands without approval rows.

**Architecture:** Implement one concrete tool beside `ShellReadTool`, register it in the daemon's existing tool registry, and rely on the already-direct registry/orchestrator path for execution and durable recording. Keep cwd validation, timeout, and bounded output inside the tool.

**Tech Stack:** Python 3.14, `subprocess`, pytest, DAN tool registry.

## Global Constraints

- Model-originated tools execute directly and return their real result.
- No approval row or awaiting-approval turn may be inserted.
- Do not start TTS or touch live audio.
- Do not commit without Ozzy's explicit command.

---

### Task 1: Implement and register `shell_exec`

**Files:**
- Modify: `dan/tools/shell_tool.py`
- Modify: `dan/daemon/app.py`
- Modify: `tests/test_tool_permissions.py`
- Create: `tests/test_shell_exec_tool.py`

**Interfaces:**
- Consumes: `Tool.run(arguments: Mapping[str, Any]) -> Mapping[str, Any]`, daemon `approved_roots`, direct `ToolRegistry.request_tool()`.
- Produces: `ShellExecTool(approved_roots, default_cwd, timeout_seconds)` registered as `shell_exec` with risk `shell_write`.

- [ ] **Step 1: Write failing behavior and registration tests**

  Test that `shell_exec` executes `printf`, permits a mutation inside `tmp_path`, rejects cwd outside its roots, appears in the initialized daemon registry, and leaves the `approvals` table empty after a model-originated request.

- [ ] **Step 2: Verify RED**

  Run: `.venv/bin/python -m pytest -q tests/test_shell_exec_tool.py`

  Expected: collection failure because `ShellExecTool` does not exist.

- [ ] **Step 3: Implement the minimal tool**

  Add command/cwd validation, `/bin/zsh -c` execution, inherited environment with a usable local PATH, timeout handling, and existing bounded output formatting. Register the tool in `create_daemon_app_from_config()` with the repo root as default cwd. Remove `ShellWritePlaceholderTool`, its export, stale scaffold comments, and its placeholder-only test.

- [ ] **Step 4: Verify GREEN and focused regressions**

  Run: `.venv/bin/python -m pytest -q tests/test_shell_exec_tool.py tests/test_model_tool_permission_policy.py tests/test_api_smoke.py -k 'shell_exec or tools_endpoint or model_originated'`

  Expected: all selected tests pass and the approval count remains zero.

- [ ] **Step 5: Verify the checkout**

  Run: `git diff --check && dan doctor --json`

  Expected: diff check exits zero; doctor reports a healthy running daemon. Do not restart the daemon and do not commit.
