"""Contract tests for the isolated DAN Release 1 test baseline."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]


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


def _write_source(root: Path, relative_path: str, source: str) -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


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
    from dan.migration.test_safety import classify_node_ids, collect_node_ids

    collected = collect_node_ids(repo_root)
    classified = classify_node_ids(repo_root, collected)

    assert set(classified) == set(collected)
    assert {row.safety for row in classified.values()} <= {"isolated", "live-manual"}


def test_automatic_group_has_no_live_primitives(repo_root: Path) -> None:
    from dan.migration.test_safety import scan_automatic_tests

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
        (
            "from pathlib import Path\n"
            "def test_hardware():\n"
            "    db = Path.home() / '.dan' / 'dan.db'\n",
            "home database",
        ),
    ],
)
def test_unmarked_live_primitives_are_manual_and_reported(
    tmp_path: Path, source: str, reason: str
) -> None:
    from dan.migration.test_safety import classify_node_ids, scan_node_ids

    node_id = _write_test_file(tmp_path, source)
    assert classify_node_ids(tmp_path, (node_id,))[node_id].safety == "live-manual"
    assert reason in " ".join(scan_node_ids(tmp_path, (node_id,)))


def test_ancestor_conftest_autouse_fixture_is_manual_and_reported(tmp_path: Path) -> None:
    from dan.migration.test_safety import classify_node_ids, scan_node_ids

    _write_source(
        tmp_path,
        "conftest.py",
        "import pytest\nimport subprocess\n\n"
        "@pytest.fixture(autouse=True)\ndef live_audio():\n"
        "    subprocess.run(['afplay', 'x.wav'])\n",
    )
    node_id = _write_test_file(tmp_path, "def test_hardware():\n    pass\n")

    assert classify_node_ids(tmp_path, (node_id,))[node_id].safety == "live-manual"
    assert "audio" in " ".join(scan_node_ids(tmp_path, (node_id,)))


def test_explicit_local_plugin_fixture_is_manual_and_reported(tmp_path: Path) -> None:
    from dan.migration.test_safety import classify_node_ids, scan_node_ids

    _write_source(
        tmp_path,
        "tests/live_plugin.py",
        "import pytest\nimport subprocess\n\n"
        "@pytest.fixture\ndef recorder():\n"
        "    subprocess.run(['launchctl', 'list'])\n",
    )
    node_id = _write_test_file(
        tmp_path,
        "pytest_plugins = ('tests.live_plugin',)\n\n"
        "def test_hardware(recorder):\n    assert recorder is None\n",
    )

    assert classify_node_ids(tmp_path, (node_id,))[node_id].safety == "live-manual"
    assert "launchctl" in " ".join(scan_node_ids(tmp_path, (node_id,)))


def test_unresolved_explicit_plugin_fails_closed(tmp_path: Path) -> None:
    from dan.migration.test_safety import classify_node_ids, scan_node_ids

    node_id = _write_test_file(
        tmp_path,
        "pytest_plugins = ('not_in_the_repository',)\n\ndef test_hardware():\n    pass\n",
    )

    assert classify_node_ids(tmp_path, (node_id,))[node_id].safety == "live-manual"
    assert scan_node_ids(tmp_path, (node_id,)) == [
        "tests/test_live.py::test_hardware: unresolved pytest plugin fixture dependency"
    ]


def test_imported_local_audio_helper_is_manual_and_reported(tmp_path: Path) -> None:
    from dan.migration.test_safety import classify_node_ids, scan_node_ids

    _write_source(
        tmp_path,
        "tests/helpers/live_audio.py",
        "import subprocess\n\ndef play_clip():\n    subprocess.run(['afplay', 'x.wav'])\n",
    )
    node_id = _write_test_file(
        tmp_path,
        "def test_hardware():\n"
        "    from tests.helpers.live_audio import play_clip\n"
        "    play_clip()\n",
    )

    row = classify_node_ids(tmp_path, (node_id,))[node_id]
    reasons = " ".join(row.reasons)
    assert row.safety == "live-manual"
    assert "afplay" in reasons
    assert "audio" in " ".join(scan_node_ids(tmp_path, (node_id,)))


def test_recursive_imported_local_audio_helper_is_manual(tmp_path: Path) -> None:
    from dan.migration.test_safety import classify_node_ids

    _write_source(
        tmp_path,
        "tests/helpers/live_audio.py",
        "import subprocess\n\ndef play_clip():\n    subprocess.run(['/usr/bin/afplay', 'x.wav'])\n",
    )
    _write_source(
        tmp_path,
        "tests/helpers/audio_wrapper.py",
        "from tests.helpers.live_audio import play_clip\n\ndef run_audio():\n    play_clip()\n",
    )
    node_id = _write_test_file(
        tmp_path,
        "from tests.helpers.audio_wrapper import run_audio\n\n"
        "def test_hardware():\n"
        "    run_audio()\n",
    )

    row = classify_node_ids(tmp_path, (node_id,))[node_id]
    assert row.safety == "live-manual"
    assert "afplay" in " ".join(row.reasons)


def test_safe_imported_local_helper_stays_isolated(tmp_path: Path) -> None:
    from dan.migration.test_safety import classify_node_ids, scan_node_ids

    _write_source(
        tmp_path,
        "tests/helpers/safe_math.py",
        "def add(left, right):\n    return left + right\n",
    )
    node_id = _write_test_file(
        tmp_path,
        "from tests.helpers.safe_math import add\n\n"
        "def test_hardware():\n"
        "    assert add(2, 3) == 5\n",
    )

    row = classify_node_ids(tmp_path, (node_id,))[node_id]
    assert row.safety == "isolated"
    assert row.reasons == ()
    assert scan_node_ids(tmp_path, (node_id,)) == []


@pytest.mark.parametrize(
    ("source", "reason"),
    [
        (
            "from tests.helpers.missing import play_clip\n\n"
            "def test_hardware():\n"
            "    play_clip()\n",
            "unresolved repository-local import dependency",
        ),
        (
            "from tests.helpers.ambiguous import play_clip\n\n"
            "def test_hardware():\n"
            "    play_clip()\n",
            "ambiguous repository-local import dependency",
        ),
    ],
)
def test_unresolved_or_ambiguous_local_helper_fails_closed(
    tmp_path: Path,
    source: str,
    reason: str,
) -> None:
    from dan.migration.test_safety import classify_node_ids

    if "ambiguous" in source:
        _write_source(
            tmp_path,
            "tests/helpers/ambiguous.py",
            "def play_clip():\n    pass\n",
        )
        _write_source(
            tmp_path,
            "tests/helpers/ambiguous/__init__.py",
            "def play_clip():\n    pass\n",
        )
    node_id = _write_test_file(tmp_path, source)

    row = classify_node_ids(tmp_path, (node_id,))[node_id]
    assert row.safety == "live-manual"
    assert reason in " ".join(row.reasons)


def test_imported_local_module_side_effect_is_manual(tmp_path: Path) -> None:
    from dan.migration.test_safety import classify_node_ids

    _write_source(
        tmp_path,
        "tests/helpers/live_module.py",
        "import subprocess\n"
        "subprocess.run(['afplay', 'module.wav'])\n\n"
        "def helper():\n"
        "    return None\n",
    )
    node_id = _write_test_file(
        tmp_path,
        "import tests.helpers.live_module as helper_module\n\n"
        "def test_hardware():\n"
        "    helper_module.helper()\n",
    )

    row = classify_node_ids(tmp_path, (node_id,))[node_id]
    assert row.safety == "live-manual"
    assert "afplay" in " ".join(row.reasons)


@pytest.mark.parametrize(
    "source",
    [
        "import pytest\n\n@pytest.mark.live_manual\ndef test_hardware():\n    pass\n",
        "import pytest\npytestmark = pytest.mark.live_manual\n\ndef test_hardware():\n    pass\n",
        (
            "import pytest\n\n"
            "@pytest.mark.live_manual\n"
            "class TestX:\n"
            "    def test_hardware(self):\n"
            "        pass\n"
        ),
    ],
)
def test_explicit_manual_marker_is_not_an_automatic_violation(tmp_path: Path, source: str) -> None:
    from dan.migration.test_safety import classify_node_ids, scan_node_ids

    node_id = (
        "tests/test_live.py::TestX::test_hardware"
        if "class TestX" in source
        else _write_test_file(tmp_path, source)
    )
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
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_report_verifier_rejects_raw_parameter_payload(tmp_path: Path) -> None:
    payload = "secret-token"
    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(_valid_report(failures=[f"tests/test_example.py::test_failure[{payload}]"])),
        encoding="utf-8",
    )
    report.chmod(0o600)
    completed = subprocess.run(
        [sys.executable, "scripts/dan-test-baseline", "--verify-report", str(report)],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 2
    assert payload not in completed.stdout + completed.stderr


def test_raw_failure_id_sanitization_hashes_every_parameter_payload() -> None:
    baseline = _load_baseline_script()

    for payload in ("secret-token-http://[::1]/", "param-0123456789abcdef"):
        node = f"tests/test_example.py::test_failure[{payload}]"
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

        sanitized = baseline.sanitize_raw_node_id(node)

        assert sanitized == f"tests/test_example.py::test_failure[param-{digest}]"
        assert sanitized != node


def test_failure_parser_ignores_warning_summary_node_ids() -> None:
    baseline = _load_baseline_script()
    output = "\n".join(
        (
            "FAILED tests/test_actual.py::test_failure - AssertionError",
            "tests/test_warning.py::test_emits_deprecation_warning",
            "  /repo/tests/test_warning.py:10: DeprecationWarning: compatibility caller",
        )
    )

    assert baseline._failure_ids(output) == ["tests/test_actual.py::test_failure"]


def test_failure_report_comparator_requires_no_new_ids(tmp_path: Path) -> None:
    baseline = _load_baseline_script()
    previous = tmp_path / "previous.json"
    current = tmp_path / "current.json"
    previous.write_text(
        json.dumps(
            _valid_report(failures=["tests/test_example.py::test_failure[param-a1b2c3d4e5f60708]"])
        ),
        encoding="utf-8",
    )
    current.write_text(
        json.dumps(
            _valid_report(
                failures=[
                    "tests/test_example.py::test_failure[param-a1b2c3d4e5f60708]",
                    "tests/test_example.py::test_new_failure",
                ]
            )
        ),
        encoding="utf-8",
    )
    previous.chmod(0o400)
    current.chmod(0o600)

    comparison = baseline.compare_failure_reports(previous, current, reference_is_canonical=True)

    assert comparison["new"] == ["tests/test_example.py::test_new_failure"]
    assert comparison["removed"] == []
    assert comparison["unchanged"] == [
        "tests/test_example.py::test_failure[param-a1b2c3d4e5f60708]"
    ]


def test_failure_report_comparator_rehashes_raw_canonical_looking_reference(tmp_path: Path) -> None:
    baseline = _load_baseline_script()
    previous = tmp_path / "previous.json"
    current = tmp_path / "current.json"
    raw_payload = "param-0123456789abcdef"
    canonical_payload = f"param-{hashlib.sha256(raw_payload.encode('utf-8')).hexdigest()[:16]}"
    previous.write_text(
        json.dumps(_valid_report(failures=[f"tests/test_example.py::test_failure[{raw_payload}]"])),
        encoding="utf-8",
    )
    current.write_text(
        json.dumps(
            _valid_report(failures=[f"tests/test_example.py::test_failure[{canonical_payload}]"])
        ),
        encoding="utf-8",
    )
    previous.chmod(0o400)
    current.chmod(0o600)

    comparison = baseline.compare_failure_reports(previous, current)

    assert comparison == {
        "new": [],
        "removed": [],
        "unchanged": [f"tests/test_example.py::test_failure[{canonical_payload}]"],
    }


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
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 2
    assert "DO_NOT_ECHO" not in completed.stdout + completed.stderr


@pytest.mark.parametrize(
    "payload",
    [
        _valid_report(status="failed", failures=[]),
        _valid_report(status="live-manual-refused", isolated=1, live_manual=0, failures=[]),
        _valid_report(status="collection-mismatch", expected_collected=1, collected=1),
    ],
)
def test_report_verifier_enforces_status_specific_invariants(
    tmp_path: Path, payload: dict[str, object]
) -> None:
    report = tmp_path / "report.json"
    report.write_text(json.dumps(payload), encoding="utf-8")
    report.chmod(0o600)

    completed = subprocess.run(
        [sys.executable, "scripts/dan-test-baseline", "--verify-report", str(report)],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2


def test_report_write_is_private_and_does_not_follow_fixed_temp_symlink(tmp_path: Path) -> None:
    baseline = _load_baseline_script()
    report = tmp_path / "report.json"
    victim = tmp_path / "victim"
    victim.write_text("untouched", encoding="utf-8")
    report.with_name(".report.json.tmp").symlink_to(victim)
    baseline._write_report(report, _valid_report())
    assert victim.read_text(encoding="utf-8") == "untouched"
    assert report.stat().st_mode & 0o777 == 0o600


def test_baseline_refuses_manual_nodes_before_pytest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = _load_baseline_script()
    from dan.migration.test_safety import SafetyClassification

    node_id = "tests/test_live.py::test_hardware"
    monkeypatch.setenv("DAN_TEST_REPORT_HOME", str(tmp_path))
    monkeypatch.setattr(baseline, "collect_node_ids", lambda *args, **kwargs: (node_id,))
    monkeypatch.setattr(
        baseline,
        "classify_node_ids",
        lambda *args, **kwargs: {node_id: SafetyClassification(node_id, "isolated")},
    )
    monkeypatch.setattr(baseline, "scan_node_ids", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        baseline,
        "classify_node_ids",
        lambda *args, **kwargs: {
            node_id: SafetyClassification(node_id, "live-manual", ("explicit",))
        },
    )
    monkeypatch.setattr(
        baseline.subprocess, "run", lambda *args, **kwargs: pytest.fail("pytest ran")
    )
    assert baseline.main(["--expect-collected", "1"]) == 2


def test_baseline_runs_each_isolated_node_explicitly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = _load_baseline_script()
    from dan.migration.test_safety import SafetyClassification

    nodes = ("tests/test_one.py::test_one", "tests/test_two.py::test_two")
    monkeypatch.setenv("DAN_TEST_REPORT_HOME", str(tmp_path))
    monkeypatch.setattr(baseline, "collect_node_ids", lambda *args, **kwargs: nodes)
    monkeypatch.setattr(baseline, "scan_node_ids", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        baseline,
        "classify_node_ids",
        lambda *args, **kwargs: {
            node_id: SafetyClassification(node_id, "isolated") for node_id in nodes
        },
    )
    seen: dict[str, object] = {}

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen["argv"] = argv
        seen["env"] = kwargs["env"]
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(baseline.subprocess, "run", fake_run)
    assert baseline.main(["--expect-collected", "2"]) == 0
    assert seen["argv"][:3] == [sys.executable, "-I", "-S"]
    assert seen["argv"][5:] == ["-q", *nodes]
    assert seen["env"]["DAN_DISABLE_AUDIO"] == "1"


def test_compare_snapshots_same_canonical_path_before_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    baseline = _load_baseline_script()
    from dan.migration.test_safety import SafetyClassification

    node_id = "tests/test_one.py::test_one"
    report = tmp_path / ".dan" / "migration" / "test-baseline.json"
    report.parent.mkdir(parents=True)
    report.write_text(json.dumps(_valid_report(failures=[node_id])), encoding="utf-8")
    report.chmod(0o600)
    monkeypatch.setenv("DAN_TEST_REPORT_HOME", str(tmp_path))
    monkeypatch.setattr(baseline, "collect_node_ids", lambda *args, **kwargs: (node_id,))
    monkeypatch.setattr(
        baseline,
        "classify_node_ids",
        lambda *args, **kwargs: {node_id: SafetyClassification(node_id, "isolated")},
    )
    monkeypatch.setattr(baseline, "scan_node_ids", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        baseline.subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(
            argv,
            1,
            stdout=(
                f"FAILED {node_id} - AssertionError\n"
                "FAILED tests/test_new.py::test_new - AssertionError\n"
            ),
            stderr="",
        ),
    )

    assert baseline.main(["--compare", str(report)]) == 2
    comparison = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert comparison["new"] == ["tests/test_new.py::test_new"]


def test_compare_accepts_known_failures_and_current_collection_delta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    baseline = _load_baseline_script()
    from dan.migration.test_safety import SafetyClassification

    nodes = ("tests/test_one.py::test_one", "tests/test_two.py::test_two")
    report = tmp_path / "reference.json"
    report.write_text(json.dumps(_valid_report(failures=[nodes[0]])), encoding="utf-8")
    report.chmod(0o400)
    monkeypatch.setenv("DAN_TEST_REPORT_HOME", str(tmp_path / "output"))
    monkeypatch.setattr(baseline, "collect_node_ids", lambda *args, **kwargs: nodes)
    monkeypatch.setattr(
        baseline,
        "classify_node_ids",
        lambda *args, **kwargs: {
            node_id: SafetyClassification(node_id, "isolated") for node_id in nodes
        },
    )
    monkeypatch.setattr(baseline, "scan_node_ids", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        baseline.subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(
            argv, 1, stdout=f"FAILED {nodes[0]} - AssertionError\n", stderr=""
        ),
    )

    assert baseline.main(["--compare", str(report)]) == 0
    comparison = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert comparison == {"new": [], "removed": [], "unchanged": [nodes[0]]}
    current = json.loads(
        (tmp_path / "output" / ".dan" / "migration" / "test-baseline.json").read_text(
            encoding="utf-8"
        )
    )
    assert current["expected_collected"] == 2
    assert current["collected"] == 2


@pytest.mark.parametrize(
    "conflicting_args",
    (
        ("--verify-report", "{report}", "--compare", "{report}"),
        ("--compare-reports", "{report}", "{report}", "--compare", "{report}"),
        (
            "--verify-report",
            "{report}",
            "--compare-reports",
            "{report}",
            "{report}",
        ),
        ("--verify-report", "{report}", "--expect-collected", "1"),
        ("--verify-report", "{report}", "--compare-report", "{output}"),
        (
            "--compare-reports",
            "{report}",
            "{report}",
            "--compare-report",
            "{output}",
        ),
    ),
)
def test_cli_rejects_conflicting_operational_modes_before_shortcuts(
    tmp_path: Path,
    conflicting_args: tuple[str, ...],
) -> None:
    report = tmp_path / "baseline.json"
    report.write_text(json.dumps(_valid_report()), encoding="utf-8")
    report.chmod(0o600)
    output = tmp_path / "comparison.json"
    argv = [argument.format(report=report, output=output) for argument in conflicting_args]

    completed = subprocess.run(
        [sys.executable, "scripts/dan-test-baseline", *argv],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 2
    assert (
        "not allowed with argument" in completed.stderr
        or "require a baseline run" in completed.stderr
    )


def test_baseline_refuses_collection_mismatch_before_pytest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = _load_baseline_script()
    node_id = "tests/test_one.py::test_one"
    monkeypatch.setenv("DAN_TEST_REPORT_HOME", str(tmp_path))
    monkeypatch.setattr(baseline, "collect_node_ids", lambda *args, **kwargs: (node_id,))
    from dan.migration.test_safety import SafetyClassification

    monkeypatch.setattr(
        baseline,
        "classify_node_ids",
        lambda *args, **kwargs: {node_id: SafetyClassification(node_id, "isolated")},
    )
    monkeypatch.setattr(baseline, "scan_node_ids", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        baseline.subprocess, "run", lambda *args, **kwargs: pytest.fail("pytest ran")
    )
    assert baseline.main(["--expect-collected", "2"]) == 2


def test_test_environment_is_minimal_and_blocks_ambient_python_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dan.migration.test_safety as safety

    monkeypatch.setenv("PYTHONPATH", "/private/ambient-python")
    monkeypatch.setenv("PYTHONUSERBASE", "/private/ambient-user-site")
    monkeypatch.setenv("UNRELATED_SECRET", "must-not-reach-child")
    monkeypatch.setenv("PATH", "/private/controlled-toolchain")
    environment = safety.test_environment(
        tmp_path / "home", tmp_path / "runtime", tmp_path / "state" / "dan.db"
    )

    assert environment["DAN_TEST_MODE"] == "1"
    assert environment["DAN_DISABLE_AUDIO"] == "1"
    assert environment["DAN_DISABLE_MIC"] == "1"
    assert environment["HOME"] == str(tmp_path / "home")
    assert environment["PYTHONNOUSERSITE"] == "1"
    assert environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
    assert environment["PATH"] == "/private/controlled-toolchain"
    assert environment["DAN_DB_PATH"] == str(tmp_path / "state" / "dan.db")
    assert "JARVIS_DB_PATH" not in environment
    assert "PYTHONPATH" not in environment
    assert "PYTHONUSERBASE" not in environment
    assert "UNRELATED_SECRET" not in environment


def test_collection_uses_isolated_controlled_interpreter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dan.migration.test_safety as safety

    seen: dict[str, object] = {}

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen["argv"] = argv
        seen["env"] = kwargs["env"]
        return subprocess.CompletedProcess(
            argv, 0, stdout="tests/test_live.py::test_hardware\n", stderr=""
        )

    monkeypatch.setattr(safety.subprocess, "run", fake_run)
    environment = safety.test_environment(
        tmp_path / "home", tmp_path / "runtime", tmp_path / "state" / "dan.db"
    )

    assert safety.collect_node_ids(tmp_path, env=environment) == (
        "tests/test_live.py::test_hardware",
    )
    assert seen["argv"][:3] == [sys.executable, "-I", "-S"]
    assert seen["env"] == environment
