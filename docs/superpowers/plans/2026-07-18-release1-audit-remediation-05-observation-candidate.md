# DAN Release 1 Audit Remediation — Batch 5 Candidate and Observation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the invalid acceptance ledger with repository-owned evidence collection, prove a new immutable candidate is ready, bind observations to its actual deployment, require seven real monotonic calendar dates and two SHA-bound daemon cold starts in distinct login cycles, and stop before final tag or merge until Ozzy signs off.

**Architecture:** The existing `dan.migration.runtime_probe` is extended as the one process probe feeding cutover, inventory, and observation. A versioned evidence envelope binds every batch, rehearsal, live acceptance, review, candidate, and deployment report to the exact release SHA and producer. A hash-chained JSONL ledger derives dates, runtime failures, legacy use, process identity, login cycles, and daemon cold starts from real evidence. Candidate and deployment gates consume SHA-bound reports and emit read-only intent or receipts; they never tag, deploy, or merge. A final validator selects one immutable deployment SHA, requires an external operator sign-off artifact, and refuses branch/ref drift.

**Tech Stack:** Python 3.11+, ps/lsof/launchctl/sysctl read-only probes, SQLite event store, ZoneInfo, JSON/JSONL, SHA-256 hash chains, fcntl file locks, pytest, ruff, git plumbing.

## Global Constraints

- Batch 5 starts only after Batches 0–4 are GREEN and independently approved with no open release debt.
- The existing `dan-v1-foundation-candidate` tag remains unchanged. The next proposed tag is `dan-v1-foundation-candidate.2` unless the read-only gate proves that name already exists, in which case stop and choose a new monotonically increasing suffix with Ozzy.
- Candidate gate, deployment receipt, recorder, and final gate are read-only with respect to git refs, production install, runtime lifecycle, audio, and user sign-off.
- Tag creation, push, deployment, runtime restart, rollback drill, audible M5 acceptance, logout/login cycles, sign-off, final tag, and merge are explicit manual operations.
- The release calendar timezone is fixed to `Europe/Warsaw`. Store timestamps in UTC and derive the calendar date through `ZoneInfo("Europe/Warsaw")`; no CLI day override exists.
- A login cycle is identified by the macOS boot-session UUID plus launchd GUI audit-session ID, not by daemon PID or restart count.
- Probe failure is `unknown/error`, never an empty collection or zero. Unknown evidence fails readiness/final gates.
- `DAN_RELEASE_EVIDENCE_ROOT` is required for every report. It must be absolute and outside the repository, active `~/.dan`, `~/.claude`, and active config/database roots. Automated tests use a fresh `tmp_path` or `mktemp -d`; they never write release evidence to the active HOME.
- Every evidence producer exclusively creates its output, fsyncs the file and parent directory, and records the producer ID, exact subject SHA, input hashes, status, finding codes, and unknown evidence. A caller cannot supply a green status directly.
- Any code or plan implementation commit after a SHA-bound report invalidates that report. After Batch 5 implementation is frozen, rerun every required producer on the final candidate HEAD before evaluating candidate readiness.
- Active-HOME audit and live M5 acceptance are post-deployment evidence: their installed/runtime identity must match the deployment receipt. They cannot be prerequisites for the pre-deployment candidate intent. Observation starts only after both reports are green and bound to the deployed candidate.
- Reports contain hashes, counts, process metadata, finding codes, and redacted paths. They never contain conversation text, memory bodies, tokens, private audio, or secrets.
- Production code uses clear English names and minimal comments; document intent in these plans and release docs instead of narrating obvious code.

---

## Task 5.1: Use one process-evidence probe for inventory and observation

**Files:**

- Modify: `dan/migration/runtime_probe.py` (existing probe used by cutover)
- Modify: `dan/migration/inventory.py`
- Modify: `dan/migration/cutover.py`
- Modify: `dan/migration/cutover_cli.py`
- Create: `tests/test_runtime_probe.py`
- Modify: `tests/test_migration_inventory_review.py`
- Modify: `tests/cutover_helpers.py`
- Modify: `tests/test_cutover_preconditions.py`
- Read for regression: `tests/test_cutover_state_machine.py`, `tests/test_cutover_rollback.py`, and `tests/test_cutover_no_replay.py`

- [ ] **Step 1: Write RED process-evidence tests**

```python
def test_process_probe_records_pid_ppid_argv_cwd_and_ports(probe_fixture: ProbeFixture) -> None:
    evidence = collect_process_evidence(
        runner=probe_fixture.runner,
        cwd_reader=probe_fixture.cwd_reader,
        port_reader=probe_fixture.port_reader,
    )
    assert evidence[0] == ProcessEvidence(
        pid=120,
        ppid=1,
        executable=Path("/Users/test/.dan/venv/bin/python"),
        argv=(
            "/Users/test/.dan/venv/bin/python",
            "-m",
            "dan.cli",
            "daemon",
            "run",
        ),
        cwd=Path("/"),
        listening_ports=(41741,),
        process_started_at_utc="2026-07-18T12:00:00Z",
        role=ProcessRole.DAN,
        probe_status=ProbeStatus.OK,
        errors=(),
    )


def test_probe_error_is_unknown_not_empty(probe_fixture: ProbeFixture) -> None:
    probe_fixture.cwd_reader.fail_for(120)
    evidence = collect_process_evidence(
        runner=probe_fixture.runner,
        cwd_reader=probe_fixture.cwd_reader,
        port_reader=probe_fixture.port_reader,
    )
    assert evidence[0].probe_status is ProbeStatus.ERROR
    assert evidence[0].cwd is None
```

Add a test treating a cwd inside configured legacy backup/migration roots as legacy use and a test proving inventory consumes the shared probe.
Add one named failure test for each executable, argv, cwd, listener, and start-time reader. A failed field is `None`, the error names the failed reader, `probe_status` is `ERROR`, and `role` is `UNKNOWN`; an empty tuple must never masquerade as successful evidence.

- [ ] **Step 2: Verify RED**

```bash
release_test_home="$(mktemp -d /private/tmp/dan-batch5-home.XXXXXX)"
release_test_evidence="$(mktemp -d /private/tmp/dan-batch5-evidence.XXXXXX)"
env HOME="$release_test_home" DAN_RELEASE_EVIDENCE_ROOT="$release_test_evidence" \
  XDG_CACHE_HOME="$release_test_evidence/cache" \
  PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
  DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p tests.audio_guard_plugin -p no:cacheprovider \
  tests/test_runtime_probe.py tests/test_migration_inventory_review.py \
  tests/test_cutover_preconditions.py tests/test_cutover_state_machine.py \
  tests/test_cutover_rollback.py tests/test_cutover_no_replay.py
```

Expected: the existing shared probe lacks the enriched per-field evidence shape and the cutover consumers still rely on the old rendered-command projection.

- [ ] **Step 3: Implement typed, injected probes**

```python
class ProbeStatus(StrEnum):
    OK = "ok"
    UNKNOWN = "unknown"
    ERROR = "error"


class ProcessRole(StrEnum):
    DAN = "dan"
    LEGACY = "legacy"
    UNRELATED = "unrelated"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ProcessEvidence:
    pid: int
    ppid: int | None
    executable: Path | None
    argv: tuple[str, ...] | None
    cwd: Path | None
    listening_ports: tuple[int, ...] | None
    process_started_at_utc: str | None
    role: ProcessRole
    probe_status: ProbeStatus
    errors: tuple[str, ...]
```

Extend `SystemProbe` and its existing `ProbedProcess` contract in place; do not create a parallel probe module or a compatibility path with independent collection logic. Default macOS readers enumerate PID/PPID only, then use `proc_pidpath` for the executable, `sysctl(KERN_PROCARGS2)` for the NUL-delimited argv, and `proc_pidinfo(PROC_PIDTBSDINFO)` for process start time. `lsof -a -p <pid> -d cwd -Fn` supplies cwd and `lsof -Pan -p <pid> -iTCP -sTCP:LISTEN` supplies ports. Rendered `ps command` text may exist only as a display projection and is never parsed or hashed as argv identity. Probe errors are retained per field and fail closed; a failed reader produces `None`, never a synthetic path, timestamp, or empty tuple. Classification is `UNKNOWN` unless every field required for that classification is present. Otherwise it compares resolved executable/argv/cwd paths and expected ports against exact installed-production and legacy roots from the SHA-bound checkpoint and deployment receipt. It does not use an unconstrained substring search. Migrate inventory, cutover orchestration, and cutover CLI projections to this enriched object in the same task so `dan.migration.runtime_probe` remains the sole live probe.

- [ ] **Step 4: Verify GREEN**

```bash
release_test_home="$(mktemp -d /private/tmp/dan-batch5-home.XXXXXX)"
release_test_evidence="$(mktemp -d /private/tmp/dan-batch5-evidence.XXXXXX)"
env HOME="$release_test_home" DAN_RELEASE_EVIDENCE_ROOT="$release_test_evidence" \
  XDG_CACHE_HOME="$release_test_evidence/cache" \
  PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
  DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p tests.audio_guard_plugin -p no:cacheprovider \
  tests/test_runtime_probe.py tests/test_migration_inventory_review.py \
  tests/test_cutover_preconditions.py tests/test_cutover_state_machine.py \
  tests/test_cutover_rollback.py tests/test_cutover_no_replay.py
env RUFF_CACHE_DIR="$release_test_evidence/ruff-cache" \
  .venv/bin/ruff check dan/migration/runtime_probe.py dan/migration/inventory.py \
  dan/migration/cutover.py dan/migration/cutover_cli.py tests/cutover_helpers.py \
  tests/test_runtime_probe.py tests/test_cutover_preconditions.py
git diff --check
```

## Task 5.2: Record real observations in a locked hash-chained ledger

**Files:**

- Create: `dan/release/observation.py`
- Create: `scripts/dan-observe`
- Create: `tests/test_release_observation.py`
- Modify: `dan/daemon/app.py`
- Modify: `dan/daemon/components.py` (`RuntimeComponentOverrides` created by Batch 4)
- Modify: `dan/api/routes_runtime.py`
- Read: `dan/api/client.py` (`DaemonClient.runtime_startup()` created by Batch 4)
- Create: `tests/fakes/runtime.py`
- Modify: `tests/fakes/__init__.py`
- Modify: `tests/test_api_smoke.py`
- Read: `dan/events/types.py` (`DAEMON_STARTED` already exists)
- Read: `dan/release/evidence.py` (Batch 0 envelope validation)

- [ ] **Step 1: Write RED date/metric/chain tests**

```python
def test_day_is_derived_not_supplied_by_cli(cli: ObservationCLI) -> None:
    assert "--day" not in cli.parser_option_strings()
    record = cli.collect(now=datetime(2026, 7, 18, 22, 30, tzinfo=timezone.utc))
    assert record.calendar_date == date(2026, 7, 19)


def test_ledger_rejects_second_entry_same_calendar_day(ledger: ObservationLedger) -> None:
    ledger.append(observation(calendar_date=date(2026, 7, 19), deployment_id="d1"))
    with pytest.raises(DuplicateObservationDay):
        ledger.append(observation(calendar_date=date(2026, 7, 19), deployment_id="d1"))


def test_ledger_rejects_a_date_older_than_the_last_deployment_entry(
    ledger: ObservationLedger,
) -> None:
    ledger.append(observation(calendar_date=date(2026, 7, 20), deployment_id="d1"))
    with pytest.raises(NonMonotonicObservationDay):
        ledger.append(observation(calendar_date=date(2026, 7, 19), deployment_id="d1"))


def test_adapter_metrics_are_computed_from_events(store: EventStore) -> None:
    store.append(EventType.BRAIN_FAILED, "claude_cli", {"error_code": "transport"})
    metrics = compute_runtime_failure_metrics(store, since=deployment_time())
    assert metrics.adapter_failures == 1


def test_observation_requires_sha_bound_daemon_start_in_current_login_cycle(
    recorder_fixture: RecorderFixture,
) -> None:
    recorder_fixture.append_daemon_started(release_sha="different", login_cycle_id="cycle-1")
    record = recorder_fixture.collect(deployed_sha="candidate-sha", login_cycle_id="cycle-1")
    assert "missing_matching_daemon_start" in record.unknown_evidence


def test_observation_refuses_missing_post_deploy_acceptance(
    recorder_fixture: RecorderFixture,
) -> None:
    recorder_fixture.remove_report("voice_acceptance_m5")
    with pytest.raises(InvalidDeploymentEvidence):
        recorder_fixture.collect()
    assert recorder_fixture.ledger_entries() == []


def test_daemon_started_binds_installed_release_and_login_cycle(
    installed_app_factory: InstalledAppFactory,
) -> None:
    installed_app = installed_app_factory.create(
        release_identity=runtime_release_identity_fixture(commit_sha="candidate-sha"),
        component_overrides=RuntimeComponentOverrides(
            login_cycle_reader=FakeLoginCycleReader(login_cycle_id="cycle-1"),
        ),
    )
    installed_app.start()
    assert installed_app.event_store is not None
    started = next(
        event
        for event in installed_app.event_store.latest()
        if event.type == "daemon.started"
    )
    assert started.payload["release_sha"] == "candidate-sha"
    assert started.payload["release_status"] == "installed"
    assert started.payload["install_id"] == installed_app.runtime_release_identity.install_id
    assert started.payload["installed_identity_sha256"] == (
        installed_app.runtime_release_identity.canonical_sha256()
    )
    assert started.payload["login_cycle_id"] == "cycle-1"
    assert started.payload["daemon_instance_id"] == installed_app.daemon_instance_id
    assert started.payload["daemon_pid"] == os.getpid()
```

Add tests for legacy-use derivation, probe-error propagation, append preservation, previous-entry hash verification, fsync, concurrent append locking, duplicate daemon-start event IDs, a startup event older than deployment, same SHA with a prior install ID, reused PID with another instance ID, current runtime-startup/event mismatch, exact installed executable mismatch, a daemon start whose login-cycle ID differs from the current cycle, a receipt/report SHA mismatch, wrong producer IDs, non-green or unknown evidence, and audit/voice reports created before deployment. Also prove that changing any stored daemon-event timestamp, release/artifact/manifest/install binding, runtime-startup-response hash, process-start timestamp, executable/argv/cwd hash, listener set, instance ID, or PID breaks canonical record hashing.

- [ ] **Step 2: Verify RED**

```bash
release_test_home="$(mktemp -d /private/tmp/dan-batch5-home.XXXXXX)"
release_test_evidence="$(mktemp -d /private/tmp/dan-batch5-evidence.XXXXXX)"
env HOME="$release_test_home" DAN_RELEASE_EVIDENCE_ROOT="$release_test_evidence" \
  XDG_CACHE_HOME="$release_test_evidence/cache" \
  PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
  DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p tests.audio_guard_plugin -p no:cacheprovider \
  tests/test_release_observation.py \
  tests/test_api_smoke.py::test_daemon_started_binds_installed_release_and_login_cycle
```

- [ ] **Step 3: Implement exact evidence and login-cycle contracts**

```python
RELEASE_FAILURE_TYPES = frozenset({
    EventType.DAEMON_FAILED,
    EventType.TURN_FAILED,
    EventType.BRAIN_FAILED,
    EventType.VOICE_SPEAK_FAILED,
    EventType.TOOL_FAILED,
    EventType.RUNTIME_LEGACY_CONFLICT_DETECTED,
    EventType.ERROR_RAISED,
})


@dataclass(frozen=True)
class StoredRuntimeStartupEvidence:
    release_status: Literal["installed"]
    release_sha: str
    artifact_sha256: str
    install_manifest_sha256: str
    install_id: str
    installed_at_utc: str
    installed_identity_sha256: str
    login_cycle_id: str
    daemon_instance_id: str
    daemon_pid: int
    daemon_start_event_id: int
    response_sha256: str


@dataclass(frozen=True)
class StoredDaemonStartEvidence:
    event_type: Literal["daemon.started"]
    release_status: Literal["installed"]
    event_id: int
    event_created_at_utc: str
    release_sha: str
    artifact_sha256: str
    install_manifest_sha256: str
    install_id: str
    installed_at_utc: str
    installed_identity_sha256: str
    login_cycle_id: str
    daemon_instance_id: str
    daemon_pid: int
    runtime_startup: StoredRuntimeStartupEvidence
    evidence_sha256: str


@dataclass(frozen=True)
class StoredProcessEvidence:
    pid: int
    ppid: int | None
    executable_path_sha256: str
    expected_executable_path_sha256: str
    argv_sha256: str
    expected_argv_sha256: str
    cwd_path_sha256: str | None
    expected_cwd_path_sha256: str | None
    listening_ports: tuple[int, ...]
    expected_listening_ports: tuple[int, ...]
    process_started_at_utc: str
    role: ProcessRole
    probe_status: ProbeStatus
    errors: tuple[str, ...]
    evidence_sha256: str


@dataclass(frozen=True)
class ObservationRecord:
    schema_version: int
    observed_at_utc: str
    calendar_date: str
    release_timezone: str
    deployment_id: str
    deployed_sha: str
    deployment_receipt_sha256: str
    active_home_audit_sha256: str
    voice_acceptance_sha256: str
    login_cycle_id: str
    legacy_runtime_use_count: int
    adapter_failure_count: int
    runtime_failure_count: int
    daemon_start: StoredDaemonStartEvidence
    process: StoredProcessEvidence
    unknown_evidence: tuple[str, ...]
    previous_entry_sha256: str | None
    entry_sha256: str
```

`login_cycle_id` is `sha256(boot_session_uuid + ":" + launchd_gui_asid)`, where values come from injected readers backed by `sysctl -n kern.bootsessionuuid` and `launchctl print gui/<uid>`. Missing values produce unknown evidence.

Do not add another release-identity reader. Batch 2's single startup load into `DaemonApp.runtime_release_identity` remains the only release source of truth for the process. Batch 5 extends the Batch 4 `RuntimeComponentOverrides` with only one typed `login_cycle_reader`; production composition supplies the macOS reader and isolated tests supply `FakeLoginCycleReader` from `tests/fakes/runtime.py`. `DaemonApp` generates one opaque `daemon_instance_id` per process, records its real PID, and retains the appended startup event ID for the lifetime of that process.

At daemon startup, `DaemonApp.start()` appends `DAEMON_STARTED` from the already-loaded release snapshot. For an installed release the payload contains exactly `release_status`, `release_sha`, `artifact_sha256`, `install_manifest_sha256`, `install_id`, `installed_at_utc`, canonical `installed_identity_sha256`, `login_cycle_id`, `daemon_instance_id`, and `daemon_pid`. A present malformed or mismatched `current-release.json` already fails in Batch 2 before the event is written. An absent identity is allowed only as explicit source-checkout development state: the event carries `release_status="unknown"`, the process/cycle fields, and no release/install/hash fields, never the checkout HEAD. Observation cannot accept that event as deployment evidence.

Extend the existing `GET /runtime/startup` projection with the current `daemon_instance_id`, `daemon_pid`, `daemon_start_event_id`, and login-cycle ID beside Batch 2's frozen release projection; `DaemonClient.runtime_startup()` remains the sole authenticated reader. Before accepting a startup event, `dan-observe` requires `event.created_at >= receipt.deployed_at_utc`; exact equality with the receipt's release SHA, artifact SHA, manifest SHA, install/deployment ID, installed timestamp, installed-identity hash, and current login cycle; and equality of event ID/instance ID/PID with the authenticated runtime-startup response. The shared runtime probe must independently find that same PID, exact installed executable/root and listener, with a process start time no later than the event. An older same-SHA event, a prior install ID, a reused PID without the current instance ID, or an event for a process no longer serving the runtime is `missing_matching_daemon_start` unknown evidence.

The ledger preserves the complete proof needed by the later final gate, not merely a detached digest. `StoredDaemonStartEvidence` records the exact event type/status and every validated event/installation field, embeds the complete typed `StoredRuntimeStartupEvidence` projection, and owns a canonical `evidence_sha256`. The event and runtime projections must match field-for-field, including event ID, release/artifact/manifest/install identity, timestamp, cycle, instance, and PID; a standalone opaque response hash is insufficient. `StoredProcessEvidence` stores PID/PPID, timestamps, role/status/errors, actual and expected listeners, and privacy-safe actual/expected SHA-256 values for the resolved executable, exact NUL-delimited argv, and cwd. Its `evidence_sha256` is recomputed over that complete stored projection, and accepted evidence requires equality of each actual/expected pair. Expected values come only from the validated install manifest/runtime composition, never caller input. Raw argv, raw HOME paths, environment values, and tokens are never written. The final gate can therefore reparse, rehash, and compare the historical proof from each ledger entry without querying an event store that may have rotated or pretending a current process proves an earlier day.

Before taking the ledger lock, `dan-observe` validates the deployment receipt plus `active_home_release_audit` from `dan-release-audit:v2` and `voice_acceptance_m5` from `dan-voice-acceptance:v2`. Both envelopes must be green, have no unknown evidence, be created after `deployed_at_utc`, and match the receipt's candidate SHA, installed artifact SHA, and deployment identity. Missing, stale, malformed, or mismatched foundational evidence raises `InvalidDeploymentEvidence` and appends nothing. The canonical SHA-256 of all three source artifacts is stored in every observation record.

The ledger acquires `fcntl.flock(LOCK_EX)`, verifies the full existing chain including each nested daemon/process evidence hash, rejects a duplicate `(deployment_id, calendar_date)`, and rejects a new date that is not greater than the last date already recorded for that deployment. It appends one canonical JSON line with `O_APPEND`, fsyncs file and parent directory, and never rewrites historical entries.

- [ ] **Step 4: Verify GREEN**

```bash
release_test_home="$(mktemp -d /private/tmp/dan-batch5-home.XXXXXX)"
release_test_evidence="$(mktemp -d /private/tmp/dan-batch5-evidence.XXXXXX)"
env HOME="$release_test_home" DAN_RELEASE_EVIDENCE_ROOT="$release_test_evidence" \
  XDG_CACHE_HOME="$release_test_evidence/cache" \
  PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
  DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p tests.audio_guard_plugin -p no:cacheprovider \
  tests/test_release_observation.py \
  tests/test_api_smoke.py::test_daemon_started_binds_installed_release_and_login_cycle
env RUFF_CACHE_DIR="$release_test_evidence/ruff-cache" \
  .venv/bin/ruff check dan/release/observation.py dan/daemon/app.py \
  tests/test_release_observation.py tests/test_api_smoke.py
git diff --check
```

## Task 5.3: Implement real report producers on the frozen evidence envelope

**Files:**

- Read: `dan/release/evidence.py` (created and schema-frozen by Batch 0)
- Read: `dan/release/producer_ids.py` (sole producer-ID authority created by Batch 0)
- Read: `release/review-scope-v1.json` (canonical task/scope registry created and checkpoint-hashed by Batch 0)
- Create: `dan/release/report_producers.py`
- Create: `dan/release/review_manifest.py`
- Create: `scripts/dan-release-report`
- Create: `scripts/dan-review-evidence`
- Read: `tests/test_release_evidence.py` (Batch 0 contract tests)
- Create: `tests/test_release_report_producers.py`
- Create: `tests/test_release_review_manifest.py`

**Interfaces:**

- Produces: `produce_batch_report(kind, repo, evidence_root, runner)` and `produce_review_summary(repo, review_manifest_path, review_paths, output, evidence_root)`, both returning the Batch 0 `ReleaseEvidenceEnvelope`. The caller manifest supplies observed heads/diffs/review hashes only; it cannot supply or override the required task set or allowed scopes.
- Consumes: Batch 0 `read_evidence_envelope(path, *, evidence_root, expected_kind, expected_producer_id)` and then validates the expected subject/artifact SHA explicitly, plus the exclusive JSON reports emitted by `dan-release-checkpoint`, `dan-test-baseline`, `dan-release-build-gate`, `dan-release-audit`, the Batch 1 rollback rehearsal, `dan-voice-acceptance`, and the two-review protocol in the execution index. No nonexistent `validate_evidence_envelope` adapter is introduced.

- [ ] **Step 1: Write RED producer and anti-self-attestation tests**

```python
def test_batch_status_is_derived_from_fixed_recipe_not_cli_input(
    producer_fixture: ProducerFixture,
) -> None:
    report = producer_fixture.produce("batch1_data_cutover", exit_code=1)
    assert report.status == "red"
    assert "--status" not in producer_fixture.parser_option_strings()


def test_envelope_binds_subject_producer_inputs_and_own_hash(
    producer_fixture: ProducerFixture,
) -> None:
    report = producer_fixture.produce("batch2_runtime_host", exit_code=0)
    assert report.subject_sha == producer_fixture.head
    assert report.producer_id == "dan-release-report:batch2_runtime_host:v1"
    assert report.input_evidence
    assert report.report_sha256 == canonical_envelope_sha256(report)


def test_review_summary_requires_spec_and_quality_approval_for_every_task(
    review_fixture: ReviewFixture,
) -> None:
    review_fixture.remove(task="2.3", role="quality")
    with pytest.raises(IncompleteReviewEvidence):
        produce_review_summary(**review_fixture.arguments())


def test_review_summary_rejects_manifest_missing_canonical_task(
    review_fixture: ReviewFixture,
) -> None:
    review_fixture.remove_manifest_task("2.3")
    with pytest.raises(IncompleteReviewEvidence):
        produce_review_summary(**review_fixture.arguments())


def test_final_integration_review_is_for_exact_current_head_and_full_delta(
    review_fixture: ReviewFixture,
) -> None:
    review_fixture.advance_head_after_final_review()
    with pytest.raises(UnreviewedFinalHead):
        produce_review_summary(**review_fixture.arguments())


def test_evidence_root_rejects_repo_and_active_runtime(
    evidence_fixture: EvidenceRootFixture,
) -> None:
    for forbidden in (evidence_fixture.repo, evidence_fixture.home / ".dan"):
        with pytest.raises(UnsafeEvidenceRoot):
            validate_evidence_root(
                forbidden,
                active_roots=evidence_fixture.active_roots,
            )
```

Add tests for exclusive output, `0600` mode, file and directory fsync, invalid input hashes, producer-kind mismatch, moved HEAD during a recipe, duplicate review roles, non-`APPROVED` verdicts, private payload keys, and an output whose stored hash does not match canonical JSON. Add named review-manifest rejection tests for a missing or extra canonical task ID, a scope outside the canonical task allowance, a duplicate scope path, an implementation head outside the checkpoint-to-final ancestry, a changed canonical-scope hash, a final-integration baseline other than the final-clean checkpoint subject SHA, a final-integration head other than current HEAD, and a final-integration diff hash/path set that does not equal the complete checkpoint-subject-to-HEAD delta.

- [ ] **Step 2: Verify RED**

```bash
release_test_home="$(mktemp -d /private/tmp/dan-batch5-home.XXXXXX)"
release_test_evidence="$(mktemp -d /private/tmp/dan-batch5-evidence.XXXXXX)"
env HOME="$release_test_home" DAN_RELEASE_EVIDENCE_ROOT="$release_test_evidence" \
  XDG_CACHE_HOME="$release_test_evidence/cache" \
  PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
  DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p tests.audio_guard_plugin -p no:cacheprovider \
  tests/test_release_report_producers.py tests/test_release_review_manifest.py
```

Expected: imports fail because the report producer module and scripts do not exist. The Batch 0 evidence module and its contract tests already exist and remain unchanged.

- [ ] **Step 3: Consume the Batch 0 envelope and implement the fixed producer registry**

Import `ReleaseEvidenceEnvelope`, `EvidenceInput`, canonical hashing, strict parsing, and `validate_evidence_root()` from the schema frozen in Batch 0. Batch 5 must not redefine, extend, or fork those types. Writers continue to use the Batch 0 exclusive-create, `0600`, file-fsync, and parent-directory-fsync contract; evidence-root validation continues to fail closed on overlap or symlinked ancestry with the repository, `$HOME/.dan`, `$HOME/.claude`, repository voice config, or active databases.

The producer registry is one alias of the central mapping, not another definition:

```python
from dan.release.producer_ids import RELEASE_PRODUCER_IDS


REPORT_PRODUCERS = RELEASE_PRODUCER_IDS
```

Every producer and validator imports the named constants from Batch 0's sole `dan/release/producer_ids.py` authority; hyphenated scripts define no duplicate strings. Tests assert object identity with `RELEASE_PRODUCER_IDS` and compare it with the exact literal contract already frozen in Batch 0. No second production mapping exists.

Each `batch*` recipe is an argv tuple stored in `report_producers.py`, uses `Path(sys.executable).resolve()`, and runs the exact isolated regression declared by that batch followed by the exact ruff scope and `git diff --check`. It snapshots HEAD before and after, records stdout/stderr hashes rather than bodies, and derives `green` only from every required exit code being zero and the subject SHA remaining unchanged. The CLI exposes `--kind`, `--repo`, and `--output`; it does not expose status, finding counts, subject SHA, producer ID, or unknown-evidence overrides.

`dan-review-evidence` derives the current subject SHA from `git rev-parse HEAD`; neither API nor CLI accepts it. `dan/release/review_manifest.py` always loads `release/review-scope-v1.json` from the verified repository root. That checkpoint-hashed file is the only authority for the fully expanded required IDs `0.1`–`0.4`, `1.1`–`1.9`, `2.1`–`2.9`, `3.1`–`3.6`, `4.1`–`4.7`, `5.1`–`5.6`, and `release1-final-integration`, plus each task's allowed path scope. The external caller manifest contains observed baseline/head/diff/review hashes but must have exactly that key set and may only narrow a task to paths allowed by the canonical registry; CLI cannot replace the canonical registry or pass an expected task list.

For every canonical implementation task, the producer requires exactly one `spec` and one `quality` review, distinct reviewer IDs, `verdict="APPROVED"`, the recorded implementation head and task-specific diff hash, and no missing, duplicate, or extra task IDs/reviews. It re-derives every scoped diff through git plumbing and requires each reviewed head on the final-clean checkpoint-subject-to-current-HEAD ancestry. The reserved `release1-final-integration` entry is stricter: its baseline is the final-clean checkpoint subject SHA, its implementation head equals current HEAD exactly, its scope/path set and canonical binary diff hash equal the complete checkpoint-subject-to-HEAD delta, and both independent reviews approve that exact head. Task 0.1 remains covered by its own two required task reviews; the final integration review covers every later release change and their interaction. Thus a later fix or an unassigned file cannot hide merely because HEAD descends from older task heads. The report input hashes bind the checkpoint, canonical scope registry, external execution manifest, every review file, and the final full-delta diff. It stores only task IDs, reviewer IDs, roles, verdicts, and hashes; review prose remains outside the release report.

- [ ] **Step 4: Adapt every non-batch producer to the same envelope**

The owning tasks must expose these exact producer outputs before Task 5.5 can pass:

| Kind | Owning plan and producer | Status source |
|---|---|---|
| `release_checkpoint` | Batch 0 `scripts/dan-release-checkpoint` | checkpoint validation succeeds |
| `baseline_v2` | Batch 0 `scripts/dan-test-baseline` | exact node set green and audio guard loaded |
| `offline_clean_clone_build` | Batch 4 `scripts/dan-release-build-gate` | offline build/install/doctor/package audit all green |
| `active_home_release_audit` | Batch 4 `scripts/dan-release-audit` | separately authorized read-only active-HOME scan has zero findings/unknowns |
| `deployment_receipt` | this plan `scripts/dan-deployment-receipt` | installed identity, candidate ref and authenticated runtime startup agree; deployment ID/time derive from the install identity |
| `rollback_rehearsal` | Batch 1 `scripts/dan-cutover-rehearsal` | fixture rehearsal plus separately authorized isolated manual drill both reconcile |
| `voice_acceptance_m5` | Batch 4 `scripts/dan-voice-acceptance` | validated live M5 hardware result and operator decision from canonical JSON stdin |
| `agent_review_summary` | this task `scripts/dan-review-evidence` | all task-scope spec and quality reviews approved |

No adapter accepts an arbitrary result object from the CLI. It parses the owning tool's strict schema, re-hashes its source artifact, and emits `unknown` for missing or unrecognized fields.

- [ ] **Step 5: Verify GREEN**

```bash
release_test_home="$(mktemp -d /private/tmp/dan-batch5-home.XXXXXX)"
release_test_evidence="$(mktemp -d /private/tmp/dan-batch5-evidence.XXXXXX)"
env HOME="$release_test_home" DAN_RELEASE_EVIDENCE_ROOT="$release_test_evidence" \
  XDG_CACHE_HOME="$release_test_evidence/cache" \
  PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
  DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p tests.audio_guard_plugin -p no:cacheprovider \
  tests/test_release_report_producers.py tests/test_release_review_manifest.py
env RUFF_CACHE_DIR="$release_test_evidence/ruff-cache" \
  .venv/bin/ruff check dan/release/report_producers.py dan/release/review_manifest.py \
  tests/test_release_report_producers.py tests/test_release_review_manifest.py
git diff --check
```

## Task 5.4: Extend the verified deployment-receipt contract with read-only capture

**Files:**

- Modify: `dan/release/deployment_receipt.py` (strict immutable view/parser created by Batch 4)
- Create: `scripts/dan-deployment-receipt`
- Modify: `tests/test_deployment_receipt.py` (strict contract tests created by Batch 4)
- Read: `dan/install/manifest.py` (`CURRENT_RELEASE_RELPATH` and strict installed identity from Batch 2)
- Read: `dan/api/client.py` (`DaemonClient.runtime_startup()` from Batch 4)
- Read: `dan/api/routes_runtime.py` (`GET /runtime/startup`, token-protected)

- [ ] **Step 1: Write RED receipt-binding tests**

```python
def test_receipt_binds_candidate_installed_artifact_and_runtime(tmp_path: Path) -> None:
    deployed = deployed_fixture(tmp_path)
    receipt = capture_deployment_receipt(
        deployed.inputs(),
        output=deployed.evidence_root.path / "receipt.json",
        evidence_root=deployed.evidence_root,
    )
    assert receipt.candidate_sha == receipt.installed_release_sha == receipt.runtime_release_sha
    assert receipt.installed_manifest_sha256 == sha256_file(deployed.install_manifest)
    assert receipt.login_cycle_id == deployed.login_cycle_id
    assert receipt.deployment_id == deployed.release_identity.install_id
    assert receipt.deployed_at_utc == deployed.release_identity.installed_at_utc


def test_receipt_refuses_runtime_sha_different_from_candidate(tmp_path: Path) -> None:
    fixture = deployed_fixture(tmp_path, runtime_sha="different")
    with pytest.raises(DeploymentMismatch):
        capture_deployment_receipt(
            fixture.inputs(),
            output=fixture.evidence_root.path / "receipt.json",
            evidence_root=fixture.evidence_root,
        )


def test_receipt_uses_authenticated_runtime_startup_projection(
    deployed: DeployedFixture,
) -> None:
    receipt = capture_deployment_receipt(
        deployed.inputs(),
        output=deployed.evidence_root.path / "receipt.json",
        evidence_root=deployed.evidence_root,
    )
    assert deployed.client.calls == [("GET", "/runtime/startup")]
    assert receipt.runtime_release_sha == deployed.runtime_startup["release"]["commit_sha"]


def test_recapture_preserves_deployment_epoch_and_id(deployed: DeployedFixture) -> None:
    first = capture_deployment_receipt(
        deployed.inputs(),
        output=deployed.evidence_root.path / "receipt-1.json",
        evidence_root=deployed.evidence_root,
    )
    second = capture_deployment_receipt(
        deployed.inputs(),
        output=deployed.evidence_root.path / "receipt-2.json",
        evidence_root=deployed.evidence_root,
    )
    assert (first.deployment_id, first.deployed_at_utc) == (
        second.deployment_id,
        second.deployed_at_utc,
    )
```

Add exact tests for Batch 0 envelope kind/producer/status, exclusive output, candidate-tag target, artifact hash, every installed/runtime identity mismatch, and no-private-content output. Existing Batch 4 parser/validator tests remain unchanged and green.

- [ ] **Step 2: Verify RED and implement read-only capture**

```python
def capture_deployment_receipt(
    inputs: DeploymentCaptureInputs,
    *,
    output: Path,
    evidence_root: ValidatedEvidenceRoot,
) -> DeploymentReceipt: ...
```

Do not redefine `DeploymentReceipt`, its result schema, or its parser in Batch 5. Import the strict Batch 4 contract and the receipt producer constant from Batch 0's central ID module. The tool resolves the immutable candidate ref, reads the strict installed identity at `CURRENT_RELEASE_RELPATH`, hashes the active install manifest, and queries the existing token-protected `GET /runtime/startup` through `DaemonClient.runtime_startup()`. The route's release status/commit/artifact/manifest/install identity, daemon instance, installed identity, candidate ref, and login-cycle evidence must agree.

`deployment_id` and `deployed_at_utc` are not capture-time values and are never generated by the caller or tool: they are exactly `RuntimeReleaseIdentity.install_id` and `RuntimeReleaseIdentity.installed_at_utc`. Re-capturing the unchanged deployment therefore preserves both values and cannot manufacture a fresh observation epoch. The outer envelope's `created_at_utc` records only receipt capture time and must be at or after the installed timestamp.

The capture constructs one Batch 0 `ReleaseEvidenceEnvelope` with kind `deployment_receipt`, producer `dan-deployment-receipt:v1`, green derived status, the exact input roles required by the Batch 4 validator, and only the validator's fixed result keys; then it writes through the Batch 0 exclusive evidence writer and returns `validate_deployment_receipt(envelope)`. The login-cycle reader is the same injected implementation used by observation. Output must be a new path beneath validated `DAN_RELEASE_EVIDENCE_ROOT`, mode `0600`, with file and parent fsync. It does not install, restart, tag, or mutate daemon state.

- [ ] **Step 3: Verify GREEN**

```bash
release_test_home="$(mktemp -d /private/tmp/dan-batch5-home.XXXXXX)"
release_test_evidence="$(mktemp -d /private/tmp/dan-batch5-evidence.XXXXXX)"
env HOME="$release_test_home" DAN_RELEASE_EVIDENCE_ROOT="$release_test_evidence" \
  XDG_CACHE_HOME="$release_test_evidence/cache" \
  PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
  DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p tests.audio_guard_plugin -p no:cacheprovider \
  tests/test_deployment_receipt.py
env RUFF_CACHE_DIR="$release_test_evidence/ruff-cache" \
  .venv/bin/ruff check dan/release/deployment_receipt.py tests/test_deployment_receipt.py
git diff --check
```

## Task 5.5: Refuse a candidate until every SHA-bound report is green

**Files:**

- Create: `dan/release/candidate_gate.py`
- Create: `scripts/dan-candidate-gate`
- Create: `tests/test_candidate_gate.py`
- Read: `dan/migration/runtime_probe.py`

- [ ] **Step 1: Write RED completeness/debt/immutability tests**

```python
def test_candidate_gate_requires_all_green_sha_bound_reports(evidence: CandidateEvidence) -> None:
    evidence.remove("rollback_rehearsal")
    result = evaluate_candidate(evidence, head=evidence.head, tag="dan-v1-foundation-candidate.2")
    assert result.ready is False
    assert "missing:rollback_rehearsal" in result.blockers


def test_candidate_gate_refuses_known_debts(evidence: CandidateEvidence) -> None:
    evidence.known_debts = ("ignored release audit failure",)
    assert evaluate_candidate(evidence, head=evidence.head, tag="dan-v1-foundation-candidate.2").ready is False


def test_candidate_gate_is_read_only_and_emits_exact_tag_intent(evidence: CandidateEvidence) -> None:
    refs_before = evidence.git_refs()
    result = evaluate_candidate(evidence, head=evidence.head, tag="dan-v1-foundation-candidate.2")
    assert result.tag_intent == {"name": "dan-v1-foundation-candidate.2", "target": evidence.head}
    assert evidence.git_refs() == refs_before


def test_candidate_gate_rejects_right_kind_from_wrong_producer(
    evidence: CandidateEvidence,
) -> None:
    evidence.replace_producer("baseline_v2", "self-attested:v1")
    assert "invalid_producer:baseline_v2" in evaluate_candidate(
        evidence,
        head=evidence.head,
        tag="dan-v1-foundation-candidate.2",
    ).blockers


def test_candidate_gate_rejects_current_legacy_process(
    evidence: CandidateEvidence,
) -> None:
    evidence.process_probe.add_legacy_process(cwd=evidence.legacy_backup_root)
    assert "current_legacy_runtime_use" in evaluate_candidate(
        evidence,
        head=evidence.head,
        tag="dan-v1-foundation-candidate.2",
    ).blockers


def test_candidate_intent_does_not_require_post_deployment_evidence(
    evidence: CandidateEvidence,
) -> None:
    evidence.remove_if_present("active_home_release_audit")
    evidence.remove_if_present("voice_acceptance_m5")
    assert evaluate_candidate(
        evidence,
        head=evidence.head,
        tag="dan-v1-foundation-candidate.2",
    ).ready is True


def test_legacy_release1_acceptance_v1_cannot_satisfy_any_required_report(
    evidence: CandidateEvidence,
    legacy_release1_acceptance_v1: Path,
) -> None:
    evidence.replace("baseline_v2", legacy_release1_acceptance_v1)
    result = evaluate_candidate(
        evidence,
        head=evidence.head,
        tag="dan-v1-foundation-candidate.2",
    )
    assert result.ready is False
    assert "invalid_envelope:baseline_v2" in result.blockers
```

`legacy_release1_acceptance_v1` is a fixture with the exact structural shape of the pre-remediation schema-1 files formerly written beneath `~/.dan/migration/`, including caller-supplied accepted gates and non-empty debts; tests never read active HOME. Add existing-tag, dirty-tree, mismatched-SHA, future-timestamp, current-process-probe-error, unknown-evidence, and moved historical-tag tests. Do not apply a rolling 24-hour freshness rule to the one-time post-deployment audit or voice acceptance: they remain valid for this deployment only when created after its receipt and hash-bound into every observation.

- [ ] **Step 2: Verify RED and implement the exact evidence set**

Required report kinds:

```python
REQUIRED_CANDIDATE_REPORTS = (
    "release_checkpoint",
    "baseline_v2",
    "batch1_data_cutover",
    "batch2_runtime_host",
    "batch3_persona_config_voice",
    "batch4_panel_test_release",
    "offline_clean_clone_build",
    "rollback_rehearsal",
    "agent_review_summary",
)
```

Every required pre-deployment report must validate as a `ReleaseEvidenceEnvelope`, come from the exact producer ID in `REPORT_PRODUCERS`, be green, bind the final candidate HEAD and applicable artifact hash, have `unknown_evidence=[]`, and match both its stored canonical hash and directory manifest hash. Reports produced before the final Batch 5 implementation commit are stale even if their tests were green; no report may claim a future timestamp. `active_home_release_audit` and `voice_acceptance_m5` are deliberately excluded because their strict identity contract can be satisfied only after the candidate is deployed. The gate also executes the shared process probe at evaluation time and blocks on legacy use, probe error, or unknown process identity. `known_debts` must be exactly empty. The gate verifies a clean release scope and the immutable old candidate target, then emits JSON intent only.

Pre-remediation `~/.dan/migration/release1-acceptance.json`, `release1-voice-acceptance.json`, cutover journals, the unnumbered `dan-v1-foundation-candidate` tag, and any schema-1 caller-attested gate result are historical inputs only. They cannot be aliased, converted in memory, or accepted as any required v2 envelope, cannot supply operator sign-off, and cannot satisfy candidate `.2` readiness even if their commit happens to match. Unknown files do not become evidence by filename. A non-empty historical debt list is a rejection signal, never something a newer gate silently drops.

- [ ] **Step 3: Verify GREEN and full pre-candidate regression**

```bash
release_test_home="$(mktemp -d /private/tmp/dan-batch5-home.XXXXXX)"
release_test_evidence="$(mktemp -d /private/tmp/dan-batch5-evidence.XXXXXX)"
env HOME="$release_test_home" DAN_RELEASE_EVIDENCE_ROOT="$release_test_evidence" \
  XDG_CACHE_HOME="$release_test_evidence/cache" \
  PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
  DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p tests.audio_guard_plugin -p no:cacheprovider \
  tests/test_candidate_gate.py tests/test_deployment_receipt.py \
  tests/test_release_evidence.py tests/test_release_report_producers.py \
  tests/test_release_observation.py tests/test_runtime_probe.py
env RUFF_CACHE_DIR="$release_test_evidence/ruff-cache" \
  .venv/bin/ruff check dan/release/candidate_gate.py tests/test_candidate_gate.py
git diff --check
```

## Manual Gate 5.A: Create and deploy the new candidate only on explicit authorization

- [ ] Freeze the final Batch 5 implementation commit. Record `candidate_head="$(git rev-parse HEAD)"`, require `git status --short` to be empty, and do not change code or plans while producing candidate evidence.
- [ ] Obtain separate authorization for the isolated manual rollback drill. Set `DAN_RELEASE_EVIDENCE_ROOT` to an operator-owned absolute directory outside the repository and outside every active runtime/config root; each tool revalidates that boundary.
- [ ] On the final `candidate_head`, rerun the Batch 0 checkpoint and baseline v2, all four fixed batch recipes, the offline clean-clone build, and both review roles for every task. Produce their exclusive envelopes beneath `$DAN_RELEASE_EVIDENCE_ROOT/candidate.2/`.
- [ ] Produce the separately authorized rollback report before candidate evaluation:

```bash
.venv/bin/python scripts/dan-cutover-rehearsal \
  --repo . \
  --isolated-home "$(mktemp -d /private/tmp/dan-cutover-rehearsal.XXXXXX)" \
  --manual-drill-request "$DAN_RELEASE_EVIDENCE_ROOT/operator-input/rollback-drill-request.json" \
  --evidence-output "$DAN_RELEASE_EVIDENCE_ROOT/candidate.2/rollback_rehearsal.json"
```

The request file is canonical JSON matching Batch 1 exactly: schema version `1`, kind `dan-cutover-isolated-manual-drill`, current subject SHA, current subject-diff SHA-256, a non-empty operator authorization ID, and scope `fixture-only-no-launchctl-no-active-home`. The producer validates and hashes it as input evidence; it cannot supply a result. Expected: the fixture plus authorized isolated manual rollback reconcile exactly and bind the final candidate HEAD. Active-HOME and live voice evidence are not produced yet because no deployed candidate identity exists.

- [ ] Run the read-only candidate intent command:

```bash
.venv/bin/python scripts/dan-candidate-gate \
  --candidate dan-v1-foundation-candidate.2 \
  --sha "$candidate_head" \
  --evidence-dir "$DAN_RELEASE_EVIDENCE_ROOT/candidate.2" \
  --json
```

Expected: `ready=true`, exact HEAD, no debts, no unknown evidence, and no git-ref change.

- [ ] Stop and obtain Ozzy's explicit authorization for candidate-tag creation only.
- [ ] After that authorization, create the annotated candidate tag without force, verify its exact target, and stop. Do not push it under the tag-creation authorization.
- [ ] If publication is desired, obtain a separate explicit push authorization and push only that verified candidate tag/ref.
- [ ] Stop and obtain a separate explicit authorization for active-HOME deployment through the reviewed installer/cutover procedure. Deployment establishes a new epoch and invalidates evidence tied to the prior installed identity, but does not yet start observation collection.
- [ ] Stop again and obtain separate explicit authorization for the production cold start/restart. Verify the new daemon's installed identity and startup projection before any receipt is accepted.
- [ ] After the separately authorized deployment and cold start, collect a new receipt outside the active runtime tree:

```bash
.venv/bin/python scripts/dan-deployment-receipt \
  --candidate dan-v1-foundation-candidate.2 \
  --output "$DAN_RELEASE_EVIDENCE_ROOT/candidate.2/deployment-receipt.json"
```

Expected: candidate, installed release, artifact, manifest and runtime SHA all agree.

- [ ] Obtain separate authorization for the read-only active-HOME audit and live M5 voice acceptance, then produce both reports against the deployed receipt:

```bash
.venv/bin/python scripts/dan-release-audit \
  --repo . --all-git-refs --home "$HOME" \
  --deployment-receipt "$DAN_RELEASE_EVIDENCE_ROOT/candidate.2/deployment-receipt.json" \
  --evidence-output "$DAN_RELEASE_EVIDENCE_ROOT/candidate.2/active_home_release_audit.json"

.venv/bin/python -I scripts/dan-voice-acceptance \
  --deployment-receipt "$DAN_RELEASE_EVIDENCE_ROOT/candidate.2/deployment-receipt.json" \
  --evidence-output "$DAN_RELEASE_EVIDENCE_ROOT/candidate.2/voice_acceptance_m5.json" \
  < "$DAN_RELEASE_EVIDENCE_ROOT/operator-input/voice-acceptance-request.json"
```

Expected: the audit is read-only and has zero findings/unknowns; the voice report proves real Apple Silicon M5 hardware, approved source-script identity, input/output WAV hashes, validated metrics, and the operator decision. Both envelopes are created after deployment and match the receipt's candidate, installed artifact, runtime SHA, and deployment ID. The acceptance text and decision are read from canonical JSON stdin and never appear in argv.

## Manual Gate 5.B: Collect seven real dates and two SHA-bound cold starts

Once on each real Warsaw calendar day, run:

```bash
.venv/bin/python scripts/dan-observe \
  --deployment-receipt "$DAN_RELEASE_EVIDENCE_ROOT/candidate.2/deployment-receipt.json" \
  --active-home-audit "$DAN_RELEASE_EVIDENCE_ROOT/candidate.2/active_home_release_audit.json" \
  --voice-acceptance "$DAN_RELEASE_EVIDENCE_ROOT/candidate.2/voice_acceptance_m5.json" \
  --ledger "$DAN_RELEASE_EVIDENCE_ROOT/candidate.2/observation-v2.jsonl"
```

Observation collection begins only after the deployment receipt, active-HOME audit, and voice-acceptance envelopes all validate and agree; deployment alone merely establishes the epoch. The recorder refuses to append before that gate. At least one observation must occur before and one after a real logout/login cycle. Each cycle must contain its own install/instance-bound `DAEMON_STARTED` event, so the selected window contains two distinct `login_cycle_id`, `daemon_instance_id`, and `daemon_start_event_id` values for the deployed identity. Re-running on the same day or appending an older date must fail. Calendar gaps are allowed; no CLI argument can invent or backfill a day. Any new install ID or deployed SHA requires a new receipt and starts a new seven-observation window without deleting history.

## Task 5.6: Validate the observation window and external sign-off

**Files:**

- Create: `dan/release/observation_gate.py`
- Create: `scripts/dan-observation-gate`
- Create: `tests/test_observation_gate.py`
- Read: `dan/release/observation.py` (strict nested daemon/process evidence and ledger parser)
- Read: `dan/release/deployment_receipt.py` (candidate tag is selected only from the validated receipt)
- Read: `dan/release/evidence.py` (strict post-deployment envelope validation)

- [ ] **Step 1: Write RED seven-day/cycle/sign-off tests**

```python
def test_accepts_seven_unique_monotonic_dates_with_calendar_gap(
    gate_fixture: GateFixture,
) -> None:
    gate_fixture.add_days(
        "2026-07-18",
        "2026-07-19",
        "2026-07-21",
        "2026-07-22",
        "2026-07-23",
        "2026-07-24",
        "2026-07-25",
    )
    gate_fixture.add_two_valid_daemon_starts()
    gate_fixture.add_valid_signoff()
    assert gate_fixture.evaluate().ready is True


def test_two_cold_starts_require_distinct_login_cycles(
    gate_fixture: GateFixture,
) -> None:
    gate_fixture.add_seven_days(login_cycle_ids=("same-cycle",) * 7)
    assert "fewer_than_two_login_cycles" in gate_fixture.evaluate().blockers


def test_two_cold_starts_require_distinct_daemon_start_event_ids(
    gate_fixture: GateFixture,
) -> None:
    gate_fixture.add_seven_days(
        login_cycle_ids=("cycle-1",) * 3 + ("cycle-2",) * 4,
        daemon_start_event_ids=(10,) * 7,
    )
    assert "fewer_than_two_daemon_cold_starts" in gate_fixture.evaluate().blockers


def test_two_cold_starts_require_distinct_daemon_instances(
    gate_fixture: GateFixture,
) -> None:
    gate_fixture.add_seven_days(
        login_cycle_ids=("cycle-1",) * 3 + ("cycle-2",) * 4,
        daemon_instance_ids=("same-instance",) * 7,
    )
    assert "fewer_than_two_daemon_instances" in gate_fixture.evaluate().blockers


def test_final_gate_rejects_tampered_historical_process_proof(
    gate_fixture: GateFixture,
) -> None:
    gate_fixture.add_valid_window()
    gate_fixture.tamper_process_evidence(day=0, listening_ports=(9999,))
    gate_fixture.recompute_outer_record_and_ledger_hashes()
    assert "invalid_process_evidence_hash" in gate_fixture.evaluate().blockers


def test_final_gate_rejects_rehashed_actual_expected_process_mismatch(
    gate_fixture: GateFixture,
) -> None:
    gate_fixture.add_valid_window()
    gate_fixture.replace_actual_argv_hash(day=0, value="f" * 64)
    gate_fixture.recompute_all_evidence_and_ledger_hashes()
    assert "process_argv_identity_mismatch" in gate_fixture.evaluate().blockers


def test_final_gate_requires_external_operator_signoff(gate_fixture: GateFixture) -> None:
    gate_fixture.add_valid_window()
    gate_fixture.remove_signoff()
    assert "missing_operator_signoff" in gate_fixture.evaluate().blockers


def test_final_gate_rejects_observation_bound_to_other_voice_acceptance(
    gate_fixture: GateFixture,
) -> None:
    gate_fixture.add_valid_window()
    gate_fixture.replace_voice_acceptance(subject_sha="other-release")
    assert "voice_acceptance_mismatch" in gate_fixture.evaluate().blockers


def test_final_gate_rejects_integration_head_after_observed_sha(
    gate_fixture: GateFixture,
) -> None:
    gate_fixture.add_valid_window()
    gate_fixture.advance_integration_branch()
    assert "integration_head_drift" in gate_fixture.evaluate().blockers


def test_final_gate_has_no_caller_selectable_branch_or_candidate_tag(
    gate_fixture: GateFixture,
) -> None:
    assert "--expected-branch" not in gate_fixture.parser_option_strings()
    assert "--candidate-tag" not in gate_fixture.parser_option_strings()


def test_final_gate_emits_only_the_observed_immutable_sha(
    gate_fixture: GateFixture,
) -> None:
    gate_fixture.add_valid_window()
    result = gate_fixture.evaluate()
    assert result.observed_sha == gate_fixture.receipt.candidate_sha
    assert result.candidate_tag_target == result.observed_sha
    assert result.integration_head == result.observed_sha
```

Add tests for fewer than seven entries, duplicate or decreasing dates, a seven-entry span shorter than six calendar days, legacy/runtime/adapter failure, unknown evidence, mixed deployment IDs, mixed SHAs, a startup event predating deployment or bound to another install/instance/PID/SHA/cycle, fewer than two distinct daemon instances, missing or non-green post-deployment reports, changed receipt/audit/voice hashes across days, sign-off before last observation, broken ledger chain, nested daemon/process evidence hash drift even after outer hashes are recomputed, a fully rehashed event/runtime or actual/expected process mismatch, candidate-tag target drift, invalid receipt candidate-tag syntax, an attempted alternate branch/tag selector, dirty worktree, branch HEAD drift, sign-off hash drift, and new-deploy window selection.

- [ ] **Step 2: Verify RED and implement fail-closed selection**

```python
@dataclass(frozen=True)
class OperatorSignoff:
    schema_version: int
    operator: str
    decision: Literal["approve", "reject"]
    candidate_sha: str
    deployment_id: str
    signed_at_utc: str
    observation_ledger_sha256: str
    deployment_receipt_sha256: str
    active_home_audit_sha256: str
    voice_acceptance_sha256: str
```

The validator owns `RELEASE_INTEGRATION_BRANCH = "agent/dan-release1-integration"` as the fixed Release 1 branch contract. It obtains the candidate tag only from `read_deployment_receipt()` after strict receipt and candidate-ref validation. Neither Python API nor CLI accepts a branch, tag, expected SHA, or observed SHA override.

The validator:

1. resolves the fixed integration ref and the validated receipt's immutable candidate tag through git plumbing, requires a clean worktree, and requires both targets to equal the receipt's candidate SHA;
2. validates receipt and complete ledger hash chain;
3. validates green post-deployment `active_home_release_audit` and `voice_acceptance_m5` envelopes against the receipt, deployed SHA, artifact, deployment ID, producer IDs, canonical hashes, and creation time;
4. selects entries matching exactly one deployment ID and deployed SHA, with identical receipt/audit/voice hashes on every selected record;
5. requires seven unique, strictly increasing Warsaw dates after deployment and a span of at least six calendar days; gaps are allowed;
6. reparses and canonically rehashes every nested `StoredDaemonStartEvidence`, `StoredRuntimeStartupEvidence`, and `StoredProcessEvidence`; requires every event after deployment; requires event and authenticated runtime projections to match field-for-field and bind the exact install ID, installed timestamp, installed-identity/artifact/manifest hashes, daemon instance/PID/event ID, deployed SHA, and recorded login cycle; and requires each actual/expected executable, argv, cwd, and listener projection to match with a valid process start time and no probe errors;
7. requires at least two distinct login-cycle IDs, two distinct daemon-instance IDs, and two distinct `DAEMON_STARTED` event IDs across the selected window;
8. requires zero legacy use, adapter failures and runtime failures on every observation;
9. rejects any unknown evidence;
10. validates an external `approve` sign-off created after the seventh observation and bound to the final ledger, receipt, active-HOME audit, and voice-acceptance hashes;
11. returns `observed_sha`, candidate-tag target, integration HEAD, deployment/receipt/audit/voice/ledger/sign-off hashes, and nothing that can redirect the release to another commit.

- [ ] **Step 3: Verify GREEN**

```bash
release_test_home="$(mktemp -d /private/tmp/dan-batch5-home.XXXXXX)"
release_test_evidence="$(mktemp -d /private/tmp/dan-batch5-evidence.XXXXXX)"
env HOME="$release_test_home" DAN_RELEASE_EVIDENCE_ROOT="$release_test_evidence" \
  XDG_CACHE_HOME="$release_test_evidence/cache" \
  PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
  DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p tests.audio_guard_plugin -p no:cacheprovider \
  tests/test_observation_gate.py tests/test_release_observation.py \
  tests/test_deployment_receipt.py tests/test_candidate_gate.py tests/test_runtime_probe.py
env RUFF_CACHE_DIR="$release_test_evidence/ruff-cache" \
  .venv/bin/ruff check dan/release/observation_gate.py tests/test_observation_gate.py
git diff --check
```

## Final Manual Gate 5.C: Sign-off, final tag, and merge decision

After seven valid days, create the external sign-off only when Ozzy explicitly approves the exact candidate/deployment/ledger. Then run:

```bash
.venv/bin/python scripts/dan-observation-gate \
  --repo . \
  --deployment-receipt "$DAN_RELEASE_EVIDENCE_ROOT/candidate.2/deployment-receipt.json" \
  --active-home-audit "$DAN_RELEASE_EVIDENCE_ROOT/candidate.2/active_home_release_audit.json" \
  --voice-acceptance "$DAN_RELEASE_EVIDENCE_ROOT/candidate.2/voice_acceptance_m5.json" \
  --ledger "$DAN_RELEASE_EVIDENCE_ROOT/candidate.2/observation-v2.jsonl" \
  --signoff "$DAN_RELEASE_EVIDENCE_ROOT/candidate.2/release1-final-signoff.json" \
  --json
```

Expected: `ready=true`, `observed_sha` equal to the validated receipt candidate-tag target, receipt candidate SHA and fixed integration-branch HEAD, seven unique strictly increasing dates across at least six calendar days, at least two distinct install-bound daemon instances/events in two login cycles, fully rehashed historical daemon/process proof, zero failures/use, no unknown evidence, and matching deployment/receipt/audit/voice/ledger/sign-off hashes. Any commit, ref, worktree, deployment or sign-off drift invalidates the gate and requires regenerated evidence; a changed deployed SHA requires a new candidate suffix, deployment, acceptance, and seven-date window.

Stop. The following remain separate explicit decisions:

- create final tag `dan-v1-foundation` explicitly at the gate's immutable `observed_sha` after rechecking candidate target, receipt SHA, sign-off hash, clean worktree and integration HEAD;
- push that exact final tag;
- merge the exact `observed_sha` to `main` (never an unchecked moving branch name);
- archive historical donor docs or remove donor worktrees.

Do not perform any of them merely because the final validator is green.
