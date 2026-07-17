"""H2 legacy DAN cleanup helpers — DIAGNOSE-ONLY (MASTER_PLAN §5 FAZA H).

The report tool inventories what the legacy DAN install left behind
(processes, launch agents, repo, /tmp droppings, model caches) so Ozzy
has a ready decision list. Decree: it never deletes anything — DAN keeps
running until Ozzy retires it by hand.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

from dan.diagnostics.legacy_dan import (
    Finding,
    collect_findings,
    main,
    match_process_lines,
    render_json,
    render_text,
)

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "dan" / "diagnostics" / "legacy_dan.py"


def _plant_artifacts(home: Path, tmp: Path) -> None:
    repo = home / "Documents" / "dev" / "dan"
    (repo / ".venv").mkdir(parents=True)
    (repo / "dan_core").mkdir()
    (repo / "dan_core" / "voice.py").write_text("x" * 1024, encoding="utf-8")
    (repo / ".venv" / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")

    agents = home / "Library" / "LaunchAgents"
    agents.mkdir(parents=True)
    (agents / "com.dan.voice-broker.plist").write_text("<plist/>", encoding="utf-8")

    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / "dan-voice-queue").mkdir()
    (tmp / "dan-panel.log").write_text("log\n", encoding="utf-8")

    hub = home / ".cache" / "huggingface" / "hub"
    (hub / "models--ResembleAI--chatterbox").mkdir(parents=True)
    (hub / "models--ResembleAI--chatterbox" / "weights.pt").write_text(
        "w" * 2048, encoding="utf-8"
    )
    (hub / "models--litmudoc--Chatterbox-Multilingual-MLX-v2-fp16").mkdir()

    tts = home / "Library" / "Application Support" / "tts"
    tts.mkdir(parents=True)
    (tts / "model.pth").write_text("m" * 512, encoding="utf-8")


class TestCollectFindings:
    def test_reports_all_expected_categories(self, tmp_path: Path) -> None:
        home, tmp = tmp_path / "home", tmp_path / "tmp"
        _plant_artifacts(home, tmp)

        findings = collect_findings(home=home, tmp_dir=tmp, ps_lines=[])
        categories = {finding.category for finding in findings}

        assert {
            "process",
            "launch_agent",
            "repo",
            "tmp_file",
            "hf_model",
            "tts_model",
            "xtts_venv",
        } <= categories

    def test_present_artifacts_have_existing_paths_and_sizes(self, tmp_path: Path) -> None:
        home, tmp = tmp_path / "home", tmp_path / "tmp"
        _plant_artifacts(home, tmp)

        findings = collect_findings(home=home, tmp_dir=tmp, ps_lines=[])
        by_label = {finding.label: finding for finding in findings}

        repo = by_label["Repo legacy DAN"]
        assert repo.exists
        assert repo.size_bytes is not None and repo.size_bytes >= 1024

        plists = [f for f in findings if f.category == "launch_agent" and f.exists]
        assert [Path(f.path).name for f in plists] == ["com.dan.voice-broker.plist"]

        tmp_files = [f for f in findings if f.category == "tmp_file" and f.exists]
        assert sorted(Path(f.path).name for f in tmp_files) == [
            "dan-panel.log",
            "dan-voice-queue",
        ]

    def test_legacy_ozzy_jarvis_plist_is_covered_via_supervisor_registry(
        self, tmp_path: Path
    ) -> None:
        # dan.runtime.supervisor is the source of truth for known legacy
        # names; com.ozzy.jarvis.plist has no "dan" in it and only that
        # registry knows it is legacy.
        home, tmp = tmp_path / "home", tmp_path / "tmp"
        tmp.mkdir()
        agents = home / "Library" / "LaunchAgents"
        agents.mkdir(parents=True)
        (agents / "com.ozzy.jarvis.plist").write_text("<plist/>", encoding="utf-8")

        findings = collect_findings(home=home, tmp_dir=tmp, ps_lines=[])
        plists = [f for f in findings if f.category == "launch_agent" and f.exists]

        assert [Path(f.path).name for f in plists] == ["com.ozzy.jarvis.plist"]

    def test_missing_artifacts_still_listed_as_absent(self, tmp_path: Path) -> None:
        home, tmp = tmp_path / "home", tmp_path / "tmp"
        home.mkdir()
        tmp.mkdir()

        findings = collect_findings(home=home, tmp_dir=tmp, ps_lines=[])

        # The checklist keeps every category visible even when clean, so the
        # report doubles as a "nothing left" confirmation on decision day.
        assert findings, "empty checklist would hide what was checked"
        assert all(not finding.exists for finding in findings)
        repo = [f for f in findings if f.category == "repo"]
        assert repo and repo[0].exists is False

    def test_mlx_chatterbox_flagged_as_jarvis_asset_not_dan_leftover(
        self, tmp_path: Path
    ) -> None:
        home, tmp = tmp_path / "home", tmp_path / "tmp"
        _plant_artifacts(home, tmp)

        findings = collect_findings(home=home, tmp_dir=tmp, ps_lines=[])
        hf = {Path(f.path).name: f for f in findings if f.category == "hf_model"}

        dan_model = hf["models--ResembleAI--chatterbox"]
        jarvis_model = hf["models--litmudoc--Chatterbox-Multilingual-MLX-v2-fp16"]
        assert "MLX" not in dan_model.note
        assert "Jarvis" in jarvis_model.note
        assert "nie kasować" in jarvis_model.note.lower()


class TestProcessMatcher:
    def test_matches_dan_signatures_only(self) -> None:
        lines = [
            "123 /usr/bin/python3 dan_core/voice_broker.py",
            "124 /opt/xtts-venv/bin/python -m xtts.server",
            "125 python chatterbox_stream.py",
            "126 claude --allow-dangerously-skip-permissions --model claude-fable-5",
            "127 /usr/sbin/distnoted agent",
            "128 python -m dan.diagnostics.legacy_dan",
        ]

        matched = match_process_lines(lines)

        assert [line.split()[0] for line in matched] == ["123", "124", "125"]

    def test_matches_supervisor_registry_patterns(self) -> None:
        # Script names known to dan.runtime.supervisor (e.g. auto_jarvis.py)
        # must match even though they carry no dan/xtts/chatterbox substring.
        lines = ["321 /usr/bin/python3 auto_jarvis.py --loop"]

        assert match_process_lines(lines) == [lines[0]]


class TestRendering:
    def test_text_report_leads_with_diagnose_only_disclaimer(self, tmp_path: Path) -> None:
        home, tmp = tmp_path / "home", tmp_path / "tmp"
        _plant_artifacts(home, tmp)
        findings = collect_findings(home=home, tmp_dir=tmp, ps_lines=[])

        text = render_text(findings)

        lowered = text.lower()
        assert "diagnose-only" in lowered
        assert "niczego nie kasuje" in lowered
        assert "com.dan.voice-broker.plist" in text
        assert "GiB" in text or "MiB" in text or "KiB" in text or "B" in text

    def test_totals_skip_informational_and_split_out_jarvis_assets(self) -> None:
        findings = [
            Finding("repo", "Repo legacy DAN", "/x", True, 1000),
            Finding(
                "repo", "Venv w repo DAN", "/x/.venv", True, 400, informational=True
            ),
            Finding("hf_model", "MLX", "/y", True, 300, jarvis_asset=True),
        ]

        text = render_text(findings)

        assert "Łącznie na dysku: 1.3 KiB" in text
        assert "Z tego zasoby Jarvisa (nie kasować): 300 B" in text
        assert "Kandydat do zwolnienia decyzją Ozzy'ego: 1000 B" in text

    def test_json_report_round_trips(self, tmp_path: Path) -> None:
        home, tmp = tmp_path / "home", tmp_path / "tmp"
        _plant_artifacts(home, tmp)
        findings = collect_findings(home=home, tmp_dir=tmp, ps_lines=[])

        payload = json.loads(render_json(findings))

        assert payload["diagnose_only"] is True
        assert isinstance(payload["findings"], list)
        labels = {entry["label"] for entry in payload["findings"]}
        assert "Repo legacy DAN" in labels

    def test_main_json_flag_prints_json(self, tmp_path: Path, capsys) -> None:
        home, tmp = tmp_path / "home", tmp_path / "tmp"
        home.mkdir()
        tmp.mkdir()

        code = main(["--json", "--home", str(home), "--tmp-dir", str(tmp), "--no-ps"])

        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["diagnose_only"] is True


class TestDiagnoseOnlyContract:
    """The decree is structural: the module must be incapable of deleting.
    Source contract, same idiom as the panel lazy-import guard."""

    def test_source_has_no_destructive_calls(self) -> None:
        source = MODULE.read_text(encoding="utf-8")

        for snippet in (
            "rmtree",
            "unlink",
            ".remove",
            "rmdir",
            "os.kill",
            "terminate",
            "pkill",
            "launchctl",
            "send2trash",
            "shutil.move",
            "rename(",
            "write_text",
            "write_bytes",
            'open(',
        ):
            assert snippet not in source, snippet

    def test_subprocess_is_used_for_ps_only(self) -> None:
        source = MODULE.read_text(encoding="utf-8")

        run_calls = [line.strip() for line in source.splitlines() if "subprocess" in line]
        assert all(
            "import subprocess" in line or '"ps"' in line for line in run_calls
        ), run_calls


class TestLauncher:
    def test_wrapper_script_execs_report_module(self) -> None:
        launcher = ROOT / "scripts" / "jarvis-dan-report"
        text = launcher.read_text(encoding="utf-8")

        assert "dan.diagnostics.legacy_dan" in text
        assert launcher.stat().st_mode & stat.S_IXUSR
