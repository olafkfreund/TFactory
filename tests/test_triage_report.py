"""Tests for the Triager report renderer — Task 8 (#9) commit 3.

Pure-compute renderer. The Triager's commit-5 wiring will pass the
dedup + rank output here to produce triage_report.{md,json}.

Covered:
  - TriageReport convenience properties (counts)
  - build_report tuples sequences (immutability)
  - JSON: deterministic key ordering, schema shape, empty buckets
  - Markdown: golden-file snapshot (4-test scenario covering
    accept/flag/reject + dedup collision)
  - Markdown content sanity: section presence, summary table,
    signal-line formatting, missing fields degrade gracefully
  - Empty-report rendering — clean placeholders, no crash
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents.triage_dedup import DedupCollision, TriageCandidate
from agents.triage_report import (
    TriageReport,
    build_report,
    render_json,
    render_markdown,
)


GOLDEN_DIR = Path(__file__).parent / "fixtures" / "triage_report"


# ── Helpers ────────────────────────────────────────────────────────────


def _cand(
    *, test_id: str, verdict: str = "accept",
    cov: float = 0.0, mut: str = "killed", stab: str = "stable",
    sem: str = "high", reasons: list | None = None,
    file_suffix: str | None = None,
) -> TriageCandidate:
    return TriageCandidate(
        test_id=test_id,
        test_file=f"tests/test_{file_suffix or test_id}.py",
        verdict={
            "test_id": test_id,
            "verdict": verdict,
            "reasons": reasons or [],
            "signals_summary": {
                "coverage_delta_pct": cov,
                "stability": stab,
                "mutation": mut,
            },
            "semantic_relevance": sem,
        },
        source="def test_x(): pass\n",
    )


# ── build_report ────────────────────────────────────────────────────────


def test_build_report_tuples_sequences() -> None:
    """build_report converts lists → tuples for immutability."""
    a = _cand(test_id="a")
    report = build_report(
        mode="initial",
        generated_at="2026-05-28T15:30:00+00:00",
        committed=[a],
        flagged=[],
        rejected=[],
        collisions=[],
        dedup_input_count=1,
    )
    assert isinstance(report.committed, tuple)
    assert isinstance(report.flagged, tuple)
    assert isinstance(report.rejected, tuple)
    assert isinstance(report.collisions, tuple)


def test_count_properties() -> None:
    a = _cand(test_id="a", verdict="accept")
    b = _cand(test_id="b", verdict="flag")
    c = _cand(test_id="c", verdict="reject")
    report = build_report(
        mode="initial",
        generated_at="2026-05-28T00:00:00+00:00",
        committed=[a],
        flagged=[b],
        rejected=[c],
        collisions=[],
        dedup_input_count=3,
    )
    assert report.committed_count == 1
    assert report.flagged_count == 1
    assert report.rejected_count == 1


# ── render_json ────────────────────────────────────────────────────────


def test_render_json_is_deterministic() -> None:
    """Same input → byte-identical output (sort_keys=True)."""
    a = _cand(test_id="a", cov=5.0, reasons=["r1"])
    report = build_report(
        mode="initial",
        generated_at="2026-05-28T00:00:00+00:00",
        committed=[a],
        flagged=[],
        rejected=[],
        collisions=[],
        dedup_input_count=1,
    )
    out1 = render_json(report)
    out2 = render_json(report)
    assert out1 == out2


def test_render_json_top_level_shape() -> None:
    a = _cand(test_id="a", cov=5.0)
    b = _cand(test_id="b", verdict="flag")
    c = _cand(test_id="c", verdict="reject")
    coll = DedupCollision(
        kind="byte_identical",
        representative=a,
        dropped=(_cand(test_id="a-dup"),),
    )
    report = build_report(
        mode="initial",
        generated_at="2026-05-28T00:00:00+00:00",
        committed=[a],
        flagged=[b],
        rejected=[c],
        collisions=[coll],
        dedup_input_count=4,
    )
    doc = json.loads(render_json(report))
    assert doc["triager_version"] == "task8-commit3"
    assert doc["mode"] == "initial"
    assert doc["generated_at"] == "2026-05-28T00:00:00+00:00"
    # Task 11: summary now includes skipped_count
    summary = doc["summary"]
    assert summary["dedup_input_count"] == 4
    assert summary["committed_count"] == 1
    assert summary["flagged_count"] == 1
    assert summary["rejected_count"] == 1
    assert summary["dedup_collision_count"] == 1
    assert "skipped_count" in summary
    assert len(doc["committed"]) == 1
    assert doc["committed"][0]["test_id"] == "a"
    assert doc["committed"][0]["test_file"] == "tests/test_a.py"
    assert doc["committed"][0]["verdict"]["verdict"] == "accept"
    assert len(doc["dedup_collisions"]) == 1
    assert doc["dedup_collisions"][0]["kind"] == "byte_identical"
    assert doc["dedup_collisions"][0]["representative"] == "a"
    assert doc["dedup_collisions"][0]["dropped"] == ["a-dup"]
    # Task 11: skipped bucket present (may be empty)
    assert "skipped" in doc


def test_render_json_empty_buckets() -> None:
    """Empty buckets render as empty arrays, not omitted keys."""
    report = build_report(
        mode="initial",
        generated_at="2026-05-28T00:00:00+00:00",
        committed=[],
        flagged=[],
        rejected=[],
        collisions=[],
        dedup_input_count=0,
    )
    doc = json.loads(render_json(report))
    assert doc["committed"] == []
    assert doc["flagged"] == []
    assert doc["rejected"] == []
    assert doc["dedup_collisions"] == []
    assert doc["summary"]["dedup_input_count"] == 0


def test_render_json_trailing_newline() -> None:
    """Output ends with a single \\n so it's git-friendly."""
    report = build_report(
        mode="initial",
        generated_at="2026-05-28T00:00:00+00:00",
        committed=[], flagged=[], rejected=[], collisions=[],
        dedup_input_count=0,
    )
    out = render_json(report)
    assert out.endswith("\n")
    assert not out.endswith("\n\n")


# ── render_markdown: golden-file snapshot ──────────────────────────────


def _build_golden_report() -> TriageReport:
    """Construct the exact 4-test scenario captured in the golden file."""
    accept = _cand(
        test_id="ac1-login-expiry",
        verdict="accept", cov=7.5, mut="killed", stab="stable", sem="high",
        reasons=["coverage +7.5%; mutation killed; semantic relevance high"],
    )
    flag = _cand(
        test_id="ac2-store-mut",
        verdict="flag", cov=1.2, mut="no_mutation", stab="stable", sem="medium",
        reasons=[
            "mutation probe found nothing to mutate",
            "shallow assertion",
        ],
    )
    reject = _cand(
        test_id="ac3-naive-true",
        verdict="reject", cov=0.0, mut="survived", stab="stable", sem="low",
        reasons=["mutation survived — assertion is tautological"],
    )
    duplicate_drop = _cand(
        test_id="ac1-login-expiry-dup",
        verdict="accept", cov=7.5, mut="killed", stab="stable", sem="high",
        reasons=["duplicate of ac1-login-expiry"],
    )
    collision = DedupCollision(
        kind="whitespace_normalised",
        representative=accept,
        dropped=(duplicate_drop,),
    )
    return build_report(
        mode="initial",
        generated_at="2026-05-28T15:30:00+00:00",
        committed=[accept],
        flagged=[flag],
        rejected=[reject],
        collisions=[collision],
        dedup_input_count=4,
    )


def test_markdown_matches_golden_file() -> None:
    """Snapshot test: change the renderer → regenerate the golden file."""
    actual = render_markdown(_build_golden_report())
    expected_path = GOLDEN_DIR / "expected.md"
    expected = expected_path.read_text()
    if actual != expected:
        # Provide a diff hint via the assertion message
        import difflib
        diff = "".join(difflib.unified_diff(
            expected.splitlines(keepends=True),
            actual.splitlines(keepends=True),
            fromfile=str(expected_path),
            tofile="render_markdown(report)",
        ))
        pytest.fail(
            "render_markdown drifted from golden file. "
            "Regenerate the golden via:\n"
            "  python -c \"from agents.triage_report import render_markdown; ...\"\n"
            f"Diff:\n{diff}"
        )


# ── render_markdown: content sanity ────────────────────────────────────


def test_markdown_has_all_sections() -> None:
    report = _build_golden_report()
    md = render_markdown(report)
    for section in (
        "# Triage Report",
        "## Summary",
        "## Committed",
        "## Flagged",
        "## Skipped",
        "## Rejected",
        "## Dedup Collisions",
    ):
        assert section in md


def test_markdown_summary_table_has_counts() -> None:
    report = _build_golden_report()
    md = render_markdown(report)
    # Counts visible in the summary table
    assert "| Dedup input | 4 |" in md
    assert "| Committed (accept) | 1 |" in md
    assert "| Flagged | 1 |" in md
    assert "| Rejected | 1 |" in md
    assert "| Dedup collisions | 1 |" in md


def test_markdown_signal_line_formatting() -> None:
    a = _cand(test_id="x", cov=5.25, mut="killed", stab="stable", sem="high")
    report = build_report(
        mode="initial",
        generated_at="2026-05-28T00:00:00+00:00",
        committed=[a],
        flagged=[], rejected=[], collisions=[],
        dedup_input_count=1,
    )
    md = render_markdown(report)
    # coverage formatted with sign + 2 decimals + percent
    assert "coverage +5.25%" in md
    assert "stability=stable" in md
    assert "mutation=killed" in md
    assert "semantic=high" in md


def test_markdown_empty_section_placeholder() -> None:
    """Empty bucket renders the _(none)_ placeholder."""
    report = build_report(
        mode="initial",
        generated_at="2026-05-28T00:00:00+00:00",
        committed=[], flagged=[], rejected=[], collisions=[],
        dedup_input_count=0,
    )
    md = render_markdown(report)
    # Task 11 added Skipped section → five sections now emit _(none)_
    # when all are empty: Committed, Flagged, Skipped, Rejected,
    # Dedup Collisions
    assert md.count("_(none)_") == 5


def test_markdown_missing_signal_degrades_gracefully() -> None:
    """A verdict dict missing signals_summary doesn't crash."""
    bad = TriageCandidate(
        test_id="bad",
        test_file="tests/test_bad.py",
        verdict={"test_id": "bad", "verdict": "accept"},  # no signals_summary
        source="def t(): pass\n",
    )
    report = build_report(
        mode="initial",
        generated_at="2026-05-28T00:00:00+00:00",
        committed=[bad],
        flagged=[], rejected=[], collisions=[],
        dedup_input_count=1,
    )
    md = render_markdown(report)
    # Default coverage 0.0%, stability '?', mutation '?'
    assert "coverage +0.00%" in md
    assert "stability=?" in md
    assert "mutation=?" in md
    assert "semantic=?" in md


def test_markdown_mode_in_header() -> None:
    """The mode is surfaced in the report header line."""
    report = build_report(
        mode="rerun",
        generated_at="2026-05-28T00:00:00+00:00",
        committed=[], flagged=[], rejected=[], collisions=[],
        dedup_input_count=0,
    )
    md = render_markdown(report)
    assert "Mode: rerun" in md


def test_markdown_trailing_newline() -> None:
    report = build_report(
        mode="initial",
        generated_at="2026-05-28T00:00:00+00:00",
        committed=[], flagged=[], rejected=[], collisions=[],
        dedup_input_count=0,
    )
    md = render_markdown(report)
    assert md.endswith("\n")
