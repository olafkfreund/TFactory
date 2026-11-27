---
status: draft
issue: 1710
spec: spec/2026-11-27-1710-discard-spec-contradicting-tests.md
coders:
  1: codex   # small, security-relevant pure logic (fail-closed evidence check, invariant)
  2: codex   # evaluator integration on the regeneration path, tricky ordering
  3: codex   # triager reader and report section, small
reviewers:
  - codex    # independent read of the discard gate (could it hide a real defect?)
  - agy      # second model on the same diff (agent-team)
---

# Plan: discard a test that twice contradicts the spec

## Approved decisions

The spec, copied here so this plan stands alone. Paths are under `apps/backend/agents/`, and
tests under `tests/`.

### Brief

In `evaluator._prepare_replacement` (~3604), only when `candidate.reason_kind ==
"spec_contradiction"`:

- add the original row's `semantic_notes`, collapsed to single spaces and capped at 500
  characters, as `Judge's note: …`, but only when it is a non-empty string;
- in place of "including ordering and edge cases targeted by the original", say: "The original
  asserted an expectation that contradicts the quoted criterion; correct or drop that expectation
  and keep the rest."

The `test_quality` brief stays byte-identical.

### Discard

After `_merge_replacement_rows` in `_run_regeneration_pass` (~3901), an entry is discarded only
if **all** of these hold:

- `reason_kind == "spec_contradiction"`;
- `outcome == "kept_reject"`;
- `replacement_id` is set;
- the replacement's second-pass row exists, is judged (`judged` is not `False`) and has
  `verdict == "reject"`;
- the replacement's bundle stability is `consistent_fail` with `failure_kind == "assertion"`;
- `regeneration.confirmed_contradiction(replacement_id, second_vote_docs, ac_texts)` returns a
  quote. It uses `_contradiction_candidate`'s rule: more than one vote doc, and every doc rejects
  that id with a ≥12-character quote that appears in one acceptance criterion.

Then:

- the original row is removed from `final_rows`;
- `outcome = "discarded"`;
- `reason = "original and replacement both contradict the quoted criterion"`;
- `replacement_quote = <quote>` is set.

There is no new verdict value. The replacement file is removed by the existing `finally` block,
because the outcome is not `replaced`.

### Invariant

In `check_merge_invariant`, a `discarded` entry consumes its original:

- the original must be a first-pass `reject` with a dict `spec_contradiction`;
- the entry must have `reason_kind == "spec_contradiction"`;
- the original must be absent from the final rows;
- `replaced` plus `discarded` must stay within `MAX_REGENERATIONS_PER_RUN`.

### Readers

- `triager._regenerated_entries` accepts `outcome: "discarded"`, which needs a `replacement_id`.
- `triage_report` gains a `## Discarded (test contradicts spec)` section listing each discarded
  original and its quote. `_regenerated_to_json` adds `replacement_quote`.

## Steps

1. **`regeneration.py`:**
   - Add `confirmed_contradiction(test_id: str, vote_docs: Sequence[Mapping[str, Any] | None],
     ac_texts: Sequence[str]) -> str | None` next to `_contradiction_candidate` (~46). Reuse that
     function: it returns `Candidate.quoted_ac` or `None`.
   - Extend `check_merge_invariant` (~203) as above.

   **Tests** in `tests/test_regeneration_select.py`:
   - `confirmed_contradiction` returns the quote for a unanimous AC quote;
   - it returns `None` for one vote with no quote, a quote not found in an AC, a single vote doc,
     a non-reject vote, or a short quote;
   - the invariant accepts a valid discard;
   - it rejects a discard whose original has no `spec_contradiction`, whose entry has the wrong
     `reason_kind`, or whose original is still in the final rows;
   - it rejects when the cap is exceeded across `replaced` plus `discarded`.

   **Verify:**
   ```bash
   /mnt/code/Source-home/GitHub/TFactory/apps/backend/.venv/bin/pytest tests/test_regeneration_select.py -q
   ```

   **Traps:**
   - Run it from the worktree root.
   - The worktree has no venv, so use the main clone's venv as above.

2. **`evaluator.py`:**
   - **Brief:** change `_prepare_replacement` (~3604–3640) as above.
   - **Helper:** add `_discard_confirmed_contradictions(final_rows, entries, generated,
     replacements, second, vote_docs, ac_texts) -> None`. Apply it in `_run_regeneration_pass`
     right after `_merge_replacement_rows`, inside the same `if generated and not
     checkout_changed` block.
   - **Inputs:**
     - read `vote_docs` with `regeneration.load_vote_docs(findings, n)` after the replacement
       judge has run;
     - skip the step when `n <= 1`;
     - load `ac_texts` once: hoist the existing `regeneration.load_ac_texts(spec_dir)` call
       (~3843) into a variable and reuse it.
   - **Where the replacement verdict comes from:** look up the second-pass row in `second` with
     `_entry_for`, and the bundle by `test_id` in `replacements`.

   **Tests** in `tests/test_evaluator_regeneration.py`, following its existing fakes:
   - **The P1 shape:** the original and the replacement are each rejected unanimously with an AC
     quote. Expect the final `verdicts.json` to have no reject for that test, the entry
     `discarded`, `replacement_quote` set, and the replacement file deleted.
   - **Kept as `kept_reject`, with the reject still in the final verdicts:**
     - one second-pass vote has no quote;
     - the replacement is not `consistent_fail`;
     - `test_quality` kind;
     - no replacement was generated.
   - **Brief:** the `spec_contradiction` brief contains the judge's note and the new line. The
     `test_quality` brief equals today's text.

   **Verify:**
   ```bash
   …/pytest tests/test_evaluator_regeneration.py tests/test_regenerate_test.py tests/test_regeneration_select.py -q
   ```

   **Traps:**
   - Any exception in the pass must still restore the first-pass bytes. Don't catch it inside
     the helper.
   - `vote_docs` from pass 1 were archived to `verdicts.pass1.vote*.json`. Only the current
     `verdicts.vote*.json` files are the second pass.

3. **`triager.py` (~1226) and `triage_report.py` (~302, ~538, ~580):**
   - `_regenerated_entries` accepts `discarded`, which needs a `replacement_id`. `replacement_quote`
     is an optional string.
   - `_regenerated_to_json` adds `replacement_quote`.
   - The report renders a `Discarded (test contradicts spec)` section after `Rejected`, with
     `- \`<original>\` — contradicts: "<quote, whitespace-collapsed, 200 chars>"`. It is omitted
     when there are none, the same way the other optional sections are.

   **Tests** in `tests/test_triage_report*.py` and the existing triager regeneration tests:
   - a `discarded` entry is read;
   - the section renders;
   - JSON includes `replacement_quote`;
   - an entry with an unknown outcome still rejects the list.

   **Verify:**
   ```bash
   …/pytest tests/test_triage_report*.py tests/test_triager*.py -q
   ```

**Every step:**
- run `ruff check` and `ruff format --check` on the changed files, with
  `/mnt/code/Source-home/GitHub/TFactory/apps/backend/.venv/bin/ruff`;
- after the lead commits, run the ratchet:
  ```bash
  python scripts/ratchet_lint.py --base origin/dev --package apps/backend --package apps/web-server --package scripts
  ```
  It must report 0 regressions. Put CI-pinned mypy 1.20.1 and ruff 0.15.17 first on PATH.

## Tests

- The verify commands above.
- The full `tests/` suite once at the end, compared with an `origin/dev` baseline.
- CI green.
- Live, after the TFactory release: a P1 E2E rerun passes S4 and S5.

## Rollback

Revert the PR. A spec-contradicting reject then fails the run again, as it did before.
