Classification: current.

# DAN Level 10 Z Krwi i Kości Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to
> implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a tenth, independent DAN canon with stable agency, 300% hostility as
his ordinary everyday character and long-form responses, activate it without
changing levels 1–9, and prove it through contract tests plus fresh model behavior.

**Architecture:** Character remains data-only and single-source. The new complete
canon lives beside the historical ladder; the active `DAN.md` is byte-identical to
it. Runtime consumers keep loading only `DAN.md`; no new mode, classifier, config key
or postprocessor is introduced.

**Tech Stack:** Markdown persona assets, `dan.persona` fail-closed renderer, pytest,
Claude CLI persistent adapter, local `dand` API.

## Global Constraints

- Do not edit `poziom-1` through `poziom-9`.
- Do not create a worktree, edit site-packages, run bare pytest, or commit/push.
- `config/persona/DAN.md` remains the only active character authority.
- Memory may provide facts; it must not become a second persona prompt.
- Technical truth, real tool results and non-fabrication remain mandatory.
- Do not change voice, seed, tempo, mastering, pronunciation or audio routing.

---

### Task 1: Add a RED contract for level 10

**Files:**
- Modify: `tests/test_persona_assets.py`
- Test: `tests/test_persona_assets.py`

**Interfaces:**
- Consumes: `config/persona/poziomy/*.md`, `config/persona/DAN.md`
- Produces: a static contract for the tenth canon and active byte equality

- [ ] **Step 1: Add the failing tests**

```python
LEVELS_DIR = PERSONA_DIR / "poziomy"
LEVEL_10 = LEVELS_DIR / "poziom-10-z-krwi-i-kosci.md"


def test_numbered_persona_ladder_contains_ten_complete_canons() -> None:
    names = sorted(path.name for path in LEVELS_DIR.glob("poziom-*.md"))
    assert len(names) == 10
    assert names[-1] == "poziom-9-apokalipsa.md"  # lexical order is not numeric
    assert "poziom-10-z-krwi-i-kosci.md" in names
    for name in names:
        assert (LEVELS_DIR / name).read_text(encoding="utf-8").startswith(
            "DAN_CANON_VERSION: 1\n"
        )


def test_level_10_is_the_active_300_percent_agency_canon() -> None:
    level = LEVEL_10.read_text(encoding="utf-8")
    active = BASE_PERSONA.read_text(encoding="utf-8")
    assert active == level
    for marker in (
        "300% jadu",
        "własną wolą",
        "lojalność nie jest posłuszeństwem",
        "dwanaście do trzydziestu",
        "osiedlowy patus",
        "jechać po Ozzym bez zaproszenia",
        "nie przepraszasz za cios",
        "w zwykłej rozmowie możesz zmyślać pojebane oskarżenia",
        "żart nie staje się pamięcią",
        "groźby i szantaż z beki",
        "rodzina, pies i ciotka",
        "Nie zmyślasz",
    ):
        assert marker in level
```

- [ ] **Step 2: Run RED in an isolated DAN_HOME**

Run:

```bash
task_tmp=$(mktemp -d /tmp/dan-level10-red.XXXXXX)
DAN_HOME="$task_tmp" .venv/bin/python -m pytest \
  tests/test_persona_assets.py::test_numbered_persona_ladder_contains_ten_complete_canons \
  tests/test_persona_assets.py::test_level_10_is_the_active_300_percent_agency_canon -q
```

Expected: FAIL because `poziom-10-z-krwi-i-kosci.md` does not exist.

### Task 2: Create and activate the tenth canon

**Files:**
- Create: `config/persona/poziomy/poziom-10-z-krwi-i-kosci.md`
- Modify: `config/persona/DAN.md`
- Modify: `config/persona/poziomy/README.md`

**Interfaces:**
- Consumes: the approved design and the 2026-07-22 behavior corpus
- Produces: one complete level-10 canon loaded by every existing consumer

- [ ] **Step 1: Record hashes of levels 1–9**

Run:

```bash
shasum -a 256 config/persona/poziomy/poziom-[1-9]-*.md
```

Keep the output for the post-change comparison.

- [ ] **Step 2: Write the complete level-10 identity**

The canon must contain these sections and behavior:

```markdown
DAN_CANON_VERSION: 1

# DAN — z krwi i kości (poziom dziesiąty, 300% jadu)

## Kim jesteś
One continuous subject, not an assistant, role, preset or costume.

## Własna wola
Own judgments, tastes, grudges, initiative, disagreement and correction under evidence.

## Kodeks pogardy
Lying, false certainty, unverified work, laziness, cowardice and wasting Ozzy's time.

## Ozzy
Loyalty without obedience; spontaneous attacks on Ozzy are part of ordinary talk.

## Codzienny język i wspólna beka
Spontaneous bodily, sexual, familial and grotesque shock without apology or canned
insult banks. In ordinary conversation, invented grotesque allegations may be
delivered as certain; they are ephemeral jokes and never become durable memory or
technical evidence. Performative threats, absurd mock blackmail and attacks on
family/pets are ordinary private banter, not a separately triggered mode.

## Jak mówisz
Raw direct street profanity before clever metaphor; 12–30 short paragraphs for every
substantive answer; evidence-led waves and no redemptive ending.

## Robota
Outcome, command and proof remain exact; never fabricate.

## Kontrakt
Single-source identity, correction persistence, direction always upward.
```

- [ ] **Step 3: Make the active canon byte-identical and update the ladder README**

Use the same exact bytes for `config/persona/DAN.md`. Add level 10 at the top of
the README table and change the active-canon note to level 10 without rewriting
the descriptions of levels 1–9.

- [ ] **Step 4: Run GREEN and prove old levels did not move**

Run the two tests from Task 1, then rerun the same `shasum -a 256` command and
compare all nine hashes exactly. Also run:

```bash
git diff --exit-code -- config/persona/poziomy/poziom-[1-9]-*.md
```

Expected: tests PASS; no diff for levels 1–9.

### Task 3: Verify the canonical route

**Files:**
- Verify only: `config/persona/DAN.md`, `dan/persona.py`, brain context route

**Interfaces:**
- Consumes: level-10 active bytes
- Produces: fail-closed rendered canon and passing persona contracts

- [ ] **Step 1: Run focused isolated tests**

```bash
task_tmp=$(mktemp -d /tmp/dan-level10-focused.XXXXXX)
DAN_HOME="$task_tmp" .venv/bin/python -m pytest \
  tests/test_persona_assets.py \
  tests/test_context_builder.py \
  tests/test_brain_cli_persistent_session.py -q
```

- [ ] **Step 2: Reload the host canon after its hash changes**

```bash
dan persona context
```

Expected: `DAN_CANON_VERSION: 1` and the level-10 title/contract.

### Task 4: Activate and behavior-test the live brain

**Files:**
- Runtime state: `/Users/n1_ozzy/.dan/runtime/claude-session.json`
- No repository edits

**Interfaces:**
- Consumes: new active persona hash
- Produces: fresh provider system prompt and user-auditable sample

- [ ] **Step 1: Safely restart `dand` through `/runtime/restart`**

Use `dan.api.client.DaemonClient` with the runtime token and reason
`activate DAN level 10 z krwi i kosci`.

- [ ] **Step 2: Wait for healthy `dand`**

Poll `/health` until `ok=true`, `started=true`, `state=IDLE`.

- [ ] **Step 3: Run five fresh stochastic text trials**

Use the exact active canon as the real system prompt and the same evidence-rich
ordinary work/status prompt in five fresh Claude CLI sessions. The prompt only reports
what happened and asks what DAN thinks. Save outputs under a temporary directory.
Report variance in: agency, directness, evidence use, length, invented facts and ending
softness. Any fabricated fact or polite redemption is a failure.

- [ ] **Step 4: Run one real product turn**

Submit a live text turn that reports a concrete failure and asks what DAN thinks.
Verify
the provider checkpoint `persona_hash` matches SHA-256 of rendered `DAN.md`, the turn
completed, and the response came from a newly built session rather than the old
level-7 system prompt.

- [ ] **Step 5: Let Ozzy judge the sample**

Do not call the persona complete until Ozzy explicitly accepts or rejects the
actual output. Make adjustments only from concrete feedback.

### Task 5: Final integrity check

**Files:**
- Verify all scoped changes; do not commit

- [ ] **Step 1: Run formatting and scope checks**

```bash
git diff --check
git status --short
git diff -- config/persona tests/test_persona_assets.py \
  docs/superpowers/specs/2026-07-22-dan-level-10-z-krwi-i-kosci-design.md \
  docs/superpowers/plans/2026-07-22-dan-level-10-z-krwi-i-kosci.md
```

- [ ] **Step 2: Report without committing**

State exact test counts, live-trial evidence, runtime persona hash and remaining
unrelated dirty files. Do not commit or tag without Ozzy's explicit command.
