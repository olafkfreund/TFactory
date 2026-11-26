---
status: draft
issue: 1701
author: olafkfreund
---

# Intent: an invalid generated test must not fail the product's verdict

## Problem

TFactory's evaluator gives every generated test one of three verdicts:

- `accept`: the test is committed;
- `flag`: the test is committed and marked for human review;
- `reject`: the test is dropped. It is surfaced to the Triager, which filters it out, and it is
  never committed.

The auto-reject rules (`prompts/evaluator.md`) mostly describe the test's own quality:

| Signal | Prompt's reading | Verdict |
|---|---|---|
| `consistent_fail` | "test is broken" | `reject` |
| `flaky` | flake will haunt CI | `reject` |
| `survived` mutation | assertion too weak | `reject` |

AIFactory consumes `findings/verdicts.json` through `pfactory.tfactory_client.tfactory_verdict`,
which returns `fail` when any entry is `reject`. The merge gate then holds the PR.

So a test that TFactory generated badly blocks a correct build. In E2E run `mvr-20261126m`
(Factory#3882), TFactory generated 7 tests. One expected alphabetical output, while the spec
requires count-descending output. It failed all three runs, so it was rejected. The evaluator's
own reason says the test contradicts the spec. The other 6 were accepted, and the build was
correct, yet the PR never auto-merged.

TFactory already sorts some of these cases. #629 tells an import failure from an assertion
failure. `not_run` marks a lane that couldn't run. The gap that's left is narrower: when an
assertion ran and failed, nothing decides whether the **test** or the **product** is wrong. Other
auto-reject rules also judge test quality, and they fail AIFactory the same way: `flaky`,
`survived` mutation, lint promotion, low semantic relevance and `new_lines == 0`.

Two existing edges also matter:

- A run whose only remaining verdicts are `reject` makes the Triager write `triaged_empty`.
  AIFactory then gets `None` (no trusted result), not `fail`.
- The best-of-N vote counts accept, flag and reject only. It has no notion of *why* a test was
  rejected.

## Proposed outcome

Owner decision (2026-11-26), revised after the spec reviews: **regenerate, don't exclude.** Both
reviewers showed that removing a failing test from the verdict can also remove a real product
failure. That applies to flaky, mutation-survived and lint signals, and even to a unanimous "contradicts
spec" quote. So no failing test is ever removed from the verdict.

- **A suspected-invalid test is regenerated, not excluded.** When every judge in the vote says a
  consistently failing test contradicts a quoted acceptance criterion, TFactory doesn't record a
  final verdict for it. It sends that subtask back through the existing replan path
  (`context/replan_request.json`, Planner replan mode, with the existing per-subtask and global
  replan caps). The replan request carries the quoted criterion and the reason.
- **Only a passing, regenerated test can turn the verdict green.** The regenerated test is judged
  like any other test. If it passes, it is accepted. If the regenerated test still fails, or the
  replan cap is reached, the verdict is `reject` and AIFactory reads `fail`.
- **No other rule changes the verdict.** Flaky, survived, lint and low-relevance rejects stay
  `reject`, as today. The vote's fail-closed behaviour is unchanged.
- **A product failure reaches AIFactory as `fail`, not `None`.** A run whose final verdicts are all
  `reject` ends as `triaged`, so AIFactory reads `fail`, and its completion envelope says failure.
- **AIFactory's gate is unchanged** (owner decision): `reject` means fail.
- **Visibility:** the report and the PR comment show any test that was regenerated, the quoted
  criterion, and what happened to the regenerated test.
- **Live proof:**
  - E2E P1 with a correct build: a contradicting generated test is regenerated, the regenerated
    test passes, AIFactory merges the PR (S4), and S5 reports the verdict on the merge SHA.
  - A deliberately wrong build still fails.

## Affected users and systems

- TFactory evaluator: `agents/evaluator.py`, `agents/confidence.py`, `prompts/evaluator.md` and
  the `verdicts.json` writer and schema.
- TFactory triager: it reads `verdicts.json`.
- AIFactory's merge gate, which reads the result, and any other `verdicts.json` consumer
  (CFactory rollups, the reports). These must keep working.
- Prod TFactory, through a normal release.

## Constraints

- AIFactory is not changed.
- A test is judged "invalid" only on evidence: a contradiction with the spec or the acceptance
  criteria that the evaluator can cite, or a deterministic quality signal such as flaky or
  survived. A product failure must never be relabelled as a test problem just to get a green.
  When the evaluator is unsure, the test counts as a product failure.
- Fail closed: an evaluator error, or a set where every test is excluded, gives no trusted verdict.
- Old `verdicts.json` files and their readers must keep working.

## Open questions

1. **Where the regeneration trigger lives.** The judge's contradiction claim is only known after the
   vote. So the evaluator, after the vote, writes the replan request for those subtasks instead of
   finishing. The evaluator must then return to the Planner for those subtasks only, without
   re-running the ones that are already decided. The spec has to show that this loop exists or can
   reuse the generation-time `criterion_literals` path.
2. **Trigger strictness.** The proposal: a unanimous vote with at least 2 calls, where every call
   quotes a criterion found in the plan's AC text. A quote that doesn't match never triggers a
   replan, so the test stays `reject`. A false trigger costs only one regeneration, because the new
   test still has to pass, so the trigger can be stricter than an exclusion would need to be
   without any risk of a false green.
3. **Cost.** At most one regeneration per subtask per run, within the existing caps.

## Status (2026-11-26)

The owner decided: **strict now, regenerate later.** TFactory is unchanged for now. A test that
contradicts the spec keeps blocking the merge, which can cause false fails but never false
greens. This intent stays a draft and is tracked as a planned feature.

The reviews of this revision set these requirements for the feature:

1. **A post-vote replan loop.** It must:
   - keep the verdicts already decided;
   - handle several regeneration requests one at a time;
   - bind each replacement to its original subtask;
   - keep intermediate votes out of the final `verdicts.json`;
   - hold the Triager until every request resolves;
   - give a stuck request an owner (today `replan_needed` is outside the liveness watchdog).
2. **Fail-closed caps.** Record the regeneration against the original subtask before dispatch.
   If the replacement is missing, truncated or out of budget, write a terminal `reject`. The
   existing STUCK path and the partial-verify path at the budget limit must never end green.
3. **The replacement must be at least as strong as the original.** A passing replacement needs
   fresh execution on the same build, plus evidence that it asserts the originally disputed
   behaviour. A replacement that checks the items but ignores their order must not pass.
4. **The trigger is judged from every raw judge call.** Each call must name the same subtask and
   AC, and there must be runner evidence of an assertion failure. A crashed or missing call
   doesn't count. Decide whether single-run browser evidence qualifies.
5. **An all-reject run must report `fail`.** Both `final_status` and the completion envelope
   (`_status_verdict`) change, and the mixed accept/reject outcome is specified.
