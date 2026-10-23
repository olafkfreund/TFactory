---
status: draft
issue: 1415
spec: spec/2026-10-23-1415-mutation-not-run.md
---

# Plan: a mutant that never ran is not a killed mutant

Decisions (from the approved spec): exit codes in
`agents.stability_runner._SHELL_NOT_RUN_CODES` (126, 127) give a new
`NOT_RUN = "not_run"` mutation verdict; 0 gives SURVIVED; other non-zero gives
KILLED. First KILLED wins; else the first non-NOT_RUN result; NOT_RUN only when
all candidates were NOT_RUN. Confidence drops a not_run mutation signal (None).
No second code list. TypeScript (Stryker JSON) unchanged.

## Steps

1. `apps/backend/agents/mutate_probe.py`: add `NOT_RUN` to `MutationVerdict`;
   classify `res.returncode` in `_probe_one` with the shared codes; selection
   loop prefers a non-NOT_RUN first result.
   `apps/backend/agents/evaluator.py` `_mutation_from_codes`: same rule.
   → verify by targeted pytest.
   Traps: import `_SHELL_NOT_RUN_CODES` from `agents.stability_runner`; check
   for an import cycle (stability_runner must not import mutate_probe).
2. `apps/backend/agents/lang_java/mutate_probe.py`,
   `apps/backend/agents/lang_rust/mutate_probe.py`: add `NOT_RUN` and classify
   126/127 as NOT_RUN. → verify by targeted pytest.
3. `apps/backend/agents/confidence.py` `_mutation_subscore`: "not_run" → None.
   `apps/backend/agents/triage_dedup.py` `_MUTATION_PRIORITY`: "not_run": 2.
   `apps/backend/prompts/evaluator.md`: document `not_run`.
   `apps/backend/agents/quality_gate.py` docstring: list not_run.
4. Regression tests in `apps/backend/tests/test_mutation_not_run_1415.py`.

## Tests

`cd apps/backend && .venv/bin/pytest tests/test_mutation_not_run_1415.py -q`
plus the existing mutate_probe / evaluator / confidence tests; ruff;
`python scripts/ratchet_lint.py`.

## Rollback

Revert the PR; no data or config migration.
