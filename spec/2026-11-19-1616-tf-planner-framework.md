---
status: approved
issue: 1616
intent: intent/2026-11-19-1616-tf-planner-framework.md
---

# Spec: normalise api to unit for unit-only frameworks

## Design

`_normalise_single_lane(plan, registry)`, run beside `_normalise_js_test_paths` in `_validate_emitted_plan` (plan saved back when changed): when the framework's lanes are exactly (unit,) and the subtask lane is api, set it to unit. Same pattern as the #1579 path rewrite.

## Alternatives rejected

- Listing the lane matrix in the prompt only: the retry already states the rule and the planner failed it twice.
- Normalising any wrong lane: would turn real planner mistakes (browser on kotlin) into silently unit tests; an existing test requires rejection.

## Risks

An API-intent test runs on the unit lane, which is what jest can execute.

## Verification

tests/test_planner.py::test_jest_declared_on_api_lane_is_moved_to_unit (one planner call, lane unit saved); the kotlin-browser rejection test still passes.
