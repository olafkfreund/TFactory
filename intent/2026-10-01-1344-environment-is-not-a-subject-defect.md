---
status: draft
issue: 1344
author: olafkfreund
---

# Intent: a failure that never reached the subject is not a subject defect

## Problem

A verification run reports how the code under test behaved. When the run could
not reach that code at all, it currently reports the same thing anyway.

Measured on run `026-myfriends-remediation-verify` (2026-10-01, branch
`aifactory/025-myfriends-web-remediation-v2-o`, claude-sonnet-4-6, 323k output
tokens, $17.65): **21 failing tests, all 21 environmental, none reaching the
subject.**

| True cause | Count | `failure_kind` assigned |
| --- | --- | --- |
| `KeyError: 'TFACTORY_TARGET_URL'` — no deployed target | 11 | `assertion` |
| `ModuleNotFoundError: No module named 'fastapi'` | 8 | `import` (correct) |
| `store.py not found at /tmp/.worktree/app/api/app/store.py` | 2 | `assertion` |

The eight import errors are classified correctly. The other **13 are classified
as assertion failures**, so `apply_consistent_fail_reason` writes, for each one:

> consistent test failure across 3 runs — the test executed and its assertions
> failed (**subject behaviour is wrong**), not an import/collection error

That sentence is false thirteen times, and it is stated as a measurement.

`classify_pytest_failure` is an ordered heuristic, and both shapes fall through
to the same place: the `KeyError` run exits 1 with `FAILED` in its output, and
the precondition check genuinely raises `AssertionError`. Neither can be
separated from a real assertion failure by exit code or exception type.

## Why this is worse than a lost run

The output is not only a report. `findings/handback_request.md` is addressed to
AIFactory and says:

> Fix the code under test so the items below pass — **do not weaken or delete
> the tests.**

A build agent receiving thirteen items that claim its authorisation code
misbehaves, about code that was never contacted, has two ways to comply: change
correct code until the tests pass, or weaken them. Both are defects introduced
into a correct implementation, caused by an unmeasured condition reported as a
measurement.

For scale on what was missed while eleven false findings were raised: the branch
under test genuinely closes the authorisation defect it was built to close (17
endpoints capture `caller_id`, nine ownership 403s) and genuinely carries two
different CRITICAL problems its own suite cannot see. The run found neither.

## Proposed outcome

- A failure that never reached the subject is never reported as the subject
  misbehaving.
- A lane whose precondition is absent says so **once, as a lane fact**, instead
  of once per generated test. One missing target URL is one problem, not eleven.
- A hand-back is not generated from such failures, so no build agent is asked
  to fix code that was not exercised.
- The assurance level reflects what was actually measured, rather than counting
  unreached tests as evidence either way.

## Affected users and systems

- `apps/backend/agents/stability_runner.py` — `classify_pytest_failure`, the
  deterministic classifier.
- `apps/backend/agents/confidence.py` — `apply_consistent_fail_reason` writes
  the false sentence; `apply_app_not_healthy_override` already implements the
  pattern this needs, reclassifying an infra failure to `not_run` on the
  stated grounds that it is "infra, not a wrong subject".
- Whatever runs a lane and generates `handback_request.md`.
- AIFactory, as the recipient of hand-backs it should never have received.

## Constraints

- **Must not** become a way to explain away real failures. A genuine assertion
  failure must still read as a subject defect; the new classification has to be
  provable by a marker, not inferred from the fact that a test failed.
- **Must not** turn an unmeasured lane into a pass. The honest outcome is
  "not run", which the never-overclaim gate already understands — the existing
  `app_not_healthy` path shows the shape.
- Should reuse `app_not_healthy`'s machinery rather than add a parallel one.
  There is already a verdict category for "infra, not a wrong subject".
- The `/tmp/.worktree` case cannot be fixed in the classifier alone: a test
  asserting its own precondition raises a real `AssertionError`. Preventing it
  belongs upstream, at whatever decides a lane may run.

## Open questions

1. **Scope.** Does this issue cover only the misreporting (classify and
   suppress correctly), or also the two underlying gaps — the api lane having
   no target (#1343) and the worktree arriving empty? The misreporting is what
   causes harm; the gaps are what made the run empty. I lean toward fixing the
   reporting here and treating the gaps as their own work, because a correct
   "not run" is useful even while the gaps remain.
2. **The empty-sandbox dependencies.** Eight unit tests could not import
   `fastapi`, which the subject declares. That is classified correctly today,
   so it is not a reporting bug — but it means the unit lane installed nothing.
   Same question: in scope, or separate?
3. **Does an unreached lane block the hand-back entirely, or only its own
   items?** If one lane measured real failures and another never ran, a
   hand-back carrying only the real ones seems right, but it changes the
   document's shape.

## Related

- #1343 — the api lane requires a deployed target and was run with none. This
  is the same missing variable; this issue is about how its absence is
  *reported*, which #1343 does not cover.
- #1341 — triage rejects every generated test when any one fails. Spec
  approved, plan not written.
- #376, #729 — hollow verify. The empty-worktree path is that failure reaching
  a different consumer.
- #629, #892 — earlier work on this same reason text, which added the
  import/assertion split and the exception detail.

Found driving olafkfreund/pfactory-friends-demo#113 and #127 through the
factory.
