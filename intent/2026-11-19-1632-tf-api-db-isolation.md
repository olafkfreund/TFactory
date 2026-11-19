---
status: draft
issue: 1632
author: olafkfreund
---

# Intent: API-lane tests must not depend on shared database state

## Problem

factory-lab-cloud PR #1: 4 of 8 api verdicts rejected (pagination, tie-ordering, stats-populated, win-persists): generated tests share Postgres state with no isolation. A generated test normally holds only the HTTP target (TFACTORY_TARGET_URL) and no DB handle (unverified for this run), so a truncate fixture is not a safe thing to generate.

## Proposed outcome

The pytest API-lane generator guidance tells the model to use unique per-test identifiers, assert on data the test created, and clean up via the SUT API when it can. Explicit acceptance-criterion assertions (pagination, ordering, stats) stay required: the test creates its own data to assert them against rather than assuming an empty or fixed dataset.

## Affected users and systems

frameworks/pytest/descriptor.yaml context_block (injected into Gen-Functional prompts).

## Constraints

Smallest correct change; tests that fail without the fix; no change to verdict semantics; other teams edit TFactory in parallel, keep the diff narrow.

## Open questions

None. Decision (owner delegation 2026-11-19): take the recommended option recorded in the issue.
