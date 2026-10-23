---
status: approved
issue: 1415
intent: intent/2026-10-23-1415-mutation-not-run.md
---

# Spec: a mutant that never ran is not a killed mutant

## Design

- Add `NOT_RUN = "not_run"` to `MutationVerdict` (`agents/mutate_probe.py`),
  `JavaMutationVerdict` and `RustMutationVerdict`.
- Every return-code classifier maps a code in
  `stability_runner._SHELL_NOT_RUN_CODES` to NOT_RUN, 0 to SURVIVED, any
  other non-zero to KILLED: `_mutation_from_codes` (evaluator.py),
  `_probe_one` in `run_mutate_probe`, `run_java_mutate_probe`,
  `run_rust_mutate_probe`. The TypeScript probe classifies from Stryker's JSON
  report, not a return code, and is unchanged.
- Candidate selection (Python batched and per-candidate paths, kept identical):
  the first KILLED still wins; otherwise the first result that is not NOT_RUN;
  NOT_RUN only when every candidate was NOT_RUN. So a NOT_RUN never hides a
  SURVIVED.
- Verdict assembly: `confidence._mutation_subscore("not_run")` returns None, so
  the mutation weight is dropped exactly as with no mutation signal.
  `triage_dedup._MUTATION_PRIORITY` ranks not_run with error. The evaluator
  prompt documents `not_run` as no evidence (flag, never treat as killed).
  `MutationResult.is_acceptable` stays KILLED/NO_MUTATION only. The quality gate
  blocks only survived, and template harvest already refuses unknown values.

## Alternatives rejected

- Map 126/127 to ERROR: ERROR scores 0.5 in confidence, which can raise a
  low-confidence verdict above its no-signal value; and it hides the cause.
- A second list of codes in the mutation module: the issue forbids it.

## Risks

A downstream consumer that switches on the verdict string sees a new value.
The consumers in the repo (confidence, triage, quality gate, template harvest,
prompt) are covered above.

## Verification

Regression tests: exit 127 (and 126) gives NOT_RUN in `_mutation_from_codes`,
`run_mutate_probe`, Java and Rust probes; exit 1 still gives KILLED;
confidence with all-127 mutants is not higher than with no mutation signal.
ruff, mypy ratchet, targeted pytest.
