---
status: draft
issue: 1419
intent: intent/2026-10-23-1419-stryker-not-run.md
---

# Spec: a Stryker run that never happened is not "no mutant"

## Design

- Add `NOT_RUN = "not_run"` to `TSMutationVerdict`.
- `run_ts_mutate_probe`: if `result.returncode` is in
  `agents.stability_runner._SHELL_NOT_RUN_CODES`, return NOT_RUN without
  parsing the output.
- `_parse_stryker_report`: output that holds no report (empty, not JSON, or a
  JSON value that is not an object with a `mutants` key) gives NOT_RUN instead
  of NO_MUTANT. A report with `mutants: []` still gives NO_MUTANT. An unknown
  mutant status keeps its current NO_MUTANT fallback (out of scope).
- Confidence: `_mutation_subscore("not_run")` already returns None (#1417), so
  the mutation weight is dropped; triage and the evaluator prompt already know
  `not_run`. No change there.

## Alternatives rejected

- Map 126/127 or a missing report to SYNTAX_ERROR or TIMEOUT: both score 0.5,
  the same inflation the issue is about.
- Classify any non-zero Stryker exit as NOT_RUN: Stryker can exit non-zero
  with a valid report (e.g. a break threshold); the report is the signal then.

## Risks

A test fixture or caller that expected `no_mutant` for empty Stryker output now
sees `not_run`. The one existing unit test asserting that is updated.

## Verification

Regression tests: runner exit 127 gives `not_run` and confidence equal to the
no-signal value; exit 0 with no report gives `not_run`; a real zero-mutant
report gives `no_mutant`; a real killed-mutant report gives `killed`. ruff,
mypy ratchet, targeted pytest.
