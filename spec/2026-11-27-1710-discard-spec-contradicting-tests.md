---
status: approved
issue: 1710
intent: intent/2026-11-27-1710-discard-spec-contradicting-tests.md
---

# Spec: discard a test that twice contradicts the spec

All paths are under `apps/backend/agents/`.

## Design

### 1. The regeneration brief names the contradiction

`evaluator._prepare_replacement` builds the brief. When `candidate.reason_kind ==
"spec_contradiction"`, the brief:

- adds `Judge's note: <original row's semantic_notes>`, whitespace-collapsed and capped at 500
  characters, and only when it is a non-empty string;
- replaces "including ordering and edge cases targeted by the original" with: "The original
  asserted an expectation that contradicts the quoted criterion; correct or drop that expectation
  and keep the rest."

The `test_quality` brief is unchanged.

### 2. A second contradiction discards the test

`_run_regeneration_pass` runs `_merge_replacement_rows`, then this new step. It applies to each
regeneration entry where:

- `reason_kind == "spec_contradiction"`;
- `outcome == "kept_reject"`;
- a replacement was generated and judged (`replacement_id` is set, and a second-pass row exists);
- the second-pass row is a `reject` that `regeneration` confirms. A new pure helper,
  `regeneration.confirmed_contradiction(test_id, vote_docs, ac_texts) -> str | None`, reuses
  `_contradiction_candidate`. It returns the quote only when every second-pass vote (the
  `verdicts.vote{i}.json` written by the replacement judge) rejects the replacement with a
  ≥12-character `spec_contradiction` quote that appears in one acceptance criterion. The
  bundle-stability check is the caller's job, step 4 of the plan.

When all of these hold:

- the original row is removed from `final_rows`;
- `entry["outcome"] = "discarded"`;
- `entry["reason"] = "original and replacement both contradict the quoted criterion"`;
- `entry["replacement_quote"]` is set.

The original row stays in `entry["original"]`, as it does today. The replacement file is
deleted, because the existing `finally` deletes anything not `replaced`.

### 3. The invariant and readers accept `discarded`

- **`regeneration.check_merge_invariant`:** a `discarded` entry consumes its original:
  - the original must be a first-pass `reject` with `spec_contradiction`, from an entry with
    `reason_kind == "spec_contradiction"`;
  - it must be absent from the final rows;
  - `replaced` plus `discarded` stays within `MAX_REGENERATIONS_PER_RUN`.
- **`triager._regenerated_entries`:** accepts `outcome: "discarded"`, which needs a
  `replacement_id`.
- **`triage_report`:** the regenerated line already prints the outcome. Add a `## Discarded
  (test contradicts spec)` section listing each discarded test and its quote, so a reader sees the
  test was dropped and not passed.

### Effects downstream

None of these change:

- **`verdicts.json`:** it no longer has the reject. The remaining kinds are `accept` or `flag`.
- **`val_block`:** VAL-1 counts the remaining verdicts.
- **The handback:** it is built from `verdicts`, so it has nothing to send for this test.
- **AIFactory's `tfactory_verdict`:** with no reject, it returns `pass`. If the discard empties
  the list, it returns `None`, which holds, and that is fail-closed.
- **The AC fidelity ledger:** the criterion stays verified only by the remaining accepted tests.

## Alternatives rejected

- **Turn the reject into a `flag`:** a flag means the test ran and passed, and this one failed.
  It would also count as a pass in VAL-1 and in AC fidelity.
- **A new verdict value (`discard`):** AIFactory and `val_block` read an unknown value as no
  result or as a failure, so the PR would hold forever.
- **Discard on the first contradiction, with no regeneration:** one generation's mistake would
  be enough to drop a test. Two independent generations, each judged unanimously, are harder to
  get wrong.
- **Brief fix only:** it would help the regeneration, but a second failure would still block
  correct code.

## Risks

- **A real defect hidden as "contradicts spec":** this needs 6 unanimous judge votes across two
  generations, each quoting a real criterion verbatim, plus a stable assertion failure, and only
  on the existing contradiction path. A wrong discard still shows in the triage report and in
  `regenerated`.
- **All tests discarded:** the verdicts are empty, AIFactory holds, and that is fail-closed.

## Verification

Unit tests in `tests/test_regeneration_select.py`, `tests/test_evaluator_regeneration.py` and
`tests/test_triage_report*.py`:

| Case | Expected |
|---|---|
| contradiction candidate; the replacement is again unanimously rejected with an AC quote | original removed, outcome `discarded`, no reject left, invariant holds |
| the same, but one second-pass vote has no quote, or the quote is not in any AC | `kept_reject`, the reject stays |
| `test_quality` candidate whose replacement is rejected | `kept_reject` (unchanged) |
| no replacement generated | `kept_reject` |
| brief for `spec_contradiction` | contains the judge's note and the "correct or drop" line; the `test_quality` brief is unchanged |
| invariant: `discarded` whose original lacks `spec_contradiction`, or is still in the final rows | violation |
| triager reads a `discarded` entry; the report shows the Discarded section | yes |

After that:

- the full `tests/` suite against an `origin/dev` baseline;
- the ruff and ratchet checks;
- CI green;
- live, after the TFactory release, a P1 E2E rerun passes S4 and S5.
