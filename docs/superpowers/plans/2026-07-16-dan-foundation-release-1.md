# DAN Foundation Release 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Zbudować Wydanie 1 jednego produktu DAN: jeden daemon `dand`, jedna baza `~/.dan/dan.db`, jeden resolver konfiguracji, jedna trwała kolejka i jeden właściciel audio, a następnie bezpiecznie przełączyć wszystkie aktywne hosty bez utraty danych, persony i jakości głosu.

**Architecture:** Rozwijamy zaakceptowany runtime z repo `jarvis`, a po przejściu testów migracji danych przemianowujemy pakiet na `dan`. Producenci wysyłają wyłącznie `SpeechIntent` przez lokalne API/CLI; resolver w `dand` zapisuje kompletny, niezmienny `RenderSnapshot` razem z rekordem kolejki; broker tylko wykonuje ten snapshot. Panel, PTT, Supertonic i integracje hostów są klientami lub dziećmi jednego daemonu, nigdy równoległymi właścicielami stanu.

**Tech Stack:** Python 3.11+, SQLite/WAL, `pytest`, `ruff`, istniejący lokalny HTTP API, Supertonic 1.3.1, FFmpeg, trwały player CoreAudio przez PyObjC/AVFoundation, PyObjC/Quartz dla PTT, `launchd`, TOML/JSON, Bash tylko jako cienka warstwa instalacyjna.

## Global Constraints

- Źródłem wymagań jest [specyfikacja konsolidacji](../specs/2026-07-16-dan-product-consolidation-design.md). Plan obejmuje wyłącznie **Wydanie 1 — Fundament DAN**. Radio, scheduler audycji, telefony i wizualizer pozostają w Wydaniu 2/3.
- Startuj z `/Users/n1_ozzy/Documents/dev/jarvis`, nigdy z katalogu domowego. Przed zmianami użyj `superpowers:using-git-worktrees` i utwórz izolowany worktree z zaakceptowanej gałęzi integracyjnej.
- Kod rozwijaj w worktree. Zatrzymanie starego runtime'u jest obowiązkowe dopiero przed kopiowaniem baz, zmianą aktywnych adapterów/plistów/symlinków i finalnym cutoverem. Nie edytuj pliku używanego przez żywy proces.
- Nie używaj `git add -A`, `git stash`, `git reset --hard`, ślepego `rm`, kopiowania żywego WAL/SHM ani globalnego replace po `$HOME`. Stage'uj wyłącznie pliki bieżącego zadania.
- Nie dotykaj `~/.claude/archive/`. Nie commituj `~/.dan/`, baz, pamięci, historii, transkryptów, prywatnych próbek, tokenów, sekretów, `owner.toml`, absolutnych ścieżek użytkownika ani raportów z prywatną treścią.
- Dokładnie jeden `dand`, jeden label `com.dan.dand`, jeden globalny listener PTT/hotkey, jeden resolver i jeden player. Panel, skille, hooki i providerzy nie uruchamiają alternatywnego toru.
- Brak silnika, assetu, persony lub pola snapshotu jest jawnym błędem przed `queued`. Nie ma cichego fallbacku do XTTS, innego głosu, innego tempa ani playbacku poza brokerem.
- Testy automatyczne używają tymczasowego `HOME`, bazy i mock audio. Realny mikrofon, globalna kolejka oraz słyszalne audio są dozwolone tylko w jawnych krokach live.
- Po Task 1, 3, 7, 11 i 14 zatrzymaj wykonanie na bramce przeglądu. Nie przykrywaj czerwonej bramki kolejnym refaktorem.
- W każdym zadaniu: test RED, oczekiwany FAIL, minimalna implementacja, test GREEN, szersza regresja, `git diff --check`, wąski commit.
- Istniejące `.superpowers/` jest własnością użytkownika; pozostaje nietknięte i niestage'owane.

## Target File Map

| Obszar | Docelowy właściciel |
|---|---|
| persona | `config/persona/DAN.md` + lokalny `~/.dan/owner.toml` |
| głosy/mastering | `config/voice/` |
| resolver | `dan/voice/resolver.py` |
| intencja/snapshot | `dan/voice/models.py` |
| kolejka/broker | `dan/voice/queue.py`, `dan/voice/broker.py`, `dan/voice/service.py` |
| konfiguracja instalacji | `~/.dan/config.toml`, walidowana przez `dan/config_registry.py` |
| stan trwały | `~/.dan/dan.db` |
| API/CLI | `dan/api/`, `dan/cli.py` |
| PTT/hotkey | `dan/input/`, uruchamiane wyłącznie przez `dand` |
| panel | `dan/panel/` jako cienki klient API |
| instalacja/hosty | `dan/install/`, `integrations/`, `launchd/com.dan.dand.plist.example` |
| migracja/cutover | `dan/migration/`, `scripts/dan-*` |

## Spec Coverage

| Wydanie 1 ze spec | Zadanie wykonawcze |
|---|---|
| manifest źródeł/procesów/formatów | Task 1 |
| audyt refów i WIP | Task 1 |
| klasyfikacja pełnych 2327 testów | Task 2 |
| zatrzymanie aktywnego runtime'u | Task 12 i Task 14 |
| backup/migracja `jarvis.db` + `memory.db` | Task 3 i Task 12 |
| rename `jarvis` -> `dan` | Task 4, finalna ścieżka w Task 14 |
| jeden config/resolver + prywatność persony | Task 5 |
| pełna obsada, styles, mastering, wymowa, Żaneta | Task 6 |
| trwała kolejka i jedyny broker/player | Task 7 |
| wspólne API/CLI | Task 8 |
| PTT i dzieci procesu w `dand` | Task 9 |
| panel + funkcje menubar-controller | Task 10 |
| hosty, skille, hook, standup, instalator, launchd | Task 11 |
| journaled cutover/rollback | Task 12 i Task 14 |
| prosta dokumentacja i clean clone | Task 13 |
| siedem dni + dwa cold starty + sign-off | Task 15 |

---

## Task 1: Freeze source truth and choose the integration line

**Files:**

- Create: `jarvis/migration/__init__.py`
- Create: `jarvis/migration/inventory.py`
- Create: `scripts/dan-inventory`
- Create: `tests/test_migration_inventory.py`
- Create: `docs/migration/MANIFEST-CONTRACT.md`
- Create: `docs/migration/REF-DECISIONS.md`

- [ ] **Step 1: Create the implementation worktree**

```bash
cd /Users/n1_ozzy/Documents/dev/jarvis
git status --short --branch
git rev-parse HEAD
git worktree add /Users/n1_ozzy/Documents/dev/DAN-release1-wt \
  -b feat/dan-foundation-release1 spike/jarvis-local-runtime-check
cd /Users/n1_ozzy/Documents/dev/DAN-release1-wt
git merge-base --is-ancestor f60c42c HEAD
```

Expected: clean worktree containing spec commit `f60c42c`. Otherwise stop; do not recreate the spec.

- [ ] **Step 2: Write the failing inventory contract**

```python
def test_inventory_has_every_release1_surface(tmp_path: Path) -> None:
    manifest = build_inventory(fixture_roots(tmp_path))
    assert set(manifest["surfaces"]) == {
        "repositories", "git_refs", "processes", "launchd", "databases",
        "voice_assets", "config_sources", "skills", "hooks", "symlinks",
        "producers", "request_formats", "runtime_paths", "input_materials",
    }
    assert manifest["schema_version"] == 1
    assert "contents" not in json.dumps(manifest)


def test_inventory_records_symlink_target_and_sha256(tmp_path: Path) -> None:
    target = tmp_path / "target.sh"
    target.write_text("#!/bin/sh\n", encoding="utf-8")
    link = tmp_path / "link.sh"
    link.symlink_to(target)
    item = inspect_path(link)
    assert item.kind == "symlink"
    assert item.target == str(target)
    assert item.sha256 == hashlib.sha256(target.read_bytes()).hexdigest()
```

- [ ] **Step 3: Verify RED**

```bash
pytest -q tests/test_migration_inventory.py
```

Expected: import failure for `jarvis.migration.inventory`.

- [ ] **Step 4: Implement a read-only inventory**

```python
@dataclass(frozen=True)
class InventoryItem:
    path: str
    kind: Literal["file", "directory", "symlink", "process", "launchd", "database"]
    target: str | None
    sha256: str | None
    status: str
    consumers: tuple[str, ...] = ()
    request_format: str | None = None


def build_inventory(roots: InventoryRoots, *, runner: Runner = subprocess.run) -> dict[str, object]:
    return InventoryBuilder(roots=roots, runner=runner).collect().to_mapping()


def write_manifest_atomic(manifest: Mapping[str, object], destination: Path) -> None:
    """Write mode 0600 through a sibling temporary file and os.replace()."""
```

The production roots must include:

- `Documents/dev/jarvis`, `dan`, `DANv2`, `menubar-controller`;
- `~/.dan`, `~/.jarvis`, `~/.config/voice`, `~/.cache/supertonic3/custom_styles`;
- active `~/.agents`, `~/.claude` excluding `archive`, `~/.codex`, `~/.openclaw`;
- `~/AGENTS.md`, `~/.claude/CLAUDE.md`, `~/Library/LaunchAgents`;
- `/tmp/dan-*`, `/tmp/claude-loud-thinking`, every producer and old request format;
- `summary.md`, `opinia-planu.md`, old Radio plan, Voice Lab and `_sesja-glosy-2026-07-11` as input material.

Also record `_quarantine-continuity-fix-2026-07-08`, `_quarantine-wcinki-2026-07-11` and `~/.claude/skills/_quarantine-gadanie-2026-07-14` as historical candidates, then search live code/process/instructions for consumers. A live reference changes the item to an active source; otherwise the decision is `archive/do-not-copy`, never silent deletion.

For SQLite record only schema version, tables, counts and WAL mode. For Git record every local/remote/rescue/spike ref and commits unreachable from the chosen base. Never copy row contents or private text into the report.

- [ ] **Step 5: Generate and check the private machine manifest**

```bash
mkdir -p ~/.dan/migration
python scripts/dan-inventory \
  --output ~/.dan/migration/release1-source-manifest.json \
  --exclude ~/.claude/archive
chmod 600 ~/.dan/migration/release1-source-manifest.json
python scripts/dan-inventory --check ~/.dan/migration/release1-source-manifest.json
```

- [ ] **Step 6: Audit refs and finish every decision**

```bash
git for-each-ref --format='%(refname) %(objectname)' refs/heads refs/remotes refs/rescue refs/spike | sort
git log --all --graph --decorate --oneline --max-count=250
git branch --no-merged feat/dan-foundation-release1
```

`REF-DECISIONS.md` has one row per non-merged ref: ref, head SHA, unique commits, decision (`merge`, `cherry-pick`, `superseded`, `archive`), evidence and resulting commit. It is invalid with `pending`, `TBD` or an empty decision. Explicitly inspect `claude/fix-brain-wiring`, `claude/amazing-hawking-c80907`, `rescue/*` and `spike/*` before rename.

- [ ] **Step 7: Verify and commit**

```bash
pytest -q tests/test_migration_inventory.py
! rg -n 'pending|TBD|TODO' docs/migration/REF-DECISIONS.md
git diff --check
git add jarvis/migration/__init__.py jarvis/migration/inventory.py \
  scripts/dan-inventory tests/test_migration_inventory.py \
  docs/migration/MANIFEST-CONTRACT.md docs/migration/REF-DECISIONS.md
git commit -m "feat: freeze DAN migration source truth"
```

**Review gate:** compare the manifest against `ps`, `lsof`, `launchctl`, Git refs and filesystem. Every source, process and producer needs a named decision.

---

## Task 2: Establish a safe full-suite baseline

**Files:**

- Create: `jarvis/migration/test_safety.py`
- Create: `scripts/dan-test-baseline`
- Create: `tests/test_test_safety.py`
- Create: `docs/migration/TEST-BASELINE.md`
- Modify: `pyproject.toml`

- [ ] **Step 1: Write failing classification tests**

```python
def test_every_collected_test_has_a_safety_class(repo_root: Path) -> None:
    collected = collect_node_ids(repo_root)
    classified = classify_node_ids(repo_root, collected)
    assert set(classified) == set(collected)
    assert {row.safety for row in classified.values()} <= {"isolated", "live-manual"}


def test_automatic_group_has_no_live_primitives(repo_root: Path) -> None:
    assert scan_automatic_tests(repo_root) == []
```

Flag unmocked audio/microphone binaries, `launchctl`, `/tmp/dan-*`, real home DBs and live voice ports.

- [ ] **Step 2: Verify RED**

```bash
pytest -q tests/test_test_safety.py
```

- [ ] **Step 3: Implement classification and isolation**

`scripts/dan-test-baseline` creates a temporary `HOME`/runtime/DB, sets `DAN_TEST_MODE=1`, `DAN_DISABLE_AUDIO=1`, `DAN_DISABLE_MIC=1`, refuses `live-manual`, runs every isolated node and writes counts/duration/failures to `~/.dan/migration/test-baseline.json` mode `0600`. Register `live_manual` in `pyproject.toml`. Commit only sanitized counts and failure IDs to `TEST-BASELINE.md`.

- [ ] **Step 4: Collect and run all tests**

```bash
pytest --collect-only -q > /tmp/dan-release1-collected.txt
python scripts/dan-test-baseline --expect-collected 2327
```

The Task 2 collection gate includes the frozen pre-Task-1 baseline, the original
Task 1 tests, the review-fix tests, and the FIX FIRST regressions:

`2176 + 32 + 41 + 29 + 49 = 2327`

If collection differs from `2327`, stop and reconcile the spec with actual
collection; never silently change the expected number. Existing failures are
recorded exactly and may not increase.

- [ ] **Step 5: Verify and commit**

```bash
pytest -q tests/test_test_safety.py
python scripts/dan-test-baseline --verify-report ~/.dan/migration/test-baseline.json
git diff --check
git add jarvis/migration/test_safety.py scripts/dan-test-baseline \
  tests/test_test_safety.py docs/migration/TEST-BASELINE.md pyproject.toml
git commit -m "test: establish isolated DAN release baseline"
```

---

## Task 3: Build controlled SQLite backup and data migration

**Files:**

- Create: `jarvis/migration/sqlite_backup.py`
- Create: `jarvis/migration/legacy_data.py`
- Create: `jarvis/migration/db_report.py`
- Create: `tests/test_sqlite_backup.py`
- Create: `tests/test_legacy_data_migration.py`
- Create: `tests/fixtures/memory_v1.sql`
- Modify: `jarvis/store/migrations.py`

- [ ] **Step 1: Write failing WAL-safe backup tests**

```python
def test_backup_preserves_committed_wal_rows(tmp_path: Path) -> None:
    source = create_wal_database(tmp_path / "source.db", rows=3)
    destination = tmp_path / "backup.db"
    report = backup_database(source, destination)
    assert report.integrity == "ok"
    assert report.source_counts == report.destination_counts
    assert rows(destination, "events") == 3


def test_backup_refuses_unapproved_writer(tmp_path: Path) -> None:
    source = create_database(tmp_path / "source.db")
    with pytest.raises(ActiveWriterError):
        assert_quiescent_database(source, handles=[fake_writer(pid=777)])
```

- [ ] **Step 2: Write failing lineage/import test**

```python
def test_dan_db_evolves_from_jarvis_and_imports_unique_memory(tmp_path: Path) -> None:
    target, report = migrate_databases(
        create_jarvis_fixture(tmp_path / "jarvis.db"),
        create_memory_fixture(tmp_path / "memory.db"),
        tmp_path / "dan.db",
    )
    assert existing_jarvis_tables(target) <= table_names(target)
    assert report.jarvis_rows_preserved is True
    assert (report.memory.imported, report.memory.merged, report.memory.rejected) == (2, 1, 0)
    assert every_imported_row_has_source_id(target)
```

- [ ] **Step 3: Verify RED**

```bash
pytest -q tests/test_sqlite_backup.py tests/test_legacy_data_migration.py
```

- [ ] **Step 4: Implement backup and quiescence contracts**

```python
@dataclass(frozen=True)
class BackupReport:
    source: str
    destination: str
    checkpoint: tuple[int, int, int]
    integrity: str
    source_counts: Mapping[str, int]
    destination_counts: Mapping[str, int]
    sha256: str


def backup_database(source: Path, destination: Path) -> BackupReport:
    assert_quiescent_database(source)
    source_conn = sqlite3.connect(source)
    checkpoint = tuple(source_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone())
    destination_conn = sqlite3.connect(destination)
    source_conn.backup(destination_conn)
    # close both, reopen destination read-only, integrity_check, compare counts, hash
```

`assert_quiescent_database()` runs `lsof -Fpc -- "$DB" "$DB-wal" "$DB-shm"` for the concrete source path. Never copy WAL/SHM using `cp`/`shutil.copy`.

- [ ] **Step 5: Implement versioned lineage and idempotent memory import**

```sql
CREATE TABLE IF NOT EXISTS migration_sources (
  id TEXT PRIMARY KEY, source_path_hash TEXT NOT NULL,
  source_schema TEXT NOT NULL, imported_at TEXT NOT NULL, source_sha256 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS migration_record_map (
  source_id TEXT NOT NULL, source_table TEXT NOT NULL, source_record_id TEXT NOT NULL,
  target_table TEXT, target_record_id TEXT,
  outcome TEXT NOT NULL CHECK (outcome IN ('imported','merged','rejected')),
  reason TEXT, PRIMARY KEY (source_id, source_table, source_record_id)
);
```

The target starts as a SQLite Backup API copy of `jarvis.db`, then applies current migrations. Map each `memory.db` source table explicitly; never infer meaning from equal column names. Re-running the same source SHA is idempotent.

- [ ] **Step 6: Verify and commit**

```bash
pytest -q tests/test_sqlite_backup.py tests/test_legacy_data_migration.py \
  tests/test_db_schema.py tests/test_daemon_db_concurrency.py
ruff check jarvis/migration tests/test_sqlite_backup.py tests/test_legacy_data_migration.py
git diff --check
git add jarvis/migration/sqlite_backup.py jarvis/migration/legacy_data.py \
  jarvis/migration/db_report.py jarvis/store/migrations.py \
  tests/test_sqlite_backup.py tests/test_legacy_data_migration.py tests/fixtures/memory_v1.sql
git commit -m "feat: add lossless DAN database migration"
```

**Review gate:** migrate twice on disposable copies of real schemas; compare counts, `integrity_check` and every merged/rejected class. Do not touch live DBs yet.

---

## Task 4: Rename the product and package internally

**Files:**

- Move: `jarvis/` -> `dan/`
- Move: `config/jarvis.example.toml` -> `config/dan.example.toml`
- Move: `scripts/jarvis-panel` -> `scripts/dan-panel`
- Move: `scripts/jarvisd` -> `scripts/dand`
- Move: `launchd/com.ozzy.jarvisd.plist.example` -> `launchd/com.dan.dand.plist.example`
- Modify: `pyproject.toml`, active imports, scripts and tests
- Modify: `tests/test_imports.py`, `tests/test_scaffold_contracts.py`, `tests/test_launchd_assets.py`

- [ ] **Step 1: Write final-name tests**

```python
def test_final_python_package_is_dan() -> None:
    assert importlib.util.find_spec("dan") is not None
    assert importlib.util.find_spec("jarvis") is None


def test_console_entrypoints_are_final_names() -> None:
    scripts = project_scripts(Path("pyproject.toml"))
    assert scripts["dan"] == "dan.cli:main"
    assert scripts["dand"] == "dan.cli:daemon_main"
    assert not any("jarvis" in name for name in scripts)
```

- [ ] **Step 2: Verify RED**

```bash
pytest -q tests/test_imports.py tests/test_scaffold_contracts.py tests/test_launchd_assets.py
```

- [ ] **Step 3: Move with Git and update metadata**

```bash
git mv jarvis dan
git mv config/jarvis.example.toml config/dan.example.toml
git mv scripts/jarvis-panel scripts/dan-panel
git mv scripts/jarvisd scripts/dand
git mv launchd/com.ozzy.jarvisd.plist.example launchd/com.dan.dand.plist.example
```

Set:

```toml
[project]
name = "dan-runtime"
description = "DAN local runtime daemon"

[project.scripts]
dan = "dan.cli:main"
dand = "dan.cli:daemon_main"
dan-memory-mcp = "dan.mcp.memory_server:main"

[tool.hatch.build.targets.wheel]
packages = ["dan"]
```

Rename active imports, thread names, API user agents and labels. Keep `Jarvis`/`DANv2` only in migration provenance and narrow legacy tests.

- [ ] **Step 4: Set final runtime paths without moving live data**

`dan/paths.py` resolves a temporary HOME to `~/.dan/config.toml`, `dan.db`, `logs/`, `runtime/`, `owner.toml`, `secrets.env`. Importing modules must never auto-move `~/.jarvis`; only the cutover command may migrate live data.

- [ ] **Step 5: Verify and commit**

```bash
pytest -q tests/test_imports.py tests/test_scaffold_contracts.py tests/test_launchd_assets.py
python scripts/dan-test-baseline --compare ~/.dan/migration/test-baseline.json
rg -n '\b(jarvis|Jarvis|DANv2)\b' dan scripts launchd config tests \
  | tee /tmp/dan-release1-legacy-names.txt
git diff --check
git add pyproject.toml dan config/dan.example.toml scripts/dan-panel scripts/dand \
  launchd/com.dan.dand.plist.example tests
git commit -m "refactor: rename Jarvis runtime to DAN"
```

Every remaining legacy-name match needs a path-specific allowlist reason; never suppress the full scan.

---

## Task 5: Create one configuration registry, persona boundary and render resolver

**Files:**

- Create: `dan/config_registry.py`, `dan/persona.py`, `dan/voice/resolver.py`
- Create: `config/owner.example.toml`
- Create: `tests/test_config_registry.py`, `tests/test_persona_privacy.py`, `tests/test_voice_resolver.py`
- Modify: `dan/config.py`, `dan/voice/models.py`, `dan/api/routes_settings.py`, `dan/daemon/app.py`
- Modify: `config/persona/DAN.md`, `tests/test_runtime_settings_legacy_approval.py`, `tests/test_shared_voice.py`

- [ ] **Step 1: Write failing intent/snapshot tests**

```python
def test_resolver_creates_complete_snapshot_once(catalog, installation_config, engines) -> None:
    snapshot = VoiceResolver(catalog, installation_config, engines).resolve(SpeechIntent(
        text="Zażółć gęślą jaźń.", persona="dan", source="codex", session="smoke",
        participant="dan", priority=0, lane="live",
        interrupt_policy="interruptible", utterance_index=0,
    ))
    assert snapshot.engine == "supertonic"
    assert snapshot.engine_version and snapshot.voice_or_style
    assert snapshot.speed > 0 and snapshot.mastering_profile
    assert snapshot.dsp is not None and snapshot.pronunciations
    assert snapshot.pronunciations_sha256
    assert snapshot.gain > 0 and snapshot.asset_sha256 and snapshot.config_revision


def test_intent_cannot_override_resolver_fields() -> None:
    with pytest.raises(IntentValidationError, match="voice"):
        SpeechIntent.from_mapping(
            {"text": "Nie oszukuj.", "persona": "dan", "voice": "M1"},
            source="hook", session="s1",
        )
```

- [ ] **Step 2: Write failing config persistence tests**

```python
@pytest.mark.parametrize("key", ["jarvis_speed", "voice.unknown", "persona.dan.voice"])
def test_config_rejects_dead_unknown_or_versioned_key_without_write(key, config_store) -> None:
    before = config_store.bytes()
    with pytest.raises(ConfigWriteRejected):
        config_store.set(key, "M2")
    assert config_store.bytes() == before


def test_set_restart_explain_resolve_uses_one_value(runtime_factory) -> None:
    runtime_factory().config.set("voice.output_gain", 0.92)
    restarted = runtime_factory(restart=True)
    assert restarted.config.explain("voice.output_gain").value == 0.92
    assert restarted.resolver.resolve(speech_intent("dan")).gain == 0.92


def test_every_runtime_config_field_is_registered() -> None:
    assert discovered_runtime_config_keys() == set(REGISTRY)
```

- [ ] **Step 3: Verify RED**

```bash
pytest -q tests/test_config_registry.py tests/test_persona_privacy.py \
  tests/test_voice_resolver.py tests/test_runtime_settings_legacy_approval.py
```

- [ ] **Step 4: Implement typed contracts**

```python
@dataclass(frozen=True)
class SpeechIntent:
    text: str
    persona: str
    source: str
    session: str
    participant: str
    priority: int
    lane: Literal["live", "normal", "background"]
    interrupt_policy: Literal["interruptible", "finish_current"]
    utterance_index: int


@dataclass(frozen=True)
class RenderSnapshot:
    engine: str
    engine_version: str
    voice_or_style: str
    speed: float
    mastering_profile: str
    dsp: str
    pronunciations: Mapping[str, str]
    pronunciations_sha256: str
    gain: float
    asset_sha256: Mapping[str, str]
    config_revision: str

    def validate_complete(self) -> None:
        if not self.engine or not self.engine_version or not self.voice_or_style:
            raise SnapshotValidationError("engine/version/voice is incomplete")
        if self.speed <= 0 or self.gain <= 0 or not self.asset_sha256:
            raise SnapshotValidationError("speed/gain/assets are incomplete")

    def canonical_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
```

Normalize text to UTF-8/NFC. Producer fields are exactly the `SpeechIntent` fields; engine, voice/style, speed, mastering, DSP, pronunciation and gain are resolver-owned.

- [ ] **Step 5: Implement the key registry and atomic config store**

```python
class ConfigOwner(StrEnum):
    VERSIONED = "versioned"
    INSTALLATION = "installation"
    OWNER = "owner"
    RUNTIME = "runtime"


REGISTRY = {
    "voice.output_gain": ConfigKey(ConfigOwner.INSTALLATION, True, parse_gain),
    "voice.hook_enabled": ConfigKey(ConfigOwner.INSTALLATION, True, parse_bool),
    "input.ptt_hotkey": ConfigKey(ConfigOwner.INSTALLATION, True, parse_hotkey),
}
```

The three rows above show the shape, not the entire registry. Classify **every** current `JarvisConfig`/`VoiceConfig` field and every imported `jarvis.toml`/override key as versioned, installation, owner, runtime or rejected; the completeness test must make an omitted field fail. Validate key and owner **before** any DB/event/file write. Write `config.toml` through mode-`0600` sibling + fsync + `os.replace()`. Voice/persona mappings are versioned and API-read-only. Duplicate/unregistered keys abort daemon startup. `dan config explain` exposes value, owner, source file, revision and consumers.

- [ ] **Step 6: Separate owner data without cloning or taming the persona**

`config/persona/DAN.md` remains the only behavior canon. Replace owner-specific literals with `{{ owner.display_name }}`; `config/owner.example.toml` uses a neutral example. `dan/persona.py` renders canon + local owner at runtime. Tests prove: no real `owner.toml` in Git, no Ozzy facts in example, full uncensored canon survives render, missing canon fails visibly, and no provider-specific persona file exists.

- [ ] **Step 7: Implement the sole resolver**

`VoiceResolver(catalog, installation_config, engine_registry).resolve()` reads the immutable `VoiceCatalog`, applies only installation-owned output gain, freezes the resolved pronunciation mapping, validates engine/version and every asset SHA, and returns `RenderSnapshot`. Remove resolution from `shared_voice.py`, `shared_broker.py`, `tts.py`, panel and routes. Temporary compatibility callers may only delegate to this resolver and emit a migration warning.

- [ ] **Step 8: Verify and commit**

```bash
pytest -q tests/test_config_registry.py tests/test_persona_privacy.py \
  tests/test_voice_resolver.py tests/test_runtime_settings_legacy_approval.py tests/test_shared_voice.py
ruff check dan/config_registry.py dan/persona.py dan/voice/resolver.py dan/voice/models.py
git diff --check
git add dan/config.py dan/config_registry.py dan/persona.py dan/voice/models.py \
  dan/voice/resolver.py dan/api/routes_settings.py dan/daemon/app.py \
  config/persona/DAN.md config/owner.example.toml \
  tests/test_config_registry.py tests/test_persona_privacy.py tests/test_voice_resolver.py \
  tests/test_runtime_settings_legacy_approval.py tests/test_shared_voice.py
git commit -m "feat: centralize DAN configuration and render resolution"
```

---

## Task 6: Version the complete voice catalog and offline pipelines

**Files:**

- Create: `config/voice/personas.toml`, `pronunciations.toml`, `gains.json`
- Create: `config/voice/custom_styles/manifest.json` and distributable style JSON files
- Create: `config/voice/pipelines/chatterbox-v3-zaneta.toml`
- Create: `dan/voice/assets.py`, `dan/voice/pipelines/__init__.py`, `dan/voice/pipelines/chatterbox_v3.py`
- Create: `tests/test_voice_assets.py`, `tests/test_voice_catalog.py`, `tests/test_chatterbox_v3_pipeline.py`, `tests/test_voice_route_matrix.py`
- Create: `docs/migration/VOICE-DECISIONS.md`
- Remove after import: `config/voice/personas.example.toml`, `config/voice/pronunciations.example.toml`

- [ ] **Step 1: Write failing catalog/asset tests**

```python
EXPECTED_PERSONAS = {
    "dan", "danusia", "zaneta", "zdzicho", "krysia", "komentator", "spiker",
    "ksiadz", "typ_z_telefonu", "blondyna", "zagadka", "radiowiec",
    "M1", "M2", "M3", "M4", "M5", "F1", "F2", "F3", "F4", "F5",
}


def test_catalog_has_full_cast_and_one_owner() -> None:
    catalog = load_voice_catalog(Path("config/voice"))
    assert EXPECTED_PERSONAS <= set(catalog.personas)
    assert catalog.duplicate_keys == ()


def test_twenty_custom_styles_are_versioned_and_hash_valid() -> None:
    manifest = load_asset_manifest(Path("config/voice/custom_styles/manifest.json"))
    assert len(manifest.assets) == 20
    assert all(a.sha256 == sha256_file(a.path) for a in manifest.assets)
    assert all(a.source and a.license_decision for a in manifest.assets)
```

- [ ] **Step 2: Write failing reconciliation/route tests**

```python
def test_every_legacy_override_has_final_decision() -> None:
    decisions = load_voice_decisions(Path("docs/migration/VOICE-DECISIONS.md"))
    assert not decisions.pending
    assert "state/overrides.json:jarvis_supertonic_voice" in decisions.source_keys
    assert "state/overrides.json:jarvis_speed" in decisions.source_keys


def test_route_matrix_matches_snapshot_and_playback(route_matrix) -> None:
    for row in route_matrix:
        assert (row.snapshot_voice, row.snapshot_speed, row.snapshot_dsp) == (
            row.playback_voice, row.playback_speed, row.playback_dsp,
        )
```

- [ ] **Step 3: Verify RED**

```bash
pytest -q tests/test_voice_assets.py tests/test_voice_catalog.py \
  tests/test_chatterbox_v3_pipeline.py tests/test_voice_route_matrix.py
```

- [ ] **Step 4: Reconcile all real sources before choosing values**

Compare `~/.config/voice/personas.toml` plus six backups and gains, `state/overrides.json`, `say.py`, both `voice_turn` copies, ramówka/scenariusze, panel, `~/.jarvis/jarvis.toml`, custom-style cache, Chatterbox V3 generators/references, Voice Lab verdicts and accepted samples. `VOICE-DECISIONS.md` gets one final row per persona and legacy key: sources, reader, effective old value, final route, asset, audio evidence and decision. Correct the false legacy comment claiming DSP was feeder-owned.

- [ ] **Step 5: Implement hash-checked, licensed assets**

```python
@dataclass(frozen=True)
class VoiceAsset:
    name: str
    path: Path
    sha256: str
    source: str
    license_decision: Literal["redistributable", "installer-fetch", "local-only"]


def verify_assets(manifest: AssetManifest, *, repo_root: Path) -> None:
    """Fail on missing/hash mismatch; never search user cache as fallback."""
```

Commit only redistributable JSON. For `installer-fetch`, commit legal source + checksum; for `local-only`, commit metadata and return a clear capability error. Clearing `~/.cache/supertonic3` must not change resolution.

- [ ] **Step 6: Add Żaneta's explicit offline Chatterbox V3 pipeline**

```python
class ChatterboxV3ZanetaPipeline:
    live_capable = False

    def render(self, text: str, output: Path, *, manifest: PipelineManifest) -> RenderArtifact:
        verify_reference_rights_and_hash(manifest)
        return run_pinned_generator(text, output, manifest)
```

Offline V3 never silently substitutes another reference/engine. Live fallback is allowed only as an explicit catalog route.

- [ ] **Step 7: Verify cold cache and commit**

```bash
HOME="$(mktemp -d)" pytest -q tests/test_voice_assets.py tests/test_voice_catalog.py \
  tests/test_chatterbox_v3_pipeline.py tests/test_voice_route_matrix.py
python -m dan.voice.assets verify config/voice/custom_styles/manifest.json
git diff --check
git add config/voice dan/voice/assets.py dan/voice/pipelines \
  tests/test_voice_assets.py tests/test_voice_catalog.py \
  tests/test_chatterbox_v3_pipeline.py tests/test_voice_route_matrix.py \
  docs/migration/VOICE-DECISIONS.md
git commit -m "feat: version DAN voice catalog and offline pipelines"
```

---

## Task 7: Persist complete snapshots and make the native broker the sole player

**Files:**

- Modify: `dan/store/schema.sql`, `dan/store/migrations.py`
- Modify: `dan/voice/models.py`, `queue.py`, `broker.py`, `tts.py`
- Create: `dan/voice/player.py`, `tests/test_audio_player.py`
- Modify: `pyproject.toml`
- Create: `dan/voice/service.py`, `tests/test_voice_snapshot_queue.py`, `tests/test_voice_service.py`
- Modify: `tests/test_voice_queue.py`, `tests/test_voice_broker.py`, `tests/test_voice_tts_supertonic.py`
- Remove: `dan/voice/shared_broker.py`, `dan/voice/shared_voice.py`
- Remove/rewrite: `tests/test_shared_voice_broker.py`, `tests/test_shared_voice_runtime_truth.py`

- [ ] **Step 1: Write failing queue/snapshot tests**

```python
def test_incomplete_snapshot_never_reaches_queued(service: VoiceService) -> None:
    with pytest.raises(SnapshotValidationError):
        service.submit(speech_intent("dan"), resolver=resolver_missing_asset_sha())
    assert service.queue.list() == []


def test_snapshot_is_immutable_after_enqueue(queue, complete_snapshot) -> None:
    request = queue.enqueue(speech_intent("dan"), complete_snapshot)
    with pytest.raises(sqlite3.IntegrityError, match="immutable render snapshot"):
        queue.connection.execute(
            "UPDATE voice_queue SET render_snapshot_json = '{}' WHERE id = ?", (request.id,),
        )
```

- [ ] **Step 2: Write failing sole-owner tests**

```python
def test_broker_executes_stored_snapshot_without_reresolve(runtime) -> None:
    request = runtime.voice.submit(speech_intent("dan"))
    runtime.catalog.replace_persona("dan", voice="M1")
    runtime.broker.drain_all()
    assert runtime.engine.synth_calls[0].snapshot == request.render_snapshot


def test_two_producers_never_overlap(runtime) -> None:
    submit_concurrently(runtime, source="claude", text="pierwszy")
    submit_concurrently(runtime, source="codex", text="drugi")
    runtime.broker.drain_all()
    assert runtime.engine.max_parallel_players == 1


def test_backpressure_rejects_instead_of_clogging_queue(runtime) -> None:
    fill_pending_limit(runtime, session="standup")
    with pytest.raises(QueueBackpressure, match="standup"):
        runtime.voice.submit(speech_intent("dan", session="standup"))


def test_live_lane_is_not_starved_by_background(runtime) -> None:
    runtime.voice.submit(speech_intent("dan", lane="background"))
    live = runtime.voice.submit(speech_intent("dan", lane="live"))
    assert runtime.queue.claim_next().id == live.id


def test_multiple_chunks_reuse_one_coreaudio_engine(player, wav_chunks) -> None:
    for chunk in wav_chunks:
        player.play(chunk)
    assert player.engine_start_count == 1
    assert player.max_parallel_buffers == 1
    assert player.measured_inter_chunk_gap_ms < 80
```

- [ ] **Step 3: Verify RED**

```bash
pytest -q tests/test_voice_snapshot_queue.py tests/test_voice_service.py \
  tests/test_voice_queue.py tests/test_voice_broker.py tests/test_audio_player.py
```

- [ ] **Step 4: Add the versioned queue migration**

Add `source`, `session_id`, `participant`, `persona`, `lane`, `utterance_index`, canonical `render_snapshot_json`, synthesis/playback timestamps and `playback_confirmed`. Backfill old rows as `legacy-unresolved` and keep them non-playable until reconciled. New rows cannot use that marker.

```sql
CREATE TRIGGER voice_queue_snapshot_immutable
BEFORE UPDATE OF render_snapshot_json ON voice_queue
WHEN OLD.render_snapshot_json IS NOT NEW.render_snapshot_json
BEGIN SELECT RAISE(ABORT, 'immutable render snapshot'); END;
```

Insert intent + validated snapshot in one statement/transaction; a DB constraint rejects incomplete runtime rows.

Enforce transitions `queued -> synthesizing -> speaking -> done`; cancellation/failure may leave any nonterminal state, but no code may claim a row directly as `speaking`. Add transition tests and timestamps for each edge.

- [ ] **Step 5: Make `VoiceService.submit()` the only enqueue path**

```python
class VoiceService:
    def submit(self, intent: SpeechIntent) -> VoiceRequest:
        snapshot = self._resolver.resolve(intent)
        snapshot.validate_complete()
        return self._queue.enqueue(intent, snapshot)
```

Cancel current + pending chunks atomically by request/session. Producers cannot call `VoiceQueue.enqueue()` directly.

Admission enforces versioned defaults for global and per-session pending limits. A full queue returns `QueueBackpressure`/HTTP `429`/nonzero CLI; it never accepts unlimited work and never hides a dropped request. Claim order is deterministic: lane `live`, then `normal`, then `background`; within a lane use priority and creation sequence. Prefetch is limited to one request so synthesis cannot silently build a second uncontrolled queue.

- [ ] **Step 6: Make TTS snapshot-only and remove fallback resolution**

```python
class TTSEngine(Protocol):
    def synthesize(self, text: str, snapshot: RenderSnapshot) -> SynthesizedChunk:
        raise NotImplementedError


class AudioPlayer(Protocol):
    def play(self, chunk: SynthesizedChunk, *, should_play, on_started) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError
```

Delete `_voice_for()`, `_mastering_filter_for()` and fallback selection. Supertonic serve may fall back to pinned Supertonic CLI for the **same snapshot**, never to another route.

Implement one long-lived `CoreAudioPlayer` with `AVAudioEngine` + `AVAudioPlayerNode`. It accepts mastered PCM/WAV bytes, schedules one buffer at a time, reports start/completion from native callbacks and stops the current node on barge-in. It must not spawn `play`, `afplay` or `ffplay` per chunk; repeated chunks reuse the same audio engine, eliminating device reopen gaps. Add `pyobjc-framework-AVFoundation==12.2.1` to the voice extra. Only native completion yields `playback_confirmed=1` and `done`; emit distinct accepted/synthesis/playback/cancel/failed events.

- [ ] **Step 7: Remove external shared runtime**

Delete active `external_shared`, `/tmp/dan-voice/req`, `DAN_BROKER_ENGINE` and `SharedBrokerClient`. Do not delete live machine queue files; cutover inventories and backs them up.

- [ ] **Step 8: Verify and commit**

```bash
pytest -q tests/test_voice_snapshot_queue.py tests/test_voice_service.py \
  tests/test_voice_queue.py tests/test_voice_broker.py tests/test_voice_tts_supertonic.py \
  tests/test_audio_player.py tests/test_voice_cancellation.py tests/test_voice_fix04.py \
  tests/test_voice_anti_echo.py
! rg -n '/tmp/dan-voice|external_shared|SharedBrokerClient|DAN_BROKER_ENGINE|afplay|ffplay|playback_binary' dan tests
ruff check dan/voice
git diff --check
git add pyproject.toml dan/store/schema.sql dan/store/migrations.py dan/voice tests
git commit -m "feat: make dand queue and broker the sole audio owner"
```

**Review gate:** inspect one mock DB request. Intent, snapshot and playback event must match; cancellation must leave no tail; code scan must show one resolver call site and one player owner.

---

## Task 8: Expose one voice/config contract through API and CLI

**Files:**

- Create: `dan/api/client.py`
- Modify: `dan/api/routes_voice.py`, `dan/api/routes_settings.py`, `dan/api/__init__.py`, `dan/daemon/lifecycle.py`, `dan/cli.py`
- Create: `tests/test_voice_api_contract.py`, `tests/test_cli_speak.py`, `tests/test_cli_queue.py`, `tests/test_cli_config.py`
- Modify: `tests/test_cli_input.py`

- [ ] **Step 1: Write failing machine-contract tests**

```python
def test_speak_json_stdin_contract(cli, daemon) -> None:
    result = cli.run(
        ["speak", "--json", "--as", "dan", "--session", "smoke",
         "--source", "codex", "--stdin"],
        stdin="Zażółć gęślą jaźń.\n",
    )
    payload = json.loads(result.stdout)
    assert result.returncode == 0 and payload["status"] == "queued"
    assert daemon.queue.get(payload["request_id"]).text == "Zażółć gęślą jaźń."


def test_speak_nonzero_means_not_accepted(cli, daemon_without_voice_asset) -> None:
    result = cli.run(["speak", "--json", "--as", "dan", "--stdin"], stdin="test")
    assert result.returncode != 0
    assert daemon_without_voice_asset.queue.list() == []
```

- [ ] **Step 2: Write queue/config API tests**

```python
def test_queue_flush_is_session_scoped(api, queue) -> None:
    a = queue_request(queue, session="radio")
    b = queue_request(queue, session="standup")
    api.post("/voice/queue/flush", {"session": "radio"})
    assert queue.get(a).status == "cancelled"
    assert queue.get(b).status == "queued"


def test_config_explain_names_owner_source_and_value(api) -> None:
    body = api.get("/settings/explain/voice.output_gain").json()
    assert set(body) >= {"key", "value", "owner", "source", "revision", "consumers"}
```

- [ ] **Step 3: Verify RED**

```bash
pytest -q tests/test_voice_api_contract.py tests/test_cli_speak.py \
  tests/test_cli_queue.py tests/test_cli_config.py
```

- [ ] **Step 4: Implement API endpoints**

```text
POST /voice/speak
GET  /voice/queue
POST /voice/queue/{request_id}/cancel
POST /voice/queue/flush
POST /voice/pause
POST /voice/resume
GET  /voice/runtime
GET  /settings/explain/{key}
PUT  /settings/{key}
```

`POST /voice/speak` calls only `VoiceService.submit()` and returns `201` after complete commit. Validation failure creates no queue/event row. Runtime responses redact text by default.

- [ ] **Step 5: Implement final CLI commands**

```text
dan speak [--json] --as PERSONA [--session ID] [--source HOST] [--stdin] [TEXT]
dan queue list [--json]
dan queue cancel REQUEST_ID
dan queue flush --session SESSION
dan config explain KEY [--json]
dan config set KEY VALUE
dan voice hook off|on|status
dan doctor [--json]
```

`--stdin` decodes strict UTF-8 and normalizes NFC. JSON mode prints exactly one object to stdout; logs go to stderr. Exit `0` from `speak` means a complete row is committed as `queued`, nonzero means not accepted.

- [ ] **Step 6: Verify and commit**

```bash
pytest -q tests/test_voice_api_contract.py tests/test_cli_speak.py \
  tests/test_cli_queue.py tests/test_cli_config.py tests/test_cli_input.py
printf 'Zażółć gęślą jaźń.' | DAN_TEST_MODE=1 python -m dan.cli \
  speak --json --as dan --session smoke --source codex --stdin
ruff check dan/api dan/cli.py
git diff --check
git add dan/api dan/daemon/lifecycle.py dan/cli.py tests/test_voice_api_contract.py tests/test_cli_speak.py \
  tests/test_cli_queue.py tests/test_cli_config.py tests/test_cli_input.py
git commit -m "feat: expose DAN voice queue and config contracts"
```

---

## Task 9: Move PTT/hotkey and engine supervision into `dand`

**Files:**

- Create: `dan/input/__init__.py`, `dan/input/hotkey.py`, `dan/input/macos_event_tap.py`
- Create: `dan/daemon/supervisor.py`, `dan/daemon/restart.py`
- Modify: `dan/daemon/app.py`, `dan/daemon/lifecycle.py`, `dan/api/routes_voice.py`
- Move reusable parsing/edge logic from: `dan/panel/hotkey.py`
- Create: `tests/test_daemon_hotkey.py`, `tests/test_engine_supervisor.py`, `tests/test_daemon_single_instance.py`
- Modify: `tests/test_panel_hotkey.py`, `tests/test_daemon_sigterm.py`
- Create: `tests/test_voice_listening.py`

- [ ] **Step 1: Write failing exclusive-hotkey tests**

```python
def test_one_physical_press_creates_one_ptt_pair(runtime, fake_event_tap) -> None:
    runtime.start()
    fake_event_tap.flags_changed(RIGHT_OPTION_DOWN)
    fake_event_tap.flags_changed(RIGHT_OPTION_DOWN)
    fake_event_tap.flags_changed(RIGHT_OPTION_UP)
    assert runtime.events.of_type("ptt.down").count() == 1
    assert runtime.events.of_type("ptt.up").count() == 1


def test_second_hotkey_owner_is_rejected(runtime, lock_path) -> None:
    runtime.hotkey.start()
    with pytest.raises(SingleOwnerError):
        MacOSHotkeyMonitor(lock_path=lock_path).start()
```

- [ ] **Step 2: Write failing supervised-child tests**

```python
def test_supertonic_is_one_supervised_child(supervisor, fake_process_factory) -> None:
    first = supervisor.ensure_running("supertonic")
    second = supervisor.ensure_running("supertonic")
    assert first.pid == second.pid
    assert fake_process_factory.starts == 1


def test_daemon_shutdown_reaps_player_engine_and_hotkey(runtime) -> None:
    runtime.start()
    runtime.stop()
    assert runtime.child_pids() == []
    assert runtime.hotkey.running is False
    assert runtime.broker.current_player is None
```

- [ ] **Step 3: Verify RED**

```bash
pytest -q tests/test_daemon_hotkey.py tests/test_engine_supervisor.py \
  tests/test_daemon_single_instance.py tests/test_daemon_sigterm.py
```

- [ ] **Step 4: Implement daemon-owned Quartz event tap**

Move `parse_hotkey` and `HotkeyEdgeDetector` to `dan/input/hotkey.py`. `macos_event_tap.py` owns one `CGEventTap` + CFRunLoop source in a named daemon thread, reports Accessibility permission as a health blocker and calls the in-process PTT controller. It never sends HTTP back into the same daemon.

```python
class MacOSHotkeyMonitor:
    def start(self) -> None:
        self._owner_lock.acquire()
        self._event_tap.start(self._on_flags_changed)

    def stop(self) -> None:
        self._event_tap.stop()
        self._owner_lock.release()

    def health(self) -> HotkeyHealth:
        return HotkeyHealth(running=self._event_tap.running, accessibility=self._trusted())
```

Remove `NSEvent.addGlobalMonitor...` and `PttHotkeyClient` ownership from panel code. The panel may display hotkey state and post manual PTT intent only.

Because this is a GUI-session `LaunchAgent`, preflight records the exact executable identity used by TCC (`~/.dan/venv/bin/python` plus `~/.dan/bin/dand`). Installer and `dan doctor` must detect whether that identity has Accessibility permission and print one concrete System Settings path when it does not. Never fall back to a panel listener; permission missing means PTT is visibly unavailable while the rest of `dand` remains healthy.

- [ ] **Step 5: Implement supervised Supertonic lifecycle**

```python
@dataclass(frozen=True)
class ChildSpec:
    name: str
    argv: tuple[str, ...]
    health_url: str
    restart_limit: int
    backoff_seconds: tuple[float, ...]


class ChildSupervisor:
    def start(self, spec: ChildSpec) -> ChildHandle:
        return self._children.get(spec.name) or self._spawn_and_probe(spec)

    def stop(self, name: str, timeout: float = 5.0) -> None:
        self._terminate_process_group(self._children.pop(name), timeout)

    def status(self) -> Mapping[str, ChildStatus]:
        return {name: child.status() for name, child in self._children.items()}
```

Only `dand` starts `supertonic serve`. Startup detects and rejects an unrelated existing owner of the configured port; it does not adopt or kill it silently. Shutdown terminates the process group and proves no orphan remains.

- [ ] **Step 6: Add safe daemon restart semantics**

`POST /runtime/restart` first closes intake, drains or explicitly cancels in-flight voice, stops child/player/hotkey and exits with the documented restart code. In production `launchd` restarts `dand`; in tests `RestartCoordinator` is injected. The endpoint must never call `launchctl` or `pkill`.

- [ ] **Step 7: Verify and commit**

```bash
pytest -q tests/test_daemon_hotkey.py tests/test_engine_supervisor.py \
  tests/test_daemon_single_instance.py tests/test_daemon_sigterm.py \
  tests/test_panel_hotkey.py tests/test_voice_listening.py
! rg -n 'addGlobalMonitorForEventsMatchingMask|PttHotkeyClient' dan/panel
ruff check dan/input dan/daemon
git diff --check
git add dan/input dan/daemon dan/api/routes_voice.py dan/panel/hotkey.py \
  tests/test_daemon_hotkey.py tests/test_engine_supervisor.py \
  tests/test_daemon_single_instance.py tests/test_daemon_sigterm.py \
  tests/test_panel_hotkey.py tests/test_voice_listening.py
git commit -m "feat: make dand own hotkey and engine lifecycle"
```

---

## Task 10: Make the existing panel the only operator UI

**Files:**

- Modify: `dan/panel/menubar_app.py`, `dan/panel/webview_bridge.py`
- Modify: `dan/panel/assets/index.html`, `app.js`, `styles.css`
- Modify: `dan/api/routes_runtime.py`, `dan/api/routes_voice.py`
- Create: `dan/api/routes_sessions.py`
- Create: `tests/test_panel_operator_api.py`, `tests/test_panel_no_runtime_ownership.py`
- Modify: `tests/test_panel_assets.py`, `tests/test_panel_menubar.py`, `tests/test_panel_daemon_assets.py`
- Read-only donor: `/Users/n1_ozzy/Documents/dev/menubar-controller/menubar_controller.py`

- [ ] **Step 1: Write failing thin-client tests**

```python
FORBIDDEN_PANEL_TOKENS = {
    "/tmp/dan-", "launchctl", "pkill", "pgrep", "voice_broker.py",
    "personas.toml", "subprocess.Popen", "os.kill",
}


def test_panel_has_no_runtime_ownership(repo_root: Path) -> None:
    source = all_source_under(repo_root / "dan/panel")
    assert not (FORBIDDEN_PANEL_TOKENS & source.tokens)


def test_panel_controls_are_api_intents(panel, fake_api) -> None:
    panel.pause_voice()
    panel.skip_current()
    panel.safe_restart()
    assert fake_api.calls == [
        ("POST", "/voice/pause"),
        ("POST", "/voice/queue/current/cancel"),
        ("POST", "/runtime/restart"),
    ]
```

- [ ] **Step 2: Write state/error notification tests**

```python
def test_panel_distinguishes_accepted_synthesized_and_played(panel) -> None:
    state = panel.render(runtime_fixture(playback_confirmed=False))
    assert state.voice_label == "syntetyzowanie"
    state = panel.render(runtime_fixture(playback_confirmed=True))
    assert state.voice_label == "odtworzono"


def test_down_and_recovered_notify_once(panel, notifier) -> None:
    panel.poll(api_down())
    panel.poll(api_down())
    panel.poll(api_up())
    assert notifier.messages == ["DAN padł", "DAN znów działa"]
```

- [ ] **Step 3: Verify RED**

```bash
pytest -q tests/test_panel_operator_api.py tests/test_panel_no_runtime_ownership.py \
  tests/test_panel_assets.py tests/test_panel_menubar.py
```

- [ ] **Step 4: Expose truthful operator endpoints first**

Add API responses for daemon, brain/provider, memory, microphone, hotkey, broker, TTS/STT, queue, active request, session/model usage and health blockers. Report `unknown` when a metric is not owned or measurable; never synthesize fake green state. Include only IDs/previews safe for local panel.

- [ ] **Step 5: Port only useful donor behavior**

From `menubar_controller.py`, reimplement through API:

- visible state of daemon/broker/TTS/STT/queue/active speech;
- pause, resume, skip, cancel and safe restart;
- health errors;
- session/model usage when daemon has authoritative data;
- one-shot `padło` / `wróciło` notifications.

Do **not** copy its `/tmp` readers, persona lookup, request-file writes, `launchctl`, `os.kill`, `pgrep`, hardcoded repo paths, doctor shell or service management.

- [ ] **Step 6: Keep one panel and one hotkey owner**

Delete the panel's global hotkey monitor. The panel launches on demand as a client; losing `dand` shows `offline` and disables mutations. No second widget or Radio tab is built in Wydanie 1.

- [ ] **Step 7: Verify and commit**

```bash
pytest -q tests/test_panel_operator_api.py tests/test_panel_no_runtime_ownership.py \
  tests/test_panel_assets.py tests/test_panel_menubar.py tests/test_panel_daemon_assets.py
! rg -n '/tmp/dan-|launchctl|pkill|pgrep|voice_broker\.py|personas\.toml|os\.kill' dan/panel
ruff check dan/panel dan/api/routes_runtime.py dan/api/routes_sessions.py
git diff --check
git add dan/panel dan/api/routes_runtime.py dan/api/routes_voice.py \
  dan/api/routes_sessions.py tests/test_panel_operator_api.py \
  tests/test_panel_no_runtime_ownership.py tests/test_panel_assets.py \
  tests/test_panel_menubar.py tests/test_panel_daemon_assets.py
git commit -m "feat: consolidate operator controls in DAN panel"
```

---

## Task 11: Install one daemon and migrate every active host adapter

**Files:**

- Create: `integrations/manifest.toml`
- Create: `integrations/claude/skills/dan-persona/SKILL.md`
- Create: `integrations/claude/hooks/tts-message-display.sh`
- Create: `integrations/codex/skills/dan-persona/SKILL.md`
- Create: `integrations/openclaw/skills/dan/SKILL.md`
- Create: thin shared adapter templates for `gadanie`, `dobranocka`, `trio-live`, `danusia-live`, `gpt-say`, `voice-report`, standup and `screen-control`
- Create: `dan/install/__init__.py`, `dan/install/adapters.py`, `dan/install/launchd.py`, `dan/install/preflight.py`
- Create: `dan/jobs/__init__.py`, `dan/jobs/scheduler.py`, `dan/jobs/standup.py`
- Create: `scripts/install.sh`, `scripts/uninstall.sh`
- Modify: `launchd/com.dan.dand.plist.example`, `scripts/install-launchd.sh`, `scripts/uninstall-launchd.sh`
- Create: `tests/test_adapter_contracts.py`, `tests/test_hook_fail_open.py`, `tests/test_installer_atomicity.py`, `tests/test_launchd_single_owner.py`, `tests/test_jobs_scheduler.py`
- Modify: `tests/test_launchd_assets.py`

- [ ] **Step 1: Write the common-adapter contract tests**

```python
HOSTS = {"claude", "codex", "openclaw", "gpt-say", "standup", "hook"}


@pytest.mark.parametrize("host", sorted(HOSTS))
def test_machine_adapter_uses_exact_speak_contract(host, installed_adapter) -> None:
    invocation = installed_adapter(host).invoke("Zażółć gęślą jaźń.")
    assert invocation.argv == [
        "dan", "speak", "--json", "--as", invocation.persona,
        "--session", invocation.session, "--source", host, "--stdin",
    ]
    assert invocation.stdin_encoding == "utf-8"
    assert "/tmp/dan" not in " ".join(invocation.argv)


def test_adapter_manifest_accounts_for_every_inventory_producer(manifest, inventory) -> None:
    assert set(manifest.producer_ids) == set(inventory.producer_ids)
    assert not manifest.pending
```

- [ ] **Step 2: Write fail-open and installer safety tests**

```python
def test_message_display_hook_is_fail_open_when_dand_is_down(hook, clock) -> None:
    result = hook.run("tekst", daemon="offline")
    assert result.returncode == 0
    assert result.elapsed < 1.0
    assert result.started_fallback is False


def test_installer_never_touches_claude_archive(tmp_home, installer) -> None:
    archive = seed_archive(tmp_home)
    before = tree_hash(archive)
    installer.apply(home=tmp_home)
    assert tree_hash(archive) == before
```

- [ ] **Step 3: Write single-launchd-owner tests**

```python
def test_install_has_one_product_launchd_label(installed_home) -> None:
    labels = product_launchd_labels(installed_home / "Library/LaunchAgents")
    assert labels == ["com.dan.dand"]


def test_standup_schedule_runs_inside_dand(job_scheduler) -> None:
    job_scheduler.tick(at="09:00")
    assert job_scheduler.submissions == [speech_intent(source="standup", session="standup")]
```

- [ ] **Step 4: Verify RED**

```bash
pytest -q tests/test_adapter_contracts.py tests/test_hook_fail_open.py \
  tests/test_installer_atomicity.py tests/test_launchd_single_owner.py tests/test_jobs_scheduler.py
```

- [ ] **Step 5: Build a complete host decision manifest**

Each producer from Task 1 gets: source path, host, old format, behavior to preserve, destination template, status (`migrated`, `disabled`, `rejected`) and test. The minimum explicit rows are:

- global/repo `dan-persona`, `maintaining-dan-persona`, `gadanie`, `dobranocka`, `trio-live`, `danusia-live`, `gpt-say`, `voice-report`, `screen-control`, `voice-doctor`;
- Claude MessageDisplay hook and global `CLAUDE.md`;
- Codex rules and active procedural skill/memory references;
- OpenClaw `radio-dan`, disabled `danv2-enhanced` and live `ai.openclaw.gateway` host;
- voice standup, hook switch, panel and any script writing old request JSON;
- old broker, XTTS and standup plists plus installed `~/.jarvis/bin/jarvisd`.

No row may be omitted because it appears dead; dead is a tested `disabled` decision.

For each old JSON schema, trace every field to an actual reader. In particular, record whether per-request `engine` was consumed and mark `DAN_BROKER_ENGINE`/its comment as no-op if runtime evidence shows it never affected the request. Preserve no lying compatibility export. For the old dobranocka prefix behavior, add a regression proving the new adapter routes persona through explicit `--as`; a textual `DAN:` prefix remains spoken content or is normalized by one documented importer, never an implicit second router.

- [ ] **Step 6: Implement thin generated adapters**

Adapters contain only host invocation/context and call the same CLI. They do not copy persona text, voice maps, engine choice, mastering or fallback logic. Canonical persona adapters load `config/persona/DAN.md` and owner context from `dan persona context`; missing canon fails visibly.

The hook uses a hard timeout below one second, logs locally, returns `0` when `dand` is unavailable and never starts old audio. `dan voice hook off|on|status` maps to `voice.hook_enabled` in `~/.dan/config.toml`; session override is explicit and does not create `/tmp/claude-loud-thinking`.

- [ ] **Step 7: Move standup scheduling into `dand`**

Implement a minimal persistent job scheduler for existing standup timing. It submits through `VoiceService`; it is **not** the Radio scheduler. Import enabled/time settings from the old plist once, record the source, then disable the old plist during cutover.

- [ ] **Step 8: Implement an atomic, backup-first installer**

`InstallPlan` exposes exactly five phases: `preflight() -> PreflightReport`,
`render(staging: Path) -> None`, `verify(staging: Path) -> None`,
`apply(backup_root: Path) -> InstallReport` and
`rollback(report: InstallReport) -> None`. `apply()` may run only after the
same staging tree has passed `verify()`; its report contains every installed
path, backup path, before/after SHA and inverse operation.

Installer renders into staging, verifies hashes/permissions, backs up every replaced path, then `os.replace()`s files/symlinks. Global `AGENTS.md` and `CLAUDE.md` are changed only inside named managed blocks. It never follows a symlink and overwrites its target blindly. It excludes `~/.claude/archive` structurally, not just by convention.

`scripts/install.sh` is the single human entrypoint. It discovers the clone root, creates `~/.dan/venv`, installs the checkout in editable mode with `[voice,panel]`, creates `~/.dan/bin/dan` and `~/.dan/bin/dand` wrappers, installs product config/assets and then delegates launchd/adapters to the Python installer. `--stage-only` performs render/hash/import checks in a temporary staging root and changes no active home path. `--no-launchd` performs a complete install into the selected HOME but does not bootstrap the agent, which is used for clean-clone tests. `scripts/uninstall.sh` removes only paths owned by its install manifest and never deletes `~/.dan/dan.db`, owner data or migration backups.

- [ ] **Step 9: Install one launchd agent**

`launchd/com.dan.dand.plist.example` uses label `com.dan.dand`, `~/.dan/bin/dand`, the discovered product root, `RunAtLoad=true`, `KeepAlive=true`, log paths under `~/.dan/logs`, no secrets in plist and no second broker/TTS/hotkey job. `dand` supervises Supertonic. OpenClaw gateway and unrelated `com.ozzy.higiena` stay external and are not relabeled as DAN.

- [ ] **Step 10: Verify in a disposable HOME**

```bash
TMP_HOME="$(mktemp -d)"
HOME="$TMP_HOME" python -m dan.install.preflight --json
HOME="$TMP_HOME" bash scripts/install.sh --stage-only
HOME="$TMP_HOME" pytest -q tests/test_adapter_contracts.py tests/test_hook_fail_open.py \
  tests/test_installer_atomicity.py tests/test_launchd_single_owner.py tests/test_jobs_scheduler.py \
  tests/test_launchd_assets.py
rg -n '/tmp/dan-|voice_broker\.py|feeder\.sh|Documents/dev/(dan|jarvis|DANv2)' \
  "$TMP_HOME/.agents" "$TMP_HOME/.claude" "$TMP_HOME/.codex" "$TMP_HOME/.openclaw" \
  && exit 1 || true
```

- [ ] **Step 11: Commit**

```bash
git diff --check
git add integrations dan/install dan/jobs launchd/com.dan.dand.plist.example \
  scripts/install.sh scripts/uninstall.sh scripts/install-launchd.sh scripts/uninstall-launchd.sh \
  tests/test_adapter_contracts.py tests/test_hook_fail_open.py \
  tests/test_installer_atomicity.py tests/test_launchd_single_owner.py \
  tests/test_jobs_scheduler.py tests/test_launchd_assets.py
git commit -m "feat: install one DAN runtime for every host"
```

**Review gate:** install and rollback twice in a disposable HOME. Verify all producer rows, one product plist, unchanged Claude archive, hook under one second when offline, and no adapter with an old repo/request-file path.

---

## Task 12: Build journaled cutover and rollback tooling

**Files:**

- Create: `dan/migration/runtime_probe.py`
- Create: `dan/migration/cutover.py`
- Create: `dan/migration/rollback.py`
- Create: `dan/migration/journal.py`
- Create: `scripts/dan-cutover`, `scripts/dan-rollback`
- Create: `tests/test_cutover_preconditions.py`
- Create: `tests/test_cutover_state_machine.py`
- Create: `tests/test_cutover_rollback.py`
- Create: `tests/test_cutover_no_replay.py`
- Create: `tests/fixtures/cutover/manifest.json` and its synthetic source tree/databases

- [ ] **Step 1: Write failing precondition tests**

```python
@pytest.mark.parametrize("state", ["queued", "synthesizing", "speaking"])
def test_cutover_refuses_non_quiescent_queue(state, cutover) -> None:
    cutover.fixture_queue(state)
    with pytest.raises(CutoverBlocked, match=state):
        cutover.prepare()


def test_cutover_refuses_live_db_writer(cutover) -> None:
    cutover.fixture_writer(pid=777, path="~/.jarvis/jarvis.db")
    with pytest.raises(CutoverBlocked, match="writer"):
        cutover.prepare()


def test_cutover_requires_every_producer_decision(cutover) -> None:
    cutover.manifest.producers["old-feeder"].decision = None
    with pytest.raises(CutoverBlocked, match="old-feeder"):
        cutover.prepare()
```

- [ ] **Step 2: Write failing rollback/no-replay tests**

```python
def test_rollback_restores_paths_plists_databases_and_adapters(cutover_fixture) -> None:
    report = cutover_fixture.apply()
    cutover_fixture.rollback(report.journal)
    assert cutover_fixture.tree_hash() == cutover_fixture.before_hash


def test_interrupted_request_is_not_replayed_after_rollback(cutover_fixture) -> None:
    request_id = cutover_fixture.speaking_request()
    report = cutover_fixture.apply(cancel_in_flight=True)
    cutover_fixture.rollback(report.journal)
    assert cutover_fixture.request(request_id).status == "cancelled"
    assert cutover_fixture.play_count(request_id) == 0
    assert cutover_fixture.runtime_state().speaking is None
```

- [ ] **Step 3: Verify RED**

```bash
pytest -q tests/test_cutover_preconditions.py tests/test_cutover_state_machine.py \
  tests/test_cutover_rollback.py tests/test_cutover_no_replay.py
```

- [ ] **Step 4: Implement an explicit phase journal**

```python
class CutoverPhase(StrEnum):
    INVENTORIED = "inventoried"
    INTAKE_CLOSED = "intake_closed"
    QUEUE_QUIESCENT = "queue_quiescent"
    RUNTIME_STOPPED = "runtime_stopped"
    DATABASES_BACKED_UP = "databases_backed_up"
    DATABASES_MIGRATED = "databases_migrated"
    PATHS_MOVED = "paths_moved"
    ADAPTERS_INSTALLED = "adapters_installed"
    LAUNCHD_INSTALLED = "launchd_installed"
    COLD_STARTED = "cold_started"
    VERIFIED = "verified"


@dataclass(frozen=True)
class JournalEntry:
    phase: CutoverPhase
    operation: str
    source: str | None
    destination: str | None
    before_sha256: str | None
    after_sha256: str | None
    rollback_operation: str
```

Write journal/report atomically under a generated UTC directory such as `~/.dan/migration/cutover-20260716T220000Z/` with directory/file modes `0700/0600`. Every mutation has a recorded inverse before execution. Default command is dry-run; mutation requires both `--apply` and exact manifest SHA.

- [ ] **Step 5: Implement quiescence and controlled backups**

Order is fixed:

1. close old and new intake;
2. drain or explicitly cancel every queued/synthesizing/speaking request;
3. record old request files in backup manifest and move them, never blind `rm`;
4. boot out old product agents so they cannot respawn;
5. prove process/port/player/hotkey and DB-writer absence using `ps`, `lsof`, `launchctl`;
6. checkpoint, SQLite Backup API copy, `integrity_check`, counts and SHA for every DB;
7. migrate disposable copies to `dan.db`, verify, then atomically install target.

An unrecognized process or DB handle blocks instead of being killed.

The same backup manifest preserves `~/.jarvis/jarvis.toml`, `bin/jarvisd`, `model_cache.json`, `backups/` metadata and existing `~/.dan/` non-DB files before replacement. Their manifest decisions say import, retain as historical backup or reject with reason; nothing is dropped merely because `lsof` shows no current handle.

- [ ] **Step 6: Implement reversible path and adapter moves**

On macOS case-insensitive filesystem:

1. set `STAMP=$(date -u +%Y%m%dT%H%M%SZ)` and move old `/Users/n1_ozzy/Documents/dev/dan` to `/Users/n1_ozzy/Documents/DAN-migration-backups/$STAMP/dev-dan`;
2. move final accepted `/Users/n1_ozzy/Documents/dev/jarvis` to `/Users/n1_ozzy/Documents/dev/DAN`;
3. run installer from calculated new root;
4. keep `DANv2` and `menubar-controller` as untouched donors until the observation gate;
5. never delete a donor in cutover.

The running migration process must reopen all post-move resources from the new absolute root; tests simulate the rename. Rollback first closes new intake and `dand`, verifies `speaking=null`, restores adapters/plists/config/DB/path order and only then allows the old runtime to start.

- [ ] **Step 7: Add dry-run and status commands**

```text
python scripts/dan-cutover preflight --manifest ~/.dan/migration/release1-source-manifest.json
python scripts/dan-cutover plan --manifest ~/.dan/migration/release1-source-manifest.json
python scripts/dan-cutover apply --apply --manifest-sha256 SHA256
LATEST_JOURNAL="$(find ~/.dan/migration -name journal.jsonl -type f -print | sort | tail -1)"
python scripts/dan-cutover status --journal "$LATEST_JOURNAL"
python scripts/dan-rollback apply --apply --journal "$LATEST_JOURNAL"
```

Commands print the exact processes, paths, DB counts and pending destructive operations. `apply` refuses a dirty final integration tree, missing backup space, unresolved manifest row, stale manifest SHA or unavailable rollback destination.

- [ ] **Step 8: Verify and commit**

```bash
pytest -q tests/test_cutover_preconditions.py tests/test_cutover_state_machine.py \
  tests/test_cutover_rollback.py tests/test_cutover_no_replay.py tests/test_sqlite_backup.py
HOME="$(mktemp -d)" python scripts/dan-cutover plan \
  --fixture tests/fixtures/cutover --manifest tests/fixtures/cutover/manifest.json
ruff check dan/migration
git diff --check
git add dan/migration scripts/dan-cutover scripts/dan-rollback \
  tests/fixtures/cutover \
  tests/test_cutover_preconditions.py tests/test_cutover_state_machine.py \
  tests/test_cutover_rollback.py tests/test_cutover_no_replay.py
git commit -m "feat: add journaled DAN cutover and rollback"
```

---

## Task 13: Add human documentation, privacy audit and clean-clone verification

**Files:**

- Modify: `README.md`
- Create: `docs/CO-JEST-GDZIE.md`
- Create: `docs/GLOS-I-KOLEJKA.md`
- Create: `docs/PANEL.md`
- Create: `docs/RADIO-DAN.md`
- Create: `docs/PRZENOSZENIE.md`
- Create: `docs/ODZYSKIWANIE.md`
- Create: `docs/adr/001-dand-single-owner.md`
- Modify: active specs/plans/migration docs containing `/Users/n1_ozzy`
- Create: `dan/release_audit.py`
- Create: `scripts/dan-release-audit`
- Create: `scripts/dan-voice-acceptance`
- Create: `tests/test_docs_commands.py`
- Create: `tests/test_release_privacy.py`
- Create: `tests/test_active_reference_scan.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Write failing documentation/command tests**

```python
REQUIRED_DOCS = {
    "README.md", "docs/CO-JEST-GDZIE.md", "docs/GLOS-I-KOLEJKA.md",
    "docs/PANEL.md", "docs/RADIO-DAN.md", "docs/PRZENOSZENIE.md",
    "docs/ODZYSKIWANIE.md",
}


def test_every_documented_command_parses(repo_root: Path) -> None:
    for command in extract_dan_commands(REQUIRED_DOCS):
        result = run_cli_help_or_dry_run(command, home=temp_home())
        assert result.returncode == 0, command


def test_voice_doc_contains_six_real_examples() -> None:
    examples = extract_shell_examples(Path("docs/GLOS-I-KOLEJKA.md"))
    assert len([e for e in examples if e.startswith("dan ")]) >= 6
```

- [ ] **Step 2: Write failing privacy/reference tests**

```python
def test_repository_has_no_private_runtime_data(repo_root: Path) -> None:
    findings = audit_worktree(repo_root)
    assert findings.private_paths == []
    assert findings.absolute_owner_paths == []
    assert findings.secrets == []


def test_active_roots_have_no_executable_legacy_reference(fake_home: Path) -> None:
    findings = scan_active_roots(fake_home, exclude=(fake_home / ".claude/archive",))
    assert findings == []
```

- [ ] **Step 3: Verify RED**

```bash
pytest -q tests/test_docs_commands.py tests/test_release_privacy.py \
  tests/test_active_reference_scan.py
```

- [ ] **Step 4: Write short operator-first documentation**

Keep each usage document task-oriented, no migration diary:

- `README.md`: install, first start, panel, three first commands;
- `CO-JEST-GDZIE.md`: one table of element, owner, path;
- `GLOS-I-KOLEJKA.md`: broker, statuses, snapshot, queue, old feeder vs current W1 behavior, offline render and at least six copy/paste CLI examples;
- `PANEL.md`: every state/button and `offline` meaning;
- `RADIO-DAN.md`: clearly says Radio Studio is Wydanie 2, names current compatible adapters, does not pretend scheduler exists;
- `PRZENOSZENIE.md`: Git vs local/private, asset licensing, clean M5 install;
- `ODZYSKIWANIE.md`: exactly five primary diagnostics plus journaled rollback command.

Examples must include Polish characters, two personas, JSON/stdin, queue list, cancel/flush, config explain and hook switch.

- [ ] **Step 5: Implement release/privacy audit**

`scripts/dan-release-audit` scans:

- current worktree and every Git ref/object for secret/private patterns;
- active instructions/adapters under `~/AGENTS.md`, `~/.agents`, `~/.claude` excluding archive, `~/.codex`, `~/.openclaw`, LaunchAgents and active repos;
- executable legacy refs to old repos, `/tmp/dan-*`, `/tmp/claude-loud-thinking`, feeder, broker and direct player;
- versioned asset license decisions and SHA.

Before the release audit, replace owner-specific absolute paths in the **current tree** with `$HOME`, `$DAN_REPO`, `$DAN_BACKUP_ROOT` or neutral examples using explicit path-scoped edits. This includes the accepted spec and this implementation plan; it is not a blind home-wide replace. Historical Git objects may retain non-secret migration evidence, but secrets are scanned across all refs/history. Executable current files get no exemption. The scanner uses path/context allowlists, not a global string exemption.

- [ ] **Step 6: Add deterministic voice acceptance harness**

`scripts/dan-voice-acceptance` submits one bounded phrase per approved persona through `dan speak`, waits for terminal state and exports a local JSON report with intent, snapshot, timings and playback event. It never bulk-fills the queue; default max pending is one. `--mock` is automatic, `--live-audio` requires explicit flag and confirmation.

- [ ] **Step 7: Verify clean clone and package**

Add `build>=1.2` to the development extra so packaging is part of the reproducible toolchain rather than an undeclared machine dependency.

```bash
pytest -q tests/test_docs_commands.py tests/test_release_privacy.py tests/test_active_reference_scan.py
python scripts/dan-release-audit --repo . --all-git-refs
python -m build
CLONE_ROOT="$(mktemp -d)"
git clone --no-local . "$CLONE_ROOT/DAN"
CLEAN_HOME="$(mktemp -d)"
HOME="$CLEAN_HOME" bash "$CLONE_ROOT/DAN/scripts/install.sh" --no-launchd
HOME="$CLEAN_HOME" "$CLEAN_HOME/.dan/bin/dan" doctor --json
```

Expected: no dependency on user caches/repos, no private data, valid custom styles after empty cache, installer preflight explains every local-only asset instead of hiding a fallback.

Record `uname -m` and `system_profiler SPHardwareDataType` with the clean-clone report. The portability gate is complete only on an actual Apple Silicon M5 clean profile (this Mac if it qualifies, otherwise the friend's Mac in the later transfer run); a test on different hardware is useful evidence but not a substitute.

- [ ] **Step 8: Commit**

```bash
git diff --check
git add README.md docs/CO-JEST-GDZIE.md docs/GLOS-I-KOLEJKA.md docs/PANEL.md \
  docs/RADIO-DAN.md docs/PRZENOSZENIE.md docs/ODZYSKIWANIE.md \
  docs/adr/001-dand-single-owner.md \
  docs/superpowers/specs/2026-07-16-dan-product-consolidation-design.md \
  docs/superpowers/plans/2026-07-16-dan-foundation-release-1.md \
  docs/migration pyproject.toml dan/release_audit.py \
  scripts/dan-release-audit scripts/dan-voice-acceptance \
  tests/test_docs_commands.py tests/test_release_privacy.py tests/test_active_reference_scan.py
git commit -m "docs: add DAN operator and transfer guide"
```

---

## Task 14: Pass final gates, rehearse rollback and execute cutover

**Files:**

- Update after evidence: `docs/migration/TEST-BASELINE.md`
- Update after evidence: `docs/migration/VOICE-DECISIONS.md`
- Local only: `~/.dan/migration/release1-acceptance.json`
- Local only: generated `~/.dan/migration/cutover-YYYYMMDDTHHMMSSZ/`

- [ ] **Step 1: Freeze the accepted branch and run automated gates**

```bash
cd /Users/n1_ozzy/Documents/dev/DAN-release1-wt
git status --short --branch
python scripts/dan-test-baseline --compare ~/.dan/migration/test-baseline.json
pytest -q -m 'not live_manual'
ruff check dan tests
python scripts/dan-release-audit --repo . --all-git-refs --active-home
python -m build
git diff --check
```

Expected: no new failures, no unclassified tests, no active old refs, no privacy/secret findings, clean tree. Record exact test counts and SHAs; do not write “all good” without output.

- [ ] **Step 2: Rehearse cutover and rollback in a full disposable fixture**

```bash
FIXTURE_HOME="$(mktemp -d)"
python scripts/dan-cutover apply --apply \
  --fixture tests/fixtures/cutover --home "$FIXTURE_HOME" \
  --manifest-sha256 "$(python scripts/dan-cutover fixture-sha tests/fixtures/cutover)"
python scripts/dan-rollback apply --apply \
  --journal "$FIXTURE_HOME/.dan/migration/latest/journal.jsonl"
python scripts/dan-cutover verify-fixture --home "$FIXTURE_HOME"
```

Expected: byte-identical restored fixture, DB counts/integrity equal, `speaking=null`, no replay and no leftover launchd/adapters.

- [ ] **Step 3: Run explicit live voice gates before cutover**

First confirm the old live feeder/broker can be stopped and no show is expected. Then run the new runtime in an isolated port/runtime directory and **only now** allow audio:

```bash
python scripts/dan-voice-acceptance --live-audio --max-pending 1 \
  --output ~/.dan/migration/release1-voice-acceptance.json
```

Required evidence:

- every approved persona matches its accepted voice, speed, mastering/DSP;
- Polish diacritics sound correct;
- prepared short/long phrases have no swallowed endings or artificial long gaps;
- two concurrent producers remain serial;
- cancel/barge-in stops without late tail;
- Żaneta offline V3 and explicit live route behave as documented;
- Ozzy records accept/reject per route in `VOICE-DECISIONS.md` and no rejected route proceeds.

- [ ] **Step 4: Fast-forward the original integration tree**

Before touching the original path, stop any runtime consuming it. Then:

```bash
cd /Users/n1_ozzy/Documents/dev/jarvis
git status --short --branch
git merge --ff-only feat/dan-foundation-release1
git rev-parse HEAD
git worktree remove /Users/n1_ozzy/Documents/dev/DAN-release1-wt
```

If the original tree has unrelated dirty changes, stop and preserve them explicitly; never stash or overwrite. The commit SHA must equal the fully verified feature head.

- [ ] **Step 5: Run real preflight without mutation**

```bash
cd /Users/n1_ozzy/Documents/dev/jarvis
python scripts/dan-inventory --check ~/.dan/migration/release1-source-manifest.json
python scripts/dan-cutover preflight \
  --manifest ~/.dan/migration/release1-source-manifest.json
python scripts/dan-cutover plan \
  --manifest ~/.dan/migration/release1-source-manifest.json
```

Read the printed process list, queue states, DB writers, disk space, backups, path moves, plist/adapter changes and rollback destination. Any drift from Task 1 regenerates the manifest and invalidates the old SHA.

- [ ] **Step 6: Execute the real cutover**

```bash
MANIFEST=~/.dan/migration/release1-source-manifest.json
MANIFEST_SHA="$(shasum -a 256 "$MANIFEST" | awk '{print $1}')"
python scripts/dan-cutover apply --apply \
  --manifest "$MANIFEST" --manifest-sha256 "$MANIFEST_SHA"
```

The tool closes intake, drains/cancels queue, stops old agents/processes, proves no writers, backs up/migrates DBs, moves old `dev/dan` outside active `Documents/dev`, renames `dev/jarvis` to `dev/DAN`, installs adapters + `com.dan.dand`, cold-starts and writes the journal. It must not delete `DANv2`, `menubar-controller` or backups.

- [ ] **Step 7: Prove the new runtime from the final path**

Open a fresh shell/session in `/Users/n1_ozzy/Documents/dev/DAN` and run:

```bash
cd /Users/n1_ozzy/Documents/dev/DAN
dan doctor --json
dan config explain voice.output_gain --json
printf 'Zażółć gęślą jaźń.' | dan speak --json \
  --as dan --session cutover-smoke --source operator --stdin
dan queue list --json
launchctl print "gui/$(id -u)/com.dan.dand"
ps -axo pid,ppid,command | rg 'dand|supertonic|voice_broker|jarvisd|feeder'
lsof ~/.dan/dan.db ~/.dan/dan.db-wal ~/.dan/dan.db-shm
```

Then verify panel, PTT exactly once, Claude, Codex, OpenClaw, standup, hook on/off/offline fail-open, dobranocka adapter, `gpt-say`, `voice-report`, Trio/screen-control and a logout/login-equivalent cold start. Expected: one `dand`, one supervised Supertonic, no old feeder/broker/jarvisd, one DB writer, one queue.

- [ ] **Step 8: Perform one controlled real rollback drill**

Before normal use or new requests, run rollback from the new final path:

```bash
python scripts/dan-rollback apply --apply \
  --journal ~/.dan/migration/latest/journal.jsonl
```

Prove old runtime can start, DBs pass integrity/counts, `speaking=null`, and the cutover smoke request is not replayed. Stop it again and repeat Steps 5–7 with a new journal. This second cutover begins the observation period.

- [ ] **Step 9: Record acceptance and tag the candidate**

Write local `release1-acceptance.json` containing commit SHA, manifest SHA, DB backup SHAs/counts, test report SHA, voice report SHA, cold-start results, first rollback journal and final cutover journal. Do not put private report content in Git.

```bash
cd /Users/n1_ozzy/Documents/dev/DAN
git status --short --branch
git tag -a dan-v1-foundation-candidate -m "DAN Foundation Release 1 cutover candidate"
```

**Review gate:** no observation starts until Ozzy has heard the live matrix, accepted every final voice route, seen one-queue/no-overlap behavior and confirmed the real rollback drill.

---

## Task 15: Observe for seven days and close the donor-deletion gate

**Files:**

- Local only: `~/.dan/migration/observation.jsonl`
- Local only: `~/.dan/migration/operator-signoff.json`

- [ ] **Step 1: Record normal-use evidence for seven consecutive days**

For each day append one JSON line from `dan doctor --json` with timestamp, daemon PID, child PIDs, DB integrity, queue terminal counts, failed requests, adapter failures and whether any old runtime was needed. Do not include conversation text.

- [ ] **Step 2: Prove at least two full cold starts**

After two separate login/cold-start cycles, record:

```bash
launchctl print "gui/$(id -u)/com.dan.dand"
dan doctor --json
ps -axo pid,ppid,command | rg 'dand|supertonic|voice_broker|jarvisd|feeder'
```

Both cycles require one daemon, one supervised engine, working panel/PTT/CLI/host adapter and no old path/process.

- [ ] **Step 3: Re-run final active-reference and data checks on day seven**

```bash
cd /Users/n1_ozzy/Documents/dev/DAN
python scripts/dan-release-audit --repo . --all-git-refs --active-home
dan doctor --json
sqlite3 ~/.dan/dan.db 'PRAGMA integrity_check;'
```

- [ ] **Step 4: Obtain explicit operator sign-off**

`operator-signoff.json` records: seven-day window, two cold-start evidence IDs, no old-runtime use, accepted audio report SHA, DB/rollback journal SHAs and Ozzy's explicit approval. Absence of sign-off means donors stay.

- [ ] **Step 5: Mark the release only after sign-off**

```bash
cd /Users/n1_ozzy/Documents/dev/DAN
git tag -a dan-v1-foundation -m "DAN Foundation Release 1 accepted"
```

At this point — and only at this point — Ozzy may delete `DANv2`, `menubar-controller`, the migrated old `dan` donor and dated backups according to his retention decision. The installer/cutover never deletes them automatically.

---

## Final Definition of Done

- One product/repo path: `/Users/n1_ozzy/Documents/dev/DAN`.
- One `dand`, one product launchd label, one DB, one config registry, one resolver, one queue, one player and one global PTT owner.
- Every producer uses `dan speak --json --as PERSONA --session SESSION --source HOST --stdin` or the equivalent local API; no executable old feeder/broker/request-file path remains.
- Every queued request contains a complete immutable `RenderSnapshot`; playback proves what actually played.
- Full voice cast, custom styles, mastering, pronunciation and permitted pipelines are versioned or have an explicit licensed installation path.
- `jarvis.db` data and unique `memory.db` records are preserved with lineage, counts, integrity report and tested rollback.
- Panel is a truthful API client; no `/tmp`, `launchctl`, process killing, voice-map copy or hotkey listener lives inside it.
- Clean clone + empty cache installation works on macOS/M5 without private data.
- Ozzy can understand and use broker, queue, voice config and recovery from the short operator docs.
- Seven-day observation, two cold starts and explicit operator sign-off pass before any donor deletion.
