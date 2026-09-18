---
status: approved
issue: 1174
author: Olaf Krasicki-Freund
---

# Intent: Stop generated tests shipping imports that resolve to nothing

## Problem

Generated tests keep importing modules that do not exist — `app/games/...`
for a module at `games/tictactoe/game.js`. The assertions are correct; the
test is still worthless, and it surfaces as `flaky`/`consistent_fail`, which
points investigations at test quality instead of at the import.

Where it stands on `dev`:

- **Root causes partly fixed.** Serve/target scoping for polyglot repos
  (#1178/#1180/#1182) and planner path conventions (#1185) landed.
- **Detection landed, as a report only (#1233).** `_unresolvable_imports`
  records the file and specifier in `status.json` → `unresolvable_imports`
  and `gen_functional_warnings`, then generation continues.
- **Rejection was tried and reverted (#1192 → #1194).** It worked — the
  `app/` prefix went away — but every rejection routed through
  `_reject_subtask_for_replan`, i.e. a full Planner pass. Measured: committed
  6 → 3, rejected 6 → 12, browser ok true → false, duration 24.7 → 47.0 min.
  The cost was the replan loop, not the check.
- **Still broken:** specs 193/194/195 still emit `app/...`; the import mapper
  repairs it in the jest lane but not in the Docker fallback runner (and
  would mis-repair a project that really ships `app/`); nothing reads
  `unresolvable_imports`, so a doomed test still costs a full 3× stability
  run and a judge call before it is rejected under a misleading reason.

## Proposed outcome

- A test whose import cannot resolve is fixed **within Gen-Functional**,
  without a Planner replan, in the common case.
- When it cannot be fixed, the verdict says so explicitly (unresolvable
  import naming the specifier), not `flaky`.
- Behaviour is the same across runners (jest lane, Docker fallback).
- Measured on a real run against the #1194 baseline: committed count and
  duration no worse than the report-only build, and zero shipped tests with
  unresolvable imports.

## Affected users and systems

- `apps/backend/agents/gen_functional.py` (`_unresolvable_imports`,
  per-subtask loop, `_reject_subtask_for_replan`)
- `apps/backend/agents/evaluator_verdicts.py` / evaluator (reading the signal)
- The import mapper and the Docker fallback runner
  (`tools/runners/docker_runner.py`)
- LLM cost per run (a retry is one more generation call per bad test)
- Cluster runs on https://tfactory.freundcloud.org.uk (measurement)

## Constraints

- Must not route through the Planner replan loop — that is what #1194
  reverted.
- Bounded: a fixed retry budget per subtask; never an open loop.
- Must not "repair" by guessing when the basename is ambiguous (same rule as
  #1258's resolver).
- The deciding evidence is a real cluster run compared to the #1194 numbers,
  not only unit tests.

## Open questions

1. Retry with feedback (re-prompt Gen-Functional for that one subtask, naming
   the bad specifier and the real module path) vs. deterministic rewrite
   (rewrite the specifier when the basename resolves uniquely) vs. both
   (rewrite when unambiguous, retry otherwise). Recommend **both** — the
   rewrite is free and handles the observed `app/` case; the retry covers the
   rest.
2. Keep or delete the jest-lane-only import mapper once generation stops
   producing bad specifiers? Recommend delete (it is the runner divergence),
   but only after the measured run.
