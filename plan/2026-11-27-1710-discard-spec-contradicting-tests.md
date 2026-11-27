---
status: approved
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

The spec, copied here so this plan stands alone, with the plan review folded in (see "Review"
below). Paths are under `apps/backend/agents/`, and tests under `tests/`.

### A. Generator brief

In `evaluator._prepare_replacement` (~3604–3640), only when `candidate.reason_kind ==
"spec_contradiction"`:

- add the original row's `semantic_notes`, collapsed to single spaces and hard-sliced to 500
  characters, as `Judge's note: …`, only when it is a non-empty string after stripping;
- in place of "including ordering and edge cases targeted by the original", say: "The original
  asserted an expectation that contradicts the quoted criterion; correct or drop that expectation
  and keep the rest."

The `test_quality` brief stays byte-identical.

### B. Independent replacement judge

In `_replacement_prompt` (~3653–3677), entries whose `reason_kind == "spec_contradiction"`:

- **Leave out of the context:** pass 1's `rejection_reasons` and `quoted_ac`. The context keeps
  `replacement_id`, `original_id`, `original_test_file` and `reason_kind`.
- **Instruction:** they get their own line in place of "assert the originally targeted behaviour
  at least as strongly":
  > "Judge each spec_contradiction replacement on its own against the acceptance criteria. It
  > may omit or correct an expectation of the original that contradicts a criterion; it must
  > still assert every other behaviour the original targeted."

`test_quality` entries keep today's context and instruction. When only `test_quality` entries
are present, the prompt text is byte-identical to today's.

### C. Discard gate

The gate is evaluated after `_merge_replacement_rows` in `_run_regeneration_pass` (~3901). An
entry is discarded only if **all** of these hold:

1. **The entry:**
   - `reason_kind == "spec_contradiction"`;
   - `outcome == "kept_reject"`;
   - `replacement_id` is a non-empty string.
2. **Votes:** `n >= 3`, set by a new constant `MIN_DISCARD_VOTES = 3`. That means at least 6
   votes across the two generations.
3. **The replacement's evidence:**
   - its merged second-pass row exists, has `judged` other than `False`, and has `verdict ==
     "reject"`;
   - its bundle stability is `consistent_fail` with `failure_kind == "assertion"`. Unwrap enums
     the way `regeneration._candidate` does;
   - its file still exists (`(spec_dir / gen.test_file).is_file()`).
4. **The confirmation:** `regeneration.confirmed_contradiction(replacement_id, docs2,
   ac_texts)` returns an AC text. The caller **checks that this equals the original
   candidate's `ac_text`, after `normalize_quote`**.
   - **Where `docs2` comes from:** the per-call docs returned in memory by `_judge_replacements`,
     which now returns `(merged, docs2)`. They are never read from files.
   - **What the helper requires:** every doc is non-`None`, has exactly one row for the id, and
     that row is a `reject` that is not `judged: False`, with a ≥12-character `spec_contradiction`
     quote that appears in one AC.
5. **The original's plan phase still has a pass:** at least one other row in `final_rows` has
   verdict `accept` or `flag`. Find the phase via `_resolve_subtask` on the plan, then pick the
   phase that contains that subtask.
6. **The run still has a pass:** after the removal, `final_rows` still holds at least one
   `accept`, so a run can never discard to empty.

**When the gate passes:**
- the original row is removed from `final_rows`;
- `outcome = "discarded"`;
- `reason = "original and replacement both contradict the quoted criterion"`;
- `replacement_quote` = the AC text.

There is no new verdict value. The replacement file is removed by the existing `finally` block,
because the outcome is not `replaced`. Any failed check leaves `kept_reject` (fail-closed).

### D. Invariant

In `check_merge_invariant` (~203), a `discarded` entry:
- needs `reason_kind == "spec_contradiction"`, a non-empty string `replacement_id` and a
  non-empty string `replacement_quote`;
- **consumes** an original that is still in the working list, so it can't be consumed twice;
- needs that original to be a first-pass `reject` with a dict `spec_contradiction`;
- needs no row with the original's `test_id`, or with the `replacement_id`, left in the final
  rows.

`replaced` plus `discarded` stays within `MAX_REGENERATIONS_PER_RUN`.

### E. Readers

- **`triager._regenerated_entries`** (~1226):
  - accepts `discarded`, which needs a non-empty `replacement_id` and a non-empty string
    `replacement_quote`;
  - `replacement_quote` joins the optional string keys.
- **`triage_report._regenerated_to_json`** (~302) adds `replacement_quote`.
- **The report** adds `## Discarded (test contradicts spec)` after `Rejected`, rendered only
  when there is at least one discarded entry. `_section` would print `_(none)_`, so the call
  needs an explicit conditional. Each line reads:
  ```text
  - `<original>` — contradicts: "<replacement_quote, whitespace-collapsed, hard-sliced to 200>"
  ```

### Known effects (accepted)

- **The discarded row is gone from `verdicts.json`:**
  - the handback sees no reject for it;
  - VAL levels count only the remaining rows;
  - triage's rejected count drops, while the evaluator's `verdicts_count` stays the pass-1 count;
  - AC fidelity gets no evidence from the discarded test.
- **AIFactory** `tfactory_verdict` returns `pass` when no reject remains.
- **Residual risk:** judges could misattribute a real defect to the test. The guards (§B
  independence, §C2 votes, §C4 same AC, §C5 phase pass, §C6 run pass) bound it, and every
  discard is listed in the triage report and in `regenerated`.

## Review (plan draft, 2026-11-27)

Codex (gpt-6.2-sol) and agy (model not reported) reviewed the draft. Both were read-only, and
the worktree was unchanged after each.

| Finding | Verdict | Change made |
|---|---|---|
| The replacement judge is primed with pass 1's quote and reasons, and told to reject anything weaker (both) | confirmed (`evaluator.py:3661-3677`) | §B |
| `n=2` allows 4 votes; no check that both passes quote the same AC (both) | confirmed (`regeneration.py:51`) | §C2, §C4 |
| The evidence can't tell which side of the assertion is wrong (Codex) | confirmed, no deterministic check available | §C5, §C6, residual risk documented |
| Vote files as freshness proof, and `judged:false` is ignored (Codex) | confirmed (`_vote_quote` ignores `judged`) | in-memory `docs2`, `judged` checked |
| The replacement file can vanish after bundling (Codex) | confirmed (`evaluator.py:3805`) | §C3 file check |
| Enum unwrap (agy) | confirmed (`regeneration.py:84-86`) | §C3 |
| The invariant and triager disagree on `discarded` fields (agy) | confirmed | §D, §E |
| `_section` prints `_(none)_` (Codex) | confirmed (`triage_report.py:472-477`) | §E |
| Spec "step 4" reference (both) | confirmed, a typo | the stability check is step 2 here |
| `assertion_manifest` pins discarded files on a later handback (agy) | unverified (call sites not traced) | left as-is; the original file stays on disk as today for any reject |
| Staged replacement copies in the project checkout (Codex) | unverified | unchanged from today's `kept_reject` behaviour |

## Steps

1. **`regeneration.py`:**
   - Add `MIN_DISCARD_VOTES = 3`.
   - Add `confirmed_contradiction(test_id, docs, ac_texts) -> str | None`. It returns the
     matching AC text under the §C4 rule and rejects `judged: False` rows. Share the quote logic
     with `_contradiction_candidate`; don't duplicate it.
   - Extend `check_merge_invariant` (~203) per §D.

   **Tests** in `tests/test_regeneration_select.py`:
   - `confirmed_contradiction` returns the AC for a unanimous quote;
   - it returns `None` for: one doc `None`; one vote with no quote; a quote not found in an AC; a
     single doc; a non-reject vote; a `judged: False` row; a short quote; two rows for the same id;
   - the invariant accepts a valid discard;
   - it rejects: an original with no `spec_contradiction`; the wrong `reason_kind`; an empty
     `replacement_id` or quote; an original still present; the replacement id present; the same
     original consumed twice; the cap exceeded across `replaced` plus `discarded`.

   **Verify:**
   ```bash
   /mnt/code/Source-home/GitHub/TFactory/apps/backend/.venv/bin/pytest tests/test_regeneration_select.py -q
   ```

2. **`evaluator.py`:**
   - §A in `_prepare_replacement`.
   - §B in `_replacement_prompt`.
   - `_judge_replacements` returns `(merged, docs)`. Update its one caller.
   - Add `_discard_confirmed_contradictions(spec_dir, final_rows, entries, generated,
     replacements, second, docs2, ac_texts, n) -> None` per §C. Call it right after
     `_merge_replacement_rows`, inside the same block, and only when `second is not None`.
   - Hoist `regeneration.load_ac_texts(spec_dir)` (~3843) into a variable and reuse it.
   - Use `_entry_for`, `_resolve_subtask` and `_read_plan`.

   **Tests** in `tests/test_evaluator_regeneration.py`. Fake `_judge_once`, not
   `_judge_replacements`, so the real archive, collect and merge code runs.
   - **The P1 shape**, with `n=3`: the final `verdicts.json` has no reject for the test, the
     entry is `discarded` with `replacement_quote` set, the replacement file is deleted, and the
     first-pass bytes are restored on an injected exception.
   - **Kept as `kept_reject`:**
     - `n=2`;
     - one pass-2 call returns `None`;
     - a pass-2 vote has no quote;
     - pass 2 quotes a different AC;
     - the replacement is not `consistent_fail`, or has a non-assertion `failure_kind`;
     - the replacement file was removed;
     - the phase has no other pass;
     - the run has no other accept;
     - `test_quality` kind;
     - no replacement was generated.
   - **A corrected replacement** that drops the contradicting case is judged accept and ends
     `replaced`.
   - **Brief:** has the note and the new line; empty, whitespace-only, non-string and over-500
     notes are handled; the `test_quality` brief is byte-identical.
   - **Replacement prompt:** the contradiction entry carries no `rejection_reasons` or
     `quoted_ac`; a `test_quality`-only prompt is byte-identical to today's.

   **Verify:**
   ```bash
   …/pytest tests/test_evaluator_regeneration.py tests/test_regenerate_test.py tests/test_regeneration_select.py -q
   ```

   **Traps:**
   - An exception must still restore the first-pass bytes, so don't catch it in the helper.
   - Pass-1 `vote_docs` and pass-2 `docs2` stay separate variables.

3. **`triager.py` and `triage_report.py`:** §E.

   **Tests:**
   - a `discarded` entry is read;
   - a `discarded` entry with an empty `replacement_id`, or with a non-string or empty
     `replacement_quote`, rejects the list;
   - an unknown outcome rejects the list;
   - the section renders, and is absent when there are no discards;
   - JSON has `replacement_quote`.

   **Verify:**
   ```bash
   …/pytest tests/test_triage_report*.py tests/test_triager*.py -q
   ```

**Every step:**

- Run from the worktree root, with the main clone's venv
  (`/mnt/code/Source-home/GitHub/TFactory/apps/backend/.venv/bin/`).
- Run `ruff check` and `ruff format --check` on the changed files.
- After the lead commits, run the ratchet. It must report 0 regressions.
  ```bash
  python scripts/ratchet_lint.py --base origin/dev --package apps/backend --package apps/web-server --package scripts
  ```
  Put CI-pinned mypy 1.20.1 and ruff 0.15.17 first on PATH.

## Tests

- The verify commands above.
- The full `tests/` suite once at the end, compared with an `origin/dev` baseline.
- CI green.
- Live, after the TFactory release: a P1 E2E rerun passes S4 and S5.

## Rollback

Revert the PR. A spec-contradicting reject then fails the run again, as it did before.
