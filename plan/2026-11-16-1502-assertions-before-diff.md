---
status: draft
issue: 1502
spec: spec/2026-11-16-1502-assertions-before-diff.md
coders:
  1: lead (small, one prompt file plus one helper; a handoff costs more than it saves)
  2: lead (same files as step 1)
  3: lead (tests for steps 1-2)
reviewers:
  - codex  # independent read of the final diff; agy is off by owner decision
---

# Plan: two-step generation, assertions before the diff

Approved spec decisions carried over:

- v0.2 path only, single output file only (not `descriptor.multi_artifact`, not
  more than one `files_to_create`), kill switch `TFACTORY_GEN_TWO_STEP=0`
  (any of `0/false/off`); multi-artifact and legacy stay single-step.
- Step 1 (assert): new prompt `gen_functional_assertions.md` (write the test from
  the criterion alone, guess imports from `target`, do not Read `diff.patch` or
  project source; an existing test file may be read for `intent: update`); the SUBTASK CONTEXT
  block omits the `diff.patch` line; the prompt does not tell the model to read
  the diff or target source and tells it not to.
- Step 2 (wire): `gen_functional.md` unchanged, plus an appended "WIRING STEP"
  block. If wiring writes nothing the draft stays and the usual guardrails judge it.
- Lock: `criterion_literals.code_values(step1_source, language)` must be a
  subset of the final source's values (after `_repair_imports`); else reject via
  `_reject_subtask_for_replan`, phase `gen_functional_assertions_changed`, before
  the other guardrails. Presence-based; known gap recorded in the spec.
- Cost/duration: not measurable here; reported in the PR.

## Steps

1. `apps/backend/prompts/gen_functional_assertions.md` (new) and
   `apps/backend/prompts_pkg/prompts.py::get_tfactory_gen_functional_prompt`
   (~1417-1560): add `step: str = "single"`. For `"assert"`: drop the
   `diff.patch` bullet from the context block and load the new prompt body
   (FRAMEWORK CONTEXT kept). For `"wire"`: return the normal prompt plus a
   WIRING STEP block. Default behaviour unchanged → verify by
   `pytest tests/test_gen_functional_prompts.py`.
   Traps: ruff; keep the `- write the file at:` and `Subtask: ` lines (test
   mocks parse them).
2. `apps/backend/agents/gen_functional.py::_generate_one_subtask` (~1056-1135):
   when two-step applies, run the assert session (error and no-write handled as
   today), snapshot `code_values`, run the wiring session with the wire prompt (status `error` fails the subtask
   through the existing error path),
   keep the import-repair retry on the wire prompt, then run the lock check
   before `_source_guardrail_rejection`. Add `_two_step_enabled()` and
   `_assertions_lock_changed()` helpers. → verify by
   `pytest tests/test_gen_functional*.py tests/test_criterion_literals.py`.
   Traps: existing mocks count sessions; adjust only the ones that assert call
   counts. Imports lazy like the neighbours.
3. `tests/test_gen_functional_two_step.py` (new): prompt content/order, lock
   helper cases, contradiction flow (wiring error skips the subtask, a retry that overwrites the
   locked value is rejected, a flagged `multi_artifact` descriptor with one file
   stays single-step, accept keep, reject rewrite, wire-writes-
   nothing keeps draft, kill switch single session, multi-file single session),
   and execution of the step-1 file against a `11.75` implementation failing.
   → verify by `pytest tests/test_gen_functional_two_step.py`.

## Tests

`cd` repo root, run the repo's pytest invocation (see Justfile / CI) for the
files above plus `ruff check` and `ruff format --check` on touched files. All pass.

## Rollback

Set `TFACTORY_GEN_TWO_STEP=0`, or revert the commit; no data or schema change.
