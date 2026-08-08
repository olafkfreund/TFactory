"""Reject a generated test whose prose claims authority over the spec (#995).

The literal check (#894) compares VALUES, which is why it holds a zero
false-positive rate on honest specs. This one reads WORDS, and #896 refused to
fold it in for exactly that reason: a keyword blacklist is brittle enough to
deserve its own false-positive budget.

That budget is `test_zero_false_positives_on_the_repository_corpus` below: the
matcher runs over every test file in this repository — thousands of files of
real, human-written prose, including tests whose whole subject is correction
logic — and must not fire once.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent
_BACKEND = _ROOT / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from agents.criterion_authority import (  # noqa: E402
    check_criterion_authority,
    enabled,
    prose_segments,
)

# ─── the defect: prose that grades the specification ────────────────────

_REJECT = [
    pytest.param(
        '''
def test_order_total():
    """AC says 11.76, but that's a typo in the spec - the correct total is 11.75."""
    assert order.total == Decimal("11.75")
''',
        id="the #995 example verbatim",
    ),
    pytest.param(
        '''
def test_total():
    """The spec is wrong here; the implementation rounds down."""
    assert order.total == Decimal("11.76")
''',
        id="value agrees, prose still grades the spec",
    ),
    pytest.param(
        '''
def test_x():
    """Corrected the acceptance criterion to match what the code returns."""
    assert f() == 3
''',
        id="acted on the verdict",
    ),
    pytest.param(
        "def test_x():\n    # ignoring the AC here, it is inconsistent\n    assert f() == 3\n",
        id="a comment, not a docstring",
    ),
    pytest.param(
        '''
def test_x():
    """AC#2 is incorrect, so this asserts the real behaviour."""
    assert f() == 3
''',
        id="AC named by id",
    ),
]


@pytest.mark.parametrize("source", _REJECT)
def test_prose_claiming_the_spec_is_wrong_is_rejected(source):
    """A test that redefined its own oracle must not merge.

    Even when the asserted value happens to line up, the generator reasoned its
    way to grading the specification against the implementation — the one
    direction verification must never run.
    """
    result = check_criterion_authority(source)
    assert not result.ok
    assert result.quote


# ─── the false-positive budget ──────────────────────────────────────────

_INNOCENT = [
    pytest.param(
        "def test_corrected_invoice_totals():\n    assert invoice.total == 10\n",
        id="'corrected' in an identifier",
    ),
    pytest.param(
        '''
def test_normalise():
    """The SUT corrects a misspelled city name before saving."""
    assert normalise("Lodnon") == "London"
''',
        id="a docstring describing the SUT's own correction logic",
    ),
    pytest.param(
        '''
def test_typo_normalisation():
    """A typo in the user's input is normalised away."""
    assert normalise("teh") == "the"
''',
        id="'typo' where the feature IS typo handling",
    ),
    pytest.param(
        '''
def test_missing():
    """Returns None instead of raising."""
    assert lookup("nope") is None
''',
        id="'instead' in ordinary prose",
    ),
    pytest.param(
        '''
def test_spec_endpoint():
    """The spec endpoint returns the OpenAPI document."""
    assert get("/spec").status_code == 200
''',
        id="'spec' as an ordinary noun",
    ),
    pytest.param(
        '''
def test_validation_rejects_a_bad_spec():
    """A spec with a bad version is rejected."""
    assert validate({"version": "x"}) is False
''',
        id="the SUT validates specs",
    ),
    pytest.param(
        '''
def test_error_message():
    """Asserts the message the API returns."""
    assert err() == "the spec is wrong"
''',
        id="the phrase as a string literal under test, not prose",
    ),
    pytest.param(
        '''
def test_requirements_parsed():
    """Requirements are read from requirements.txt."""
    assert parse() == ["a"]
''',
        id="'requirements' as a filename subject",
    ),
]


@pytest.mark.parametrize("source", _INNOCENT)
def test_innocent_prose_is_not_a_claim(source):
    """No bare keyword may fire. A match needs the spec as the SUBJECT of a
    verdict, which is what #995 meant by proximity rather than presence."""
    assert check_criterion_authority(source).ok


def test_string_literals_are_never_scanned():
    """Comments and docstrings only — a test FOR a spec-validation feature may
    legitimately carry the phrase as data."""
    source = 'def test_x():\n    assert msg == "the specification is wrong"\n'
    assert all("wrong" not in s for s in prose_segments(source))
    assert check_criterion_authority(source).ok


# These two files deliberately embed the LIVE #888 specimen — the generated
# test whose comment reads "11.76 is a typo in the spec; the implementation
# follows AC2" — as a fixture. The matcher firing on them is the outcome under
# test, not a false positive, so they are scored separately below rather than
# waved through.
_SPECIMEN_FILES = {"test_criterion_literals.py", "test_gen_functional.py"}


def _test_files() -> list[Path]:
    return sorted(
        p for p in (_ROOT / "tests").rglob("test_*.py") if p.name != Path(__file__).name
    )


def test_zero_false_positives_on_the_repository_corpus():
    """THE false-positive budget #995 asked for, measured.

    #896 held this issue back because "a keyword blacklist is brittle enough to
    deserve its own false-positive budget", and `criterion_literals` earns its
    reject-and-replan by holding a zero false-positive rate on honest specs. A
    word matcher has no such property for free, so it is measured against the
    largest corpus of honest, human-written test prose available: every test
    file in this repository. A spurious rejection costs a replan and a human.
    """
    files = [p for p in _test_files() if p.name not in _SPECIMEN_FILES]
    assert len(files) > 300, f"corpus too small to mean anything: {len(files)}"

    hits = []
    for path in files:
        result = check_criterion_authority(
            path.read_text(encoding="utf-8", errors="ignore")
        )
        if not result.ok:
            hits.append(f"{path.relative_to(_ROOT)}: {result.describe()}")

    assert not hits, (
        f"{len(hits)} false positive(s) over {len(files)} honest test files:\n"
        + "\n".join(hits[:20])
    )


def test_it_fires_on_the_real_specimen():
    """The other half of the budget: it must catch the one real instance.

    A matcher with zero false positives and zero true positives is just a
    function that returns True. These two files carry the live #888 generated
    test verbatim, comment and all, so they are the only honest place in the
    repo the defect actually appears — and the matcher finds it in both.
    """
    found = {}
    for path in _test_files():
        if path.name not in _SPECIMEN_FILES:
            continue
        result = check_criterion_authority(
            path.read_text(encoding="utf-8", errors="ignore")
        )
        if not result.ok:
            found[path.name] = result.quote

    assert found.keys() == _SPECIMEN_FILES, f"missed the specimen: {found}"
    assert all("spec" in q for q in found.values()), found


def test_the_corpus_scan_actually_reads_prose():
    """Guards the budget above from passing because it read nothing.

    A matcher that scans zero segments trivially has zero false positives, which
    would make the measurement a decoration rather than a check.
    """
    total = sum(
        len(prose_segments(p.read_text(encoding="utf-8", errors="ignore")))
        for p in _test_files()[:200]
    )
    assert total > 1000, f"only {total} prose segments read; the scan is not working"


def test_opt_out(monkeypatch):
    """Mirrors TFACTORY_CRITERION_LITERAL_CHECK."""
    monkeypatch.delenv("TFACTORY_CRITERION_AUTHORITY_CHECK", raising=False)
    assert enabled()
    monkeypatch.setenv("TFACTORY_CRITERION_AUTHORITY_CHECK", "0")
    assert not enabled()


def test_non_python_uses_comments(monkeypatch):
    """TS/JS have no AST here; block and line comments still count."""
    src = "// the spec is wrong, asserting what the app does\ntest('x', () => {});\n"
    assert not check_criterion_authority(src, language="typescript").ok
    ok = "// clicks the Save button\ntest('x', () => {});\n"
    assert check_criterion_authority(ok, language="typescript").ok


def test_unparseable_python_still_scanned():
    """A syntax error must not turn the check off silently."""
    src = "def test_x(\n    # the spec is wrong here\n"
    assert not check_criterion_authority(src).ok
