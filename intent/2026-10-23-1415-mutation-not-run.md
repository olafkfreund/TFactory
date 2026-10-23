---
status: draft
issue: 1415
author: olafkfreund
---

# Intent: a mutant that never ran is not a killed mutant

## Problem

Every mutation classifier that keys on a return code treats any non-zero exit
as KILLED: `_mutation_from_codes` (batched Nix path, `agents/evaluator.py`),
`run_mutate_probe` (`agents/mutate_probe.py`), and the Java and Rust probes.
An exit of 126 (not executable) or 127 (command not found) means the shell
never started the test runner, e.g. a Node-only devShell without `python`.
#1401 made 126/127 mean not_run/environment for stability; mutation still
reports "killed" and inflates the test's strength and confidence.

## Proposed outcome

A mutant run exiting 126 or 127 is NOT_RUN. A real assertion failure is still
KILLED. A NOT_RUN mutation never raises a verdict's confidence and never counts
as a kill.

## Affected users and systems

TFactory Evaluator (mutation signal, confidence, triage ranking, the evaluator
prompt). No cluster or gitops change.

## Constraints

- One shared definition of the not-run codes: reuse
  `stability_runner._SHELL_NOT_RUN_CODES`, no second list.
- Mypy ratchet must pass.

## Open questions

None.
