"""Static safety classification for the Release 1 test baseline.

No function in this module imports test modules or starts a runtime.  Explicitly
marked hardware tests and unmarked direct live primitives are excluded from the
automatic baseline; the latter are additionally reported as an audit failure.
"""

from __future__ import annotations

import ast
import os
import re
import shlex
import site
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


Safety = Literal["isolated", "live-manual"]
_NODE_ID = re.compile(r"^tests/[A-Za-z0-9_./-]+\.py::[^\r\n]+$")
_AUDIO_COMMANDS = frozenset({"afplay", "aplay", "arecord", "ffmpeg", "ffplay", "parec", "play", "pw-record", "rec", "sox"})
_TMP_DAN = re.compile(r"/tmp/dan-[A-Za-z0-9_.-]*")
_LIVE_PORT = re.compile(r"(?:localhost|127\.0\.0\.1|0\.0\.0\.0):7788\b|\bport\s*=\s*7788\b")


@dataclass(frozen=True)
class SafetyClassification:
    node_id: str
    safety: Safety
    reasons: tuple[str, ...] = ()
    explicit_manual: bool = False


def _node_file(repo_root: Path, node_id: str) -> Path:
    if not _NODE_ID.fullmatch(node_id):
        raise ValueError(f"unsupported pytest node id: {node_id!r}")
    path = (repo_root / node_id.split("::", 1)[0]).resolve()
    tests = (repo_root / "tests").resolve()
    if tests not in path.parents or path.suffix != ".py":
        raise ValueError(f"node id outside tests/: {node_id!r}")
    return path


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _is_live_marker(node: ast.AST) -> bool:
    return any(isinstance(item, ast.Attribute) and item.attr == "live_manual" for item in ast.walk(node))


def _explicit_manual(tree: ast.Module, node_id: str) -> bool:
    for statement in tree.body:
        if isinstance(statement, (ast.Assign, ast.AnnAssign)):
            targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
            if any(isinstance(target, ast.Name) and target.id == "pytestmark" for target in targets):
                if statement.value is not None and _is_live_marker(statement.value):
                    return True
    parts = node_id.split("::")[1:]
    if not parts:
        return False
    parts[-1] = parts[-1].split("[", 1)[0]
    body = tree.body
    for part in parts:
        definition = next((item for item in body if isinstance(item, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == part), None)
        if definition is None:
            return False
        if any(_is_live_marker(decorator) for decorator in definition.decorator_list):
            return True
        body = definition.body
    return False


def _literal_command(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
        first = node.elts[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return first.value
    return None


def _definition(tree: ast.Module, node_id: str) -> ast.AST | None:
    parts = node_id.split("::")[1:]
    if not parts:
        return None
    parts[-1] = parts[-1].split("[", 1)[0]
    body = tree.body
    definition: ast.AST | None = None
    for part in parts:
        definition = next((item for item in body if isinstance(item, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == part), None)
        if definition is None:
            return None
        body = definition.body  # type: ignore[union-attr]
    return definition


def _is_fixture_decorator(decorator: ast.expr) -> bool:
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    return isinstance(target, ast.Attribute) and target.attr == "fixture"


def _fixture_definitions(tree: ast.Module) -> tuple[dict[str, ast.AST], set[str]]:
    fixtures: dict[str, ast.AST] = {}
    autouse: set[str] = set()
    for statement in tree.body:
        if not isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        decorator = next(
            (item for item in statement.decorator_list if _is_fixture_decorator(item)),
            None,
        )
        if decorator is None:
            continue
        fixtures[statement.name] = statement
        if isinstance(decorator, ast.Call) and any(
            keyword.arg == "autouse"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is True
            for keyword in decorator.keywords
        ):
            autouse.add(statement.name)
    return fixtures, autouse


def _fixture_arguments(definition: ast.AST) -> set[str]:
    if not isinstance(definition, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return set()
    return {
        argument.arg
        for argument in (*definition.args.posonlyargs, *definition.args.args, *definition.args.kwonlyargs)
        if argument.arg not in {"self", "cls"}
    }


def _live_reasons(tree: ast.Module, node_id: str) -> tuple[str, ...]:
    definition = _definition(tree, node_id)
    scope: list[ast.AST] = [item for item in tree.body if not isinstance(item, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))]
    if isinstance(definition, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        # Decorators carry parametrized fixture source in our contract tests;
        # only executable test/module bodies are subject to the primitive scan.
        scope.extend(definition.body)
        fixtures, pending = _fixture_definitions(tree)
        pending.update(_fixture_arguments(definition))
        scanned: set[str] = set()
        while pending:
            name = pending.pop()
            fixture = fixtures.get(name)
            if fixture is None or name in scanned:
                continue
            scanned.add(name)
            scope.extend(fixture.body)  # type: ignore[union-attr]
            pending.update(_fixture_arguments(fixture))
    reasons: set[str] = set()
    for root in scope:
      for node in ast.walk(root):
        if isinstance(node, ast.Call):
            name = _call_name(node)
            if name == "home":
                reasons.add("real home database path")
            if name in {"call", "check_call", "check_output", "Popen", "run", "system", "execv", "execve", "create_subprocess_exec", "create_subprocess_shell"}:
                argument = node.args[0] if node.args else next((kw.value for kw in node.keywords if kw.arg == "args"), None)
                if argument is not None:
                    command = _literal_command(argument)
                    if command:
                        try:
                            executable = Path(shlex.split(command)[0]).name
                        except (IndexError, ValueError):
                            executable = ""
                        if executable in _AUDIO_COMMANDS:
                            reasons.add(f"unmocked audio or microphone binary: {executable}")
                        if executable == "launchctl":
                            reasons.add("unmocked launchctl invocation")
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and "\n" not in node.value:
            if _TMP_DAN.search(node.value):
                reasons.add("legacy /tmp/dan-* runtime path")
            if _LIVE_PORT.search(node.value):
                reasons.add("live DAN voice port 7788")
    return tuple(sorted(reasons))


def collect_node_ids(repo_root: Path, *, env: Mapping[str, str] | None = None) -> tuple[str, ...]:
    completed = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q"], cwd=repo_root, env=dict(env) if env else None, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "pytest collection failed")
    nodes = tuple(line.strip() for line in completed.stdout.splitlines() if _NODE_ID.fullmatch(line.strip()))
    if not nodes:
        raise RuntimeError("pytest collection returned no node IDs")
    return nodes


def classify_node_ids(repo_root: Path, node_ids: Sequence[str]) -> dict[str, SafetyClassification]:
    cached: dict[Path, ast.Module] = {}
    result: dict[str, SafetyClassification] = {}
    for node_id in node_ids:
        path = _node_file(repo_root, node_id)
        if path not in cached:
            cached[path] = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        tree = cached[path]
        reasons = _live_reasons(tree, node_id)
        marked = _explicit_manual(tree, node_id)
        safety: Safety = "live-manual" if marked or reasons else "isolated"
        result[node_id] = SafetyClassification(node_id, safety, reasons, marked)
    return result


def scan_node_ids(repo_root: Path, node_ids: Sequence[str]) -> list[str]:
    """List unmarked direct live primitives; marker-only tests are intentional manual work."""
    classified = classify_node_ids(repo_root, node_ids)
    return sorted(f"{node}: {reason}" for node, row in classified.items() if row.reasons and not row.explicit_manual for reason in row.reasons)


def scan_automatic_tests(repo_root: Path) -> list[str]:
    return scan_node_ids(repo_root, collect_node_ids(repo_root))


def isolated_node_ids(classified: Mapping[str, SafetyClassification]) -> tuple[str, ...]:
    return tuple(node for node, row in classified.items() if row.safety == "isolated")


def live_manual_node_ids(classified: Mapping[str, SafetyClassification]) -> tuple[str, ...]:
    return tuple(node for node, row in classified.items() if row.safety == "live-manual")


def test_environment(home: Path, runtime: Path, database: Path) -> dict[str, str]:
    environment = dict(os.environ)
    environment.update({"HOME": str(home), "XDG_CACHE_HOME": str(home / ".cache"), "XDG_CONFIG_HOME": str(home / ".config"), "XDG_DATA_HOME": str(home / ".local" / "share"), "XDG_RUNTIME_DIR": str(runtime), "TMPDIR": str(runtime), "DAN_RUNTIME_DIR": str(runtime), "DAN_DB_PATH": str(database), "JARVIS_DB_PATH": str(database), "DAN_TEST_MODE": "1", "DAN_DISABLE_AUDIO": "1", "DAN_DISABLE_MIC": "1"})
    user_site = Path(site.getusersitepackages())
    if user_site.is_dir():
        environment["PYTHONPATH"] = os.pathsep.join(filter(None, (str(user_site), environment.get("PYTHONPATH", ""))))
    return environment
