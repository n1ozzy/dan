"""Live model discovery for the Claude Code CLI.

The panel's model picker must never carry a hand-maintained model list — the
truth of which ``--model`` ids this account/CLI accepts lives inside Claude Code
itself and shifts as new models ship. So we ask Claude Code, live: one cheap
``-p`` turn on the smallest model, prompting for a bare JSON array of the model
ids it accepts. The result is filtered (junk plugin/setup ids and legacy models
dropped), deduped, cached on disk (TTL), and — on any failure — falls back to
the last good cache, then to a tiny hard-coded safety net.

The safety net is a NET, not a source of truth: it only exists so the picker
still renders when Claude Code is unreachable. As soon as a live probe or a
fresh-enough cache is available, that wins.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger(__name__)

# One cheap turn on the cheapest model. We keep the prompt terse and explicit so
# the model answers with data, not prose — we still parse defensively.
_DISCOVERY_MODEL = "claude-haiku-4-5"
_DISCOVERY_PROMPT = (
    "Output ONLY a compact JSON array of the exact Claude model id strings that "
    "THIS Claude Code CLI currently accepts as the --model argument (e.g. "
    '["claude-opus-4-8","claude-sonnet-5","claude-haiku-4-5"]). '
    "No prose, no markdown, no code fences — just the JSON array on one line."
)
_DISCOVERY_TIMEOUT = 30.0

# A valid model id: claude- followed by an alnum then id characters. Kept strict
# so plugin/skill ids (which use words like "setup"/"plugins") are easy to drop.
_MODEL_ID_RE = re.compile(r"^claude-[a-z0-9][a-z0-9.\-]+$")

# Substrings that mark a ~/.claude.json entry as NOT a model (setup flows,
# plugin/skill namespaces, guest passes, loud-thinking/mythos skills, code-*
# helpers). Any id containing one of these is dropped.
_JUNK_SUBSTRINGS = (
    "setup",
    "plugins",
    "guest",
    "md-management",
    "loud",
    "mythos",
    "code-",
)

# Legacy families the account may still list but which we never surface.
_LEGACY_PREFIXES = ("claude-3-",)

# Last-resort net if Claude Code is unreachable AND no cache exists. NOT a source
# of truth — real ids come live from the CLI.
_FALLBACK_MODELS: tuple[str, ...] = (
    "claude-opus-4-8",
    "claude-sonnet-5",
    "claude-haiku-4-5-20251001",
    "claude-fable-5",
    "claude-opus-4-7",
)

# Empirically verified ids the discovery prompt routinely OMITS even though the
# CLI accepts them (checked live: `claude -p --model claude-sonnet-4-6` answers).
# Appended to every returned list so the panel picker always offers them —
# discovery order first, pinned after, deduped.
_PINNED_MODELS: tuple[str, ...] = ("claude-sonnet-4-6",)


def _with_pinned(models: list[str]) -> list[str]:
    merged = list(models)
    merged.extend(model for model in _PINNED_MODELS if model not in merged)
    return merged

_DEFAULT_TTL = 3600.0

# Runner contract: (argv, timeout) -> stdout. Injected in tests so the real
# `claude` binary is never spawned there.
CliRunner = Callable[[list[str], float], str]


def _dan_home() -> Path:
    """Resolve the ~/.dan state directory without needing a DANConfig.

    dan.paths only resolves runtime paths from a config object; model
    discovery runs in contexts (capability graph, tests) that have no config in
    hand, so we resolve the conventional home directly.
    """

    return Path.home() / ".dan"


def _default_cache_path() -> Path:
    return _dan_home() / "model_cache.json"


def _default_runner(argv: list[str], timeout: float) -> str:
    completed = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"claude model discovery exited {completed.returncode}: "
            f"{(completed.stderr or '').strip()[:200]}"
        )
    return completed.stdout or ""


def _extract_json_array(stdout: str) -> list[Any]:
    """Pull the first bracketed JSON array out of arbitrary CLI stdout."""

    start = stdout.find("[")
    if start == -1:
        raise ValueError("no JSON array found in stdout")
    end = stdout.find("]", start)
    if end == -1:
        raise ValueError("unterminated JSON array in stdout")
    parsed = json.loads(stdout[start : end + 1])
    if not isinstance(parsed, list):
        raise ValueError("parsed JSON is not an array")
    return parsed


def _is_junk(model_id: str) -> bool:
    if any(token in model_id for token in _JUNK_SUBSTRINGS):
        return True
    if any(model_id.startswith(prefix) for prefix in _LEGACY_PREFIXES):
        return True
    return False


def filter_model_ids(candidates: list[Any]) -> list[str]:
    """Keep only real, current Claude model ids; dedup preserving order."""

    seen: dict[str, None] = {}
    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        model_id = candidate.strip()
        if not _MODEL_ID_RE.match(model_id):
            continue
        if _is_junk(model_id):
            continue
        seen.setdefault(model_id, None)
    return list(seen)


def _read_cache(cache_path: Path) -> tuple[float, list[str]] | None:
    try:
        with open(cache_path, "rb") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    ts = data.get("ts")
    models = data.get("models")
    if not isinstance(ts, (int, float)) or not isinstance(models, list):
        return None
    clean = [str(item) for item in models if isinstance(item, str) and item]
    if not clean:
        return None
    return float(ts), clean


def _write_cache(cache_path: Path, models: list[str], *, now: float) -> None:
    try:
        cache_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        payload = json.dumps({"ts": now, "models": models})
        # Owner-only: the file lives next to the DB and logs.
        fd = os.open(cache_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, payload.encode("utf-8"))
        finally:
            os.close(fd)
        os.chmod(cache_path, 0o600)
    except OSError:
        _LOGGER.debug("could not persist model cache to %s", cache_path, exc_info=True)


# In-process memo: a settings request touches available_models() more than once
# (adapter loop + provider-capability build), and the panel polls that endpoint
# on a heartbeat. The memo returns the last resolved list without re-reading the
# disk cache or re-filtering, keyed by command. Only used on the production path
# (default cache_path/now) so injected-clock tests never share global state.
_MEMO_LOCK = threading.Lock()
_MEMO: dict[str, tuple[float, list[str]]] = {}

# Single-flight guard so concurrent stale reads spawn at most one background
# probe per (command, cache file) instead of a thundering herd of CLI spawns.
_INFLIGHT_LOCK = threading.Lock()
_INFLIGHT: set[tuple[str, str]] = set()


def _memo_get(key: str, clock: float, ttl: float) -> list[str] | None:
    if ttl <= 0:
        return None
    with _MEMO_LOCK:
        entry = _MEMO.get(key)
    if entry is None:
        return None
    ts, models = entry
    if (clock - ts) < ttl:
        return list(models)
    return None


def _memo_put(key: str, models: list[str], ts: float) -> None:
    with _MEMO_LOCK:
        _MEMO[key] = (ts, list(models))


def _probe_live(
    command: str,
    runner: CliRunner | None,
    resolved_cache: Path,
    cached: tuple[float, list[str]] | None,
    clock: float,
) -> list[str]:
    """One live ``claude -p`` discovery turn; on ANY failure fall back to the
    last-good cache, then the hard-coded net. Writes the cache on success."""

    run = runner or _default_runner
    argv = [command, "-p", "--model", _DISCOVERY_MODEL, _DISCOVERY_PROMPT]
    try:
        stdout = run(argv, _DISCOVERY_TIMEOUT)
        models = filter_model_ids(_extract_json_array(stdout))
        if not models:
            raise ValueError("no usable model ids after filtering")
    except Exception as exc:  # noqa: BLE001 - any failure falls back to cache/net
        if cached is not None:
            _LOGGER.warning(
                "Claude model discovery failed (%s); using last-good cache.", exc
            )
            return list(cached[1])
        _LOGGER.warning(
            "Claude model discovery failed (%s) and no cache; using safety net.",
            exc,
        )
        return list(_FALLBACK_MODELS)

    _write_cache(resolved_cache, models, now=clock)
    return models


def _spawn_background_refresh(
    command: str,
    runner: CliRunner | None,
    resolved_cache: Path,
    memo_key: str,
    use_memo: bool,
) -> None:
    """Refresh the model list off the request path, single-flight per target."""

    key = (command, str(resolved_cache))
    with _INFLIGHT_LOCK:
        if key in _INFLIGHT:
            return
        _INFLIGHT.add(key)

    def _work() -> None:
        try:
            now = time.time()
            cached = _read_cache(resolved_cache)
            models = _probe_live(command, runner, resolved_cache, cached, now)
            if use_memo:
                _memo_put(memo_key, models, now)
        except Exception:  # noqa: BLE001 - background task, never raise
            _LOGGER.debug("background model refresh failed", exc_info=True)
        finally:
            with _INFLIGHT_LOCK:
                _INFLIGHT.discard(key)

    threading.Thread(target=_work, name="dan-model-refresh", daemon=True).start()


def resolve_available_models(
    command: str = "claude",
    *,
    runner: CliRunner | None = None,
    cache_path: Path | str | None = None,
    ttl: float = _DEFAULT_TTL,
    now: float | None = None,
    block: bool = True,
) -> list[str]:
    """Return the current Claude model ids this CLI accepts as ``--model``.

    Live source: one cheap ``claude -p --model <cheap> <prompt>`` turn. Result is
    filtered, deduped, and cached to ``~/.dan/model_cache.json`` (TTL
    ``ttl``), plus an in-process memo. Within the TTL the cached/memoed list is
    returned without spawning the CLI.

    ``block`` controls what happens on a stale/cold cache:
    - ``True`` (default; tests, warmup): probe the CLI synchronously and return
      the fresh list — may take up to the discovery timeout.
    - ``False`` (the request path, e.g. ``GET /runtime/settings``): NEVER wait on
      the probe. Serve the last-known list immediately (stale cache, else the
      safety net) and refresh in the background, single-flight. Stale-while-
      revalidate — the next poll gets the fresh list.

    ``runner`` is injectable for tests — the real ``claude`` binary must never be
    invoked from tests. ``now``/``ttl`` are injectable for deterministic cache
    tests (fake clock).
    """

    resolved_cache = Path(cache_path) if cache_path is not None else _default_cache_path()
    clock = time.time() if now is None else now
    # Memo only on the production path — an injected clock/cache_path means a test
    # that must not see (or leak into) cross-call global state.
    use_memo = cache_path is None and now is None
    memo_key = command

    if use_memo:
        hit = _memo_get(memo_key, clock, ttl)
        if hit is not None:
            return _with_pinned(hit)

    cached = _read_cache(resolved_cache)
    if cached is not None and ttl > 0 and (clock - cached[0]) < ttl:
        if use_memo:
            _memo_put(memo_key, cached[1], cached[0])
        return _with_pinned(list(cached[1]))

    if not block:
        _spawn_background_refresh(command, runner, resolved_cache, memo_key, use_memo)
        if cached is not None:
            return _with_pinned(list(cached[1]))
        return _with_pinned(list(_FALLBACK_MODELS))

    models = _probe_live(command, runner, resolved_cache, cached, clock)
    if use_memo:
        _memo_put(memo_key, models, clock)
    return _with_pinned(models)


__all__ = [
    "resolve_available_models",
    "filter_model_ids",
    "CliRunner",
]
