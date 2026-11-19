---
status: approved
issue: 1630
spec: spec/2026-11-19-1630-tf-literal-gate-ac-match.md
coders:
  1: codex   # small tricky regex logic plus its tests
reviewers:
  - codex    # independent read of the final diff
---

# Plan: Criterion-literal gate ignores layout parameters

Approved spec decision: strip layout-parameter numbers (width/wide/padded/padding/pad/indent N, and "N [characters] wide") from the criterion text before extracting literals. Values outside the phrase still count.

## Steps

1. apps/backend/agents/criterion_literals.py (near `_CLOCK`, and in `criterion_literals()` after `_CLOCK.sub`): add `_LAYOUT_PARAM = re.compile(r"\b(?:width|wide|padded|padding|pad|indent(?:ed|ation)?)\b\s*(?:of|to|by|=|:)?\s*\d+(?:\.\d+)?|(?<![\w.])\d+(?:\.\d+)?(?:\s*-\s*|\s+)(?:(?:characters?|chars?|columns?|spaces?)\s+)?(?:wide|width)\b", re.I)` and apply `.sub(" ", cleaned)`; add a module-docstring bullet. Tests in tests/test_criterion_literals.py: PR-body text yields {2,3,6}; "8 characters wide", "padded to 8", "indent 4" excluded; "total 8" and "prints 8 in width 8" yield 8; decimals stay whole ("width 12.5" and "12.5 characters wide" leave nothing behind, "total 12.5" keeps 12.5); a padded-string test passes, one missing 2 or 3 fails. → verify: `pytest tests/test_criterion_literals.py -q` (new tests fail with the code change reverted).
   Traps: keep the module's zero-false-positive bias; no new dependency.

## Tests

`pytest tests/test_criterion_literals.py tests/test_gen_functional*.py -q`; `python scripts/ratchet_lint.py --base origin/dev --package apps/backend --package apps/web-server`.

## Rollback

Revert the commit; the gate returns to extracting every standalone number.
