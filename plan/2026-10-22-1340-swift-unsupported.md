---
status: approved
issue: 1340
spec: spec/2026-10-22-1340-swift-unsupported.md
---

# Plan: mark Swift explicitly not supported in TFactory

Decision (user): Swift is explicitly not supported in TFactory until a measured
library-only lane exists. Do it TFactory-locally; never edit the vendored
`apps/backend/tools/runners/languages/swift.yaml` (byte-exact drift gate vs the hub).

## Steps

1. `apps/backend/tools/runners/lang_registry.py` (~L95-215): add module-level
   `UNSUPPORTED_LANGUAGES = {"swift": "<reason naming TFactory#1340: no in-cluster
   Swift lane (no run_swift_lane_via_nix, no frameworks/swift); a library-only lane
   needs a cluster measurement first>"}`. In `_descriptor_registry()` `continue`
   for names in it. In `get_tool_for_lane()` raise `UnsupportedLanguageError(reason)`
   for it before the registry/manifest lookup. Update
   `tests/test_lang_registry_descriptors.py` and `tests/test_lang_registry.py`
   (swift no longer in unit lists; swift refused with "#1340", also with a
   manifest carrying verify_commands; `unavailable_lane_reason` behaviour unchanged).
   → verify by `cd apps/backend && pytest ../../tests/test_lang_registry*.py -q`
   Traps: `_descriptor_registry` is lru_cached; static `_REGISTRY` must not change.
2. `apps/backend/agents/evaluator.py` `run_evaluator` (~L3290, after
   `_load_plan_or_fail`): collect completed subtasks via `_filter_completed_subtasks`
   whose `language` (lowercased) is in `UNSUPPORTED_LANGUAGES`; if any, write
   `status="evaluator_failed"`, `phase=f"evaluator_{mode}_unsupported_language"`,
   `evaluator_error=<reason>` and return False, before the empty check. Add a test
   in `tests/test_evaluator.py` beside `test_kotlin_only_plan_is_evaluated_not_empty`:
   Swift-only completed plan → `evaluator_failed`, error mentions swift and #1340,
   SDK not called.
   → verify by `pytest tests/test_evaluator.py -q`
   Traps: import lazily or at top consistent with the module; no new deps.

## Tests

`pytest tests/test_lang_registry.py tests/test_lang_registry_descriptors.py tests/test_evaluator.py -q`
all pass; the new evaluator test fails on origin/dev (evaluated_empty). ruff clean,
`python scripts/ratchet_lint.py` green.

## Rollback

Revert the PR; the descriptor was never touched, so Swift returns to its previous
(registry-supported, evaluator-dropped) state.
