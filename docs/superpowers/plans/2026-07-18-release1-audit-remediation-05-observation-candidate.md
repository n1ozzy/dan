# DAN Release 1 Audit Remediation — Batch 5 Candidate and Observation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the invalid acceptance ledger with repository-owned evidence collection, prove a new immutable candidate is ready, bind observations to its actual deployment, require seven real calendar days and two login cycles, and stop before final tag or merge until Ozzy signs off.

**Architecture:** One shared process probe feeds inventory and observation. A hash-chained JSONL ledger derives date, runtime failures, legacy use, process identity, and login cycle from real evidence. Candidate and deployment gates consume SHA-bound reports and emit read-only intent/receipts; they never tag, deploy, or merge. A final validator selects the observation window for one deployment and requires an external operator sign-off artifact.

**Tech Stack:** Python 3.11+, ps/lsof/launchctl/sysctl read-only probes, SQLite event store, ZoneInfo, JSON/JSONL, SHA-256 hash chains, fcntl file locks, pytest, ruff, git plumbing.

## Global Constraints

- Batch 5 starts only after Batches 0–4 are GREEN and independently approved with no open release debt.
- The existing `dan-v1-foundation-candidate` tag remains unchanged. The next proposed tag is `dan-v1-foundation-candidate.2` unless the read-only gate proves that name already exists, in which case stop and choose a new monotonically increasing suffix with Ozzy.
- Candidate gate, deployment receipt, recorder, and final gate are read-only with respect to git refs, production install, runtime lifecycle, audio, and user sign-off.
- Tag creation, push, deployment, runtime restart, rollback drill, audible M5 acceptance, logout/login cycles, sign-off, final tag, and merge are explicit manual operations.
- The release calendar timezone is fixed to `Europe/Warsaw`. Store timestamps in UTC and derive the calendar date through `ZoneInfo("Europe/Warsaw")`; no CLI day override exists.
- A login cycle is identified by the macOS boot-session UUID plus launchd GUI audit-session ID, not by daemon PID or restart count.
- Probe failure is `unknown/error`, never an empty collection or zero. Unknown evidence fails readiness/final gates.
- Reports contain hashes, counts, process metadata, finding codes, and redacted paths. They never contain conversation text, memory bodies, tokens, private audio, or secrets.
- Production code uses clear English names and minimal comments; document intent in these plans and release docs instead of narrating obvious code.

---

## Task 5.1: Use one process-evidence probe for inventory and observation

**Files:**

- Create: `dan/migration/process_probe.py`
- Modify: `dan/migration/inventory.py`
- Create: `tests/test_process_probe.py`
- Modify: `tests/test_migration_inventory_review.py`

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
        argv=("/Users/test/.dan/bin/dand", "serve"),
        cwd=Path("/Users/test/.dan/releases/candidate-2"),
        listening_ports=(41741,),
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

- [ ] **Step 2: Verify RED**

```bash
env HOME=/private/tmp/dan-batch5-home DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_process_probe.py tests/test_migration_inventory_review.py
```

Expected: missing shared module and current incomplete process shape.

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


@dataclass(frozen=True)
class ProcessEvidence:
    pid: int
    ppid: int
    argv: tuple[str, ...]
    cwd: Path | None
    listening_ports: tuple[int, ...]
    role: ProcessRole
    probe_status: ProbeStatus
    errors: tuple[str, ...]
```

Default macOS readers use `ps` for pid/ppid/argv, `lsof -a -p <pid> -d cwd -Fn` for cwd, and `lsof -Pan -p <pid> -iTCP -sTCP:LISTEN` for ports. Classification compares resolved argv/cwd paths and expected ports against exact production/legacy roots from the SHA-bound checkpoint. It does not use an unconstrained substring search.

- [ ] **Step 4: Verify GREEN**

```bash
env HOME=/private/tmp/dan-batch5-home DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_process_probe.py tests/test_migration_inventory_review.py
.venv/bin/ruff check dan/migration/process_probe.py dan/migration/inventory.py \
  tests/test_process_probe.py
git diff --check
```

## Task 5.2: Record real observations in a locked hash-chained ledger

**Files:**

- Create: `dan/release/observation.py`
- Create: `scripts/dan-observe`
- Create: `tests/test_release_observation.py`
- Modify: `dan/events/types.py` only if a required integration failure has no canonical event type

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


def test_adapter_metrics_are_computed_from_events(store: EventStore) -> None:
    store.append(EventType.BRAIN_FAILED, "claude_cli", {"error_code": "transport"})
    metrics = compute_runtime_failure_metrics(store, since=deployment_time())
    assert metrics.adapter_failures == 1
```

Add tests for legacy-use derivation, probe-error propagation, append preservation, previous-entry hash verification, fsync, and concurrent append locking.

- [ ] **Step 2: Verify RED**

```bash
env HOME=/private/tmp/dan-batch5-home DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_release_observation.py
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
class ObservationRecord:
    schema_version: int
    observed_at_utc: str
    calendar_date: str
    release_timezone: str
    deployment_id: str
    deployed_sha: str
    login_cycle_id: str
    process_evidence_sha256: str
    legacy_runtime_use_count: int
    adapter_failure_count: int
    runtime_failure_count: int
    unknown_evidence: tuple[str, ...]
    previous_entry_sha256: str | None
    entry_sha256: str
```

`login_cycle_id` is `sha256(boot_session_uuid + ":" + launchd_gui_asid)`, where values come from injected readers backed by `sysctl -n kern.bootsessionuuid` and `launchctl print gui/<uid>`. Missing values produce unknown evidence.

The ledger acquires `fcntl.flock(LOCK_EX)`, verifies the full existing chain, rejects a duplicate `(deployment_id, calendar_date)`, appends one canonical JSON line with `O_APPEND`, fsyncs file and parent directory, and never rewrites historical entries.

- [ ] **Step 4: Verify GREEN**

```bash
env HOME=/private/tmp/dan-batch5-home DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_release_observation.py
.venv/bin/ruff check dan/release/observation.py tests/test_release_observation.py
git diff --check
```

## Task 5.3: Create a verified deployment receipt without deploying

**Files:**

- Create: `dan/release/deployment_receipt.py`
- Create: `scripts/dan-deployment-receipt`
- Create: `tests/test_deployment_receipt.py`
- Modify: Batch 2 installer release metadata if it does not expose installed commit/artifact hashes

- [ ] **Step 1: Write RED receipt-binding tests**

```python
def test_receipt_binds_candidate_installed_artifact_and_runtime(tmp_path: Path) -> None:
    receipt = capture_deployment_receipt(deployed_fixture(tmp_path))
    assert receipt.candidate_sha == receipt.installed_release_sha == receipt.runtime_release_sha
    assert receipt.installed_manifest_sha256 == sha256_file(deployed_fixture(tmp_path).install_manifest)
    assert receipt.login_cycle_id == deployed_fixture(tmp_path).login_cycle_id


def test_receipt_refuses_runtime_sha_different_from_candidate(tmp_path: Path) -> None:
    fixture = deployed_fixture(tmp_path, runtime_sha="different")
    with pytest.raises(DeploymentMismatch):
        capture_deployment_receipt(fixture)
```

Add exclusive-output, candidate-tag-target, artifact-hash, and no-private-content tests.

- [ ] **Step 2: Verify RED and implement read-only capture**

```python
@dataclass(frozen=True)
class DeploymentReceipt:
    schema_version: int
    deployment_id: str
    deployed_at_utc: str
    release_timezone: str
    candidate_tag: str
    candidate_sha: str
    artifact_sha256: str
    installed_manifest_sha256: str
    installed_release_sha: str
    runtime_release_sha: str
    login_cycle_id: str
```

The tool reads the immutable candidate ref, installed release metadata generated by the installer, active install manifest, daemon runtime status, and login-cycle evidence. It creates a new output with exclusive create and fsync. It does not install, restart, tag, or mutate daemon state.

- [ ] **Step 3: Verify GREEN**

```bash
env HOME=/private/tmp/dan-batch5-home DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_deployment_receipt.py
.venv/bin/ruff check dan/release/deployment_receipt.py tests/test_deployment_receipt.py
git diff --check
```

## Task 5.4: Refuse a candidate until every SHA-bound report is green

**Files:**

- Create: `dan/release/candidate_gate.py`
- Create: `scripts/dan-candidate-gate`
- Create: `tests/test_candidate_gate.py`

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
```

Add existing-tag, dirty-tree, mismatched-SHA, expired-report, unknown-evidence, and moved historical-tag tests.

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
    "active_home_release_audit",
    "rollback_rehearsal",
    "voice_acceptance_m5",
    "agent_review_summary",
)
```

Every report must be green, contain the exact candidate HEAD or artifact hash it audits, have `unknown_evidence=[]`, and be referenced by SHA-256. `known_debts` must be exactly empty. The gate verifies a clean release scope and the immutable old candidate target, then emits JSON intent only.

- [ ] **Step 3: Verify GREEN and full pre-candidate regression**

```bash
env HOME=/private/tmp/dan-batch5-home DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_candidate_gate.py tests/test_deployment_receipt.py tests/test_release_observation.py \
  tests/test_process_probe.py
.venv/bin/ruff check dan/release/candidate_gate.py tests/test_candidate_gate.py
git diff --check
```

## Manual Gate 5.A: Create and deploy the new candidate only on explicit authorization

- [ ] Run the read-only intent command:

```bash
.venv/bin/python scripts/dan-candidate-gate \
  --candidate dan-v1-foundation-candidate.2 \
  --sha HEAD \
  --evidence-dir /Users/n1_ozzy/.dan/release \
  --json
```

Expected: `ready=true`, exact HEAD, no debts, no unknown evidence, and no git-ref change.

- [ ] Stop and obtain Ozzy's explicit authorization for tag creation and deployment.
- [ ] After authorization, create the annotated candidate tag without force and push only if push is separately authorized.
- [ ] Deploy through the reviewed installer/cutover procedure. This starts a new observation window and invalidates every observation tied to the old deployment.
- [ ] Run the approved rollback drill if authorized, restore the candidate deployment, and collect a new receipt:

```bash
.venv/bin/python scripts/dan-deployment-receipt \
  --candidate dan-v1-foundation-candidate.2 \
  --output /Users/n1_ozzy/.dan/migration/deploy-candidate-2.json
```

Expected: candidate, installed release, artifact, manifest and runtime SHA all agree.

## Manual Gate 5.B: Collect seven real days and two login cycles

Once on each real Warsaw calendar day, run:

```bash
.venv/bin/python scripts/dan-observe \
  --deployment-receipt /Users/n1_ozzy/.dan/migration/deploy-candidate-2.json \
  --ledger /Users/n1_ozzy/.dan/migration/observation-v2.jsonl
```

At least one observation must occur before and one after a real logout/login cycle so the window contains two distinct `login_cycle_id` values. Re-running on the same day must fail. Any new deploy produces a new receipt/deployment ID and starts a new seven-day window without deleting history.

## Task 5.5: Validate the observation window and external sign-off

**Files:**

- Create: `dan/release/observation_gate.py`
- Create: `scripts/dan-observation-gate`
- Create: `tests/test_observation_gate.py`

- [ ] **Step 1: Write RED seven-day/cycle/sign-off tests**

```python
def test_requires_seven_distinct_consecutive_dates_after_deployment(gate_fixture: GateFixture) -> None:
    gate_fixture.add_days("2026-07-18", "2026-07-19", "2026-07-21", "2026-07-22", "2026-07-23", "2026-07-24", "2026-07-25")
    result = gate_fixture.evaluate()
    assert result.ready is False
    assert "non_consecutive_calendar_dates" in result.blockers


def test_two_cold_starts_require_distinct_login_cycle_ids(gate_fixture: GateFixture) -> None:
    gate_fixture.add_seven_days(login_cycle_ids=("same-cycle",) * 7)
    assert "fewer_than_two_login_cycles" in gate_fixture.evaluate().blockers


def test_final_gate_requires_external_operator_signoff(gate_fixture: GateFixture) -> None:
    gate_fixture.add_valid_window()
    gate_fixture.remove_signoff()
    assert "missing_operator_signoff" in gate_fixture.evaluate().blockers
```

Add tests for legacy/runtime/adapter failure, unknown evidence, mixed deployment IDs, mixed SHAs, sign-off before last observation, broken ledger chain, and new-deploy window selection.

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
```

The validator:

1. validates receipt and complete ledger hash chain;
2. selects entries matching exactly one deployment ID and deployed SHA;
3. requires seven unique, strictly consecutive Warsaw dates after deployment;
4. requires at least two distinct login-cycle IDs;
5. requires zero legacy use, adapter failures and runtime failures on every day;
6. rejects any unknown evidence;
7. validates an external `approve` sign-off created after the seventh observation and bound to the final ledger hash.

- [ ] **Step 3: Verify GREEN**

```bash
env HOME=/private/tmp/dan-batch5-home DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_observation_gate.py tests/test_release_observation.py \
  tests/test_deployment_receipt.py tests/test_candidate_gate.py tests/test_process_probe.py
.venv/bin/ruff check dan/release/observation_gate.py tests/test_observation_gate.py
git diff --check
```

## Final Manual Gate 5.C: Sign-off, final tag, and merge decision

After seven valid days, create the external sign-off only when Ozzy explicitly approves the exact candidate/deployment/ledger. Then run:

```bash
.venv/bin/python scripts/dan-observation-gate \
  --deployment-receipt /Users/n1_ozzy/.dan/migration/deploy-candidate-2.json \
  --ledger /Users/n1_ozzy/.dan/migration/observation-v2.jsonl \
  --signoff /Users/n1_ozzy/.dan/migration/release1-final-signoff.json \
  --json
```

Expected: `ready=true`, seven consecutive dates, at least two login cycles, zero failures/use, no unknown evidence, matching SHA/deployment/ledger sign-off.

Stop. The following remain separate explicit decisions:

- create/push final tag `dan-v1-foundation`;
- merge `agent/dan-release1-integration` to `main`;
- archive historical donor docs or remove donor worktrees.

Do not perform any of them merely because the final validator is green.
