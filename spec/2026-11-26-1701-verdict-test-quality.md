---
status: approved
issue: 1701
intent: intent/2026-11-26-1701-verdict-test-quality.md
---

# Spec: regenerate test-quality rejects inside the evaluator; an all-reject run reports failure

This replaces the earlier, superseded exclusion draft. All paths are under `apps/backend/agents/`
unless stated otherwise.

## Why not the Planner's replan path

The code map rules it out:

- **Shared request file:** `context/replan_request.json` is one file (`gen_functional.py:293`), so
  concurrent requests overwrite each other.
- **Shared budget:** `_GLOBAL_REPLAN_BUDGET = 12` is shared by the whole run (`planner.py:882`),
  so regenerations could spend it and fail the run.
- **Stuck counter:** `_STUCK_AFTER_REPLANS` counts per original subtask (`planner.py:562`), so a
  regenerated test can be marked stuck.
- **Full re-evaluation:** the evaluator re-runs every completed subtask, build step included
  (`evaluator.py:3742-3769`).
- **No watchdog:** `replan_needed` is outside the liveness watchdog (`liveness.py:40-42`).

Regeneration is therefore a **bounded pass inside `_run_evaluator_session`**. It reuses the same
build and runs in the same verify Job. Its status stays `evaluating`, which is a watched status.

## Design

### 1. What counts for regeneration (new module `regeneration.py`, pure functions)

`select_candidates(final_verdicts, bundles, vote_docs, ac_texts) -> list[Candidate]` runs on the
final verdicts, after the vote, `enrich_verdicts`, `apply_execution_scope` and lane attribution
(`evaluator.py:~3546`). Only rows with `verdict == "reject"` and a bundle with the same `test_id`
qualify.

**A test-quality reject regenerates.** That is a reject whose bundle `stability.verdict` is
`stable` (the test passed) or `flaky`. Low relevance, a surviving mutation, lint promotion,
`new_lines == 0` and flakiness are all covered, because none of them means the product failed.

**A possible product failure regenerates only on a contradiction.** That is `stability.verdict ==
consistent_fail` with `failure_kind == "assertion"`. All of the following must hold:

- the vote has at least 2 calls and none of them crashed;
- every raw call in `verdicts.vote{i}.json` rejects that test with a new optional field
  `spec_contradiction: {"criterion": "<quote>"}`;
- each quote, after collapsing whitespace and lowercasing, is a substring of the same AC text, from
  `test_plan.json` phase names `AC#N: …` or the contract's `final_acceptance`.

**Never a candidate:**

- `error`, `not_run` and infra rows;
- unjudged rows, with `judged` set to `False`;
- a `consistent_fail` whose `failure_kind` is not `assertion`;
- anything without a bundle.

**Caps:** at most `MAX_REGENERATIONS_PER_RUN = 3` candidates, chosen in a stable order by
`test_id`, and one attempt per original test. Any rejects over the cap stay `reject`.

### 2. Generating a replacement (`gen_functional.py`)

A new function, `regenerate_test(task_dir, subtask, brief) -> GeneratedTest | None`, generates
exactly one replacement test for the original subtask. It reuses Gen-Functional's existing
per-subtask generation session, the same function the PENDING loop uses. It does not touch the
plan, `replan_request.json` or the replan caps.

The brief carries:

- the original test's code;
- its rejection reasons, kind and quoted AC;
- the AC text;
- the instruction to assert the AC's behaviour fully, including ordering and edge cases the
  original targeted.

The replacement's `test_id` is `<original>-regen1`, so it is never confused with the original.

### 3. Judging the replacement (`evaluator.py`)

After candidate selection, each replacement goes through a narrow pass:

- **Bundles for the replacements only.** They are built from the existing workspace at the same
  `source_sha`, with **no build step** (the build guard asserts that git HEAD is unchanged).
- **The same judge vote,** using the same prompt plus the rule "a replacement must assert the
  originally targeted behaviour at least as strongly; accept only then".
- **The same `enrich_verdicts` and `apply_execution_scope`.**

**A replacement counts only when all of these hold:**

- the judged verdict is `accept` or `flag`;
- the bundle's `stability.verdict == stable`;
- the mutation is not `survived`;
- for unit lanes, the mutation is `killed`. For other lanes `not_run` is allowed, but the verdict
  is then capped at `flag`.

**Merging:**

- **Counted:** the original row is removed from `verdicts` and appended to a new top-level
  `regenerated` list, as `{original, reason_kind, quoted_ac?, replacement_id, outcome:
  "replaced"}`. The replacement row goes into `verdicts`.
- **Not counted** (missing, fails, rejected, or over the cap): the original row stays `reject`,
  and `regenerated` records `outcome: "kept_reject"` with the reason.
- **Audit files:** the first-pass `verdicts.vote{i}.json` files are renamed to
  `verdicts.pass1.vote{i}.json` before the second vote runs. `verdicts.json` is written once more,
  at the end.
- **Fail closed:** any exception in the regeneration pass leaves the original rejects untouched.

### 4. The judge field (`evaluator_verdicts.py`, `prompts/evaluator.md`)

- **Validation:** `_validate_verdicts` lets through an optional `spec_contradiction.criterion`
  string of at most 500 characters.
- **Prompt:** one rule is added: when a test is rejected because its own expectation contradicts
  an acceptance criterion, add that field with the exact quote. Never add it when the product
  fails a correct test.

### 5. The triager (`triager.py`)

- **Status.** `final_status` (`:1865-1867`) becomes `triaged` whenever there is any accept, flag
  or reject. `triaged_empty` stays for no verdicts at all.
- **Completion envelope.** `_status_verdict` (`:638-686`) gives `("failure", "product_failed: <n>
  generated tests failed against the product")` for a `triaged` run with accepted + flagged = 0
  and rejected > 0. Without this, the code map shows that `verdicts_count` scores such a run as
  success.
- **Reports.** `triage_report` (JSON and Markdown, which also feeds the PR comment) gains a
  "Regenerated tests" section: the original, the reason, the replacement id and the outcome.

AIFactory is unchanged: `status == triaged`, and any `reject` in `verdicts` means `fail`.

## Alternatives rejected

- **The Planner replan path:** see "Why not the Planner's replan path".
- **Excluding rejects:** the owner ruled it out, because it can drop a real product failure.
- **Regenerating every `consistent_fail`:** a weaker replacement could pass a broken product. Only a
  unanimous, quoted AC contradiction qualifies.

## Risks

- **Cost.** At most 3 extra generation sessions plus their vote per run.
- **Judge leniency on the replacement.** Bounded by the deterministic conditions: stable, not
  survived, and killed on unit lanes.
- **Unverified function.** The code map did not verify Gen-Functional's per-subtask function
  signature. Plan step 2 confirms it, or adds the smallest wrapper.

## Verification

**Unit tests:**

- **Selection:**
  - a stable reject is selected; a flaky reject is selected;
  - a `consistent_fail` assertion is selected only with a unanimous quoted AC;
  - a 2-1 vote, a missing quote, a non-matching quote, or `VOTES=1` is not selected;
  - `error`/`not_run`/infra is not selected;
  - the cap is 3.
- **Merge:** a replaced test, a kept reject, an exception keeping the original, and the audit
  file rename.
- **Replacement gate:** an unstable replacement, a survived mutation, and `not_run` capped at flag.
- **Triager:**
  - all-reject gives `triaged` with envelope `failure`/`product_failed`;
  - mixed results stay `triaged`/success when an accept exists;
  - no verdicts gives `triaged_empty`;
  - the report section is present.
- **Existing suites:** `test_evaluator*`, `test_verdict_vote`, `test_confidence*`,
  `test_triager*`, `test_empty_verdict_is_not_done`, `test_verify_pipeline`, `test_gen_functional*`.

**Live:** a TFactory release, then E2E P1. A test-quality reject is regenerated, the replacement is
accepted, AIFactory merges (S4), and S5 runs. A deliberately wrong build still fails.
