---
status: draft
issue: 1419
author: olafkfreund
---

# Intent: a Stryker run that never happened is not "no mutant"

## Problem

The TypeScript mutation probe (`agents/lang_typescript/mutate_probe.py`)
classifies only from Stryker's JSON report and ignores the return code. When
the Stryker command is missing (exit 127) or not executable (126), there is no
report, and the probe returns `NO_MUTANT`. Confidence scores `no_mutant` at 0.5,
so a run where mutation never happened still contributes a mutation sub-score.
#1417 fixed this for every return-code classifier but left TypeScript out.

## Proposed outcome

Stryker exiting 126/127, or writing no report, gives `not_run`, and confidence
drops the mutation weight exactly as #1417 does. `no_mutant` stays only for a
real report that lists zero mutants (or a test with no mutable assertion).

## Affected users and systems

TFactory Evaluator mutation signal for TypeScript/JavaScript subtasks. No
cluster or gitops change.

## Constraints

- Reuse `stability_runner._SHELL_NOT_RUN_CODES`; no second list.
- Mypy ratchet must pass.

## Open questions

None.
