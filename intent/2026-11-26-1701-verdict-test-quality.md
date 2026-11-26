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

The reverse matters as much. A *valid* test that fails because the product is wrong also lands on
`consistent_fail` and then `reject`. Today that is indistinguishable from a broken test, inside
TFactory and in what AIFactory reads.

## Proposed outcome

- **A test-quality problem never fails the verdict AIFactory consumes.** That covers a test that
  contradicts the spec, a flaky test, and a test too weak to catch a mutation. Such tests are
  still dropped and still reported. They are kept out of the set of verdicts that decides the
  merge.
- **A product failure on a valid test still fails it.** That is a test which matches the spec and
  fails consistently on its assertions against the product.
- **No green from nothing.** If every generated test is excluded as invalid, AIFactory gets no
  verdict it can trust (`None`), not a `pass`.
- **AIFactory's merge gate is unchanged** (owner decision, 2026-11-26). It stays strict:
  `reject` means fail.
- **Visibility:** the TFactory report and the PR comment still show the excluded tests and why.
- **Live proof:** E2E P1 gets a passing verdict for a correct build, AIFactory merges the PR (S4),
  and S5 reports the verdict on the merge SHA. A deliberately wrong build still fails.

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

1. **How excluded tests are represented.** Options:
   - (a) move them to a separate `excluded` list in `verdicts.json`, outside `verdicts`;
   - (b) keep them in `verdicts` with a new verdict value.

   (b) breaks AIFactory, which treats an unknown value as `None`, so (a) is proposed.
2. **Should an excluded test be regenerated** within the run, so the product still gets coverage
   for that acceptance criterion? This is proposed as a follow-up, not part of this fix.
3. **Is a `survived`-mutation test "invalid", or a weak but valid test?** It doesn't show a
   product failure, so it is proposed for `excluded`.
