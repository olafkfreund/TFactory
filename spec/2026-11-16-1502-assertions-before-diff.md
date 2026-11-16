---
status: draft
issue: 1502
intent: intent/2026-11-16-1502-assertions-before-diff.md
---

# Spec: two-step generation, assertions before the diff

## Design

In `agents/gen_functional.py::_generate_one_subtask`, for the v0.2 path (a
framework descriptor exists), a single output file (multi-artifact sets such as
Cucumber stay single-step: explicit exclusion) and unless
`TFACTORY_GEN_TWO_STEP=0`:

1. **Assertion step.** Run a session with a new prompt
   `prompts/gen_functional_assertions.md`, built by
   `get_tfactory_gen_functional_prompt(..., step="assert")`. Same SUBTASK and
   FRAMEWORK CONTEXT blocks, but the prompt (a) names no diff path and no target
   source, (b) tells the model to write the test file from the criterion alone,
   to guess imports from the `target` field, and not to Read `diff.patch` or
   project source (an existing test file for `intent: update` may be read).
   The SUBTASK CONTEXT block lists `diff.patch` today; in this step that line is
   dropped.
2. **Wiring step.** The existing prompt `gen_functional.md`, unchanged (so the
   single-step and kill-switch paths keep their instructions), with a
   "WIRING STEP" block appended (like `_import_feedback`): the draft at the
   path holds the expected values and they are fixed; read the diff and the
   target only to fix imports, call signatures and fixtures; Write the file
   back at the same path. If the wiring session writes nothing, the step-1
   draft stays and the existing guardrails (preflight, unresolvable imports,
   flake lint, #888, #995) judge it as they judge any file.
3. **Lock check.** Between the steps, take the numeric values the step-1 source
   executes on, using `criterion_literals.code_values` (comments and docstrings
   excluded). After wiring and the import-repair retries, the final source must
   still contain every one of them; otherwise reject through
   `_reject_subtask_for_replan` with phase `gen_functional_assertions_changed`,
   like the #888 check. The check runs before the existing guardrails.

If step 1 errors or writes no file, the normal error / no-write paths apply to
step 1. **Known gap:** the lock and the #888 check are both presence-based on
numbers. A step 1 file with no numeric literal locks nothing, and a final file
that keeps `11.76` as unused data while asserting `11.75` passes both. String
and structural expectations are protected by the step-1 ordering and the prompt
only. Closing this needs an assertion-level AST comparison; follow-up, not in
this change.

## Alternatives rejected

- **Withhold the tools in step 1.** The SDK client has one tool grant per
  session and the diff sits in `spec_dir`; withholding needs a per-session
  tool/path filter in `core/client.py`, a larger change than the issue's
  "locate code afterwards" intent warrants. Recorded as the reduced guarantee.
- **Lock strings and structure too.** A string/AST-diff of assertions breaks on
  legitimate wiring edits (renamed fixtures); numbers are what #888 showed.
- **Prompt-only reorder in one session.** The issue's whole point is that a
  rule inside one session depends on the model ignoring what it read.

## Risks

- One extra session per subtask: cost and wall-clock rise. Bounded by the
  existing 480 s per-session ceiling; kill switch `TFACTORY_GEN_TWO_STEP=0`.
- Step 1 imports are guesses; the wiring step and import repair fix them.
- A wiring step may legitimately drop a step-1 number (a removed unused
  constant): this rejects and replans, which costs a replan, not a wrong verdict.
- Existing tests that count sessions see two calls.

## Verification

- Unit: a wiring session that writes nothing leaves the draft in place.
- Unit: step-1 prompt has no `diff.patch` and no target-source instruction;
  wiring prompt says expected values are fixed.
- Unit: lock helper accepts an unchanged / extended file, rejects a file whose
  step-1 value is gone, ignores a value left only in a comment.
- Execution: the step-1 file from the contradiction case is executed against
  an implementation returning `11.75` and fails on the assertion.
- Flow (mocked sessions): contradictory criterion (`11.76` vs implementation
  `11.75`): step 1 asserts `11.76`; a step 2 that rewrites to `11.75` is
  rejected and a replan request is written; a step 2 that keeps it passes. The
  prompt order is asserted: step 1 is invoked before any prompt that mentions
  the diff.
- Existing `tests/test_gen_functional*.py` and `tests/test_criterion_literals.py` pass.
- Cost and duration: not measurable without live runs; reported in the PR.
