---
status: approved
issue: 1619
author: olafkfreund
---

# Intent: criterion-literal gate loops on derived values

## Problem

On 0.13.1 two runs (aifactory-demo #624, factory-lab-go #1) burned the global replan budget (12, 8-11 USD) because the #888 criterion-literal gate rejected tests whose expected values are derived: a padded field width `8`, `"a" * (MAX + 1)` for 101, or a value (201) that belongs to another case in the same criterion text. Each rewrite was rejected again, as a new subtask id (`x`, `x-r1`, `x-r2`...). The issue also says the partial-verify handoff after exhaustion runs no lanes.

## Proposed outcome

A subtask lineage the gate keeps rejecting stops replanning after a bounded number of identical rejections and its test is kept, so the budget is not spent on one systemic cause. The partial-verify handoff runs the lanes for the accepted tests.

## Affected users and systems

Gen-Functional (`apps/backend/agents/gen_functional.py`), Planner replan budget, every verify run with numeric criteria.

## Constraints

Must not weaken #888 for first rejections: a criterion silently reinterpreted must still be replanned. Other guardrails (authority, preflight) are never waived. Smallest change.

## Open questions

None. Evidence on the live cluster (read-only): the partial-verify handoff DID dispatch a verify Job (`factory-tfactory-o-lab-goc-20261119r2`, 09:03) and the spec reached `triaged` with 6 verdicts at 09:21; the "lanes pending" the issue saw is the ~17 min Job latency. The Go unit lane then errored on a duplicate `TestTFGenCount` across sibling files (a separate collateral of the loop, not in scope).
