"""Prompt output-contract vs parser agreement (#1250).

``prompts/evaluator.md`` tells the model its output shape; the consumer is
``agents.evaluator_verdicts``. Nothing else asserts the two agree, so either
side can drift independently — a prompt gaining a verdict value the parser
rejects turns real model output into a rejection that reads as a
model-quality problem.

Every assertion here binds prompt text to EXECUTABLE code (the real
``_validate_verdicts`` / ``_validate_one_verdict`` / ``compute_confidence``),
not to prose. No model calls.

Cases (issue #1250):
  c1 — the ``"verdict"`` alternatives in the prompt schema EQUAL
       ``_VALID_VERDICTS`` (equality, so drift on either side fails).
  c2 — the ``<!-- eval:example -->`` block in the prompt parses through the
       real validator and scorer.
  c3 — a string ``coverage_delta_pct`` ("N/A", "12.3") is REJECTED by the
       validator — the rule is enforced, not merely written down.
  c4 — planner prompt keys vs ``_validate_emitted_plan``: SKIPPED. The
       validated class is ``test_plan.ImplementationPlan``, a tolerant
       dataclass whose ``from_dict`` defaults nearly every key
       (``data.get(...)``); its enforced-required set is a tiny subset of the
       keys the planner prompt documents, so a key-set EQUALITY assertion has
       no true value to bind to and a containment one is drift-blind.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from agents.confidence import compute_confidence
from agents.evaluator_verdicts import (
    _VALID_VERDICTS,
    _validate_one_verdict,
    _validate_verdicts,
)

_PROMPT = Path(__file__).parent.parent / "apps" / "backend" / "prompts" / "evaluator.md"

# The schema line: `"verdict": "accept" | "reject" | "flag",` — requires at
# least one `|` so the single-valued example block below cannot match.
_ALTERNATION = re.compile(r'"verdict":\s*((?:"[a-z_]+"\s*\|\s*)+"[a-z_]+")')

_EXAMPLE = re.compile(r"<!--\s*eval:example\s*-->\s*```json\n(.*?)```", re.DOTALL)


def _example_doc() -> dict:
    m = _EXAMPLE.search(_PROMPT.read_text())
    assert m, "evaluator.md lost its <!-- eval:example --> fenced JSON block"
    return json.loads(m.group(1))


# ── c1: enum equality — drift on EITHER side must fail ──────────────────


def test_prompt_verdict_enum_equals_parser_enum() -> None:
    matches = _ALTERNATION.findall(_PROMPT.read_text())
    assert len(matches) == 1, (
        f"expected exactly one verdict alternation in evaluator.md, "
        f"found {len(matches)}: {matches!r}"
    )
    prompt_values = set(re.findall(r'"([a-z_]+)"', matches[0]))
    assert prompt_values == set(_VALID_VERDICTS)


# ── c2: the prompt's example parses through the REAL validator ──────────


def test_prompt_example_passes_real_validator(tmp_path: Path) -> None:
    doc = _example_doc()
    path = tmp_path / "verdicts.json"
    path.write_text(json.dumps(doc))
    ok, err, count = _validate_verdicts(path)
    assert ok, f"prompt example rejected by _validate_verdicts: {err}"
    assert count == len(doc["verdicts"]) >= 1


def test_prompt_example_scores_through_real_confidence() -> None:
    for v in _example_doc()["verdicts"]:
        c = compute_confidence(v)
        assert isinstance(c, float)
        assert 0.0 <= c <= 1.0


# ── c3: the string-coverage rule is ENFORCED, not merely written ────────


@pytest.mark.parametrize("bad", ["N/A", "12.3"])
def test_string_coverage_delta_is_rejected_by_code(bad: str) -> None:
    verdict = {
        "test_id": "t1",
        "verdict": "accept",
        "signals_summary": {"coverage_delta_pct": bad},
    }
    err = _validate_one_verdict(0, verdict, frozenset())
    assert err is not None, (
        f"coverage_delta_pct={bad!r} must be rejected — the prompt promises "
        "a number or null, and the parser must enforce it"
    )
    assert "coverage_delta_pct" in err
