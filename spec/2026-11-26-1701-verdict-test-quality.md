---
status: draft
superseded: true  # exclusion design rejected 2026-11-26; see intent (regenerate, planned)
issue: 1701
intent: intent/2026-11-26-1701-verdict-test-quality.md
---

# Spec: exclude proven-invalid tests from the verdict, keep product failures failing

## Design

The code map has one key finding: the deterministic test-quality signals live only in the
`EvaluatorSignals` bundles (`agents/evaluator.py` ~line 691). Bundles carry `stability`,
`mutation`, `lint_promotion` and `flaky_history`. Verdict rows hold copies the LLM wrote, and
nothing in code enforces the prompt's reject rules. **Exclusion therefore reads bundles, never row
fields.**

### 1. New module `agents/verdict_exclusion.py`

```python
def apply_exclusions(doc: dict, bundles: list[EvaluatorSignals], ac_texts: list[str]) -> dict
```

The evaluator calls this in `_run_evaluator_session`, after `enrich_verdicts` and
`apply_execution_scope` and before `_apply_lane_attribution` (~line 3533). `bundles` is in scope
there, and row ids still equal bundle ids at that point.

**Overwrite first.** `doc["excluded"]` is first set to `[]`, discarding any `excluded` key a judge
wrote.

**Which rows it considers.** Only rows with `verdict == "reject"`, `judged` not `False`, and a
bundle with the same `test_id`. Unjudged rows (crashes, a fail-closed vote) are never excluded.

**`product_failure` comes first.** A row is a product failure when its bundle's
`stability.verdict` is `CONSISTENT_FAIL` and `failure_kind` is `assertion`. Such a row stays
`reject` unless the spec-contradiction rule below is met. When both rules apply, the failure
wins, which answers the intent's precedence question.

**Measured quality rules.** A non-product-failure row moves to `excluded` with
`exclusion = {"kind": <k>, "evidence": <short fixed string>}` when its bundle shows one of these:

| `kind` | Bundle signal |
|---|---|
| `flaky` | `flaky_history.classification == "flaky"` (flip_rate > 0), or `stability.verdict == FLAKY` |
| `mutation_survived` | `mutation.verdict == SURVIVED` |
| `lint_rejected` | `lint_promotion.should_reject is True` |

**Not excluded, stays `reject`.** Two of the intent's signals aren't measured, so this fix doesn't
exclude them:

- **Low semantic relevance:** only the LLM provides it.
- **`new_lines == 0`:** no baseline is written, so `CoverageDelta.new_lines` is unavailable.

**Spec contradiction**, `kind: "spec_contradiction"`. A `CONSISTENT_FAIL` assertion row moves to
`excluded` only when all of these hold:

1. The row has a `vote` block, so the vote count is 2 or more. `TFACTORY_VERDICT_VOTES=1` never
   qualifies.
2. The split is unanimous for reject.
3. **Every** call's row carries `spec_contradiction.criterion`. That field is a new, optional
   field the judge writes when it claims the test contradicts the spec.
4. Each quoted criterion, after collapsing whitespace and lowercasing, is a substring of one of
   the `ac_texts`.

`ac_texts` are the `AC#N: <text>` phase names from `test_plan.json`, plus the contract's
`final_acceptance` items when present.

`evidence` holds the matched AC label, for example `"AC#3"`, never the free-text reasons.

**Carrying each call's quote through the vote.** `_merge_voted_verdicts` (~line 3355) adds
`vote.contradictions`: one list entry per call, holding that call's quoted criterion or `None`.

**Prompt.** `prompts/evaluator.md` gains one rule. When the verdict is `reject` because the test's
own expectation contradicts an acceptance criterion, add
`"spec_contradiction": {"criterion": "<exact quote of that criterion>"}`. Never add it for a
product that fails a correct test. `_validate_one_verdict` lets the optional field through. It
already ignores unknown keys, and a test pins this.

### 2. Coverage floor, and reject-only means fail (`agents/triager.py`)

**Loading.** `_load_verdicts_or_fail` (~line 1190) also returns `doc.get("excluded", [])`.

**Coverage floor.** This is a new helper, `_uncovered_criteria(plan, verdicts, excluded)`. It
runs after `_apply_lane_attribution` has rekeyed ids to plan subtask ids, so the ids join to
`test_plan.json` phases. `excluded` rows are rekeyed the same way: `_stamp_verdict_lanes` is
applied to `excluded` as well.

- A criterion phase is **uncovered** when at least one of its subtasks produced a row in
  `verdicts` or `excluded`, but none has `accept` or `flag`.
- If excluding tests leaves any phase uncovered, while that phase also has an excluded row, the
  final status becomes `triaged_uncovered`.
- AIFactory requires `status == "triaged"`, so it reads `triaged_uncovered` as no trusted verdict
  (`None`).
- `_outcome_for_status` maps `triaged_uncovered` to `"empty"`, and the triager warning names the
  uncovered AC labels.
- A phase that is uncovered only because of product-failure rejects keeps the reject-means-fail
  path below. The floor does not mask a real failure.

**Reject-only means fail.** `final_status` (~line 1865) becomes `"triaged"` whenever there are
rejects, even with zero accepted and flagged tests. AIFactory then reads `verdicts.json`, sees a
`reject` and returns `fail`.

To keep the completion envelope honest, `_status_verdict` (~line 638) gains a rule modelled on
`_zero_tests_verdict`. A `triaged` result with accepted plus flagged equal to 0 and rejected > 0
gives `("failure", "product_failed: <n> generated tests failed against the product")`.

### 3. Visibility

- `triage_report.py`:
  - `TriageReport` gains `excluded`;
  - `render_json` adds `"excluded"`;
  - `render_markdown` adds an **Excluded (test-quality)** section after Rejected, one line per test
    with its `kind` and `evidence`;
  - the PR comment is built from this Markdown, so it gets the section automatically.
- `_write_ac_fidelity` (~line 1413) receives `excluded`, so the AC ledger shows those tests as
  excluded rather than missing.
- Readers that keep counting `verdicts` only are documented in the module docstring as
  intended. Excluded tests aren't product evidence:
  - `quality_gate`, `val_block`, `lane_progress`;
  - handback (`_FAILING_VERDICTS = {"reject"}`, so excluded tests don't trigger a correction);
  - backstage counts, and `mcp_copilot` counts.

### 4. AIFactory

AIFactory doesn't change. It reads `status == "triaged"` and the `verdicts` list, and it ignores
`excluded`.

## Alternatives rejected

- **A new verdict value.** AIFactory reads an unknown kind as `None`, which would hide a real
  reject in the same run.
- **Trusting row-level `stability` / `mutation`.** These are LLM-copied, so a judge could fake an
  exclusion.
- **Excluding on a majority vote for spec contradiction.** That's too weak against a false green;
  the intent requires a unanimous vote.
- **Using `ac_fidelity` / `criterion_authority` as the contradiction proof.** They run at
  generation, before execution, and don't judge a failing assertion. They are a candidate follow-up.

## Risks

- **False green from a spec-contradiction exclusion.** Four things limit it: the vote must be
  unanimous, every call must quote a criterion that matches the contract text, a product failure
  takes precedence, and the coverage floor applies. A residual risk remains: all judges quote a
  real criterion and are all wrong. Mitigation: the excluded test is listed in the PR comment with
  its AC label.
- **Readers that ignore `excluded`** under-report test counts on badges and in VAL. This is
  documented and accepted.
- **Reject-only runs** now report `triaged` with envelope outcome `failure`, where they used to be
  `triaged_empty` and `empty`. Consumers that keyed on `triaged_empty` for this case will change.
  `_triaged_empty_warnings` is still used when there are no rejects either.
- **Prod.** A normal TFactory release. No data migration is needed: old `verdicts.json` files have
  no `excluded` key, which reads as `[]`.

## Verification

Unit tests:

- **`apply_exclusions`, one test per rule:**
  - flaky moves to `excluded`;
  - mutation survived moves;
  - lint rejected moves;
  - low relevance only stays `reject`;
  - an unjudged reject stays;
  - a CONSISTENT_FAIL assertion stays `reject`;
  - the same row with a unanimous 3/3 vote, all quotes matching AC text, moves (`spec_contradiction`);
  - a 2/1 split stays;
  - one call missing the quote stays;
  - a quote that matches no AC stays;
  - with `VOTES=1` it stays;
  - a judge-written `excluded` key is overwritten.
- **Vote:** `vote.contradictions` keeps each call's quote.
- **Triager:**
  - reject-only gives status `triaged` and envelope `failure` with `product_failed`;
  - an excluded test that leaves its AC uncovered gives `triaged_uncovered` and outcome `empty`;
  - an excluded test whose AC is still covered by an accept stays `triaged`;
  - the report JSON and Markdown list the excluded tests;
  - the AC ledger marks them.
- **AIFactory compatibility fixture:** `verdicts.json` with an `excluded` list and accept-only
  `verdicts` gives `pass` through a copy of `tfactory_verdict`'s rules.
- **Existing suites** keep passing: `test_verdict_vote`, `test_evaluator*`, `test_confidence*`,
  `test_triager*`, `test_triage_report`, `test_quality_gate`, `test_ac_fidelity`, `test_val_block`
  and `test_lane_progress`.
- **Live:** release TFactory, then rerun E2E P1.
  - The run-`m` style bad test lands in `excluded` (`spec_contradiction`, AC#N).
  - The verdict is `pass`, and AIFactory merges the PR (S4).
  - S5 reports the verdict on the merge SHA.
