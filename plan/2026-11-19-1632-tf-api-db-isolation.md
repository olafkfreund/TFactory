---
status: approved
issue: 1632
spec: spec/2026-11-19-1632-tf-api-db-isolation.md
coders:
  3: coder   # prompt text, done by the lead; no handoff
reviewers:
  - codex
---

# Plan: API-lane generator keeps DB-backed tests isolated

Approved spec decision: prompt guidance in the pytest descriptor's API-lane block, not a generated truncate fixture; AC assertions stay required.

## Steps

3. frameworks/pytest/descriptor.yaml (API lane block, after the "Do not assume a fixed request/response schema" bullet): add an isolation bullet group (unique uuid identifiers per test; create own data and assert pagination/ordering/stats against it, relative order and before/after deltas, never an assumed empty or fixed dataset; clean up via the SUT API in a fixture finalizer when a delete endpoint exists; never connect to the database directly; assumes no concurrent writers). Test in tests/test_framework_registry.py next to the real-descriptor test (~line 533): the loaded pytest context block contains the guidance and TFACTORY_TARGET_URL. → verify: `pytest tests/test_framework_registry.py -q`.
   Traps: YAML block scalar indentation; descriptor validator must still pass.

## Tests

`pytest tests/test_framework_registry.py tests/test_gen_functional_prompts.py -q`.

## Rollback

Revert the commit.
