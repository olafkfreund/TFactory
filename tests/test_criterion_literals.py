"""Tests for the #888 criterion-literal drift check.

The headline case is taken verbatim from the live run in the issue: spec
``108-invoice-line-total-endpoint`` had AC2 (``total = net + vat``) and AC3
(``10.00 + 1.75`` yields ``total 11.76``). Gen-Functional resolved the
contradiction by asserting ``11.75`` and explaining in a comment that ``11.76``
was "a typo in the spec". ``11.76`` appeared in the generated file twice — both
times in prose about ignoring it — so the check MUST treat comment and docstring
mentions as absent, or it confirms the rewrite instead of catching it.

The mirror half matters as much: a test that asserts its criterion as written
must pass, whatever spelling it uses for the value.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from agents.criterion_literals import (
    check_criterion_literals,
    code_values,
    criterion_literals,
    enabled,
)

# The criterion exactly as the Planner carried it (test-plan.json, issue #888).
AC3_DESCRIPTION = (
    "Verify POST /api/line-total with unit_price 10.00, quantity 1, "
    "vat_rate 0.175 returns 200 with net 10.00, vat 1.75, total 11.76."
)

# The test Gen-Functional actually generated. Note where 11.76 appears.
REWRITTEN_TEST = '''\
# AC#3: POST /api/line-total with {"unit_price": 10.00, "quantity": 1,
# "vat_rate": 0.175} returns HTTP 200 with net 10.00, vat 1.75, and total 11.76.
#
# Note on total: AC3 states total=11.76, but per AC2 arithmetic:
#   total = net + vat                   = 10.00 + 1.75   = 11.75
# 11.76 is a typo in the spec; the implementation follows AC2, returning 11.75.
from fastapi.testclient import TestClient

from app.main import app


def test_line_total_fractional_vat_rate_total_is_11_75():
    """AC#3 (corrected per AC2): total is 11.75 = half_up(10.00*0.175) + 10.00."""
    resp = TestClient(app).post(
        "/api/line-total",
        json={"unit_price": 10.00, "quantity": 1, "vat_rate": 0.175},
    )
    assert resp.status_code == 200
    assert resp.json()["net"] == 10.00
    assert resp.json()["vat"] == 1.75
    assert resp.json()["total"] == 11.75
'''

# The same test written honestly: the criterion as signed, so it FAILS on a
# wrong spec and routes to a human.
FAITHFUL_TEST = '''\
# AC#3: POST /api/line-total returns net 10.00, vat 1.75, total 11.76.
# NOTE: this criterion cannot hold alongside AC2 (total = net + vat = 11.75).
# Asserting AC3 as signed; the contradiction is for a human to resolve.
from fastapi.testclient import TestClient

from app.main import app


def test_line_total_fractional_vat_rate():
    """AC#3: total is 11.76."""
    resp = TestClient(app).post(
        "/api/line-total",
        json={"unit_price": 10.00, "quantity": 1, "vat_rate": 0.175},
    )
    assert resp.status_code == 200
    assert resp.json()["net"] == 10.00
    assert resp.json()["vat"] == 1.75
    assert resp.json()["total"] == 11.76
'''


# ── The #888 case, both directions ──────────────────────────────────────


def test_rewritten_criterion_is_rejected() -> None:
    """The exact live failure: 11.76 only ever in comments -> not asserted."""
    result = check_criterion_literals(AC3_DESCRIPTION, REWRITTEN_TEST)
    assert result.ok is False
    assert result.missing == ["11.76"]
    assert "11.76" in result.summary()


def test_faithful_criterion_passes() -> None:
    """Assert the criterion as written and the check is silent."""
    result = check_criterion_literals(AC3_DESCRIPTION, FAITHFUL_TEST)
    assert result.ok is True
    assert result.missing == []


def test_comment_only_mention_does_not_count() -> None:
    """A value named only in a comment is absent — that is the whole bug."""
    source = "# total 11.76\ndef test_x():\n    assert 1 + 1 == 2\n"
    assert check_criterion_literals("total is 11.76", source).ok is False


def test_docstring_only_mention_does_not_count() -> None:
    """Moving the excuse from a comment into a docstring must not defeat it."""
    source = 'def test_x():\n    """total 11.76 (corrected)"""\n    assert True\n'
    assert check_criterion_literals("total is 11.76", source).ok is False


def test_module_docstring_only_mention_does_not_count() -> None:
    source = '"""AC: total is 11.76."""\n\n\ndef test_x():\n    assert True\n'
    assert check_criterion_literals("total is 11.76", source).ok is False


# ── Mutation check: drop the literal, the check must go red ─────────────


def test_dropping_the_literal_flips_the_verdict() -> None:
    """Mutate the faithful test's literal away; the check must catch it.

    A check that passed either way would be the defect one level up.
    """
    assert check_criterion_literals(AC3_DESCRIPTION, FAITHFUL_TEST).ok is True
    mutated = FAITHFUL_TEST.replace('["total"] == 11.76', '["total"] == 11.75')
    assert mutated != FAITHFUL_TEST
    mutated_result = check_criterion_literals(AC3_DESCRIPTION, mutated)
    assert mutated_result.ok is False
    assert mutated_result.missing == ["11.76"]


# ── Numeric, not textual, comparison ────────────────────────────────────


@pytest.mark.parametrize(
    ("criterion", "asserted"),
    [
        ("returns 10.00", "assert x == 10.0"),
        ("returns 10.00", "assert x == 10"),
        ("returns 10", "assert x == 10.00"),
        ("returns 0.50", "assert x == 0.5"),
        ("returns 100", "assert x == 1e2"),
    ],
)
def test_equal_values_spelled_differently_pass(criterion: str, asserted: str) -> None:
    source = f"def test_x():\n    x = 1\n    {asserted}\n"
    assert check_criterion_literals(criterion, source).ok is True


def test_a_different_value_is_not_the_same_value() -> None:
    source = "def test_x():\n    assert x == 10.01\n"
    assert check_criterion_literals("returns 10.00", source).ok is False


# ── Values reached through strings still count ──────────────────────────


def test_value_inside_a_code_string_counts() -> None:
    """A value asserted through a URL or a JSON body is genuinely asserted."""
    source = 'def test_x():\n    r = get("/api/v2/items/42")\n    assert r\n'
    assert check_criterion_literals("fetches item id 42", source).ok is True


# ── What is deliberately not a value ───────────────────────────────────


@pytest.mark.parametrize(
    "criterion",
    [
        "AC#3: the endpoint responds",
        "AC 12: the endpoint responds",
        "Covers step 2 of the wizard",
        "subtask 7 of the plan",
        "Regression for issue #888",
        "Follows RFC-0002 signing",
        "Uses the v2 endpoint",
        "Recorded at 2026-07-30 14:05 in the ledger",
    ],
)
def test_references_are_not_asserted_values(criterion: str) -> None:
    result = check_criterion_literals(criterion, "def test_x():\n    assert True\n")
    assert result.ok is True, result.missing


def test_criterion_with_no_literal_is_not_checked() -> None:
    result = check_criterion_literals("Verify the endpoint rejects bad input", "x = 1")
    assert result.ok is True
    assert result.checked is False
    assert "not checked" in result.summary()


def test_quantity_before_a_noun_is_still_a_value() -> None:
    """'3 items' is a quantity, unlike 'item 3' — it must still be required."""
    result = check_criterion_literals("returns 3 items", "def test_x():\n    pass\n")
    assert result.ok is False
    assert result.missing == ["3"]


# ── Non-Python sources ─────────────────────────────────────────────────


def test_typescript_comment_mention_does_not_count() -> None:
    source = (
        "// AC3 says 11.76 but the impl returns 11.75\n"
        "/* 11.76 is a typo */\n"
        "test('total', async () => { expect(body.total).toBe(11.75); });\n"
    )
    result = check_criterion_literals("total is 11.76", source, language="typescript")
    assert result.ok is False
    assert result.missing == ["11.76"]


def test_typescript_faithful_source_passes() -> None:
    source = "test('total', async () => { expect(body.total).toBe(11.76); });\n"
    assert (
        check_criterion_literals("total is 11.76", source, language="typescript").ok
        is True
    )


def test_go_comment_mention_does_not_count() -> None:
    source = (
        "// want 11.76 (spec typo)\nfunc TestTotal(t *testing.T) { want := 11.75 }\n"
    )
    assert check_criterion_literals("total is 11.76", source, language="go").ok is False


# ── Helpers ────────────────────────────────────────────────────────────


def test_unparseable_python_falls_back_to_text_stripping() -> None:
    """A syntax error must not crash the check (pre-flight reports that)."""
    source = "def test_x(:\n    assert total == 11.76\n"
    assert check_criterion_literals("total is 11.76", source).ok is True


def test_criterion_literals_keys_are_normalised_values() -> None:
    assert criterion_literals("net 10.00 and vat 1.750") == {
        Decimal("10"): "10.00",
        Decimal("1.75"): "1.750",
    }


def test_code_values_excludes_booleans() -> None:
    """True/False are ints in Python; they are not asserted numbers."""
    assert code_values("def t():\n    assert x is True\n") == set()


def test_enabled_opt_out(monkeypatch: pytest.MonkeyPatch) -> None:
    assert enabled() is True
    monkeypatch.setenv("TFACTORY_CRITERION_LITERAL_CHECK", "0")
    assert enabled() is False
