---
status: approved
issue: 1340
intent: intent/2026-10-22-1340-swift-unsupported.md
---

# Spec: mark Swift explicitly not supported in TFactory

## Design

Chosen (the user's decision): **mark Swift explicitly not supported**, as a
TFactory-local fact, not a descriptor edit.

1. `apps/backend/tools/runners/lang_registry.py`: add
   `UNSUPPORTED_LANGUAGES: dict[str, str]` mapping `"swift"` to its reason (no
   in-cluster Swift lane, #1340; library-only lane is a future measured issue).
   `_descriptor_registry()` skips these names, so Swift leaves
   `languages_supporting_lane`. `get_tool_for_lane()` raises
   `UnsupportedLanguageError(reason)` for them before the manifest fallback, so an
   environment manifest cannot re-admit Swift either.
2. `apps/backend/agents/evaluator.py` `run_evaluator`: after loading the plan,
   if any completed subtask's `language` is in `UNSUPPORTED_LANGUAGES`, write
   `status=evaluator_failed`, `phase=evaluator_<mode>_unsupported_language`,
   `evaluator_error=<reason>` and return False. This runs before the empty check,
   so a Swift-only plan no longer ends `evaluated_empty`, and a mixed plan does
   not report green while its Swift part went unverified.

The descriptor `swift.yaml` stays byte-exact with the hub: its toolchain facts
(proven by hand 2026-09-03) remain true and AIFactory still consumes them.

## Alternatives rejected

- **Build the Swift lane now** (`run_swift_lane_via_nix` + `frameworks/swift`):
  rejected by the user's decision; the issue says the next step is a cluster
  measurement first. Can be a new issue once measured.
- **Flip `lanes.unit.available: false` in `swift.yaml`**: the file is a hub
  canonical vendored byte-exact into three services; editing it here fails the
  drift gate, and it would also be false for AIFactory, whose builds do run Swift.
- **Skip Swift subtasks and evaluate the rest**: a partial green over unverified
  Swift work is the "looks supported" failure the issue is about.

## Risks

- A mixed Swift + Kotlin plan now fails the whole verify instead of verifying
  the Kotlin half. Intended: the failure names Swift, the reason is actionable.
- Existing tests that assert Swift registry rows must change to assert refusal.

## Verification

- `pytest tests/test_lang_registry_descriptors.py tests/test_lang_registry.py tests/test_evaluator.py`
- New tests: `get_tool_for_lane("swift","unit")` raises with "#1340" in the
  message (also with a manifest); swift absent from `languages_supporting_lane`;
  a Swift-only completed plan ends `evaluator_failed` with the reason, SDK not called.
  The evaluator test fails on origin/dev (it ends `evaluated_empty`).
- ruff + `scripts/ratchet_lint.py` on touched files.
