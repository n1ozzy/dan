"""Safe subprocess Claude CLI brain adapter.

Two paths share one prompt format and one safety net:

- **Blocking** (default): one `subprocess.run`, full stdout parsed once.
- **Streaming** (G4d, G0 §2): when the caller passes `on_delta`, the CLI
  runs with `--output-format stream-json` and text fragments are forwarded
  best effort as they arrive. Deltas carry NO authority — the canonical
  `BrainResponse.text` comes from the CLI's final `result` event only, and
  nothing about deltas is persisted here. The subprocess registers a kill
  handle in the GenerationRegistry (barge-in leg 1, VOICE_STREAMING §7):
  a cancelled generation dies mid-stream and surfaces as
  ``BrainGenerationCancelled`` (a distinct subclass), so the turn is
  CANCELLED, never a fake success and never a misreported failure.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
from collections.abc import Callable, Sequence
from typing import Any

from jarvis.brain.base import (
    BrainAdapterError,
    BrainGenerationCancelled,
    BrainRequest,
    BrainResponse,
    BrainUsage,
)
from jarvis.brain.tool_call_parser import parse_tool_call_blocks
from jarvis.logging import get_logger, redact_secrets


_LOGGER = get_logger("brain.claude_cli")

CliRunner = Callable[[list[str], str, float], subprocess.CompletedProcess[str]]
DEFAULT_STDERR_PREVIEW_CHARS = 800

# Streaming flags appended to the configured args when on_delta is passed.
# stream-json requires --verbose in -p mode; --include-partial-messages is
# what turns whole-message events into token-level text deltas.
DEFAULT_STREAM_ARGS = (
    "--output-format",
    "stream-json",
    "--verbose",
    "--include-partial-messages",
)


def format_cli_prompt(request: BrainRequest) -> str:
    """Build a deterministic, stateless prompt for provider CLI stdin."""

    lines = [
        "Jarvis v4.1 stateless brain request.",
        "",
        "Rules:",
        "- Answer as Jarvis using only the context in this request.",
        "- Do not expose hidden chain-of-thought; provide the final answer only.",
        "- Provider sessions are not Jarvis memory.",
        "- Tools are not executable in this call; tool requests remain pending approval.",
        '- If you need a tool, request it using exactly: <jarvis_tool_call>{"name":"tool_name","arguments":{...}}</jarvis_tool_call>',
        "- Tool requests are not executed automatically. Human approval is required.",
        "- Do not claim a requested tool has already been executed.",
        "- Do not include dangerous shell, file, network, or system mutation requests.",
        "",
        f"Conversation: {request.conversation_id}",
        f"Turn: {request.turn_id}",
        "",
        "System context:",
    ]
    system_messages = [message for message in request.context_messages if message.role == "system"]
    lines.extend(_format_messages(system_messages))

    lines.extend(["", "Memory blocks:"])
    lines.extend(_format_memory_blocks(request))

    lines.extend(["", "Recent context:"])
    recent_messages = [message for message in request.context_messages if message.role != "system"]
    lines.extend(_format_messages(recent_messages))

    lines.extend(["", "Available tools:"])
    lines.extend(_format_tools(request))

    lines.extend(
        [
            "",
            "Current user input:",
            _clean_text(request.input_text),
            "",
            "Respond now as Jarvis.",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def default_subprocess_runner(
    command: list[str],
    input_text: str,
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        input=input_text,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def default_stream_process_factory(command: list[str], prompt: str) -> subprocess.Popen[str]:
    """Spawn the CLI in its own process group so a barge-in kill takes the
    whole tree (a node CLI may fork; an orphan must not hold the pipes).

    The prompt is deliberately NOT written here (FIX-07 HIGH): writing the whole
    prompt to stdin before the watchdog is armed and stdout/stderr are drained
    deadlocks on a large prompt. ``stream_cli_response`` writes it on its own
    thread, concurrent with the stdout drain."""

    return subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )


def _signal_process(proc: Any, *, force: bool) -> None:
    """Kill the process group when real, fall back to the object's own
    terminate/kill (test doubles carry no usable pid)."""

    sig = signal.SIGKILL if force else signal.SIGTERM
    pid = getattr(proc, "pid", None)
    if isinstance(pid, int) and pid > 0:
        try:
            os.killpg(pid, sig)
            return
        except (ProcessLookupError, PermissionError, OSError):
            pass
    try:
        if force:
            proc.kill()
        else:
            proc.terminate()
    except Exception:  # noqa: BLE001 — the process may already be gone
        pass


class _StreamJsonParser:
    """stream-json lines -> best-effort deltas + the canonical result text.

    Partial `stream_event` text deltas are forwarded as they arrive; the
    whole-message `assistant` event that follows them is NOT re-emitted
    (it repeats text already streamed). Garbage lines are skipped — deltas
    are transport, not truth.
    """

    def __init__(self, on_delta: Callable[[str], None]) -> None:
        self._on_delta = on_delta
        self._saw_partial = False
        self.result_text: str | None = None
        self.result_is_error = False
        self.result_usage: dict[str, Any] = {}

    def feed_line(self, line: str) -> None:
        stripped = line.strip()
        if not stripped:
            return
        try:
            data = json.loads(stripped)
        except ValueError:
            _LOGGER.debug("skipping non-JSON stream line: %r", stripped[:120])
            return
        if not isinstance(data, dict):
            return
        kind = data.get("type")
        if kind == "stream_event":
            self._feed_stream_event(data.get("event"))
        elif kind == "assistant":
            self._feed_assistant(data.get("message"))
        elif kind == "result":
            self.result_is_error = bool(data.get("is_error"))
            result = data.get("result")
            if isinstance(result, str):
                self.result_text = result
            usage = data.get("usage")
            if isinstance(usage, dict):
                self.result_usage = usage

    # -- internals -------------------------------------------------------------

    def _feed_stream_event(self, event: Any) -> None:
        if not isinstance(event, dict) or event.get("type") != "content_block_delta":
            return
        delta = event.get("delta")
        if isinstance(delta, dict) and delta.get("type") == "text_delta":
            text = delta.get("text")
            if isinstance(text, str) and text:
                self._saw_partial = True
                self._emit(text)

    def _feed_assistant(self, message: Any) -> None:
        if self._saw_partial:
            # This message's text already streamed as partial deltas.
            self._saw_partial = False
            return
        if not isinstance(message, dict):
            return
        blocks = message.get("content")
        if not isinstance(blocks, list):
            return
        text = "".join(
            str(block.get("text") or "")
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "text"
        )
        if text:
            self._emit(text)

    def _emit(self, text: str) -> None:
        try:
            self._on_delta(text)
        except Exception:  # noqa: BLE001 — a consumer bug must not kill generation
            _LOGGER.exception("on_delta consumer raised; delta dropped.")


def stream_cli_response(
    *,
    adapter_name: str,
    command_name: str,
    args: Sequence[str],
    stream_args: Sequence[str],
    default_model: str,
    timeout_seconds: float,
    process_factory: Callable[[list[str], str], Any],
    request: BrainRequest,
    on_delta: Callable[[str], None],
    generation_registry: Any | None = None,
) -> BrainResponse:
    command = [command_name, *list(args), *list(stream_args)]
    _reject_unsafe_args(command)
    prompt = format_cli_prompt(request)
    try:
        proc = process_factory(command, prompt)
    except FileNotFoundError as exc:
        raise BrainAdapterError(f"{adapter_name} executable not found: {command_name}") from exc
    except OSError as exc:
        raise BrainAdapterError(
            f"{adapter_name} failed to run: {redact_secrets(str(exc))}"
        ) from exc

    timed_out = threading.Event()

    def _timeout_kill() -> None:
        timed_out.set()
        _signal_process(proc, force=True)

    watchdog = threading.Timer(timeout_seconds, _timeout_kill)
    watchdog.daemon = True
    watchdog.start()

    cancelled = threading.Event()

    def _cancel() -> None:
        # Barge-in leg 1 (§7): cancel = terminate this subprocess; pending
        # deltas are discarded because they were never truth. The flag lets
        # the wait below tell "we killed it" apart from a real crash.
        cancelled.set()
        _signal_process(proc, force=False)

    registered = bool(generation_registry is not None and request.turn_id)
    if registered:
        generation_registry.register(request.turn_id, _cancel)

    parser = _StreamJsonParser(on_delta)
    stderr_lines: list[str] = []
    stderr_thread = _drain_stderr(proc, stderr_lines)
    # Feed stdin on its own thread, concurrent with the stdout drain below
    # (FIX-07 HIGH): a large prompt would otherwise fill the pipe and deadlock,
    # and the watchdog (already armed) can now kill a stuck write instead of
    # hanging forever.
    stdin_thread = _write_stdin(proc, prompt)
    try:
        for line in proc.stdout:
            parser.feed_line(line)
        returncode = proc.wait()
    finally:
        watchdog.cancel()
        if registered:
            generation_registry.unregister(request.turn_id)
        if stdin_thread is not None:
            stdin_thread.join(timeout=2)
        if stderr_thread is not None:
            stderr_thread.join(timeout=2)

    if timed_out.is_set():
        raise BrainAdapterError(f"{adapter_name} timed out after {timeout_seconds:g}s")
    if returncode != 0 and cancelled.is_set():
        # We killed it (barge-in leg 1), so a nonzero exit here is the cancel
        # landing, not a failure. A rc==0 clean finish that raced a late cancel
        # is honoured normally below — a completed answer is never discarded.
        raise BrainGenerationCancelled(f"{adapter_name} generation cancelled (barge-in)")
    if returncode != 0:
        stderr = _stderr_preview("".join(stderr_lines))
        raise BrainAdapterError(
            f"{adapter_name} exited with code {returncode}: {stderr}"
        )
    if parser.result_is_error:
        raise BrainAdapterError(
            f"{adapter_name} stream reported an error result: "
            f"{redact_secrets(str(parser.result_text or ''))[:200]}"
        )
    if parser.result_text is None:
        raise BrainAdapterError(
            f"{adapter_name} stream ended without a result event; "
            "deltas carry no authority, so there is no canonical answer."
        )

    parsed = parse_tool_call_blocks(parser.result_text)
    raw_metadata: dict[str, Any] = {
        "adapter": adapter_name,
        "command_name": command_name,
        "stateless": True,
        "streamed": True,
        "parsed_tool_call_count": len(parsed.tool_calls),
    }
    if parsed.parse_errors:
        raw_metadata["tool_call_parse_errors"] = list(parsed.parse_errors)

    return BrainResponse(
        text=parsed.text,
        tool_calls=parsed.tool_calls,
        model=default_model,
        usage=_usage_from_stream(parser.result_usage),
        raw_metadata=raw_metadata,
    )


def _drain_stderr(proc: Any, sink: list[str]) -> threading.Thread | None:
    stderr = getattr(proc, "stderr", None)
    if stderr is None:
        return None

    def drain() -> None:
        try:
            sink.append(stderr.read() or "")
        except Exception:  # noqa: BLE001
            pass

    thread = threading.Thread(target=drain, name="jarvis-cli-stderr", daemon=True)
    thread.start()
    return thread


def _write_stdin(proc: Any, prompt: str) -> threading.Thread | None:
    """Write the prompt to the child's stdin on a background thread (FIX-07
    HIGH), so a full pipe never blocks the stdout drain. A broken/closed pipe
    (the child already exited or was killed) is swallowed — the read loop and
    the watchdog own the outcome."""

    stdin = getattr(proc, "stdin", None)
    if stdin is None:
        return None

    def write() -> None:
        try:
            stdin.write(prompt)
            stdin.close()
        except (BrokenPipeError, OSError, ValueError):
            pass

    thread = threading.Thread(target=write, name="jarvis-cli-stdin", daemon=True)
    thread.start()
    return thread


def _usage_from_stream(usage: dict[str, Any]) -> BrainUsage:
    def _int_or_none(key: str) -> int | None:
        value = usage.get(key)
        return value if isinstance(value, int) else None

    input_tokens = _int_or_none("input_tokens")
    output_tokens = _int_or_none("output_tokens")
    total = None
    if input_tokens is not None and output_tokens is not None:
        total = input_tokens + output_tokens
    return BrainUsage(
        input_tokens=input_tokens, output_tokens=output_tokens, total_tokens=total
    )


def generate_cli_response(
    *,
    adapter_name: str,
    command_name: str,
    args: Sequence[str],
    default_model: str,
    timeout_seconds: float,
    runner: CliRunner,
    request: BrainRequest,
) -> BrainResponse:
    command = [command_name, *list(args)]
    _reject_unsafe_args(command)
    prompt = format_cli_prompt(request)
    try:
        result = runner(command, prompt, timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        raise BrainAdapterError(
            f"{adapter_name} timed out after {timeout_seconds:g}s"
        ) from exc
    except FileNotFoundError as exc:
        raise BrainAdapterError(f"{adapter_name} executable not found: {command_name}") from exc
    except OSError as exc:
        raise BrainAdapterError(
            f"{adapter_name} failed to run: {redact_secrets(str(exc))}"
        ) from exc

    if result.returncode != 0:
        stderr = _stderr_preview(result.stderr)
        raise BrainAdapterError(
            f"{adapter_name} exited with code {result.returncode}: {stderr}"
        )

    stdout = (result.stdout or "").strip()
    if not stdout:
        raise BrainAdapterError(f"{adapter_name} returned empty stdout")

    parsed = parse_tool_call_blocks(stdout)
    raw_metadata: dict[str, Any] = {
        "adapter": adapter_name,
        "command_name": command_name,
        "stateless": True,
        "parsed_tool_call_count": len(parsed.tool_calls),
    }
    if parsed.parse_errors:
        raw_metadata["tool_call_parse_errors"] = list(parsed.parse_errors)

    return BrainResponse(
        text=parsed.text,
        tool_calls=parsed.tool_calls,
        model=default_model,
        raw_metadata=raw_metadata,
    )


class ClaudeCliAdapter:
    name = "claude_cli"

    def __init__(
        self,
        *,
        command: str = "claude",
        args: Sequence[str] | None = None,
        model: str = "",
        timeout_seconds: float = 120,
        runner: CliRunner | None = None,
        stream_args: Sequence[str] | None = None,
        process_factory: Callable[[list[str], str], Any] | None = None,
        generation_registry: Any | None = None,
    ) -> None:
        self.command = _required_text(command, "command")
        self.args = list(args) if args is not None else ["-p"]
        self.default_model = model.strip() or "claude-cli"
        self.timeout_seconds = float(timeout_seconds)
        self._runner = runner or default_subprocess_runner
        self.stream_args = (
            list(stream_args) if stream_args else list(DEFAULT_STREAM_ARGS)
        )
        self._process_factory = process_factory or default_stream_process_factory
        self._generation_registry = generation_registry

    def available_models(self) -> list[str]:
        return [self.default_model]

    def generate(
        self,
        request: BrainRequest,
        *,
        on_delta: Callable[[str], None] | None = None,
    ) -> BrainResponse:
        if on_delta is None:
            return generate_cli_response(
                adapter_name=self.name,
                command_name=self.command,
                args=self.args,
                default_model=self.default_model,
                timeout_seconds=self.timeout_seconds,
                runner=self._runner,
                request=request,
            )
        return stream_cli_response(
            adapter_name=self.name,
            command_name=self.command,
            args=self.args,
            stream_args=self.stream_args,
            default_model=self.default_model,
            timeout_seconds=self.timeout_seconds,
            process_factory=self._process_factory,
            request=request,
            on_delta=on_delta,
            generation_registry=self._generation_registry,
        )


def _format_messages(messages: list[Any]) -> list[str]:
    if not messages:
        return ["- none"]
    lines: list[str] = []
    for message in messages:
        role = _clean_text(str(getattr(message, "role", "message"))) or "message"
        name = getattr(message, "name", None)
        label = role if not name else f"{role}/{_clean_text(str(name))}"
        content = _clean_text(str(getattr(message, "content", "")))
        if content:
            lines.append(f"- {label}: {content}")
    return lines or ["- none"]


def _format_memory_blocks(request: BrainRequest) -> list[str]:
    if not request.memory_blocks:
        return ["- none"]
    lines: list[str] = []
    for block in request.memory_blocks:
        title = _clean_text(block.title)
        kind = _clean_text(block.kind)
        body = _clean_text(block.body)
        lines.append(f"- {title} [{kind}, priority {block.priority}]: {body}")
    return lines


def _format_tools(request: BrainRequest) -> list[str]:
    if not request.available_tools:
        return ["- none"]
    lines: list[str] = []
    for tool in request.available_tools:
        name = _clean_text(tool.name)
        risk = _clean_text(tool.risk)
        description = _clean_text(tool.description)
        lines.append(f"- {name} [{risk}]: {description} (unavailable; pending approval)")
    return lines


def _stderr_preview(stderr: str | None) -> str:
    redacted = redact_secrets((stderr or "").strip())
    if not redacted:
        return "no stderr"
    if len(redacted) <= DEFAULT_STDERR_PREVIEW_CHARS:
        return redacted
    return f"{redacted[:DEFAULT_STDERR_PREVIEW_CHARS]}..."


def _clean_text(value: str) -> str:
    return " ".join(value.strip().split())


def _required_text(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BrainAdapterError(f"{label} must be a non-empty string")
    return value.strip()


# Allowlist of CLI flags the adapter legitimately uses (FIX-07). A denylist on
# one token missed equivalent spellings (the permission-skip flag with a value
# attached, an --allow-… alias, and so on); an allowlist fails closed on
# anything unexpected — including permission-bypass flags — while non-flag
# tokens (the binary, values like 'stream-json', a model name) are never
# treated as flags.
_ALLOWED_CLI_FLAGS = frozenset(
    {
        "-p",
        "--print",
        "--output-format",
        "--input-format",
        "--verbose",
        "--include-partial-messages",
        "--model",
    }
)


def _reject_unsafe_args(command: list[str]) -> None:
    for token in command:
        if not token.startswith("-"):
            continue  # the binary and flag values are not flags
        flag = token.split("=", 1)[0]
        if flag not in _ALLOWED_CLI_FLAGS:
            raise BrainAdapterError(f"unsafe CLI argument is not allowed: {flag}")
