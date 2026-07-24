"""Regression gate for the canonical public voice source-of-truth policy."""

from __future__ import annotations

import ast
import importlib.util
import re
import tomllib
from collections.abc import Iterable
from pathlib import Path

from dan.install.adapters import ADAPTERS, adapter_install_items
from dan.voice.assets import load_voice_catalog
from dan.voice.policy import OWNER_VOICE_PERSONAS

ROOT = Path(__file__).resolve().parents[1]
VOICE_DIR = ROOT / "config" / "voice"
OWNER_CAST = frozenset({"dan", "danusia"})

_AS_VALUE = re.compile(
    r"--as(?:=|\s+)\s*[`'\"]*(?P<value>"
    r"\$\{?[A-Za-z_][A-Za-z0-9_]*\}?"
    r"|<[^>\s]+>"
    r"|[A-Za-z][A-Za-z0-9_-]*)"
)
_REMOVED_CHATTERBOX_ZANETA_PATHS = (
    ROOT / "config" / "voice" / "pipelines" / "chatterbox-v3-zaneta.toml",
    ROOT / "dan" / "voice" / "pipelines" / "__init__.py",
    ROOT / "dan" / "voice" / "pipelines" / "__main__.py",
    ROOT / "dan" / "voice" / "pipelines" / "chatterbox_v3.py",
    ROOT / "tests" / "test_chatterbox_v3_pipeline.py",
    ROOT / "tests" / "test_offline_render_entrypoint.py",
)
_REMOVED_PIPELINE_MARKERS = (
    "chatterbox-v3-zaneta",
    "chatterbox_v3",
    "dan.voice.pipelines",
    "offline_pipeline",
    "dan_zaneta_reference_wav",
    "cb3-venv",
)


def _call_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _module_string_bindings(tree: ast.Module) -> dict[str, str]:
    bindings: dict[str, str] = {}
    for node in tree.body:
        target: ast.expr | None = None
        value: ast.expr | None = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            target = node.target
            value = node.value
        if (
            isinstance(target, ast.Name)
            and isinstance(value, ast.Constant)
            and isinstance(value.value, str)
        ):
            bindings[target.id] = value.value
    return bindings


def _job_persona_routes() -> list[tuple[Path, int, str]]:
    routes: list[tuple[Path, int, str]] = []
    for source_path in sorted((ROOT / "dan" / "jobs").rglob("*.py")):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        bindings = _module_string_bindings(tree)
        for name, value in bindings.items():
            if "PERSONA" in name.upper():
                routes.append((source_path, 1, value))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or _call_name(node.func) != "SpeechIntent":
                continue
            persona_keyword = next(
                (keyword for keyword in node.keywords if keyword.arg == "persona"),
                None,
            )
            assert persona_keyword is not None, (
                source_path,
                node.lineno,
                "SpeechIntent must declare persona explicitly",
            )
            value_node = persona_keyword.value
            if isinstance(value_node, ast.Constant) and isinstance(value_node.value, str):
                value = value_node.value
            elif isinstance(value_node, ast.Name) and value_node.id in bindings:
                value = bindings[value_node.id]
            else:
                raise AssertionError(
                    f"{source_path}:{node.lineno}: job persona is not statically resolvable"
                )
            routes.append((source_path, node.lineno, value))
    return routes


def _installed_template_paths() -> tuple[Path, ...]:
    return tuple(sorted({template for _, template, _ in adapter_install_items()}))


def _template_persona_routes() -> list[tuple[Path, int, str]]:
    routes: list[tuple[Path, int, str]] = []
    for template_path in _installed_template_paths():
        assert template_path.is_file(), template_path
        for line_number, line in enumerate(
            template_path.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            for match in _AS_VALUE.finditer(line):
                routes.append((template_path, line_number, match.group("value")))
    return routes


def _active_pipeline_sources() -> Iterable[Path]:
    yield ROOT / "pyproject.toml"
    for source_path in sorted((ROOT / "dan").rglob("*.py")):
        yield source_path
    for source_path in sorted(VOICE_DIR.rglob("*.toml")):
        yield source_path
    for suffix in ("*.md", "*.sh", "*.toml"):
        for source_path in sorted((ROOT / "integrations").rglob(suffix)):
            yield source_path
    for source_path in sorted((ROOT / "scripts").iterdir()):
        if source_path.is_file():
            yield source_path


def test_real_catalog_and_policy_are_exactly_the_owner_cast() -> None:
    with (VOICE_DIR / "personas.toml").open("rb") as handle:
        raw_catalog = tomllib.load(handle)
    runtime_catalog = load_voice_catalog(VOICE_DIR)

    assert frozenset(raw_catalog) == OWNER_CAST
    assert frozenset(runtime_catalog.personas) == OWNER_CAST
    assert OWNER_VOICE_PERSONAS == OWNER_CAST
    assert raw_catalog["dan"]["voice"] == "M3"
    assert len({spec["voice"] for spec in raw_catalog.values()}) == len(OWNER_CAST)
    assert {float(spec["speed"]) for spec in raw_catalog.values()} == {1.0}
    assert {spec["dsp"] for spec in raw_catalog.values()} == {"none"}
    assert {spec["mastering"] for spec in raw_catalog.values()} == {"default"}
    assert set(runtime_catalog.gains) == {
        f"{spec['voice']}|{spec['mastering']}"
        for spec in raw_catalog.values()
    }


def test_adapters_and_jobs_publish_only_the_owner_cast() -> None:
    assert ADAPTERS
    adapter_routes = {
        (name, spec.persona)
        for name, spec in ADAPTERS.items()
    }
    invalid_adapters = sorted(
        (name, persona)
        for name, persona in adapter_routes
        if persona not in OWNER_CAST
    )

    job_routes = _job_persona_routes()
    invalid_jobs = sorted(
        (str(source_path.relative_to(ROOT)), line_number, persona)
        for source_path, line_number, persona in job_routes
        if persona not in OWNER_CAST
    )

    assert not invalid_adapters
    assert job_routes, "no job persona route was discovered"
    assert not invalid_jobs


def test_scene_examples_publish_only_the_owner_cast() -> None:
    scene_paths = sorted((ROOT / "examples").rglob("*.scene.txt"))
    assert scene_paths, "no scene example was discovered"

    routes: list[tuple[str, int, str]] = []
    for scene_path in scene_paths:
        for line_number, raw_line in enumerate(
            scene_path.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            assert "|" in line, f"{scene_path}:{line_number}: missing persona separator"
            control = line.split("|", 1)[0].strip().lower()
            persona = control.split(";", 1)[0].strip()
            routes.append((str(scene_path.relative_to(ROOT)), line_number, persona))

    assert routes, "scene examples contain no spoken routes"
    assert not [route for route in routes if route[2] not in OWNER_CAST]


def test_installable_templates_use_only_literal_owner_cast_routes() -> None:
    routes = _template_persona_routes()
    assert routes, "no --as route was discovered in installable templates"

    generic_routes = [
        (str(template.relative_to(ROOT)), line_number, value)
        for template, line_number, value in routes
        if value.startswith("$") or value.startswith("<")
    ]
    literal_routes = [
        (str(template.relative_to(ROOT)), line_number, value.lower())
        for template, line_number, value in routes
        if not value.startswith("$") and not value.startswith("<")
    ]
    invalid_literals = [route for route in literal_routes if route[2] not in OWNER_CAST]

    assert not generic_routes
    assert not invalid_literals
    assert {route[2] for route in literal_routes} == OWNER_CAST


def test_removed_chatterbox_zaneta_pipeline_stays_absent() -> None:
    lingering_paths = [path for path in _REMOVED_CHATTERBOX_ZANETA_PATHS if path.exists()]
    assert not lingering_paths
    assert importlib.util.find_spec("dan.voice.pipelines.chatterbox_v3") is None

    active_hits: list[tuple[str, str]] = []
    for source_path in dict.fromkeys(_active_pipeline_sources()):
        text = source_path.read_text(encoding="utf-8", errors="replace").lower()
        for marker in _REMOVED_PIPELINE_MARKERS:
            if marker in text:
                active_hits.append((str(source_path.relative_to(ROOT)), marker))

    assert not active_hits
