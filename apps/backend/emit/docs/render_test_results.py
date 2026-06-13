"""Render a TFactory triage report into a durable test-result :class:`DocBundle`.

This is TFactory's *only* new code on top of the vendored PFactory docs-emit core
(#341). It turns ``findings/triage_report.json`` into a Markdown page + a
registry entry keyed by the **plan's ``correlation_key``**, with
``generated_by="tfactory"`` — so a run's results resolve by the same key as the
plan they verify, closing the PARR doc trail (plan → code → verify).

Pure: no network, no clock, no filesystem — the ``updated_at`` stamp is injected
by the target. Byte-identical output for identical input (idempotency).
"""

from __future__ import annotations

from typing import Any

from .bundle import DocBundle

# Marks every emission so cross-factory enrichment can attribute the source and
# skip its own output. Mirrors PFactory's GENERATED_BY="pfactory".
GENERATED_BY = "tfactory"


def _h(level: int, text: str) -> str:
    return f"{'#' * level} {text}\n"


def _signal(verdict: dict[str, Any], key: str, default: str = "—") -> str:
    summary = verdict.get("signals_summary") or {}
    val = summary.get(key)
    return str(val) if val not in (None, "") else default


def _coverage(verdict: dict[str, Any]) -> str:
    summary = verdict.get("signals_summary") or {}
    cov = summary.get("coverage_delta_pct")
    if cov is None:
        return "—"
    try:
        return f"+{float(cov):.1f}%" if float(cov) >= 0 else f"{float(cov):.1f}%"
    except (TypeError, ValueError):
        return "—"


def _verdict_rows(candidates: list[dict[str, Any]]) -> list[str]:
    rows: list[str] = []
    for c in candidates:
        verdict = c.get("verdict") or {}
        rows.append(
            f"| `{c.get('test_id', '?')}` "
            f"| {c.get('verdict', {}).get('verdict') if isinstance(c.get('verdict'), dict) else c.get('verdict', '?')} "
            f"| {_coverage(verdict)} "
            f"| {_signal(verdict, 'mutation')} "
            f"| {_signal(verdict, 'stability')} "
            f"| {c.get('ci_parity') or _signal(verdict, 'ci_parity')} |\n"
        )
    return rows


def _render_markdown(
    triage: dict[str, Any],
    *,
    correlation_key: str,
    spec_id: str,
    component_ref: str | None,
) -> str:
    summary = triage.get("summary") or {}
    committed = triage.get("committed") or []
    flagged = triage.get("flagged") or []
    rejected = triage.get("rejected") or []
    skipped = triage.get("skipped") or []

    parts: list[str] = []
    # Front matter (TechDocs/Backstage-friendly; also human-readable).
    parts.append("---\n")
    parts.append(f"title: Test results — {spec_id}\n")
    parts.append(f"correlation_key: {correlation_key}\n")
    parts.append(f"spec_id: {spec_id}\n")
    parts.append(f"generated_by: {GENERATED_BY}\n")
    parts.append("---\n\n")

    parts.append(_h(1, f"Test results — {spec_id}"))

    # Provenance.
    parts.append("\n" + _h(2, "Provenance"))
    parts.append(f"- **Spec id:** `{spec_id}`\n")
    parts.append(f"- **Correlation key:** `{correlation_key}`\n")
    if component_ref:
        parts.append(f"- **Component under test:** `{component_ref}`\n")
    if triage.get("mode"):
        parts.append(f"- **Triager mode:** {triage['mode']}\n")

    # Verdict summary.
    committed_n = int(summary.get("committed_count", len(committed)))
    flagged_n = int(summary.get("flagged_count", len(flagged)))
    rejected_n = int(summary.get("rejected_count", len(rejected)))
    skipped_n = int(summary.get("skipped_count", len(skipped)))
    graded = committed_n + flagged_n + rejected_n
    accept_rate = (committed_n / graded) if graded else 0.0

    parts.append("\n" + _h(2, "Verdict summary"))
    parts.append(f"- **Accepted:** {committed_n}\n")
    parts.append(f"- **Flagged:** {flagged_n}\n")
    parts.append(f"- **Rejected:** {rejected_n}\n")
    if skipped_n:
        parts.append(f"- **Skipped:** {skipped_n}\n")
    parts.append(f"- **Accept rate:** {accept_rate * 100:.0f}%\n")

    # Per-test signals (the moat: coverage delta · mutation · stability · CI-parity).
    graded_candidates = committed + flagged + rejected
    if graded_candidates:
        parts.append("\n" + _h(2, "Per-test verdicts"))
        parts.append("| Test | Verdict | Coverage Δ | Mutation | Stability | CI-parity |\n")
        parts.append("|---|---|---|---|---|---|\n")
        parts.extend(_verdict_rows(graded_candidates))

    return "".join(parts)


def render_test_results(
    triage: dict[str, Any],
    *,
    correlation_key: str,
    spec_id: str,
    component_ref: str | None = None,
) -> DocBundle:
    """Render a triage report into a :class:`DocBundle` (pure).

    Args:
        triage: the parsed ``findings/triage_report.json`` document.
        correlation_key: the plan's shared key (RFC-0001) — same key the plan
            it verifies was published under.
        spec_id: the TFactory spec/task id (the doc slug stem).
        component_ref: optional Backstage entity ref for the system under test.

    Returns a bundle whose ``registry_entry`` carries ``generated_by="tfactory"``
    and the ``correlation_key``, so it resolves by the same key as its plan.
    """
    slug = f"{spec_id}-tests"
    title = f"Test results — {spec_id}"
    markdown = _render_markdown(
        triage,
        correlation_key=correlation_key,
        spec_id=spec_id,
        component_ref=component_ref,
    )

    summary = triage.get("summary") or {}
    committed_n = int(summary.get("committed_count", len(triage.get("committed") or [])))
    flagged_n = int(summary.get("flagged_count", len(triage.get("flagged") or [])))
    rejected_n = int(summary.get("rejected_count", len(triage.get("rejected") or [])))
    graded = committed_n + flagged_n + rejected_n
    accept_rate = round(committed_n / graded, 4) if graded else 0.0

    registry_entry: dict[str, Any] = {
        "correlation_key": correlation_key,
        "title": title,
        "plan_type": "test-results",
        "doc_file": f"{slug}.md",
        "dependencies": [],
        "generated_by": GENERATED_BY,
        # TFactory-specific result facts (the verify side of the trail).
        "spec_id": spec_id,
        "component_ref": component_ref,
        "accepted": committed_n,
        "flagged": flagged_n,
        "rejected": rejected_n,
        "accept_rate": accept_rate,
    }

    return DocBundle(
        plan_id=spec_id,
        slug=slug,
        title=title,
        correlation_key=correlation_key,
        content_hash="",  # triage report has no single content hash; not required
        markdown=markdown,
        registry_entry=registry_entry,
    )
