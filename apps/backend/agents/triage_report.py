"""Triager report rendering — Task 8 (#9) commit 3 + Task 11 (#27) commit 4.

Pure-compute renderer that takes the output of commit 2's dedup +
rank primitives and produces two artefacts:

  - ``triage_report.json`` — structured, machine-readable, stable
    key ordering. Task 9's portal reads this for the lane status grid.

  - ``triage_report.md`` — human-readable. Task 8 commit 5's PR
    comment helper pastes this into the gh pr comment body.

Both renderers are deterministic: given the same TriageReport input,
they produce byte-identical output. That's enforced via a golden-file
snapshot test in tests/test_triage_report.py — anyone who changes the
format has to regenerate the golden file deliberately.

Task 11 (commit 4) extends the report with per-candidate ``intent``
fields (``"create"`` / ``"update"`` / ``"skip"``) surfaced from the
catalog lookup step in ``agents/triager.py``.  A new ``skipped`` bucket
lists operator-locked candidates that were not committed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Sequence

from agents.triage_dedup import DedupCollision, TriageCandidate

if TYPE_CHECKING:
    pass


# ─── Data shape ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TriageReport:
    """Structured report assembled from dedup + rank output.

    The Triager's commit-5 wiring will:
      1. Read verdicts.json from the Evaluator
      2. Wrap entries as TriageCandidates
      3. Filter out rejects → call dedup_candidates → rank_candidates
      4. Bucket the ranked survivors by verdict label
      5. Pass everything here

    Args (positional):
        mode: 'initial' or 'rerun'.
        generated_at: ISO-8601 timestamp (caller-supplied so tests
            can pin it for the golden snapshot).
        committed: Ranked TriageCandidates with verdict == 'accept'.
        flagged: Ranked TriageCandidates with verdict == 'flag'.
        rejected: TriageCandidates with verdict == 'reject' (NOT ranked
            among the survivors; just listed in input order for the
            report).
        skipped: TriageCandidates excluded because operator_locked
            (Task 11 / #27).
        collisions: DedupCollisions encountered while building the
            survivor set.
        dedup_input_count: How many candidates went IN to dedup. The
            difference vs len(committed)+len(flagged)+len(collisions)
            shows what happened.
        decisions: Mapping from test_id → CandidateDecision (Task 11).
            When absent, all candidates are treated as CREATE intent
            in the rendered output.
    """

    mode: str
    generated_at: str
    committed: tuple[TriageCandidate, ...] = field(default_factory=tuple)
    flagged: tuple[TriageCandidate, ...] = field(default_factory=tuple)
    rejected: tuple[TriageCandidate, ...] = field(default_factory=tuple)
    skipped: tuple[TriageCandidate, ...] = field(default_factory=tuple)
    collisions: tuple[DedupCollision, ...] = field(default_factory=tuple)
    dedup_input_count: int = 0
    decisions: dict = field(default_factory=dict)  # test_id → CandidateDecision

    @property
    def committed_count(self) -> int:
        return len(self.committed)

    @property
    def rejected_count(self) -> int:
        return len(self.rejected)

    @property
    def flagged_count(self) -> int:
        return len(self.flagged)

    @property
    def skipped_count(self) -> int:
        return len(self.skipped)


# ─── Build helper ───────────────────────────────────────────────────────


def build_report(
    *,
    mode: str,
    generated_at: str,
    committed: Sequence[TriageCandidate],
    flagged: Sequence[TriageCandidate],
    rejected: Sequence[TriageCandidate],
    collisions: Sequence[DedupCollision],
    dedup_input_count: int,
    skipped: Sequence[TriageCandidate] = (),
    decisions: dict | None = None,
) -> TriageReport:
    """Construct a TriageReport from the Triager's commit-5 working set.

    All sequences are converted to tuples so the resulting dataclass
    is fully immutable. Caller controls ordering — pass ranked
    sequences for ``committed`` + ``flagged`` if you want the report
    to reflect rank order.

    Args:
        skipped: Candidates excluded because ``operator_locked`` (Task 11).
        decisions: Mapping test_id → CandidateDecision (Task 11); surfaced
            per candidate in the JSON + Markdown renderers.
    """
    return TriageReport(
        mode=mode,
        generated_at=generated_at,
        committed=tuple(committed),
        flagged=tuple(flagged),
        rejected=tuple(rejected),
        skipped=tuple(skipped),
        collisions=tuple(collisions),
        dedup_input_count=dedup_input_count,
        decisions=dict(decisions) if decisions else {},
    )


# ─── JSON renderer ──────────────────────────────────────────────────────


def _candidate_to_json(c: TriageCandidate, decisions: dict | None = None) -> dict:
    """Minimal candidate projection for the JSON report. The full
    verdict dict is included so portal consumers don't need to
    cross-reference verdicts.json.

    Task 11: includes the ``intent`` field from the CandidateDecision
    when decisions is provided.
    """
    result: dict = {
        "test_id": c.test_id,
        "test_file": c.test_file,
        "verdict": c.verdict,
    }
    if decisions is not None:
        decision = decisions.get(c.test_id)
        if decision is not None:
            result["intent"] = decision.intent
            if decision.update_target_file is not None:
                result["update_target_file"] = decision.update_target_file
            if decision.skip_reason is not None:
                result["skip_reason"] = decision.skip_reason
            if decision.derived_test_file is not None:
                result["derived_test_file"] = decision.derived_test_file
        else:
            result["intent"] = "create"
    return result


def _collision_to_json(coll: DedupCollision) -> dict:
    return {
        "kind": coll.kind,
        "representative": coll.representative.test_id,
        "dropped": [d.test_id for d in coll.dropped],
    }


def render_json(report: TriageReport) -> str:
    """Render the report as deterministic JSON.

    Uses sort_keys=True + indent=2 so byte-identical output is
    reproducible. The keys at the top level follow a stable order
    via sort_keys; per-test entries use the natural dict order
    (which Python now preserves).

    Task 11: includes per-candidate ``intent`` field and a
    ``skipped`` bucket for operator-locked candidates.
    """
    decisions = report.decisions or None
    doc = {
        "triager_version": "task8-commit3",
        "mode": report.mode,
        "generated_at": report.generated_at,
        "summary": {
            "dedup_input_count": report.dedup_input_count,
            "committed_count": report.committed_count,
            "flagged_count": report.flagged_count,
            "rejected_count": report.rejected_count,
            "skipped_count": report.skipped_count,
            "dedup_collision_count": len(report.collisions),
        },
        "committed": [_candidate_to_json(c, decisions) for c in report.committed],
        "flagged": [_candidate_to_json(c, decisions) for c in report.flagged],
        "rejected": [_candidate_to_json(c) for c in report.rejected],
        "skipped": [_candidate_to_json(c, decisions) for c in report.skipped],
        "dedup_collisions": [_collision_to_json(c) for c in report.collisions],
    }
    return json.dumps(doc, indent=2, sort_keys=True) + "\n"


# ─── Markdown renderer ──────────────────────────────────────────────────


def _signal_summary_line(c: TriageCandidate) -> str:
    """Compact one-line summary of the verdict's signals for the MD report."""
    summary = c.verdict.get("signals_summary") or {}
    cov = summary.get("coverage_delta_pct", 0.0)
    try:
        cov_str = f"{float(cov):+.2f}%"
    except (TypeError, ValueError):
        cov_str = "?"
    return (
        f"coverage {cov_str}, "
        f"stability={summary.get('stability', '?')}, "
        f"mutation={summary.get('mutation', '?')}, "
        f"semantic={c.verdict.get('semantic_relevance', '?')}"
    )


def _intent_label(decisions: dict, test_id: str) -> str:
    """Return a human-readable intent label for the Markdown report.

    Formats:
      - ``UPDATE existing tests/foo.py``
      - ``CREATE new tests/bar.spec.ts``
      - ``SKIP (operator locked)``
      - ``CREATE new`` (when derived_test_file is unknown)
    """
    decision = decisions.get(test_id) if decisions else None
    if decision is None:
        return ""
    if decision.intent == "update":
        target = decision.update_target_file or "?"
        return f"UPDATE existing {target}"
    if decision.intent == "skip":
        reason = decision.skip_reason or "unknown reason"
        return f"SKIP ({reason})"
    # intent == "create"
    derived = decision.derived_test_file
    if derived:
        return f"CREATE new {derived}"
    return "CREATE new"


def _candidate_md_block(
    c: TriageCandidate,
    *,
    show_reasons: bool = True,
    decisions: dict | None = None,
) -> str:
    """Render one candidate as a markdown sub-section."""
    lines = [
        f"- **`{c.test_id}`** — `{c.test_file}`",
        f"  - signals: {_signal_summary_line(c)}",
    ]
    if decisions:
        label = _intent_label(decisions, c.test_id)
        if label:
            lines.append(f"  - intent: {label}")
    if show_reasons:
        reasons = c.verdict.get("reasons") or []
        if reasons:
            for r in reasons:
                lines.append(f"  - reason: {r}")
    return "\n".join(lines)


def _section(title: str, body: str | None) -> str:
    """One markdown section. ``body`` may be None or empty → emits
    a placeholder line so the section's structure stays consistent."""
    if not body:
        return f"## {title}\n\n_(none)_\n"
    return f"## {title}\n\n{body}\n"


def render_markdown(report: TriageReport) -> str:
    """Render the TriageReport as human-readable Markdown.

    Sections:
      1. # Triage Report  (title + mode + generated_at line)
      2. ## Summary  (counts table)
      3. ## Committed  (ranked accepted tests, with intent)
      4. ## Flagged  (ranked flagged tests, with intent)
      5. ## Skipped  (operator-locked, Task 11)
      6. ## Rejected  (rejects, input order, with reasons)
      7. ## Dedup Collisions  (representative ← dropped ids)
      8. Footer line with the triager_version

    Output ends with a single trailing newline.
    """
    decisions = report.decisions or None

    summary_table = (
        "| Bucket | Count |\n"
        "|---|---:|\n"
        f"| Dedup input | {report.dedup_input_count} |\n"
        f"| Committed (accept) | {report.committed_count} |\n"
        f"| Flagged | {report.flagged_count} |\n"
        f"| Skipped (operator locked) | {report.skipped_count} |\n"
        f"| Rejected | {report.rejected_count} |\n"
        f"| Dedup collisions | {len(report.collisions)} |\n"
    )

    committed_body = "\n".join(
        _candidate_md_block(c, decisions=decisions) for c in report.committed
    )
    flagged_body = "\n".join(
        _candidate_md_block(c, decisions=decisions) for c in report.flagged
    )
    skipped_body = "\n".join(
        _candidate_md_block(c, show_reasons=False, decisions=decisions)
        for c in report.skipped
    )
    rejected_body = "\n".join(_candidate_md_block(c) for c in report.rejected)
    collisions_body = "\n".join(
        f"- **{coll.kind}**: kept `{coll.representative.test_id}`, "
        f"dropped {', '.join(f'`{d.test_id}`' for d in coll.dropped)}"
        for coll in report.collisions
    )

    parts = [
        "# Triage Report\n",
        f"_Mode: {report.mode} · Generated at {report.generated_at}_\n",
        _section("Summary", summary_table),
        _section("Committed", committed_body),
        _section("Flagged", flagged_body),
        _section("Skipped", skipped_body),
        _section("Rejected", rejected_body),
        _section("Dedup Collisions", collisions_body),
        "---\n",
        "_Rendered by triager-task8-commit3._\n",
    ]
    return "\n".join(parts)
