---
status: approved
issue: 1630
intent: intent/2026-11-19-1630-tf-literal-gate-ac-match.md
---

# Spec: Criterion-literal gate ignores layout parameters

## Design

`criterion_literals()` already removes numbers that identify things (`_REF_WORD`), ISO dates and clock times before extraction. Add one more strip, `_LAYOUT_PARAM`, for numbers that name a layout parameter: `width|wide|padded|padding|pad|indent` followed by a number (`width 8`, `padded to 8`, `padding of 4`), and a number followed by `wide` (`8 characters wide`, `8-wide`). Exact pattern (case-insensitive, word-bounded): `\b(?:width|wide|padded|padding|pad|indent(?:ed|ation)?)\b\s*(?:of|to|by|=|:)?\s*\d+(?!\.\d)|\b\d+(?:\s*-\s*|\s+)(?:(?:characters?|chars?|columns?|spaces?)\s+)?(?:wide|width)\b`. Applied in `criterion_literals()` next to the other strips. A value outside the layout phrase survives: "prints 8 in width 8" still wants 8. Result: for "prints counts 2 3 6, counts right-aligned in width 8" the wanted set is {2, 3, 6}; a padded-string assertion passes; a test missing 2/3/6 still fails.

Why this, not "match the right AC": the gate already receives one subtask's own description; the stray 8 is inside that description (reproduced from the PR body), so the fault is extraction, not AC matching. The planner description itself was not inspected (unverified).

## Alternatives rejected

- Accept a padded-string match for any literal: needs per-language format parsing; large.
- Raise the waiver (#1619) sooner: hides the wrong rejection instead of removing it.
- Drop the gate: loses the #888 protection.

## Risks

A criterion whose real expected value follows one of these words ("width 8" meaning the output must contain 8) loses that check; the verdict-path assertions still grade the test. A miss, not a misfire, matching the module's stated bias toward zero false positives.

## Verification

Unit tests in tests/test_criterion_literals.py: the PR-body criterion yields {2,3,6}; "8 characters wide", "padded to 8" and "indent 4" are excluded; "total 8" still yields 8; "total 8" and "prints 8 in width 8" still yield 8; a test asserting `"       2       3       6"` passes, one asserting only 2 3 fails. Tests fail on the current code.
