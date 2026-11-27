---
status: approved
issue: 1710
author: olafkfreund
---

# Intent: a test that contradicts the spec no longer fails the build

## Problem

E2E run `mvr-20261127t` (Factory#3882) built a correct P1 `wordfreq` CLI, but the factory never
merged it.

TFactory generated `main-invalid-args-and-missing-file-exit-2`, which expects `--top 0` to exit 2.
The spec makes only `top < 0` invalid, so the code is right to return 0.

- **TFactory saw the mistake:** all three judge votes rejected the test with `spec_contradiction`,
  quoting that criterion, and the semantic note says the test "contradicts AC#1".
- **Regeneration didn't fix it:** the `-regen1` replacement kept the same `top_zero` case. Its
  brief said "include the edge cases targeted by the original" and never mentioned which
  expectation was wrong. Its three votes rejected it the same way.
- **The surviving `reject` was treated as a product failure:**
  - VAL-1 failed;
  - a handback asked AIFactory to "fix the code";
  - AIFactory's merge gate fails on any `reject` in `verdicts.json` and held the PR until the
    run timed out.

## Proposed outcome

- A regenerated test is told which expectation contradicted the spec, so it can correct that
  expectation.
- When the original test and its replacement are both unanimously judged to contradict the same
  acceptance criterion, TFactory discards the test as a test defect:
  - it is recorded in the audit and the triage report;
  - it is left out of the final verdicts, VAL-1 and the handback.
- The other tests still decide the run.

## Affected users and systems

- TFactory evaluator: `agents/evaluator.py`, `agents/regeneration.py`.
- TFactory triager and triage report: `agents/triager.py`, `agents/triage_report.py`.
- **Downstream (no change):**
  - AIFactory's `tfactory_verdict` merge gate;
  - the handback;
  - the E2E harness S4/S5.

## Constraints

- **A real product bug must still fail the run.** A discard needs:
  - the existing contradiction evidence: a stable assertion failure, and a reject from every
    vote quoting a real acceptance criterion verbatim;
  - the same unanimous evidence again for an independently regenerated replacement.
- **No new verdict value.** AIFactory treats an unknown verdict as "no result".
- **A discarded test is never committed,** and its record is kept.
- A normal TFactory release, with no AIFactory change.

## Open questions

None.
