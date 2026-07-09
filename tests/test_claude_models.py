"""Tests for live Claude model discovery (jarvis/brain/claude_models.py).

The real `claude` binary is never spawned here — every path uses an injected
runner or a tmp cache file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis.brain.claude_models import (
    _FALLBACK_MODELS,
    filter_model_ids,
    resolve_available_models,
)


def _cache(tmp_path: Path) -> Path:
    return tmp_path / "model_cache.json"


class TestFilterModelIds:
    def test_keeps_real_claude_ids_and_dedups_preserving_order(self) -> None:
        result = filter_model_ids(
            [
                "claude-opus-4-8",
                "claude-sonnet-5",
                "claude-opus-4-8",  # duplicate
                "claude-haiku-4-5-20251001",
            ]
        )
        assert result == [
            "claude-opus-4-8",
            "claude-sonnet-5",
            "claude-haiku-4-5-20251001",
        ]

    def test_drops_plugin_setup_and_skill_junk(self) -> None:
        result = filter_model_ids(
            [
                "claude-opus-4-8",
                "claude-code-setup",
                "claude-plugins-official",
                "claude-code-guest-passes",
                "claude-md-management",
                "claude-loud-thinking",
                "claude-mythos",
                "claude-code-review",
            ]
        )
        assert result == ["claude-opus-4-8"]

    def test_drops_legacy_claude_3_family(self) -> None:
        result = filter_model_ids(
            ["claude-3-haiku", "claude-3-5-haiku", "claude-sonnet-5"]
        )
        assert result == ["claude-sonnet-5"]

    def test_drops_non_strings_and_malformed_ids(self) -> None:
        result = filter_model_ids(
            [123, None, "gpt-4", "Claude-Opus", "claude-", "claude-opus-4-8"]
        )
        assert result == ["claude-opus-4-8"]


class TestResolveLiveDiscovery:
    def test_parses_json_array_from_noisy_stdout(self, tmp_path: Path) -> None:
        def runner(argv: list[str], timeout: float) -> str:
            assert argv[0] == "claude"
            assert "--model" in argv
            return (
                "here you go:\n"
                '["claude-opus-4-8", "claude-sonnet-5", "claude-code-setup"]\n'
                "hope that helps"
            )

        result = resolve_available_models(runner=runner, cache_path=_cache(tmp_path))
        assert result == ["claude-opus-4-8", "claude-sonnet-5"]

    def test_writes_cache_after_success(self, tmp_path: Path) -> None:
        cache = _cache(tmp_path)

        def runner(argv: list[str], timeout: float) -> str:
            return '["claude-opus-4-8"]'

        resolve_available_models(runner=runner, cache_path=cache, now=1000.0)
        data = json.loads(cache.read_text())
        assert data["ts"] == 1000.0
        assert data["models"] == ["claude-opus-4-8"]


class TestCacheTtl:
    def test_fresh_cache_is_returned_without_calling_runner(self, tmp_path: Path) -> None:
        cache = _cache(tmp_path)
        cache.write_text(json.dumps({"ts": 100.0, "models": ["claude-cached-9"]}))

        def runner(argv: list[str], timeout: float) -> str:  # pragma: no cover
            raise AssertionError("runner must not be called within TTL")

        result = resolve_available_models(
            runner=runner, cache_path=cache, ttl=3600.0, now=200.0
        )
        assert result == ["claude-cached-9"]

    def test_expired_cache_triggers_fresh_discovery(self, tmp_path: Path) -> None:
        cache = _cache(tmp_path)
        cache.write_text(json.dumps({"ts": 100.0, "models": ["claude-old-1"]}))
        calls: list[list[str]] = []

        def runner(argv: list[str], timeout: float) -> str:
            calls.append(argv)
            return '["claude-fresh-2"]'

        result = resolve_available_models(
            runner=runner, cache_path=cache, ttl=3600.0, now=100_000.0
        )
        assert calls, "expired cache should trigger the runner"
        assert result == ["claude-fresh-2"]


class TestFallback:
    def test_runner_raises_falls_back_to_last_good_cache(self, tmp_path: Path) -> None:
        cache = _cache(tmp_path)
        cache.write_text(json.dumps({"ts": 100.0, "models": ["claude-lastgood-3"]}))

        def runner(argv: list[str], timeout: float) -> str:
            raise RuntimeError("claude exploded")

        # Cache is stale (now well past ttl) so discovery is attempted, fails,
        # and the last-good cache is returned.
        result = resolve_available_models(
            runner=runner, cache_path=cache, ttl=1.0, now=100_000.0
        )
        assert result == ["claude-lastgood-3"]

    def test_runner_raises_no_cache_falls_back_to_safety_net(self, tmp_path: Path) -> None:
        def runner(argv: list[str], timeout: float) -> str:
            raise RuntimeError("claude missing")

        result = resolve_available_models(runner=runner, cache_path=_cache(tmp_path))
        assert result == list(_FALLBACK_MODELS)

    def test_empty_output_no_cache_falls_back_to_safety_net(self, tmp_path: Path) -> None:
        def runner(argv: list[str], timeout: float) -> str:
            return "no array here"

        result = resolve_available_models(runner=runner, cache_path=_cache(tmp_path))
        assert result == list(_FALLBACK_MODELS)

    def test_all_junk_output_no_cache_falls_back_to_safety_net(self, tmp_path: Path) -> None:
        def runner(argv: list[str], timeout: float) -> str:
            return '["claude-code-setup", "claude-3-haiku"]'

        result = resolve_available_models(runner=runner, cache_path=_cache(tmp_path))
        assert result == list(_FALLBACK_MODELS)
