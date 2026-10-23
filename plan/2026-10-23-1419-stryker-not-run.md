---
status: approved
issue: 1419
spec: spec/2026-10-23-1419-stryker-not-run.md
---

# Plan: a Stryker run that never happened is not "no mutant"

Decisions (from the approved spec): `TSMutationVerdict.NOT_RUN = "not_run"`.
Stryker exit in `agents.stability_runner._SHELL_NOT_RUN_CODES` (126, 127) gives
NOT_RUN before parsing. Output holding no report (empty, not JSON, or no
`mutants` object) gives NOT_RUN; `mutants: []` stays NO_MUTANT. Any other
non-zero exit still classifies from the report. Confidence is unchanged: it
already drops `not_run`. No second code list.

## Steps

1. `apps/backend/agents/lang_typescript/mutate_probe.py`: add `NOT_RUN`; check
   the return code against the shared codes in `run_ts_mutate_probe`; make
   `_parse_stryker_report` return NOT_RUN when there is no report.
   Trap: `stability_runner` must not import the TS probe (no cycle).
2. Tests: `tests/test_stryker_not_run_1419.py` (127 → not_run with no-signal
   confidence; no report → not_run; zero-mutant report → no_mutant; killed
   report unchanged). Update
   `tests/test_lang_typescript_mutate_probe.py::test_parse_empty_string_returns_no_mutant`
   to expect NOT_RUN.

## Tests

`apps/backend/.venv/bin/pytest tests/test_stryker_not_run_1419.py
tests/test_lang_typescript_mutate_probe.py tests/test_lang_typescript_integration.py
tests/test_mutation_dispatch.py tests/test_mutation_not_run_1415.py -q`; ruff;
`python scripts/ratchet_lint.py`.

## Rollback

Revert the PR; no data or config migration.
