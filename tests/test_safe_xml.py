"""XML entity-expansion DoS on sandbox-produced reports (Factory#722).

Three parsers in this repo read XML that the SYSTEM UNDER TEST wrote:

* ``agents.coverage_delta.parse_coverage_xml``      -- Cobertura coverage.xml
* ``agents.nix_env.parse_browser_junit``            -- Playwright junit.xml
* ``agents.lang_java.jacoco_coverage.parse_jacoco_xml`` -- JaCoCo jacoco.xml

For this fleet the system under test is LLM-generated or user-supplied code
executing inside the verification sandbox, so those bytes are hostile input.
``xml.etree`` is documented by CPython as vulnerable to entity-expansion
("billion laughs", quadratic blowup): a few hundred bytes expand to gigabytes
during parsing and wedge the verifier pod.

The bomb below is SMALL on purpose. A full billion-laughs payload would hang
the test suite for minutes and exhaust the runner before failing, which is a
test that punishes you for running it. Five nested levels amplify ~600x --
enough to prove the mechanism, fast enough to run in milliseconds.

OWASP: A05:2021 Security Misconfiguration.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from agents.coverage_delta import CoverageParseError, parse_coverage_xml
from agents.lang_java.jacoco_coverage import parse_jacoco_xml
from agents.nix_env import parse_browser_junit
from agents.safe_xml import EntityDeclarationError, fromstring, parse

# Five levels of ten-fold nesting: &lol5; expands to 100,000 "lol"s (300 KB)
# from a 500-byte document. Enough to demonstrate the amplification, small
# enough to parse instantly.
_BOMB = """<?xml version="1.0"?>
<!DOCTYPE lolz [
 <!ENTITY lol "lol">
 <!ENTITY lol1 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
 <!ENTITY lol2 "&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;">
 <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">
 <!ENTITY lol4 "&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;">
 <!ENTITY lol5 "&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;">
]>
{body}"""

# An external-entity declaration. Not the headline exposure -- expansion is --
# but it is declared in the same place and rejected by the same handler.
_EXTERNAL = """<?xml version="1.0"?>
<!DOCTYPE r [ <!ENTITY xxe SYSTEM "file:///etc/passwd"> ]>
{body}"""


# Each bomb body is a VALID report of its format with the entity reference in a
# field the parser actually reads. That matters: a bomb whose payload lands in an
# element the parser ignores is discarded either way, so a test built on one
# passes with the fix reverted and proves nothing.
_PAYLOAD = "&lol5;"
_COVERAGE_BODY = "<coverage line-rate='0.5'><payload>{p}</payload></coverage>"
_JUNIT_BODY = (
    "<testsuites><testsuite name='{p}' tests='1' failures='0' errors='0'/></testsuites>"
)
_JACOCO_BODY = (
    "<report><package name='{p}'><sourcefile name='App.java'>"
    "<line nr='1' ci='3'/></sourcefile></package></report>"
)


def _bomb(body: str) -> str:
    return _BOMB.replace("{body}", body.replace("{p}", _PAYLOAD))


def _external(body: str) -> str:
    return _EXTERNAL.replace("{body}", body.replace("{p}", "&xxe;"))


def test_the_bomb_really_is_a_bomb() -> None:
    """The fixture expands under a stock parser -- otherwise it proves nothing.

    A test that feeds harmless XML to a hardened parser and sees no explosion is
    a test that would pass with the fix reverted. This asserts the payload has
    teeth before any of the others rely on it.
    """
    # The UNHARDENED parser is the instrument here: proving the fixture explodes
    # requires something that lets it explode, and the payload is a literal in
    # this file. Hence the suppression on the next line.
    expanded = ET.fromstring(_bomb(_COVERAGE_BODY))  # noqa: S314
    text = expanded.findtext("payload") or ""
    assert len(text) == 3 * 100_000, len(text)
    # The amplification is the attack: 494 bytes becomes 300 KB here, and the
    # same document with four more nesting levels becomes 3 GB.
    assert len(text) > len(_bomb(_COVERAGE_BODY)) * 500


# ─── the helper itself ───────────────────────────────────────────────────


def test_helper_rejects_entity_declarations() -> None:
    with pytest.raises(EntityDeclarationError):
        fromstring(_bomb(_COVERAGE_BODY))


def test_helper_rejects_external_entity_declarations() -> None:
    with pytest.raises(EntityDeclarationError):
        fromstring(_external(_COVERAGE_BODY))


def test_helper_reports_malformed_input_as_parse_error() -> None:
    """Broken XML stays a ParseError, so existing except-clauses still catch it."""
    with pytest.raises(ET.ParseError):
        fromstring("<a><unclosed>")


@pytest.mark.parametrize(
    "doc",
    [
        "<coverage line-rate='0.5'><packages/></coverage>",
        "<t:suite xmlns:t='urn:x'><t:case n='1'>text</t:case>tail</t:suite>",
        "<a><b/>between<c x='1'/></a>",
    ],
)
def test_helper_matches_elementtree_on_ordinary_documents(doc: str) -> None:
    """Hardening must not change how a well-formed report parses.

    Compared structurally against `xml.etree` itself: tags, attributes, text and
    tails, including the `{uri}local` spelling for namespaced names.
    """

    def shape(el: ET.Element, depth: int = 0) -> list[object]:
        out: list[object] = [
            (depth, el.tag, sorted(el.attrib.items()), el.text, el.tail)
        ]
        for child in el:
            out += shape(child, depth + 1)
        return out

    # `xml.etree` is the reference implementation being matched against here, on
    # three constant well-formed documents declared above. Hence the suppression.
    assert shape(fromstring(doc)) == shape(ET.fromstring(doc))  # noqa: S314


def test_helper_parses_from_a_path(tmp_path: Path) -> None:
    target = tmp_path / "coverage.xml"
    target.write_text("<coverage line-rate='0.5'/>")
    assert parse(target).getroot().get("line-rate") == "0.5"


# ─── the three real call sites ───────────────────────────────────────────


def test_coverage_delta_rejects_a_bomb(tmp_path: Path) -> None:
    """A hostile coverage.xml is a parse failure, not an OOM."""
    target = tmp_path / "coverage.xml"
    target.write_text(_bomb(_COVERAGE_BODY))
    with pytest.raises(CoverageParseError):
        parse_coverage_xml(target)


def test_coverage_delta_still_parses_a_real_report(tmp_path: Path) -> None:
    target = tmp_path / "coverage.xml"
    target.write_text(
        "<coverage line-rate='0.5'><packages><package name='p'><classes>"
        "<class filename='a.py'><lines>"
        "<line number='1' hits='1'/><line number='2' hits='0'/>"
        "</lines></class></classes></package></packages></coverage>"
    )
    snapshot = parse_coverage_xml(target)
    assert snapshot.line_rate == 0.5
    assert snapshot.covered_lines["a.py"] == frozenset({1})


def test_browser_junit_rejects_a_bomb(tmp_path: Path) -> None:
    """A hostile junit.xml degrades to "no evidence", the same as a broken one.

    The entity sits in the ``name`` attribute this parser keys its result on, so
    without the fix the call returns a dict whose single key is 300 KB of "lol".
    """
    target = tmp_path / "junit.xml"
    target.write_text(_bomb(_JUNIT_BODY))
    assert parse_browser_junit(target) == {}


def test_browser_junit_still_parses_a_real_report(tmp_path: Path) -> None:
    target = tmp_path / "junit.xml"
    target.write_text(
        "<testsuites><testsuite name='login.spec.ts' tests='2' failures='0' "
        "errors='0'/><testsuite name='cart.spec.ts' tests='1' failures='1' "
        "errors='0'/></testsuites>"
    )
    assert parse_browser_junit(target) == {
        "login.spec.ts": True,
        "cart.spec.ts": False,
    }


def test_jacoco_rejects_a_bomb() -> None:
    """The site that claimed "trusted local report" -- it is not trusted.

    The entity sits in the package name, which becomes part of every source path
    this parser returns, so without the fix one line of coverage is reported
    under a 300 KB path.
    """
    assert parse_jacoco_xml(_bomb(_JACOCO_BODY)).total_lines == 0


def test_jacoco_still_parses_a_real_report() -> None:
    coverage = parse_jacoco_xml(
        "<report><package name='com/acme'><sourcefile name='App.java'>"
        "<line nr='1' ci='3' mi='0'/><line nr='2' ci='0' mi='4'/>"
        "</sourcefile></package></report>"
    )
    assert coverage.total_lines == 2
    assert coverage.covered_lines == frozenset({("com/acme/App.java", 1)})
