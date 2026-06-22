"""Regression report rendering — RFC-0018 #483 (Phase 1).

Deterministic markdown + JSON rendering of a :class:`RegressionDiff` against
its :class:`RegressionRun`. Deterministic so it is golden-file testable and
safe to post to a PR or surface in the portal (RFC-0018 #489).
"""

from __future__ import annotations

from typing import Any

from .diff import RegressionClass, RegressionDiff
from .models import RegressionRun

# Order sections by actionability — regressions first.
_SECTION_ORDER = (
    (RegressionClass.REGRESSION, "Regressions (was passing, now failing)"),
    (RegressionClass.STILL_FAILING, "Still failing"),
    (RegressionClass.FLAKY, "Flaky (history-classified)"),
    (RegressionClass.QUARANTINED, "Quarantined (excluded from gate)"),
    (RegressionClass.FIXED, "Fixed (was failing, now passing)"),
    (RegressionClass.NEW, "New tests"),
    (RegressionClass.DROPPED, "Dropped (gone from corpus)"),
)


def render_markdown(diff: RegressionDiff, run: RegressionRun) -> str:
    """Render a human-readable regression report."""
    totals = run.totals
    lines: list[str] = []
    verdict = "REGRESSIONS DETECTED" if diff.has_regressions else "no regressions"
    lines.append(f"# Regression report — {verdict}")
    lines.append("")
    lines.append(f"- Project: `{run.project_id}`")
    lines.append(f"- Run: `{run.run_id}` @ `{run.ran_at}`")
    lines.append(f"- Commit: `{run.commit or 'n/a'}`")
    lines.append(f"- Baseline: `{diff.baseline_run_id or 'none (first run)'}`")
    lines.append(
        f"- Totals: {totals['total']} tests — "
        f"{totals['passed']} passed, {totals['failed']} failed, "
        f"{totals['skipped']} skipped, {totals['quarantined']} quarantined"
    )
    if run.coverage_pct is not None:
        lines.append(f"- Coverage: {run.coverage_pct:.1f}%")
    lines.append("")

    counts = diff.counts
    for cls, heading in _SECTION_ORDER:
        ids = diff.of_class(cls)
        if not ids:
            continue
        lines.append(f"## {heading} ({counts[cls.value]})")
        for tid in ids:  # already sorted in the diff
            lines.append(f"- `{tid}`")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def render_json(diff: RegressionDiff, run: RegressionRun) -> dict[str, Any]:
    """Structured report: the run plus its classified diff."""
    return {"run": run.to_dict(), "diff": diff.to_dict()}
