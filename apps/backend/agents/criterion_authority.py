"""Reject a generated test whose PROSE claims authority over the spec (#995).

``criterion_literals`` (#894) catches the case where a generator disagreed with
a criterion and asserted its own value instead — it compares VALUES, which is
why it holds a zero false-positive rate on honest specs. What it cannot catch is
the narrative when the value happens to line up: a test that asserts the
criterion correctly while its docstring announces that the author decided the
specification was wrong.

That test has redefined its own oracle. Even when it did not act on the
decision this time, the generator reasoned its way to grading the specification
against the implementation, which is the one direction verification must never
run. It is also a far clearer message to an operator than "literal drift".

**Why this is not a keyword blacklist.** #896 deliberately did not fold this
into #894 because a word list is brittle enough to deserve its own
false-positive budget, and every obvious keyword has an innocent use:

- ``corrected`` — ``test_corrected_invoice_totals``, or a docstring describing
  the SUT's own correction logic
- ``typo`` — a test for a spelling-normalisation feature
- ``instead`` — ordinary prose ("returns None instead of raising")

So no bare keyword can fire here. A match requires a complete CLAIM: the
spec/criterion/AC named as the SUBJECT, and asserted to be wrong or to have been
overridden. "Corrected" alone is nothing; "the spec is wrong, corrected to
11.75" is the defect. That is #995's "proximity, not presence", enforced by
making the subject part of the pattern rather than by a nearby-token window.

**Scope.** Comments and docstrings only, never string literals under test — a
test *for* a spec-validation feature may legitimately carry "the spec is wrong"
as data. ``criterion_literals`` already has the machinery for the inverse
direction; this is the same split read the other way.

**Measured.** ``tests/test_criterion_authority.py`` runs this matcher over every
test file in the repository — 342 files of real, human-written prose — and fails
if it fires on any of them. That is the false-positive budget #995 asked for,
held at zero on the largest honest corpus available. The same test also asserts
it DOES fire on the two files that carry the live #888 specimen verbatim as a
fixture: a matcher with no true positives is just a function returning True.
"""

from __future__ import annotations

import ast
import os
import re
from dataclasses import dataclass, field

__all__ = [
    "AuthorityResult",
    "check_criterion_authority",
    "enabled",
    "prose_segments",
]

# The thing a claim must be ABOUT. Without this as the grammatical subject the
# pattern cannot fire, which is what keeps ordinary prose out.
_SUBJECT = (
    r"(?:the\s+)?"
    r"(?:spec(?:ification)?s?|acceptance\s+criteri(?:on|a)|criteri(?:on|a)|"
    r"AC\s*#?\s*\d*|requirements?)"
)

# "<subject> ... is wrong" — a verdict passed on the specification.
_SUBJECT_IS_WRONG = re.compile(
    rf"\b{_SUBJECT}\b[^.;\n]{{0,60}}?\b(?:is|was|are|were|has|had|contains?)\b"
    r"[^.;\n]{0,20}?\b(?:wrong|incorrect|mistaken|in\s+error|a\s+typo|typo|"
    r"a\s+mistake|a\s+bug|inconsistent|impossible)\b",
    re.IGNORECASE,
)

# "a typo in the spec" — the same verdict, subject trailing.
_WRONG_IN_SUBJECT = re.compile(
    rf"\b(?:typo|mistake|error|bug|inconsistency)\b[^.;\n]{{0,20}}?\bin\s+{_SUBJECT}\b",
    re.IGNORECASE,
)

# "corrected the spec" / "ignoring the AC" — acting on that verdict.
_OVERRODE_SUBJECT = re.compile(
    r"\b(?:correct(?:ed|ing)|fix(?:ed|ing)|overrode|overrid(?:den|ing|e)|"
    r"ignor(?:ed|ing)|disregard(?:ed|ing)|deviat(?:ed|ing)\s+from|"
    r"depart(?:ed|ing)\s+from)\b"
    rf"\s+{_SUBJECT}\b",
    re.IGNORECASE,
)

_PATTERNS = (
    ("subject_is_wrong", _SUBJECT_IS_WRONG),
    ("wrong_in_subject", _WRONG_IN_SUBJECT),
    ("overrode_subject", _OVERRODE_SUBJECT),
)

_LINE_COMMENT = re.compile(r"(?://|#)(.*)$", re.MULTILINE)
_BLOCK_COMMENT = re.compile(r"/\*(.*?)\*/", re.DOTALL)


@dataclass
class AuthorityResult:
    """``ok=False`` means the prose claims authority over the specification."""

    ok: bool = True
    checked: bool = True
    pattern: str | None = None
    quote: str | None = None
    segments: int = 0
    claims: list[str] = field(default_factory=list)

    def describe(self) -> str:
        return f"{self.pattern}: {self.quote}" if self.quote else "no claim"


def _python_prose(source: str) -> list[str] | None:
    """Docstrings + ``#`` comments from Python source. None if it does not parse.

    Docstrings come from the AST — the exact nodes ``_python_code_values``
    EXCLUDES, so the two checks partition the file between them with no overlap
    and no gap. Comments never reach the AST at all, so they are scanned from
    the text.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    out: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(
            node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
        ):
            continue
        doc = ast.get_docstring(node)
        if doc:
            out.append(doc)
    out.extend(m.group(1) for m in _LINE_COMMENT.finditer(source))
    return out


def _text_prose(source: str) -> list[str]:
    """Comments only, for languages with no AST here (TS/JS/Go/...)."""
    out = [m.group(1) for m in _BLOCK_COMMENT.finditer(source or "")]
    body = _BLOCK_COMMENT.sub(" ", source or "")
    out.extend(m.group(1) for m in _LINE_COMMENT.finditer(body))
    return out


def prose_segments(source: str, language: str | None = None) -> list[str]:
    """Every comment/docstring in *source*. Never a string literal under test.

    A test FOR a spec-validation feature may legitimately carry "the spec is
    wrong" as data; only what the author wrote ABOUT the test is a claim.
    """
    if (language or "python") == "python":
        parsed = _python_prose(source)
        if parsed is not None:
            return parsed
    return _text_prose(source)


def _first_claim(segment: str) -> tuple[str, str] | None:
    for name, pattern in _PATTERNS:
        m = pattern.search(segment)
        if m:
            return name, " ".join(m.group(0).split())[:200]
    return None


def enabled() -> bool:
    """On by default; ``TFACTORY_CRITERION_AUTHORITY_CHECK=0`` opts out.

    Mirrors ``TFACTORY_CRITERION_LITERAL_CHECK``. An escape hatch for an
    operator facing prose this matcher misreads — not a default, because off
    means a test that graded the spec against the code merges unremarked.
    """
    return os.environ.get("TFACTORY_CRITERION_AUTHORITY_CHECK", "1") != "0"


def check_criterion_authority(
    source: str, *, language: str | None = None
) -> AuthorityResult:
    """Does the generated test's prose claim the specification is wrong?

    Args:
        source: the generated test file's source.
        language: the subtask's language; ``None``/``"python"`` uses the AST path.

    Returns:
        An ``AuthorityResult``; ``ok=False`` means a comment or docstring passes
        judgement on the specification, and the test must not be merged on the
        strength of an oracle it rewrote for itself.
    """
    segments = prose_segments(source, language)
    claims = [c for c in (_first_claim(s) for s in segments) if c is not None]
    if not claims:
        return AuthorityResult(ok=True, segments=len(segments))
    pattern, quote = claims[0]
    return AuthorityResult(
        ok=False,
        pattern=pattern,
        quote=quote,
        segments=len(segments),
        claims=[q for _, q in claims],
    )
