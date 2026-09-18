---
status: draft
issue: 1258
intent: intent/2026-09-18-1258-unit-lane-coverage.md
---

# Spec: Unit-lane coverage must reach the verdict

Decisions carried from intent review: #1258 and #1259 are separate PRs;
#1258 lands first.

## Findings (from reading `apps/backend/agents/evaluator.py` on dev 1a5c1c5c)

Both deterministic stamps run after the judge, in `_apply_lane_attribution`
(`:879`, called at `:2783`). Each keys on something the run may not provide:

1. **Lane** — `_stamp_verdict_lanes` (`:751`) matches `verdict["test_id"]`
   *exactly* against the plan's subtask ids (`_lane_by_test_id`, `:730`). The
   `test_id` is written by the judge LLM and is never checked against the ids
   it was given (`evaluator_verdicts.py` only checks the key exists). A judge
   that echoes the file name, a pytest node id, or a paraphrased id makes
   every verdict `unmatched` → `lane` absent → the #1258 symptom
   (`lane = None` on all 7).
2. **Coverage** — `_measured_coverage` (`:806`) reads only
   `findings/runs/<test_id>/coverage.xml`, which `_capturing_coverage`
   (`:1555`) writes from `runner_fn`. But:
   - it is keyed by the same judge-authored `test_id`, so it also misses when
     (1) misses; and
   - the Nix batched path (`_nix_batched_signals`, `:1606`) computes stability
     without calling `runner_fn`, so on that path `runs/<id>/` is never
     written.
   Meanwhile the host-runner branch persists every run's coverage to
   `findings/_run_artifacts/<test-file-stem>/coverage.xml`
   (`_persist_run_artifact`, `:1524`) — where the issue found its 13 files.
   Nothing reads that location for the verdict.

**Unconfirmed:** the issue reports `coverage_delta_pct = 0`, but the stamp
writes `None` when unmeasured. A `0` means the stamp did not overwrite the
judge's value for those verdicts (e.g. `signals_summary` not a dict). Step 1
of the plan reproduces this against real-shaped fixtures before any fix, and
the fix is adjusted if the reproduction points elsewhere.

## Design

Make both stamps resolve a verdict to its **plan subtask** robustly, then read
everything from the subtask — one resolver, used by both.

1. **`_resolve_subtask(plan, verdict) -> dict | None`** (new, small):
   - exact `test_id` match on subtask `id` (today's behaviour), else
   - `verdict["test_file"]` matched against the subtask's `files_to_create`
     (normalised: posix, stripped of leading `./`, compared on full relative
     path, then on basename **only if the basename is unique in the plan**).
   - No match → `None` (stays unattributed, as #1018 intended — never default
     to unit).
   On a file match, the verdict's `test_id` is rewritten to the plan id, so
   every later consumer (triage, `val_block`, evidence links) keys correctly.
2. **`_stamp_verdict_lanes`** uses the resolver.
3. **`_measured_coverage`** takes the resolved subtask and looks up, in order:
   `findings/runs/<id>/coverage.xml`, then
   `findings/_run_artifacts/<stem of files_to_create[0]>/coverage.xml`.
   First existing file wins. Unresolved or neither present → `(None, None)`.
4. **`_stamp_verdict_coverage`** overwrites the judge's
   `coverage_new_lines`/`coverage_delta_pct` on every verdict whose
   `signals_summary` is a dict, and **creates** `signals_summary` when the
   judge omitted it, so a judge-authored `0` can never survive.

No change to `_capturing_coverage` or the Nix batched path: the read side now
covers every place coverage is persisted, which is one fix instead of one per
runner branch.

`coverage_delta_pct` stays `None` in practice until a baseline exists
(`_measured_coverage` docstring, unchanged). The measurable acceptance is
therefore `coverage_new_lines` non-null and non-zero for a test that exercises
SUT lines; `coverage_delta_pct` gets a non-zero value when a baseline file is
present, which the test also exercises.

## Alternatives rejected

- **Write `runs/<id>/coverage.xml` from the Nix batched path too** — fixes
  one branch; the next runner branch repeats the bug. Read-side fallback
  covers all.
- **Ask the judge harder to echo `test_id`** (prompt change) — LLM output is
  not a key; the deterministic stamp must not depend on it.
- **Default unmatched verdicts to the lane of their language/framework** —
  a guess; re-creates the #1018 unit-inflation it replaced.
- **Positional match (verdict i ↔ bundle i)** — the prompt asks for same
  order, but a dropped or merged verdict silently shifts every lane.

## Risks

- **Wrong match by basename** in a plan with two same-named files in
  different dirs — prevented by the uniqueness rule; ambiguous → unmatched.
- **Rewriting `test_id`** changes a value other readers use. It moves it
  *towards* the plan id they already expect; any reader keyed on the judge's
  raw id was already broken. Covered by running the full evaluator, triager
  and val_block test modules.
- **`_run_artifacts` holds the *last* run**, which could be a mutation run
  (deliberately broken SUT). Mutation runs in this path go through
  `runner_fn` too; the spec keeps `runs/<id>/` first so the existing
  "last non-mutation" choice wins where it exists. Documented as the ceiling.
- Deployed cluster: behaviour changes only how verdicts are stamped; no
  schema or status shape change.

## Verification

- **Reproduction first (must fail on dev):** fixture spec dir with a
  `test_plan.json` (one `(python, pytest, unit)` subtask), a
  `verdicts.json` whose `test_id` differs from the plan id but whose
  `test_file` matches, and a real `coverage.xml` under
  `_run_artifacts/<stem>/` covering 5 SUT lines. Assert `lane == "unit"` and
  `coverage_new_lines == 5`. Fails on dev, passes after.
- **Mutation check:** same fixture with the coverage file removed →
  `coverage_new_lines is None` (not 0); with a baseline file present →
  `coverage_delta_pct` non-zero. Reverting the resolver makes the first test
  fail.
- **Ambiguity:** two subtasks with the same basename → verdict stays
  unattributed.
- Full suite: `apps/backend/.venv/bin/pytest tests/ -m "not slow"` green.
- Cluster: one real unit-lane verify on the deployed build shows
  `lane: unit` and non-null `coverage_new_lines` in `findings/verdicts.json`.
