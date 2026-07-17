"""Contract tests for the isolated DAN Release 1 test baseline."""

from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
import json
import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType

import pytest


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_baseline_script() -> ModuleType:
    script = Path(__file__).resolve().parents[1] / "scripts" / "dan-test-baseline"
    loader = SourceFileLoader("dan_test_baseline_under_test", str(script))
    module = ModuleType(loader.name)
    module.__file__ = str(script)
    module.__loader__ = loader
    loader.exec_module(module)
    return module


def _write_test_file(root: Path, source: str) -> str:
    path = root / "tests" / "test_live.py"
    path.parent.mkdir(exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return "tests/test_live.py::test_hardware"


def _valid_report(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 1,
        "status": "failed",
        "expected_collected": 1,
        "collected": 1,
        "isolated": 1,
        "live_manual": 0,
        "duration_seconds": 0.5,
        "failures": ["tests/test_example.py::test_failure"],
    }
    payload.update(overrides)
    return payload


def test_every_collected_test_has_a_safety_class(repo_root: Path) -> None:
    from jarvis.migration.test_safety import classify_node_ids, collect_node_ids

    collected = collect_node_ids(repo_root)
    classified = classify_node_ids(repo_root, collected)

    assert set(classified) == set(collected)
    assert {row.safety for row in classified.values()} <= {"isolated", "live-manual"}


def test_automatic_group_has_no_live_primitives(repo_root: Path) -> None:
    from jarvis.migration.test_safety import scan_automatic_tests

    assert scan_automatic_tests(repo_root) == []


@pytest.mark.parametrize(
    "source, reason",
    [
        (
            "import pytest\nimport subprocess\n\n"
            "@pytest.fixture\ndef recorder():\n"
            "    subprocess.run(['afplay', 'x.wav'])\n\n"
            "def test_hardware(recorder):\n"
            "    assert recorder is None\n",
            "audio",
        ),
        ("import os\n\ndef test_hardware():\n    os.system('launchctl list')\n", "launchctl"),
        ("def test_hardware():\n    path = '/tmp/dan-voice/req'\n", "/tmp/dan-*"),
        ("def test_hardware():\n    url = 'http://127.0.0.1:7788/health'\n", "voice port"),
        ("from pathlib import Path\n\ndef test_hardware():\n    db = Path.home() / '.dan' / 'dan.db'\n", "home database"),
    ],
)
def test_unmarked_live_primitives_are_manual_and_reported(
    tmp_path: Path, source: str, reason: str
) -> None:
    from jarvis.migration.test_safety import classify_node_ids, scan_node_ids

    node_id = _write_test_file(tmp_path, source)
    assert classify_node_ids(tmp_path, (node_id,))[node_id].safety == "live-manual"
    assert reason in " ".join(scan_node_ids(tmp_path, (node_id,)))


@pytest.mark.parametrize(
    "source",
    [
        "import pytest\n\n@pytest.mark.live_manual\ndef test_hardware():\n    pass\n",
        "import pytest\npytestmark = pytest.mark.live_manual\n\ndef test_hardware():\n    pass\n",
        "import pytest\n\n@pytest.mark.live_manual\nclass TestX:\n    def test_hardware(self):\n        pass\n",
    ],
)
def test_explicit_manual_marker_is_not_an_automatic_violation(tmp_path: Path, source: str) -> None:
    from jarvis.migration.test_safety import classify_node_ids, scan_node_ids

    node_id = "tests/test_live.py::TestX::test_hardware" if "class TestX" in source else _write_test_file(tmp_path, source)
    if "class TestX" in source:
        _write_test_file(tmp_path, source)
    assert classify_node_ids(tmp_path, (node_id,))[node_id].safety == "live-manual"
    assert scan_node_ids(tmp_path, (node_id,)) == []


def test_report_verification_does_not_require_collection(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    report.write_text(json.dumps(_valid_report()), encoding="utf-8")
    report.chmod(0o600)
    completed = subprocess.run(
        [sys.executable, "scripts/dan-test-baseline", "--verify-report", str(report)],
        cwd=Path(__file__).resolve().parents[1], check=False, capture_output=True, text=True,
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize(
    "payload",
    [
        _valid_report(private_text="DO_NOT_ECHO"),
        _valid_report(status="passed", isolated=0, live_manual=1, failures=[]),
        _valid_report(status="collection-mismatch"),
    ],
)
def test_report_verifier_rejects_invalid_schema_without_echoing(
    tmp_path: Path, payload: dict[str, object]
) -> None:
    report = tmp_path / "report.json"
    report.write_text(json.dumps(payload), encoding="utf-8")
    report.chmod(0o600)
    completed = subprocess.run(
        [sys.executable, "scripts/dan-test-baseline", "--verify-report", str(report)],
        cwd=Path(__file__).resolve().parents[1], check=False, capture_output=True, text=True,
    )
    assert completed.returncode == 2
    assert "DO_NOT_ECHO" not in completed.stdout + completed.stderr


def test_report_write_is_private_and_does_not_follow_fixed_temp_symlink(tmp_path: Path) -> None:
    baseline = _load_baseline_script()
    report = tmp_path / "report.json"
    victim = tmp_path / "victim"
    victim.write_text("untouched", encoding="utf-8")
    report.with_name(".report.json.tmp").symlink_to(victim)
    baseline._write_report(report, _valid_report())
    assert victim.read_text(encoding="utf-8") == "untouched"
    assert report.stat().st_mode & 0o777 == 0o600


def test_baseline_refuses_manual_nodes_before_pytest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    baseline = _load_baseline_script()
    from jarvis.migration.test_safety import SafetyClassification

    node_id = "tests/test_live.py::test_hardware"
    monkeypatch.setenv("DAN_TEST_REPORT_HOME", str(tmp_path))
    monkeypatch.setattr(baseline, "collect_node_ids", lambda *args, **kwargs: (node_id,))
    from jarvis.migration.test_safety import SafetyClassification
    monkeypatch.setattr(baseline, "classify_node_ids", lambda *args, **kwargs: {node_id: SafetyClassification(node_id, "isolated")})
    monkeypatch.setattr(baseline, "scan_node_ids", lambda *args, **kwargs: [])
    monkeypatch.setattr(baseline, "classify_node_ids", lambda *args, **kwargs: {node_id: SafetyClassification(node_id, "live-manual", ("explicit",))})
    monkeypatch.setattr(baseline.subprocess, "run", lambda *args, **kwargs: pytest.fail("pytest ran"))
    assert baseline.main(["--expect-collected", "1"]) == 2


def test_baseline_runs_each_isolated_node_explicitly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    baseline = _load_baseline_script()
    from jarvis.migration.test_safety import SafetyClassification

    nodes = ("tests/test_one.py::test_one", "tests/test_two.py::test_two")
    monkeypatch.setenv("DAN_TEST_REPORT_HOME", str(tmp_path))
    monkeypatch.setattr(baseline, "collect_node_ids", lambda *args, **kwargs: nodes)
    monkeypatch.setattr(baseline, "scan_node_ids", lambda *args, **kwargs: [])
    monkeypatch.setattr(baseline, "classify_node_ids", lambda *args, **kwargs: {node_id: SafetyClassification(node_id, "isolated") for node_id in nodes})
    seen: dict[str, object] = {}
    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen["argv"] = argv
        seen["env"] = kwargs["env"]
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
    monkeypatch.setattr(baseline.subprocess, "run", fake_run)
    assert baseline.main(["--expect-collected", "2"]) == 0
    assert seen["argv"] == [sys.executable, "-m", "pytest", "-q", *nodes]
    assert seen["env"]["DAN_DISABLE_AUDIO"] == "1"


def test_baseline_refuses_collection_mismatch_before_pytest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = _load_baseline_script()
    node_id = "tests/test_one.py::test_one"
    monkeypatch.setenv("DAN_TEST_REPORT_HOME", str(tmp_path))
    monkeypatch.setattr(baseline, "collect_node_ids", lambda *args, **kwargs: (node_id,))
    from jarvis.migration.test_safety import SafetyClassification
    monkeypatch.setattr(baseline, "classify_node_ids", lambda *args, **kwargs: {node_id: SafetyClassification(node_id, "isolated")})
    monkeypatch.setattr(baseline, "scan_node_ids", lambda *args, **kwargs: [])
    monkeypatch.setattr(baseline.subprocess, "run", lambda *args, **kwargs: pytest.fail("pytest ran"))
    assert baseline.main(["--expect-collected", "2"]) == 2


def test_test_environment_redirects_state_and_preserves_dependencies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import jarvis.migration.test_safety as safety

    user_site = tmp_path / "user-site"
    user_site.mkdir()
    monkeypatch.setattr(safety.site, "getusersitepackages", lambda: str(user_site))
    environment = safety.test_environment(tmp_path / "home", tmp_path / "runtime", tmp_path / "state" / "dan.db")
    assert environment["DAN_TEST_MODE"] == "1"
    assert environment["DAN_DISABLE_AUDIO"] == "1"
    assert environment["DAN_DISABLE_MIC"] == "1"
    assert environment["HOME"] == str(tmp_path / "home")
    assert str(user_site) in environment["PYTHONPATH"].split(os.pathsep)
