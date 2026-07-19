"""Static safety classification for the Release 1 test baseline."""

from __future__ import annotations

import ast
import hashlib
import os
import re
import shlex
import subprocess
import sys
import sysconfig
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from dan.audio.execution import AUDIO_EXECUTABLE_NAMES

Safety = Literal["isolated", "live-manual"]
_TEST_PATH = re.compile(r"^tests/[A-Za-z0-9_./-]+\.py$")
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
_MODULE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
_SANITIZED_PARAM = re.compile(r"^param-[0-9a-f]{16}$")
_TMP_DAN = re.compile(r"/tmp/dan-[A-Za-z0-9_.-]*")
_LIVE_PORT = re.compile(r"(?:localhost|127\.0\.0\.1|0\.0\.0\.0):7788\b|\bport\s*=\s*7788\b")


@dataclass(frozen=True)
class SafetyClassification:
    node_id: str
    safety: Safety
    reasons: tuple[str, ...] = ()
    explicit_manual: bool = False


@dataclass(frozen=True)
class SourceModule:
    path: Path
    tree: ast.Module


def _node_parts(node_id: str, *, sanitized: bool = False) -> tuple[str, tuple[str, ...]]:
    path, separator, tail = node_id.partition("::")
    if not separator or not _TEST_PATH.fullmatch(path):
        raise ValueError(f"unsupported pytest node id: {node_id!r}")
    parameter_index = tail.find("[")
    if parameter_index >= 0:
        prefix, parameter = tail[:parameter_index], tail[parameter_index:]
        if not parameter.endswith("]"):
            raise ValueError(f"unsupported pytest node id: {node_id!r}")
        parts = prefix.split("::")
        if not parts or not parts[-1]:
            raise ValueError(f"unsupported pytest node id: {node_id!r}")
        parts[-1] = f"{parts[-1]}{parameter}"
    else:
        parts = tail.split("::")
    if not parts or any(not part for part in parts):
        raise ValueError(f"unsupported pytest node id: {node_id!r}")
    names: list[str] = []
    for part in parts:
        if not part or "\n" in part or "\r" in part:
            raise ValueError(f"unsupported pytest node id: {node_id!r}")
        name, separator, parameter = part.partition("[")
        if not _NAME.fullmatch(name):
            raise ValueError(f"unsupported pytest node id: {node_id!r}")
        if separator:
            if not parameter.endswith("]"):
                raise ValueError(f"unsupported pytest node id: {node_id!r}")
            payload = parameter[:-1]
            if sanitized and not _SANITIZED_PARAM.fullmatch(payload):
                raise ValueError(f"unsanitized pytest node id: {node_id!r}")
            names.append(f"{name}[{payload}]")
        else:
            names.append(name)
    return path, tuple(names)


def sanitize_raw_node_id(node_id: str) -> str:
    """Sanitize a pytest-produced node ID without persisting parameter payloads."""
    path, parts = _node_parts(node_id)
    sanitized: list[str] = []
    for part in parts:
        name, separator, parameter = part.partition("[")
        if not separator:
            sanitized.append(name)
            continue
        payload = parameter[:-1]
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
        sanitized.append(f"{name}[param-{digest}]")
    return "::".join((path, *sanitized))


def validate_canonical_node_id(node_id: str) -> str:
    """Validate a node ID already persisted by the sanitizer."""
    _node_parts(node_id, sanitized=True)
    return node_id


def _node_file(repo_root: Path, node_id: str) -> Path:
    relative, _ = _node_parts(node_id)
    path = (repo_root / relative).resolve()
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
    return any(
        isinstance(item, ast.Attribute) and item.attr == "live_manual"
        for item in ast.walk(node)
    )


def _explicit_manual(tree: ast.Module, node_id: str) -> bool:
    for statement in tree.body:
        if isinstance(statement, (ast.Assign, ast.AnnAssign)):
            targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
            if any(
                isinstance(target, ast.Name) and target.id == "pytestmark"
                for target in targets
            ):
                if statement.value is not None and _is_live_marker(statement.value):
                    return True
    _, parts = _node_parts(node_id)
    body = tree.body
    for part in parts:
        name = part.partition("[")[0]
        definition = next(
            (
                item
                for item in body
                if isinstance(item, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                and item.name == name
            ),
            None,
        )
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
    _, parts = _node_parts(node_id)
    body = tree.body
    definition: ast.AST | None = None
    for part in parts:
        name = part.partition("[")[0]
        definition = next(
            (
                item
                for item in body
                if isinstance(item, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                and item.name == name
            ),
            None,
        )
        if definition is None:
            return None
        body = definition.body  # type: ignore[union-attr]
    return definition


def _is_fixture_decorator(decorator: ast.expr) -> bool:
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    return (
        isinstance(target, ast.Name)
        and target.id == "fixture"
        or isinstance(target, ast.Attribute)
        and target.attr == "fixture"
    )


def _fixture_definitions(sources: Sequence[SourceModule]) -> tuple[dict[str, ast.AST], set[str]]:
    fixtures: dict[str, ast.AST] = {}
    autouse: set[str] = set()
    for source in sources:
        for statement in source.tree.body:
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
        for argument in (
            *definition.args.posonlyargs,
            *definition.args.args,
            *definition.args.kwonlyargs,
        )
        if argument.arg not in {"self", "cls"}
    }


def _pytest_plugins(tree: ast.Module) -> tuple[tuple[str, ...], bool]:
    names: list[str] = []
    unresolved = False
    for statement in tree.body:
        if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
            continue
        targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
        if not any(
            isinstance(target, ast.Name) and target.id == "pytest_plugins"
            for target in targets
        ):
            continue
        try:
            value = ast.literal_eval(statement.value)
        except ValueError:
            unresolved = True
            continue
        candidates = (value,) if isinstance(value, str) else value
        if not isinstance(candidates, (tuple, list)) or not all(
            isinstance(name, str) for name in candidates
        ):
            unresolved = True
            continue
        names.extend(candidates)
    return tuple(names), unresolved


def _local_plugin_path(repo_root: Path, plugin_name: str) -> Path | None:
    if not _MODULE_NAME.fullmatch(plugin_name):
        return None
    candidate = (repo_root / Path(*plugin_name.split("."))).with_suffix(".py").resolve()
    if repo_root not in candidate.parents or not candidate.is_file():
        return None
    return candidate


def _load_module(path: Path, cache: dict[Path, SourceModule]) -> SourceModule:
    resolved = path.resolve()
    if resolved not in cache:
        cache[resolved] = SourceModule(
            resolved,
            ast.parse(resolved.read_text(encoding="utf-8"), filename=str(resolved)),
        )
    return cache[resolved]


def _module_name(repo_root: Path, path: Path) -> str | None:
    try:
        relative = path.resolve().relative_to(repo_root.resolve())
    except ValueError:
        return None
    if relative.name == "__init__.py":
        parts = relative.parent.parts
    elif relative.suffix == ".py":
        parts = (*relative.parent.parts, relative.stem)
    else:
        return None
    name = ".".join(parts)
    return name if _MODULE_NAME.fullmatch(name) else None


def _absolute_import_name(
    source: SourceModule,
    node: ast.ImportFrom,
    repo_root: Path,
) -> str | None:
    if node.level == 0:
        return node.module
    current = _module_name(repo_root, source.path)
    if current is None:
        return None
    package = current.split(".")
    if source.path.name != "__init__.py":
        package.pop()
    parents = node.level - 1
    if parents > len(package):
        return None
    if parents:
        package = package[:-parents]
    if node.module:
        package.extend(node.module.split("."))
    return ".".join(package)


def _local_test_module_path(
    repo_root: Path,
    module_name: str,
) -> tuple[Path | None, str | None]:
    if module_name != "tests" and not module_name.startswith("tests."):
        return None, None
    if not _MODULE_NAME.fullmatch(module_name):
        return None, "unresolved repository-local import dependency"
    root = repo_root.resolve()
    tests_root = (root / "tests").resolve()
    stem = root.joinpath(*module_name.split("."))
    candidates = (stem.with_suffix(".py"), stem / "__init__.py")
    matches: list[Path] = []
    for candidate in candidates:
        resolved = candidate.resolve()
        if tests_root != resolved and tests_root not in resolved.parents:
            continue
        if resolved.is_file():
            matches.append(resolved)
    if len(matches) > 1:
        return None, "ambiguous repository-local import dependency"
    if not matches:
        return None, "unresolved repository-local import dependency"
    return matches[0], None


def _named_definition(tree: ast.Module, name: str) -> ast.AST | None:
    return next(
        (
            node
            for node in tree.body
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        ),
        None,
    )


def _local_dependency_scope(
    repo_root: Path,
    roots: Sequence[tuple[SourceModule, Sequence[ast.AST]]],
    cache: dict[Path, SourceModule],
) -> tuple[tuple[ast.AST, ...], tuple[str, ...]]:
    """Resolve referenced test helpers without importing or executing them."""

    collected: list[ast.AST] = []
    reasons: set[str] = set()
    scanned_definitions: set[tuple[Path, str]] = set()
    scanned_module_scopes = {source.path for source, _ in roots}

    def resolve(module_name: str) -> tuple[SourceModule | None, str | None]:
        path, reason = _local_test_module_path(repo_root, module_name)
        if reason or path is None:
            return None, reason
        try:
            return _load_module(path, cache), None
        except (OSError, SyntaxError, UnicodeError):
            return None, "unresolved repository-local import dependency"

    def load(module_name: str) -> SourceModule | None:
        source, reason = resolve(module_name)
        if reason is not None:
            reasons.add(reason)
        return source

    def is_namespace(module_name: str) -> bool:
        if module_name != "tests" and not module_name.startswith("tests."):
            return False
        if not _MODULE_NAME.fullmatch(module_name):
            return False
        tests_root = (repo_root.resolve() / "tests").resolve()
        candidate = repo_root.resolve().joinpath(*module_name.split(".")).resolve()
        if tests_root != candidate and tests_root not in candidate.parents:
            return False
        return candidate.is_dir()

    def load_namespace_members(
        module_name: str,
        aliases: Sequence[ast.alias],
        module_imports: dict[str, SourceModule],
    ) -> bool:
        if not is_namespace(module_name):
            return False
        resolved_any = False
        for alias in aliases:
            if alias.name == "*":
                reasons.add("unresolved repository-local import dependency")
                continue
            imported, reason = resolve(f"{module_name}.{alias.name}")
            if imported is None:
                if reason is not None:
                    reasons.add(reason)
                continue
            resolved_any = True
            scan_module_scope(imported)
            module_imports[alias.asname or alias.name] = imported
        return resolved_any

    def scan_module_scope(source: SourceModule) -> None:
        if source.path in scanned_module_scopes:
            return
        scanned_module_scopes.add(source.path)
        nodes = _module_scope(source.tree)
        collected.extend(nodes)
        scan_nodes(source, nodes)

    def scan_definition(source: SourceModule, name: str) -> None:
        key = (source.path, name)
        if key in scanned_definitions:
            return
        definition = _named_definition(source.tree, name)
        if definition is None:
            return
        scanned_definitions.add(key)
        body = definition.body  # type: ignore[union-attr]
        collected.extend(body)
        scan_nodes(source, body)

    def scan_nodes(source: SourceModule, nodes: Sequence[ast.AST]) -> None:
        symbol_imports: dict[str, tuple[SourceModule, str]] = {}
        module_imports: dict[str, SourceModule] = {}
        import_nodes = [
            node
            for statement in (
                *(
                    item
                    for item in source.tree.body
                    if isinstance(item, (ast.Import, ast.ImportFrom))
                ),
                *nodes,
            )
            for node in ast.walk(statement)
            if isinstance(node, (ast.Import, ast.ImportFrom))
        ]
        for import_node in import_nodes:
            if isinstance(import_node, ast.Import):
                for alias in import_node.names:
                    imported = load(alias.name)
                    if imported is None:
                        continue
                    scan_module_scope(imported)
                    binding = alias.asname or alias.name.split(".", 1)[0]
                    module_imports[binding] = imported
                continue
            module_name = _absolute_import_name(source, import_node, repo_root)
            if module_name is None:
                continue
            imported, resolution_reason = resolve(module_name)
            if imported is None:
                if resolution_reason is None:
                    continue
                if (
                    resolution_reason
                    == "unresolved repository-local import dependency"
                    and load_namespace_members(
                        module_name,
                        import_node.names,
                        module_imports,
                    )
                ):
                    continue
                reasons.add(resolution_reason)
                continue
            scan_module_scope(imported)
            for alias in import_node.names:
                if alias.name == "*":
                    for statement in imported.tree.body:
                        if isinstance(
                            statement,
                            (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef),
                        ):
                            scan_definition(imported, statement.name)
                    continue
                symbol_imports[alias.asname or alias.name] = (imported, alias.name)

        for root in nodes:
            for node in ast.walk(root):
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                    imported_symbol = symbol_imports.get(node.id)
                    if imported_symbol is not None:
                        scan_definition(*imported_symbol)
                if not isinstance(node, ast.Call):
                    continue
                if isinstance(node.func, ast.Name):
                    scan_definition(source, node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    owner = node.func.value
                    while isinstance(owner, ast.Attribute):
                        owner = owner.value
                    if isinstance(owner, ast.Name):
                        imported_module = module_imports.get(owner.id)
                        if imported_module is not None:
                            scan_definition(imported_module, node.func.attr)

    for source, nodes in roots:
        scan_nodes(source, nodes)
    return tuple(collected), tuple(sorted(reasons))


def _source_modules(
    repo_root: Path, test_path: Path, cache: dict[Path, SourceModule]
) -> tuple[tuple[SourceModule, ...], tuple[str, ...]]:
    root = repo_root.resolve()
    ancestors: list[SourceModule] = []
    root_conftest = root / "conftest.py"
    if root_conftest.is_file():
        ancestors.append(_load_module(root_conftest, cache))

    current = root
    for part in test_path.resolve().parent.relative_to(root).parts:
        current /= part
        conftest = current / "conftest.py"
        if conftest.is_file():
            ancestors.append(_load_module(conftest, cache))
    test_module = _load_module(test_path, cache)
    plugins: list[SourceModule] = []
    queue = [*ancestors, test_module]
    unresolved: set[str] = set()
    while queue:
        source = queue.pop(0)
        names, invalid = _pytest_plugins(source.tree)
        if invalid:
            unresolved.add("unresolved pytest plugin fixture dependency")
        for name in names:
            plugin_path = _local_plugin_path(root, name)
            if plugin_path is None:
                unresolved.add("unresolved pytest plugin fixture dependency")
                continue
            plugin = _load_module(plugin_path, cache)
            if plugin not in plugins and plugin not in ancestors and plugin != test_module:
                plugins.append(plugin)
                queue.append(plugin)
    return tuple((*ancestors, *plugins, test_module)), tuple(sorted(unresolved))


def _module_scope(tree: ast.Module) -> list[ast.AST]:
    return [
        item
        for item in tree.body
        if not isinstance(item, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def _live_reasons(
    repo_root: Path,
    test_path: Path,
    test_tree: ast.Module,
    node_id: str,
    cache: dict[Path, SourceModule],
) -> tuple[str, ...]:
    sources, unresolved = _source_modules(repo_root, test_path, cache)
    definition = _definition(test_tree, node_id)
    module_scopes = [(source, _module_scope(source.tree)) for source in sources]
    scope = [node for _, nodes in module_scopes for node in nodes]
    dependency_roots: list[tuple[SourceModule, Sequence[ast.AST]]] = [
        (source, nodes) for source, nodes in module_scopes
    ]
    reasons: set[str] = set(unresolved)
    if isinstance(definition, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        scope.extend(definition.body)
        test_source = next(source for source in sources if source.tree is test_tree)
        dependency_roots.append((test_source, definition.body))
        fixtures, pending = _fixture_definitions(sources)
        pending.update(_fixture_arguments(definition))
        scanned: set[str] = set()
        while pending:
            name = pending.pop()
            fixture = fixtures.get(name)
            if fixture is None or name in scanned:
                continue
            scanned.add(name)
            scope.extend(fixture.body)  # type: ignore[union-attr]
            fixture_source = next(
                source for source in sources if fixture in source.tree.body
            )
            dependency_roots.append(
                (fixture_source, fixture.body)  # type: ignore[union-attr]
            )
            pending.update(_fixture_arguments(fixture))
    dependency_scope, dependency_reasons = _local_dependency_scope(
        repo_root,
        dependency_roots,
        cache,
    )
    scope.extend(dependency_scope)
    reasons.update(dependency_reasons)
    for root in scope:
        for node in ast.walk(root):
            if isinstance(node, ast.Call):
                name = _call_name(node)
                if name == "home":
                    reasons.add("real home database path")
                if name in {
                    "call",
                    "check_call",
                    "check_output",
                    "Popen",
                    "run",
                    "system",
                    "execv",
                    "execve",
                    "create_subprocess_exec",
                    "create_subprocess_shell",
                }:
                    argument = node.args[0] if node.args else next(
                        (keyword.value for keyword in node.keywords if keyword.arg == "args"),
                        None,
                    )
                    if argument is not None:
                        command = _literal_command(argument)
                        if command:
                            try:
                                executable = Path(shlex.split(command)[0]).name
                            except (IndexError, ValueError):
                                executable = ""
                            if executable.casefold() in AUDIO_EXECUTABLE_NAMES:
                                reasons.add(f"unmocked audio or microphone binary: {executable}")
                            if executable == "launchctl":
                                reasons.add("unmocked launchctl invocation")
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and "\n" not in node.value
            ):
                if _TMP_DAN.search(node.value):
                    reasons.add("legacy /tmp/dan-* runtime path")
                if _LIVE_PORT.search(node.value):
                    reasons.add("live DAN voice port 7788")
    return tuple(sorted(reasons))


def _controlled_python_paths() -> tuple[str, ...]:
    paths = {
        path
        for key in ("purelib", "platlib")
        if (path := sysconfig.get_path(key)) is not None
    }
    return tuple(sorted(paths))


def _pytest_command(*arguments: str) -> list[str]:
    bootstrap = (
        "import sys; "
        f"sys.path.extend({list(_controlled_python_paths())!r}); "
        "import pytest; raise SystemExit(pytest.main(sys.argv[1:]))"
    )
    return [sys.executable, "-I", "-S", "-c", bootstrap, *arguments]


def _collection_environment(env: Mapping[str, str] | None) -> dict[str, str]:
    if env is not None:
        return dict(env)
    return {
        "PATH": os.environ.get("PATH", os.defpath),
        "PYTHONNOUSERSITE": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    }


def collect_node_ids(repo_root: Path, *, env: Mapping[str, str] | None = None) -> tuple[str, ...]:
    completed = subprocess.run(
        _pytest_command("--collect-only", "-q"),
        cwd=repo_root,
        env=_collection_environment(env),
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            completed.stderr.strip()
            or completed.stdout.strip()
            or "pytest collection failed"
        )
    nodes: list[str] = []
    for line in completed.stdout.splitlines():
        node_id = line.strip()
        try:
            _node_parts(node_id)
        except ValueError:
            continue
        nodes.append(node_id)
    if not nodes:
        raise RuntimeError("pytest collection returned no node IDs")
    return tuple(nodes)


def classify_node_ids(repo_root: Path, node_ids: Sequence[str]) -> dict[str, SafetyClassification]:
    cached: dict[Path, SourceModule] = {}
    result: dict[str, SafetyClassification] = {}
    for node_id in node_ids:
        path = _node_file(repo_root, node_id)
        tree = _load_module(path, cached).tree
        reasons = _live_reasons(repo_root, path, tree, node_id, cached)
        marked = _explicit_manual(tree, node_id)
        safety: Safety = "live-manual" if marked or reasons else "isolated"
        result[node_id] = SafetyClassification(node_id, safety, reasons, marked)
    return result


def scan_node_ids(repo_root: Path, node_ids: Sequence[str]) -> list[str]:
    """List unmarked direct live primitives with sanitized node IDs only."""
    classified = classify_node_ids(repo_root, node_ids)
    return sorted(
        f"{sanitize_raw_node_id(node)}: {reason}"
        for node, row in classified.items()
        if row.reasons and not row.explicit_manual
        for reason in row.reasons
    )


def scan_automatic_tests(repo_root: Path) -> list[str]:
    return scan_node_ids(repo_root, collect_node_ids(repo_root))


def isolated_node_ids(classified: Mapping[str, SafetyClassification]) -> tuple[str, ...]:
    return tuple(node for node, row in classified.items() if row.safety == "isolated")


def live_manual_node_ids(classified: Mapping[str, SafetyClassification]) -> tuple[str, ...]:
    return tuple(node for node, row in classified.items() if row.safety == "live-manual")


def test_environment(home: Path, runtime: Path, database: Path) -> dict[str, str]:
    return {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", os.defpath),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "XDG_RUNTIME_DIR": str(runtime),
        "TMPDIR": str(runtime),
        "DAN_RUNTIME_DIR": str(runtime),
        "DAN_DB_PATH": str(database),
        "DAN_TEST_MODE": "1",
        "DAN_DISABLE_AUDIO": "1",
        "DAN_DISABLE_MIC": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    }
