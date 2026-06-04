# PFactory test-target pickup (#195)

> Part of the PFactory tag-taxonomy pickup epic (#193). Recognition + enqueue
> only — parsing `pfactory:meta` as the test oracle is #196; generate + run +
> report is #197.

PFactory (the planning-and-governance layer) emits **governed** GitHub epics +
child issues on **dual approval** (AI gates pass *and* a human approves).
Testing work is routed to TFactory via a shared **tag taxonomy** (the "secret
language"). This module recognises that routing and enqueues the work as a
TFactory test-generation target — the analogue of ingesting an AIFactory spec.

Full taxonomy contract: PFactory repo `docs/tag-taxonomy.md` (v1).

## The gate

An issue is a governed TFactory test target when it carries **both**:

| Label | Meaning |
|---|---|
| `pfactory` | governed — PFactory reviewed it and a human approved it |
| `handoff:tfactory` | routed to TFactory for test generation |

`type:testing` is typical but **not required** — `handoff:tfactory` is also
carried by any child whose acceptance criteria need an independent test pass. A
child may also carry `handoff:aifactory` (build **and** test); TFactory owns
only the test pass (`also_aifactory=True`). Issues **without** `pfactory` are
left to existing behaviour.

### Priority → horizon

TFactory has no `sev:*` / `p0–p3`; it uses horizons. The PFactory `priority:p*`
maps as: `p0 → now`, `p1 → next`, `p2 / p3 → later`.

## Two trigger paths

- **GitHub issue** (firm path) — classified by labels.
- **`requirements.json`** (`.aifactory/specs/<plan_id>/`) — picked up only when
  its `metadata` explicitly signals TFactory routing, via a mirrored `labels`
  list **or** an explicit `handoffs`/`handoff` naming `tfactory` together with a
  governance marker (`pfactory: true` or a `taxonomy` field).

## Usage

```python
from integrations.pfactory import pickup_issue, classify_issue

# Recognise + enqueue (the #197 flow supplies the real enqueue callback):
decision = pickup_issue(issue, enqueue=my_enqueue)
if decision.picked_up:
    ...  # my_enqueue received the normalized target record

# Pure classification (no side-effects):
classify_issue(issue).picked_up   # bool
```

### CLI

```bash
# Branch a shell on whether to enqueue (exit 0 = picked up, 1 = not, 2 = usage):
gh issue view 412 --json number,title,body,labels \
  | python -m integrations.pfactory --issue -

python -m integrations.pfactory --requirements .aifactory/specs/001-x/requirements.json

# Add --oracle to also print the parsed test oracle (#196):
python -m integrations.pfactory --issue issue.json --oracle
```

## The test oracle (#196)

Every governed issue body ends with a `pfactory:meta` block (mirrored into
`requirements.json` → `metadata`). `build_oracle` parses it — preferring
`requirements.json` when present — into a `PFactoryOracle`: the **acceptance
criteria** (extracted via `spec_sources`) + **`citations[]`** (the sources the
tests assert against) + priority/horizon + plan metadata. It degrades
gracefully on a missing/old `taxonomy` or a malformed block (empty oracle, no
raise).

```python
from integrations.pfactory import build_oracle

oracle = build_oracle(issue_body=issue.body)              # or requirements=req
oracle.acceptance_criteria   # tuple[str, ...] — what the tests must assert
oracle.citations             # tuple[Citation, ...] — why, uri, source
oracle.horizon               # "now" | "next" | "later"
```

## Run + report back (#197)

`run_target` / `pickup_and_run` close the loop: seed a TFactory spec workspace
from the oracle (acceptance criteria → `AC#N` markers; citations → the
description), then schedule the existing Planner → Gen-Functional → Executor →
Evaluator → Triager pipeline. The Triager renders a triage report tied to the
target's `plan_id` and reports back on the originating issue.

The workspace's `context/source.json` carries `issue_number` — the spine
correlation key the completion envelope (#198) reports back on — and `pfactory:
true` for provenance.

```python
from integrations.pfactory import pickup_and_run

# Recognise → parse → run, all dry-run by default:
handle = pickup_and_run(
    issue, project_id="acme", project_dir=repo_path,
    repo="acme/orders", branch="feat/x",
)
if handle:
    handle.spec_dir   # .../specs/<plan_id> with the triage report
```

**No automatic pushes.** The Triager's git-commit + PR-comment side-effects stay
**dry-run by default**. Opt in with `run_target(..., dry_run=False)` (which sets
TFactory's existing `TFACTORY_TRIAGER_GIT_WRITE` / `TFACTORY_TRIAGER_PR_COMMENT`
flags) or by setting those env flags directly. `schedule` is injectable for
tests; the default fires the Planner (which auto-chains the rest).

Implementation: `apps/backend/integrations/pfactory/{pickup,oracle,run}.py`.
Tests: `tests/test_pfactory_{pickup,oracle,run}.py`.
