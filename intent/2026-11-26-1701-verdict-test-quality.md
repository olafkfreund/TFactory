---
status: approved
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

- **Test-quality problems leave the merge decision when the evidence is deterministic.** `flaky`,
  `survived`, lint promotion, low relevance and `new_lines == 0` come from measured signals, not
  from an LLM opinion. A test rejected only for these is excluded from the verdicts AIFactory
  consumes. It is still dropped from the commit and still shown in the report and the PR comment.
- **A failing assertion stays a product failure unless its invalidity is proven.** A test that
  "contradicts the spec" is excluded only when both of these hold:
  - every judge in the vote agrees;
  - the contradiction is checked against the contract's acceptance criteria by something other
    than the judge's own claim.

  If either is missing, the test stays `reject` and fails the gate.
- **Coverage floor.** A pass needs at least one accepted or flagged test for every acceptance
  criterion that had a generated test. If excluding tests leaves a criterion without evidence, the
  result is no trusted verdict, not a pass.
- **A product failure reaches AIFactory as `fail`, not `None`.** A run whose remaining verdicts are
  all `reject` is a real failure, and AIFactory sees it that way.
- **AIFactory's gate is unchanged** (owner decision, 2026-11-26): `reject` still means fail.
- **Live proof:** E2E P1 passes for a correct build, AIFactory merges the PR (S4), and S5 reports
  the verdict on the merge SHA. A deliberately wrong build still fails.

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

1. **Where exclusion happens.** Proposed: as a deterministic step after the vote, with excluded
   rows moved to a top-level `excluded` list. The vote, enrichment and traceability keep seeing
   every test. The Triager, the report and the PR comment are taught to show `excluded`.
   Rejected: a new verdict value, because AIFactory reads an unknown value as `None` for the
   whole run.
2. **How a spec contradiction is proven.** Options:
   - (a) a unanimous vote, plus the cited acceptance-criterion text quoted exactly and matched in
     the contract;
   - (b) a separate deterministic AC-fidelity check, if `ac_fidelity` or `criterion_authority`
     can decide it;
   - (c) never exclude for spec contradiction in this fix, and only handle the deterministic
     signals.

   (a) is proposed, with (c) as the fallback if (a) proves unreliable in tests. Note that the
   run-`m` case is exactly a spec contradiction, so (c) alone wouldn't unblock P1.
3. **Precedence.** When a test is both flaky or `survived` and fails consistently on an
   assertion, proposed: the failure wins, and the test stays `reject`.
4. **Regenerating an excluded test** to restore coverage of its criterion is a follow-up.

Already decided: a judge call that errors counts as a deny, as it does today. An evaluation that
fails as a whole gives no trusted verdict.
