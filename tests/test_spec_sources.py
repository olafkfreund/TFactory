"""Tests for generic acceptance-criteria ingestion (#40)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from spec_sources import (  # noqa: E402
    SpecFormat,
    SpecSourceError,
    detect_format,
    ingest,
    ingest_file,
    parse_ears,
    parse_gherkin,
    parse_markdown,
    write_spec_markdown,
)

# ── format detection ───────────────────────────────────────────────────

def test_detect_gherkin_by_filename():
    assert detect_format("anything", filename="login.feature") is SpecFormat.GHERKIN


def test_detect_gherkin_by_content():
    text = "Feature: Login\n  Scenario: ok\n    Given x\n"
    assert detect_format(text) is SpecFormat.GHERKIN


def test_detect_ears_by_shall_majority():
    text = (
        "The system shall reject expired tokens.\n"
        "When a user logs in, the system shall create a session.\n"
    )
    assert detect_format(text) is SpecFormat.EARS


def test_detect_markdown_default():
    text = "# Feature\n\n## Acceptance Criteria\n- user can log in\n"
    assert detect_format(text) is SpecFormat.MARKDOWN


# ── markdown ───────────────────────────────────────────────────────────

def test_parse_markdown_acceptance_section():
    text = (
        "# Login feature\n\n"
        "## Acceptance Criteria\n"
        "- User can log in with valid credentials\n"
        "- Login rejects an expired token\n\n"
        "## Notes\n- not a criterion\n"
    )
    spec = parse_markdown(text)
    assert spec.title == "Login feature"
    assert [c.id for c in spec.criteria] == ["AC#1", "AC#2"]
    assert spec.criteria[0].text == "User can log in with valid credentials"
    # the Notes bullet is outside the acceptance section
    assert all("not a criterion" not in c.text for c in spec.criteria)


def test_parse_markdown_numbered_under_requirements():
    text = "# Spec\n## Requirements\n1. first\n2) second\n"
    spec = parse_markdown(text)
    assert [c.text for c in spec.criteria] == ["first", "second"]


def test_parse_markdown_inline_ac_fallback():
    text = "Some intro.\nAC#1: must do X\nAC2. must do Y\n"
    spec = parse_markdown(text)
    assert [c.text for c in spec.criteria] == ["must do X", "must do Y"]


def test_parse_markdown_no_criteria_raises():
    with pytest.raises(SpecSourceError):
        parse_markdown("# Title\n\nJust prose, no criteria.\n")


# ── the spec body must survive ingestion (#855) ────────────────────────

SLUG_SPEC = """\
## Endpoints

- `GET /healthz`
- `POST /slug`

## Slug rules

- lowercase ASCII only; transliterate accented characters
- truncate to 200 characters BEFORE slugging, never after
- never cut mid-word when a hyphen boundary exists in the last 20 characters

## Acceptance Criteria

- AC#1: GET /healthz returns HTTP 200 and exactly {"status":"ok"}
- AC#2: POST /slug {"text":"Hello, World!"} returns {"slug":"hello-world"}

## Out of scope

- authentication
"""


def test_spec_body_survives_ingest_verbatim():
    """#855: behaviour stated in the body but never restated as an AC was lost.

    The planner reads the rendered spec, so the rendered spec has to still
    contain what the requester actually wrote.
    """
    md = ingest(SLUG_SPEC).to_markdown()
    for stated in (
        "transliterate accented characters",
        "BEFORE slugging",
        "hyphen boundary",
        "Out of scope",
    ):
        assert stated in md, f"spec body lost at ingest: {stated!r}"


def test_ac_prefix_is_not_duplicated():
    """#855 (minor): rendered ACs read '**AC#1:** AC#1: ...'."""
    md = ingest(SLUG_SPEC).to_markdown()
    assert "**AC#1:** AC#1:" not in md
    assert '**AC#1:** GET /healthz returns HTTP 200 and exactly {"status":"ok"}' in md


def test_title_falls_back_to_first_heading_when_no_h1():
    """#855 (minor): a spec whose top heading is '##' became 'Untitled spec'."""
    assert ingest(SLUG_SPEC).title == "Endpoints"


def test_rendered_spec_reparses_to_the_same_criteria():
    """Carrying the body must not make the canonical spec re-parse differently."""
    spec = ingest(SLUG_SPEC)
    assert [c.text for c in parse_markdown(spec.to_markdown()).criteria] == [
        c.text for c in spec.criteria
    ]


def test_unrepresented_sections_names_what_was_not_turned_into_acs():
    from spec_sources import unrepresented_sections  # noqa: PLC0415

    sections = unrepresented_sections(ingest(SLUG_SPEC).to_markdown())
    assert sections == ["Endpoints", "Slug rules", "Out of scope"]


# ── gherkin ────────────────────────────────────────────────────────────

GHERKIN = """\
Feature: User login
  As a user I want to log in.

  Scenario: valid credentials
    Given a registered user
    When they submit valid credentials
    Then a session is created

  Scenario: expired token
    Given an expired token
    Then login is rejected
"""


def test_parse_gherkin_one_ac_per_scenario():
    spec = parse_gherkin(GHERKIN)
    assert spec.source_format is SpecFormat.GHERKIN
    assert spec.title == "User login"
    assert len(spec.criteria) == 2
    assert spec.criteria[0].text.startswith("valid credentials — given a registered user")
    assert "session is created" in spec.criteria[0].text
    assert spec.criteria[1].text.startswith("expired token —")


def test_parse_gherkin_no_scenarios_raises():
    with pytest.raises(SpecSourceError):
        parse_gherkin("Feature: empty\n")


# ── EARS ───────────────────────────────────────────────────────────────

EARS = """\
# Auth requirements
The system shall reject expired tokens.
When a user submits valid credentials, the system shall create a session.
While offline, the app shall queue requests.
This line has no keyword and is ignored.
"""


def test_parse_ears_collects_shall_lines():
    spec = parse_ears(EARS)
    assert spec.source_format is SpecFormat.EARS
    assert len(spec.criteria) == 3
    assert "reject expired tokens" in spec.criteria[0].text
    assert all("ignored" not in c.text for c in spec.criteria)


def test_parse_ears_strips_bullets():
    spec = parse_ears("- The system shall log audits.\n")
    assert spec.criteria[0].text == "The system shall log audits."


def test_parse_ears_no_shall_raises():
    with pytest.raises(SpecSourceError):
        parse_ears("# reqs\nnothing here.\n")


# ── ingest + render + write ────────────────────────────────────────────

def test_ingest_autodetects_and_normalises():
    spec = ingest(GHERKIN, filename="x.feature")
    assert spec.source_format is SpecFormat.GHERKIN
    md = spec.to_markdown()
    assert "## Acceptance Criteria" in md
    assert "**AC#1:**" in md
    assert "Ingested from a gherkin source" in md


def test_ingest_empty_raises():
    with pytest.raises(SpecSourceError):
        ingest("   \n  ")


def test_ingest_force_format_overrides_detection():
    # content looks like markdown but we force EARS (has a shall line)
    spec = ingest("The system shall do X.\n", fmt=SpecFormat.EARS)
    assert spec.source_format is SpecFormat.EARS


def test_to_markdown_roundtrips_through_markdown_parser():
    spec = parse_ears(EARS)
    md = spec.to_markdown()
    # the rendered canonical markdown is itself parseable
    reparsed = parse_markdown(md)
    assert len(reparsed.criteria) == len(spec.criteria)


def test_write_spec_markdown_drops_pipeline_file(tmp_path):
    spec = ingest(GHERKIN, filename="x.feature")
    ctx = tmp_path / "context"
    dst = write_spec_markdown(spec, ctx)
    assert dst == ctx / "aifactory_spec.md"
    assert dst.exists()
    assert "## Acceptance Criteria" in dst.read_text()


def test_ingest_file_uses_extension(tmp_path):
    f = tmp_path / "login.feature"
    f.write_text(GHERKIN)
    spec = ingest_file(f)
    assert spec.source_format is SpecFormat.GHERKIN
