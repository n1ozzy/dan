# Direct Shell Execution Design

## Goal

Give DAN a real model-originated shell tool that executes immediately, returns the actual process result, and never creates an approval row or an awaiting-approval turn.

## Decision

Add `ShellExecTool` beside the existing read-only shell tool and register it as `shell_exec`. It accepts a non-empty command plus an optional working directory, invokes `/bin/zsh -c`, and returns bounded stdout, stderr, return code, and the effective cwd. The cwd must resolve inside DAN's configured roots; the default cwd is the current DAN repository.

The tool uses the existing direct `ToolRegistry.request_tool()` path, `ToolRunRecorder`, timeout handling, output clipping, and secret redaction. It does not consult or create `ApprovalGate` records. The existing `shell_read` remains available for narrow read-only requests, while `terminal_paste` keeps its visible no-Enter contract.

Because `shell_exec` replaces the unfinished write-shell scaffold, the same change removes `ShellWritePlaceholderTool`, its export, stale comments, and tests that only preserve the placeholder. No parallel legacy write-shell abstraction remains.

## Acceptance

- `/tools` exposes `shell_exec` with risk `shell_write` and an honest direct-execution description.
- A model-originated `shell_exec` request runs without approval and returns its real result.
- No approval record is created.
- Empty commands and cwd values outside configured roots fail explicitly.
- The superseded write-shell placeholder and its dead compatibility surface are removed.
- Tests never run destructive or live-audio commands.
- No commit is made without Ozzy's explicit command.
