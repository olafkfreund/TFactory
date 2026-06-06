"""Tests for the test-acceptance badge + component linkage (#241, epic #232)."""

from __future__ import annotations

import json

import pytest
from agents.badge import acceptance_badge, render_badge_svg
from agents.triage_report import build_report, render_json, render_markdown

# ─── pure SVG generator ──────────────────────────────────────────────────


def test_render_badge_is_svg():
    svg = render_badge_svg("tests", "87%", "#4c1")
    assert svg.startswith("<svg")
    assert svg.endswith("</svg>")
    assert "tests" in svg and "87%" in svg
    assert "#4c1" in svg


def test_render_badge_escapes():
    svg = render_badge_svg("a&b", "<x>", "#000")
    assert "&amp;" in svg and "&lt;x&gt;" in svg


@pytest.mark.parametrize(
    "readiness,color",
    [("high", "#4c1"), ("medium", "#dfb317"), ("low", "#e05d44")],
)
def test_acceptance_badge_colors(readiness, color):
    facts = {"verdicts_count": 4, "accept_rate": 0.75, "commit_readiness": readiness}
    svg = acceptance_badge(facts)
    assert color in svg
    assert "75%" in svg


def test_acceptance_badge_no_data():
    svg = acceptance_badge({"verdicts_count": 0})
    assert "no data" in svg
    assert "#9f9f9f" in svg


def test_acceptance_badge_rounds_rate():
    svg = acceptance_badge({"verdicts_count": 3, "accept_rate": 0.6667, "commit_readiness": "medium"})
    assert "67%" in svg


# ─── component linkage in the triage report (#241) ───────────────────────


def test_triage_report_renders_component_ref():
    report = build_report(
        mode="initial",
        generated_at="2026-06-06T00:00:00+00:00",
        committed=(),
        flagged=(),
        rejected=(),
        collisions=(),
        dedup_input_count=0,
        component_ref="component:default/aifactory",
    )
    md = render_markdown(report)
    assert "component:default/aifactory" in md
    doc = json.loads(render_json(report))
    assert doc["component_ref"] == "component:default/aifactory"


def test_triage_report_omits_component_line_when_absent():
    report = build_report(
        mode="initial",
        generated_at="2026-06-06T00:00:00+00:00",
        committed=(),
        flagged=(),
        rejected=(),
        collisions=(),
        dedup_input_count=0,
    )
    md = render_markdown(report)
    assert "_Covers:" not in md
    assert json.loads(render_json(report))["component_ref"] is None
