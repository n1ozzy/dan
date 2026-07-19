"""Contracts for the shared Release 1 evidence envelope."""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Iterator, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

import dan.release.evidence as evidence_module
from dan.release.evidence import (
    ActiveEvidenceRoots,
    EvidenceInput,
    InvalidEvidenceEnvelope,
    ReleaseEvidenceEnvelope,
    UnsafeEvidenceRoot,
    canonical_envelope_sha256,
    read_evidence_envelope,
    validate_evidence_root,
    write_evidence_envelope_exclusive,
)
from dan.release.producer_ids import (
    BATCH_REPORT_PRODUCER_IDS,
    DEPLOYMENT_RECEIPT_PRODUCER_ID,
    RELEASE_AUDIT_PRODUCER_ID,
    RELEASE_BUILD_GATE_PRODUCER_ID,
    RELEASE_CHECKPOINT_PRODUCER_ID,
    RELEASE_PRODUCER_IDS,
    REVIEW_EVIDENCE_PRODUCER_ID,
    ROLLBACK_REHEARSAL_PRODUCER_ID,
    TEST_BASELINE_PRODUCER_ID,
    VOICE_ACCEPTANCE_PRODUCER_ID,
)


def _active_roots(tmp_path: Path) -> ActiveEvidenceRoots:
    protected = tmp_path / "protected"
    return ActiveEvidenceRoots(
        repo=protected / "repo",
        home_dan=protected / "home/.dan",
        home_config=protected / "home/.config",
        home_claude=protected / "home/.claude",
        dan_config=protected / "home/.dan/config.toml",
        voice_config=protected / "home/.config/voice",
        runtime=protected / "home/.dan/runtime",
        database=protected / "home/.dan/dan.sqlite3",
    )


def _envelope() -> ReleaseEvidenceEnvelope:
    envelope = ReleaseEvidenceEnvelope(
        schema_version=1,
        kind="fixture",
        producer_id="fixture:v1",
        created_at_utc="2026-07-19T12:34:56+00:00",
        subject_sha="a" * 40,
        artifact_sha256=None,
        status="green",
        finding_codes=(),
        unknown_evidence=(),
        input_evidence=(EvidenceInput(role="input", sha256="b" * 64),),
        result={"ok": True, "rows": [1, "two"]},
        report_sha256="",
    )
    return replace(envelope, report_sha256=canonical_envelope_sha256(envelope))


def test_release_producer_ids_are_fixed_and_central() -> None:
    assert dict(RELEASE_PRODUCER_IDS) == {
        "release_checkpoint": "dan-release-checkpoint:v1",
        "baseline_v2": "dan-test-baseline:v2",
        "batch1_data_cutover": "dan-release-report:batch1_data_cutover:v1",
        "batch2_runtime_host": "dan-release-report:batch2_runtime_host:v1",
        "batch3_persona_config_voice": "dan-release-report:batch3_persona_config_voice:v1",
        "batch4_panel_test_release": "dan-release-report:batch4_panel_test_release:v1",
        "offline_clean_clone_build": "dan-release-build-gate:v1",
        "active_home_release_audit": "dan-release-audit:v2",
        "deployment_receipt": "dan-deployment-receipt:v1",
        "rollback_rehearsal": "dan-cutover-rehearsal:v1",
        "voice_acceptance_m5": "dan-voice-acceptance:v2",
        "agent_review_summary": "dan-review-evidence:v1",
    }
    assert tuple(BATCH_REPORT_PRODUCER_IDS) == (
        "batch1_data_cutover",
        "batch2_runtime_host",
        "batch3_persona_config_voice",
        "batch4_panel_test_release",
    )
    assert RELEASE_PRODUCER_IDS["release_checkpoint"] == RELEASE_CHECKPOINT_PRODUCER_ID
    assert RELEASE_PRODUCER_IDS["baseline_v2"] == TEST_BASELINE_PRODUCER_ID
    assert RELEASE_PRODUCER_IDS["deployment_receipt"] == DEPLOYMENT_RECEIPT_PRODUCER_ID
    assert RELEASE_PRODUCER_IDS["offline_clean_clone_build"] == RELEASE_BUILD_GATE_PRODUCER_ID
    assert RELEASE_PRODUCER_IDS["voice_acceptance_m5"] == VOICE_ACCEPTANCE_PRODUCER_ID
    assert RELEASE_PRODUCER_IDS["active_home_release_audit"] == RELEASE_AUDIT_PRODUCER_ID
    assert RELEASE_PRODUCER_IDS["rollback_rehearsal"] == ROLLBACK_REHEARSAL_PRODUCER_ID
    assert RELEASE_PRODUCER_IDS["agent_review_summary"] == REVIEW_EVIDENCE_PRODUCER_ID


def test_evidence_root_rejects_protected_or_symlinked_ancestry(tmp_path: Path) -> None:
    active = _active_roots(tmp_path)
    active.repo.mkdir(parents=True)
    active.home_dan.mkdir(parents=True)
    active.home_config.mkdir(parents=True)
    active.home_claude.mkdir(parents=True)

    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    alias = tmp_path / "outside-alias"
    alias.symlink_to(outside, target_is_directory=True)

    protected_roots = (
        active.repo,
        active.home_dan,
        active.home_config,
        active.home_claude,
        active.dan_config.parent,
        active.voice_config.parent,
        active.runtime.parent,
        active.database.parent,
        alias,
    )
    for root in protected_roots:
        root.mkdir(parents=True, exist_ok=True) if not root.is_symlink() else None
        with pytest.raises(UnsafeEvidenceRoot):
            validate_evidence_root(root, active_roots=active)


def test_evidence_writer_is_exclusive_0600_fsynced_and_strictly_parseable(
    tmp_path: Path,
) -> None:
    root = tmp_path / "evidence"
    root.mkdir(mode=0o700)
    validated = validate_evidence_root(root, active_roots=_active_roots(tmp_path))
    output = root / "report.json"
    envelope = _envelope()

    write_evidence_envelope_exclusive(output, envelope, evidence_root=validated)

    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert read_evidence_envelope(
        output,
        evidence_root=validated,
        expected_kind="fixture",
        expected_producer_id="fixture:v1",
    ) == envelope
    with pytest.raises(FileExistsError):
        write_evidence_envelope_exclusive(output, envelope, evidence_root=validated)


def test_strict_reader_rejects_duplicate_keys_noncanonical_bytes_and_bad_hash(
    tmp_path: Path,
) -> None:
    root = tmp_path / "evidence"
    root.mkdir(mode=0o700)
    validated = validate_evidence_root(root, active_roots=_active_roots(tmp_path))
    envelope = _envelope()
    valid_path = root / "valid.json"
    write_evidence_envelope_exclusive(valid_path, envelope, evidence_root=validated)
    parsed = json.loads(valid_path.read_text(encoding="utf-8"))

    duplicate = root / "duplicate.json"
    duplicate.write_text('{"schema_version":1,"schema_version":1}\n', encoding="utf-8")
    os.chmod(duplicate, 0o600)
    with pytest.raises(InvalidEvidenceEnvelope):
        read_evidence_envelope(duplicate, evidence_root=validated, expected_kind="fixture")

    noncanonical = root / "noncanonical.json"
    noncanonical.write_text(json.dumps(parsed, indent=2) + "\n", encoding="utf-8")
    os.chmod(noncanonical, 0o600)
    with pytest.raises(InvalidEvidenceEnvelope, match="canonical"):
        read_evidence_envelope(noncanonical, evidence_root=validated, expected_kind="fixture")

    bad_hash = root / "bad-hash.json"
    parsed["report_sha256"] = "0" * 64
    bad_hash.write_text(
        json.dumps(parsed, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    os.chmod(bad_hash, 0o600)
    with pytest.raises(InvalidEvidenceEnvelope, match="report_sha256"):
        read_evidence_envelope(bad_hash, evidence_root=validated, expected_kind="fixture")


def test_writer_refuses_output_outside_validated_root_or_symlink_parent(
    tmp_path: Path,
) -> None:
    root = tmp_path / "evidence"
    root.mkdir(mode=0o700)
    validated = validate_evidence_root(root, active_roots=_active_roots(tmp_path))
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(UnsafeEvidenceRoot):
        write_evidence_envelope_exclusive(
            outside / "report.json", _envelope(), evidence_root=validated
        )

    real_parent = root / "real"
    real_parent.mkdir()
    linked_parent = root / "linked"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    with pytest.raises(UnsafeEvidenceRoot):
        write_evidence_envelope_exclusive(
            linked_parent / "report.json", _envelope(), evidence_root=validated
        )


def test_parsed_envelope_result_is_deeply_immutable(tmp_path: Path) -> None:
    root = tmp_path / "evidence"
    root.mkdir(mode=0o700)
    validated = validate_evidence_root(root, active_roots=_active_roots(tmp_path))
    base = _envelope()
    nested = replace(
        base,
        result={"nested": {"rows": [1, {"ok": True}]}},
        report_sha256="",
    )
    nested = replace(nested, report_sha256=canonical_envelope_sha256(nested))
    output = root / "immutable.json"
    write_evidence_envelope_exclusive(output, nested, evidence_root=validated)

    parsed = read_evidence_envelope(
        output,
        evidence_root=validated,
        expected_kind="fixture",
    )
    with pytest.raises(TypeError):
        parsed.result["new"] = True  # type: ignore[index]
    nested_result = parsed.result["nested"]
    assert isinstance(nested_result, Mapping)
    with pytest.raises(TypeError):
        nested_result["new"] = True  # type: ignore[index]
    rows = nested_result["rows"]
    assert isinstance(rows, tuple)
    with pytest.raises(AttributeError):
        rows.append(2)  # type: ignore[attr-defined]
    assert parsed.report_sha256 == canonical_envelope_sha256(parsed)


class _ChangingMapping(Mapping[str, Any]):
    def __init__(self) -> None:
        self.iterations = 0

    def __getitem__(self, key: str) -> object:
        if key != "value":
            raise KeyError(key)
        return self.iterations

    def __iter__(self) -> Iterator[str]:
        self.iterations += 1
        return iter(("value",))

    def __len__(self) -> int:
        return 1


def test_envelope_construction_freezes_one_stable_mapping_snapshot(
    tmp_path: Path,
) -> None:
    changing = _ChangingMapping()
    envelope = replace(_envelope(), result=changing, report_sha256="")
    digest = canonical_envelope_sha256(envelope)
    envelope = replace(envelope, report_sha256=digest)
    root = tmp_path / "evidence"
    root.mkdir(mode=0o700)
    validated = validate_evidence_root(root, active_roots=_active_roots(tmp_path))
    output = root / "stable.json"
    write_evidence_envelope_exclusive(output, envelope, evidence_root=validated)
    assert changing.iterations == 1
    assert read_evidence_envelope(
        output,
        evidence_root=validated,
        expected_kind="fixture",
    ).result["value"] == 1


def test_reader_uses_the_opened_inode_when_leaf_path_is_swapped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "evidence"
    root.mkdir(mode=0o700)
    validated = validate_evidence_root(root, active_roots=_active_roots(tmp_path))
    source = root / "source.json"
    write_evidence_envelope_exclusive(source, _envelope(), evidence_root=validated)

    outside = tmp_path / "outside.json"
    outside_envelope = replace(_envelope(), subject_sha="c" * 40, report_sha256="")
    outside_envelope = replace(
        outside_envelope,
        report_sha256=canonical_envelope_sha256(outside_envelope),
    )
    outside.write_text(
        json.dumps(
            {
                "artifact_sha256": outside_envelope.artifact_sha256,
                "created_at_utc": outside_envelope.created_at_utc,
                "finding_codes": list(outside_envelope.finding_codes),
                "input_evidence": [
                    {"role": item.role, "sha256": item.sha256}
                    for item in outside_envelope.input_evidence
                ],
                "kind": outside_envelope.kind,
                "producer_id": outside_envelope.producer_id,
                "report_sha256": outside_envelope.report_sha256,
                "result": dict(outside_envelope.result),
                "schema_version": outside_envelope.schema_version,
                "status": outside_envelope.status,
                "subject_sha": outside_envelope.subject_sha,
                "unknown_evidence": list(outside_envelope.unknown_evidence),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    os.chmod(outside, 0o600)
    original_open = os.open
    swapped = False

    def swapping_open(
        path: os.PathLike[str] | str,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal swapped
        descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
        if path == source.name and dir_fd is not None and not swapped:
            swapped = True
            source.rename(root / "opened-source.json")
            source.symlink_to(outside)
        return descriptor

    monkeypatch.setattr(os, "open", swapping_open)
    parsed = read_evidence_envelope(
        source,
        evidence_root=validated,
        expected_kind="fixture",
    )
    assert swapped
    assert parsed.subject_sha == "a" * 40


def test_writer_cannot_be_redirected_by_parent_symlink_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "evidence"
    root.mkdir(mode=0o700)
    parent = root / "parent"
    parent.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    validated = validate_evidence_root(root, active_roots=_active_roots(tmp_path))
    original_open = os.open
    swapped = False

    def swapping_open(
        path: os.PathLike[str] | str,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal swapped
        descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
        if (
            path == parent.name
            and flags & getattr(os, "O_DIRECTORY", 0)
            and dir_fd is not None
            and not swapped
        ):
            swapped = True
            parent.rename(root / "moved-parent")
            parent.symlink_to(outside, target_is_directory=True)
        return descriptor

    monkeypatch.setattr(os, "open", swapping_open)
    write_evidence_envelope_exclusive(
        parent / "report.json",
        _envelope(),
        evidence_root=validated,
    )
    assert swapped
    assert not (outside / "report.json").exists()
    assert (root / "moved-parent/report.json").exists()


def test_writer_publishes_only_after_private_temp_is_complete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "evidence"
    root.mkdir(mode=0o700)
    validated = validate_evidence_root(root, active_roots=_active_roots(tmp_path))
    output = root / "report.json"
    original_link = os.link
    original_fsync = os.fsync
    fsync_events: list[str] = []
    publish_calls = 0

    def recording_fsync(descriptor: int) -> None:
        mode = os.fstat(descriptor).st_mode
        kind = "directory" if stat.S_ISDIR(mode) else "file"
        fsync_events.append(kind)
        original_fsync(descriptor)

    def observing_link(
        source: str | bytes,
        destination: str | bytes,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> None:
        nonlocal publish_calls
        publish_calls += 1
        assert destination == output.name
        assert not output.exists()
        assert fsync_events == ["file"]
        details = os.stat(source, dir_fd=src_dir_fd, follow_symlinks=False)
        assert stat.S_ISREG(details.st_mode)
        assert stat.S_IMODE(details.st_mode) == 0o600
        original_link(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )

    monkeypatch.setattr(os, "fsync", recording_fsync)
    monkeypatch.setattr(os, "link", observing_link)
    write_evidence_envelope_exclusive(output, _envelope(), evidence_root=validated)

    assert publish_calls == 1
    assert fsync_events == ["file", "directory"]
    assert [entry.name for entry in root.iterdir()] == [output.name]


def test_writer_retries_short_writes_before_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "evidence"
    root.mkdir(mode=0o700)
    validated = validate_evidence_root(root, active_roots=_active_roots(tmp_path))
    output = root / "short.json"
    original_write = os.write
    calls = 0
    visibility: list[bool] = []

    def short_write(descriptor: int, payload: bytes) -> int:
        nonlocal calls
        calls += 1
        visibility.append(output.exists())
        length = max(1, len(payload) // 2)
        return original_write(descriptor, payload[:length])

    monkeypatch.setattr(os, "write", short_write)
    write_evidence_envelope_exclusive(output, _envelope(), evidence_root=validated)

    assert calls > 1
    assert visibility and not any(visibility)
    assert read_evidence_envelope(
        output,
        evidence_root=validated,
        expected_kind="fixture",
    ) == _envelope()
    assert [entry.name for entry in root.iterdir()] == [output.name]


@pytest.mark.parametrize("failure", ("zero", "raise"))
def test_writer_cleans_up_failed_writes_and_same_name_retry_succeeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    root = tmp_path / "evidence"
    root.mkdir(mode=0o700)
    validated = validate_evidence_root(root, active_roots=_active_roots(tmp_path))
    output = root / f"{failure}.json"
    original_write = os.write
    calls = 0
    visibility: list[bool] = []

    def one_shot_failed_write(descriptor: int, payload: bytes) -> int:
        nonlocal calls
        calls += 1
        visibility.append(output.exists())
        if calls == 1:
            if failure == "zero":
                return 0
            raise OSError("forced write failure")
        return original_write(descriptor, payload)

    monkeypatch.setattr(os, "write", one_shot_failed_write)
    message = "no progress" if failure == "zero" else "forced write failure"
    with pytest.raises(OSError, match=message):
        write_evidence_envelope_exclusive(
            output,
            _envelope(),
            evidence_root=validated,
        )

    assert calls == 1
    assert visibility == [False]
    assert not output.exists()
    assert list(root.iterdir()) == []

    write_evidence_envelope_exclusive(output, _envelope(), evidence_root=validated)
    assert read_evidence_envelope(
        output,
        evidence_root=validated,
        expected_kind="fixture",
    ) == _envelope()
    assert [entry.name for entry in root.iterdir()] == [output.name]


@pytest.mark.parametrize("fail_kind", ("file", "directory"))
def test_writer_rolls_back_one_shot_fsync_failure_and_retry_succeeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fail_kind: str,
) -> None:
    root = tmp_path / "evidence"
    root.mkdir(mode=0o700)
    validated = validate_evidence_root(root, active_roots=_active_roots(tmp_path))
    output = root / f"{fail_kind}.json"
    original_fsync = os.fsync
    events: list[tuple[str, bool]] = []
    failed = False

    def one_shot_fsync(descriptor: int) -> None:
        nonlocal failed
        mode = os.fstat(descriptor).st_mode
        kind = "directory" if stat.S_ISDIR(mode) else "file"
        events.append((kind, output.exists()))
        if kind == fail_kind and not failed:
            failed = True
            raise OSError(f"forced {kind} fsync failure")
        original_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", one_shot_fsync)
    with pytest.raises(OSError, match=fail_kind):
        write_evidence_envelope_exclusive(
            output,
            _envelope(),
            evidence_root=validated,
        )

    assert failed
    assert ("file", False) in events
    assert not output.exists()
    assert list(root.iterdir()) == []

    write_evidence_envelope_exclusive(output, _envelope(), evidence_root=validated)
    assert read_evidence_envelope(
        output,
        evidence_root=validated,
        expected_kind="fixture",
    ) == _envelope()
    assert [entry.name for entry in root.iterdir()] == [output.name]


def test_writer_does_not_publish_when_private_temp_close_fails_and_retry_succeeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "evidence"
    root.mkdir(mode=0o700)
    validated = validate_evidence_root(root, active_roots=_active_roots(tmp_path))
    output = root / "close.json"
    original_close = os.close
    original_fstat = os.fstat
    close_failure_fired = False
    visibility: list[bool] = []

    def one_shot_temp_close_failure(descriptor: int) -> None:
        nonlocal close_failure_fired
        details = original_fstat(descriptor)
        if stat.S_ISREG(details.st_mode) and not close_failure_fired:
            close_failure_fired = True
            visibility.append(output.exists())
            original_close(descriptor)
            raise OSError("forced private temp close failure")
        original_close(descriptor)

    monkeypatch.setattr(os, "close", one_shot_temp_close_failure)
    with pytest.raises(OSError, match="private temp close failure"):
        write_evidence_envelope_exclusive(
            output,
            _envelope(),
            evidence_root=validated,
        )

    assert close_failure_fired
    assert visibility == [False]
    assert not output.exists()
    assert list(root.iterdir()) == []

    write_evidence_envelope_exclusive(output, _envelope(), evidence_root=validated)
    assert read_evidence_envelope(
        output,
        evidence_root=validated,
        expected_kind="fixture",
    ) == _envelope()
    assert [entry.name for entry in root.iterdir()] == [output.name]


def test_writer_preserves_primary_failure_when_cleanup_close_also_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "evidence"
    root.mkdir(mode=0o700)
    validated = validate_evidence_root(root, active_roots=_active_roots(tmp_path))
    output = root / "primary.json"
    root_details = root.stat()
    root_identity = (root_details.st_dev, root_details.st_ino)
    original_close = os.close
    original_fstat = os.fstat
    write_failure_fired = False
    close_failure_fired = False
    parent_close_fired = False

    def failing_write(descriptor: int, payload: bytes) -> int:
        del descriptor, payload
        nonlocal write_failure_fired
        write_failure_fired = True
        raise OSError("primary write failure")

    def failing_cleanup_close(descriptor: int) -> None:
        nonlocal close_failure_fired, parent_close_fired
        details = original_fstat(descriptor)
        identity = (details.st_dev, details.st_ino)
        if stat.S_ISREG(details.st_mode) and not close_failure_fired:
            close_failure_fired = True
            original_close(descriptor)
            raise OSError("secondary cleanup close failure")
        if stat.S_ISDIR(details.st_mode) and identity == root_identity:
            parent_close_fired = True
        original_close(descriptor)

    monkeypatch.setattr(os, "write", failing_write)
    monkeypatch.setattr(os, "close", failing_cleanup_close)
    with pytest.raises(OSError, match="primary write failure") as captured:
        write_evidence_envelope_exclusive(
            output,
            _envelope(),
            evidence_root=validated,
        )

    assert write_failure_fired
    assert close_failure_fired
    assert parent_close_fired
    assert any(
        "secondary cleanup close failure" in note
        for note in getattr(captured.value, "__notes__", ())
    )
    assert not output.exists()
    assert list(root.iterdir()) == []


def test_writer_preserves_preexisting_final_and_removes_private_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "evidence"
    root.mkdir(mode=0o700)
    validated = validate_evidence_root(root, active_roots=_active_roots(tmp_path))
    output = root / "historical.json"
    historical = b"historical evidence\n"
    output.write_bytes(historical)
    original_link = os.link
    publish_calls = 0

    def observing_link(
        source: str | bytes,
        destination: str | bytes,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> None:
        nonlocal publish_calls
        publish_calls += 1
        assert output.read_bytes() == historical
        original_link(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )

    monkeypatch.setattr(os, "link", observing_link)
    with pytest.raises(FileExistsError):
        write_evidence_envelope_exclusive(
            output,
            _envelope(),
            evidence_root=validated,
        )

    assert publish_calls == 1
    assert output.read_bytes() == historical
    assert [entry.name for entry in root.iterdir()] == [output.name]


def test_writer_cleans_private_temp_when_initial_fstat_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "evidence"
    root.mkdir(mode=0o700)
    validated = validate_evidence_root(root, active_roots=_active_roots(tmp_path))
    output = root / "fstat.json"
    original_fstat = os.fstat
    failed = False

    def one_shot_fstat(descriptor: int) -> os.stat_result:
        nonlocal failed
        details = original_fstat(descriptor)
        if stat.S_ISREG(details.st_mode) and not failed:
            failed = True
            raise OSError("forced initial fstat failure")
        return details

    monkeypatch.setattr(os, "fstat", one_shot_fstat)
    with pytest.raises(OSError, match="initial fstat"):
        write_evidence_envelope_exclusive(
            output,
            _envelope(),
            evidence_root=validated,
        )

    assert failed
    assert not output.exists()
    assert list(root.iterdir()) == []

    write_evidence_envelope_exclusive(output, _envelope(), evidence_root=validated)
    assert [entry.name for entry in root.iterdir()] == [output.name]


def test_inode_safe_cleanup_preserves_a_replacement_at_the_rename_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "evidence"
    root.mkdir(mode=0o700)
    validated = validate_evidence_root(root, active_roots=_active_roots(tmp_path))
    output = root / "failed.json"
    original_write = os.write
    original_rename = os.rename
    write_calls = 0
    swapped = False
    replacement_name: str | None = None

    def one_shot_failed_write(descriptor: int, payload: bytes) -> int:
        nonlocal write_calls
        write_calls += 1
        if write_calls == 1:
            raise OSError("forced write failure")
        return original_write(descriptor, payload)

    def swapping_rename(
        source: os.PathLike[str] | str,
        destination: os.PathLike[str] | str,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        nonlocal replacement_name, swapped
        if (
            isinstance(source, str)
            and source.startswith(".dan-evidence-")
            and source.endswith(".tmp")
            and not swapped
        ):
            replacement_name = source
            original_rename(
                source,
                "displaced-owned.tmp",
                src_dir_fd=src_dir_fd,
                dst_dir_fd=src_dir_fd,
            )
            replacement = root / source
            replacement.write_bytes(b"attacker replacement\n")
            os.chmod(replacement, 0o600)
            swapped = True
        original_rename(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    monkeypatch.setattr(os, "write", one_shot_failed_write)
    monkeypatch.setattr(os, "rename", swapping_rename)
    with pytest.raises(OSError, match="forced write failure"):
        write_evidence_envelope_exclusive(
            output,
            _envelope(),
            evidence_root=validated,
        )

    assert swapped
    assert replacement_name is not None
    assert (root / replacement_name).read_bytes() == b"attacker replacement\n"
    assert not output.exists()


def test_writer_rolls_back_when_final_parent_close_fails_and_retry_succeeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "evidence"
    root.mkdir(mode=0o700)
    validated = validate_evidence_root(root, active_roots=_active_roots(tmp_path))
    output = root / "parent-close.json"
    root_details = root.stat()
    root_identity = (root_details.st_dev, root_details.st_ino)
    original_close = os.close
    original_fstat = os.fstat
    close_failure_fired = False

    def one_shot_final_parent_close_failure(descriptor: int) -> None:
        nonlocal close_failure_fired
        details = original_fstat(descriptor)
        identity = (details.st_dev, details.st_ino)
        if output.exists() and identity == root_identity and not close_failure_fired:
            close_failure_fired = True
            original_close(descriptor)
            raise OSError("forced final parent close failure")
        original_close(descriptor)

    monkeypatch.setattr(os, "close", one_shot_final_parent_close_failure)
    with pytest.raises(OSError, match="final parent close failure"):
        write_evidence_envelope_exclusive(
            output,
            _envelope(),
            evidence_root=validated,
        )

    assert close_failure_fired
    assert not output.exists()
    assert list(root.iterdir()) == []

    write_evidence_envelope_exclusive(output, _envelope(), evidence_root=validated)
    assert read_evidence_envelope(
        output,
        evidence_root=validated,
        expected_kind="fixture",
    ) == _envelope()
    assert [entry.name for entry in root.iterdir()] == [output.name]


@pytest.mark.parametrize("failure", ("zero", "raise"))
def test_writer_rolls_back_owned_final_hardlinked_during_actual_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    root = tmp_path / "evidence"
    root.mkdir(mode=0o700)
    validated = validate_evidence_root(root, active_roots=_active_roots(tmp_path))
    output = root / "linked-during-write.json"
    original_write = os.write
    injection_fired = False

    def hardlink_then_fail(descriptor: int, payload: bytes) -> int:
        nonlocal injection_fired
        if not injection_fired:
            temporary_paths = sorted(root.glob(".dan-evidence-*.tmp"))
            assert len(temporary_paths) == 1
            os.link(temporary_paths[0], output)
            injection_fired = True
            if failure == "zero":
                return 0
            raise OSError("forced linked write failure")
        return original_write(descriptor, payload)

    monkeypatch.setattr(os, "write", hardlink_then_fail)
    message = "no progress" if failure == "zero" else "linked write failure"
    with pytest.raises(OSError, match=message):
        write_evidence_envelope_exclusive(
            output,
            _envelope(),
            evidence_root=validated,
        )

    assert injection_fired
    assert not output.exists()
    assert list(root.iterdir()) == []

    write_evidence_envelope_exclusive(output, _envelope(), evidence_root=validated)
    assert read_evidence_envelope(
        output,
        evidence_root=validated,
        expected_kind="fixture",
    ) == _envelope()
    assert [entry.name for entry in root.iterdir()] == [output.name]


def test_open_validated_root_closes_exact_descriptor_when_fstat_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "evidence"
    root.mkdir(mode=0o700)
    validated = validate_evidence_root(root, active_roots=_active_roots(tmp_path))
    root_details = root.stat()
    root_identity = (root_details.st_dev, root_details.st_ino)
    original_fstat = os.fstat
    failed_descriptor: int | None = None

    def one_shot_root_fstat(descriptor: int) -> os.stat_result:
        nonlocal failed_descriptor
        details = original_fstat(descriptor)
        if failed_descriptor is None and (details.st_dev, details.st_ino) == root_identity:
            failed_descriptor = descriptor
            raise OSError("forced evidence root fstat failure")
        return details

    monkeypatch.setattr(evidence_module.os, "fstat", one_shot_root_fstat)
    with pytest.raises(OSError, match="evidence root fstat failure"):
        evidence_module._open_validated_root(validated)

    assert failed_descriptor is not None
    with pytest.raises(OSError):
        original_fstat(failed_descriptor)


def test_open_validated_root_preserves_fstat_failure_when_close_also_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "evidence"
    root.mkdir(mode=0o700)
    validated = validate_evidence_root(root, active_roots=_active_roots(tmp_path))
    root_details = root.stat()
    root_identity = (root_details.st_dev, root_details.st_ino)
    original_fstat = os.fstat
    original_close = os.close
    failed_descriptor: int | None = None
    close_failure_fired = False

    def one_shot_root_fstat(descriptor: int) -> os.stat_result:
        nonlocal failed_descriptor
        details = original_fstat(descriptor)
        if failed_descriptor is None and (details.st_dev, details.st_ino) == root_identity:
            failed_descriptor = descriptor
            raise OSError("primary evidence fstat failure")
        return details

    def one_shot_cleanup_close(descriptor: int) -> None:
        nonlocal close_failure_fired
        if descriptor == failed_descriptor and not close_failure_fired:
            close_failure_fired = True
            original_close(descriptor)
            raise OSError("secondary evidence close failure")
        original_close(descriptor)

    monkeypatch.setattr(evidence_module.os, "fstat", one_shot_root_fstat)
    monkeypatch.setattr(evidence_module.os, "close", one_shot_cleanup_close)
    with pytest.raises(OSError, match="primary evidence fstat failure") as captured:
        evidence_module._open_validated_root(validated)

    assert close_failure_fired
    assert any(
        "secondary evidence close failure" in note
        for note in getattr(captured.value, "__notes__", ())
    )
    assert failed_descriptor is not None
    with pytest.raises(OSError):
        original_fstat(failed_descriptor)
