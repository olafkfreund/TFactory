"""Criterion-literal drift check for Gen-Functional (#888).

A verifier may never grade the specification against the implementation. #888
caught it doing exactly that: an acceptance criterion said ``total 11.76``, the
implementation returned ``11.75``, and Gen-Functional generated a test asserting
``11.75`` with a comment explaining that ``11.76`` was "a typo in the spec".
``11.76`` appeared in the generated suite only inside comments explaining its
rejection, so the signed criterion was never asserted, no test could fail on it,
and nothing reached the verdict, the AC-fidelity ledger or the VAL claim.

This is the cheap static backstop for that whole class: compare the numeric
literals in the criterion's own text against the numeric literals in the test
that claims it. If a value the criterion states appears nowhere in the test's
CODE, the generation step fails — the criterion was silently reinterpreted.

Two properties make it catch the real case rather than a strawman:

  - **Comments and docstrings do not count.** The rewritten test "mentioned"
    11.76 twice; both were prose about why it was being ignored. Only literals
    the test actually executes on count as asserting the value. For Python that
    is done over the AST (comments are absent from it, docstrings are excluded
    explicitly); for other languages the comment syntaxes are stripped first.
  - **Values compare numerically, not textually.** ``10.00``, ``10.0`` and
    ``10`` are the same value, so restating a criterion's ``10.00`` as ``10.0``
    in the test is not drift. Decimal comparison, not string matching.

What it deliberately does NOT do (kept out to hold the false-positive rate at
zero on honest specs, where a spurious rejection costs a replan):

  - Numbers glued to an identifier or reference (``v2``, ``#888``, ``RFC-0002``)
    are not values, nor is a number that merely names one ("AC#3", "step 2",
    "subtask 1") — excluded. A number in front of a noun ("3 items") is a
    quantity and stays.
  - ISO dates and clock times are stripped before extraction; a criterion that
    names a date whose test builds a ``datetime`` is not drift.
  - Negative numbers are not extracted (the leading ``-`` is indistinguishable
    from the hyphen in ``RFC-0002`` without parsing prose). A criterion whose
    only contested value is negative is not covered — a miss, not a misfire.

Pure + unit-tested. The caller (``agents.gen_functional``) treats a
``not ok`` result exactly as it treats a pre-flight or flake-lint rejection:
delete the file, write ``context/replan_request.json``, and route to the
Planner — a loud, human-reachable outcome instead of a quietly passing test.
"""

from __future__ import annotations

import ast
import logging
import os
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

logger = logging.getLogger(__name__)

# A standalone numeric value in prose. The lookbehind rejects a digit glued to a
# word char, a dot, a ``#`` or a ``-`` (so ``v2``, ``10.00``'s inner ``00``,
# ``#888`` and ``RFC-0002`` are not values); the lookahead rejects a trailing
# word char and a further decimal group, so ``11.76.`` at the end of a sentence
# still yields ``11.76``.
_NUMBER = re.compile(r"(?<![\w.#-])(\d+(?:\.\d+)?)(?![\w]|\.\d)")

# Stripped from criterion text before extraction — these carry no asserted
# value. A number FOLLOWING one of these nouns names a thing ("AC#3", "step 2",
# "subtask 1"); a number PRECEDING a noun is a quantity ("3 items") and is left
# alone. The negative lookahead keeps a decimal value intact, so a criterion
# phrased "test 11.76" still yields 11.76.
_REF_WORD = re.compile(
    r"\b(?:AC|acceptance\s+criterion|step|phase|part|case|scenario|subtask|task"
    r"|test|example|item|section|line|column|point|figure)s?\s*#?\s*\d+(?!\.\d)",
    re.IGNORECASE,
)
_ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?)?")
_CLOCK = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")

# Comment syntaxes for the non-Python fallback: // line, /* block */, # line,
# and Python-style triple-quoted blocks (harmless elsewhere).
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_TRIPLE_QUOTED = re.compile(r'""".*?"""|\'\'\'.*?\'\'\'', re.DOTALL)
_LINE_COMMENT = re.compile(r"(?://|#).*?$", re.MULTILINE)


@dataclass
class LiteralDriftResult:
    """Outcome of one criterion-literal check over a generated test source."""

    ok: bool
    missing: list[str] = field(default_factory=list)
    criterion_literals: list[str] = field(default_factory=list)
    checked: bool = True  # False when there was nothing to compare

    def summary(self) -> str:
        if not self.checked:
            return "not checked (no numeric literal in the criterion)"
        if self.ok:
            return f"OK ({len(self.criterion_literals)} criterion literal(s) asserted)"
        return f"{len(self.missing)} criterion literal(s) never asserted: " + ", ".join(
            self.missing
        )


def _norm(token: str) -> Decimal | None:
    """Numeric value of ``token``, normalised so 10.00 == 10.0 == 10."""
    try:
        return Decimal(token).normalize()
    except (InvalidOperation, ValueError):
        return None


def _numbers_in_text(text: str) -> dict[Decimal, str]:
    """Value -> first spelling, for every standalone number in ``text``."""
    found: dict[Decimal, str] = {}
    for token in _NUMBER.findall(text or ""):
        value = _norm(token)
        if value is not None:
            found.setdefault(value, token)
    return found


def criterion_literals(text: str) -> dict[Decimal, str]:
    """Numeric values a criterion states, keyed by normalised value.

    Structural references ("AC#3", "step 2"), ISO dates and clock times are
    removed first — they identify things, they are not values to assert.
    """
    cleaned = _REF_WORD.sub(" ", text or "")
    cleaned = _ISO_DATE.sub(" ", cleaned)
    cleaned = _CLOCK.sub(" ", cleaned)
    return _numbers_in_text(cleaned)


def _docstring_constant_ids(tree: ast.AST) -> set[int]:
    """ids of the ``ast.Constant`` nodes that are docstrings, not code."""
    ids: set[int] = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(
            node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
        ):
            continue
        if not isinstance(body, list) or not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            ids.add(id(first.value))
    return ids


def _python_code_values(source: str) -> set[Decimal] | None:
    """Every numeric value the Python source actually executes on.

    Comments never reach the AST; docstrings are excluded explicitly. Numbers
    inside ordinary string constants DO count — a value asserted through a URL
    or a JSON request body is genuinely asserted. Returns ``None`` when the
    source does not parse (the caller falls back to text stripping; the
    pre-flight guard reports the syntax error itself).
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    doc_ids = _docstring_constant_ids(tree)
    values: set[Decimal] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or id(node) in doc_ids:
            continue
        raw = node.value
        if isinstance(raw, bool):
            continue
        if isinstance(raw, int | float):
            value = _norm(str(raw))
            if value is not None:
                values.add(value)
        elif isinstance(raw, str):
            values.update(_numbers_in_text(raw))
    return values


def _stripped_code_values(source: str) -> set[Decimal]:
    """Numeric values outside comments, for languages without an AST here."""
    body = _BLOCK_COMMENT.sub(" ", source or "")
    body = _TRIPLE_QUOTED.sub(" ", body)
    body = _LINE_COMMENT.sub(" ", body)
    return set(_numbers_in_text(body))


def code_values(source: str, language: str | None = None) -> set[Decimal]:
    """Numeric values the generated test asserts on (comments excluded)."""
    if (language or "python") == "python":
        parsed = _python_code_values(source)
        if parsed is not None:
            return parsed
    return _stripped_code_values(source)


def enabled() -> bool:
    """The check is on by default; ``TFACTORY_CRITERION_LITERAL_CHECK=0`` opts out.

    An escape hatch for an operator facing a criterion this static check cannot
    read, not a default. Off means a silent rewrite is undetectable again.
    """
    return os.environ.get("TFACTORY_CRITERION_LITERAL_CHECK", "1") != "0"


def check_criterion_literals(
    criterion_text: str,
    source: str,
    *,
    language: str | None = None,
) -> LiteralDriftResult:
    """Does the generated test assert every numeric value its criterion states?

    Args:
        criterion_text: the subtask's ``description`` (the criterion as signed).
        source: the generated test file's source.
        language: the subtask's language; ``None``/``"python"`` uses the AST path.

    Returns:
        A ``LiteralDriftResult``; ``ok=False`` means at least one value the
        criterion states appears nowhere in the test's code.
    """
    wanted = criterion_literals(criterion_text)
    if not wanted:
        return LiteralDriftResult(ok=True, checked=False)
    present = code_values(source, language)
    missing = [token for value, token in wanted.items() if value not in present]
    return LiteralDriftResult(
        ok=not missing,
        missing=missing,
        criterion_literals=sorted(wanted.values()),
    )
