"""AC fidelity — does each acceptance criterion actually have a verifying test?

The point of TFactory is a trustworthy verdict, not a plausible-looking one. A run
can generate + commit tests and still leave an acceptance criterion UNVERIFIED
(its tests all rejected, or none generated). Reporting that as "done" is exactly
the dishonesty the pipeline exists to prevent.

This builds a per-AC ledger from the plan (which groups subtasks under a phase
named ``AC#N: <text>``) and the evaluator verdicts (keyed by ``test_id`` == the
subtask id). Each AC is graded:

  - verified      — at least one test for it was ACCEPTED (a real, kept verifier);
  - flagged_only  — tests exist but only flagged (need human review) — NOT verified;
  - unverified    — every test rejected, or no test at all;
  - unverifiable  — the criterion contradicts another, so NO test could satisfy
                    it (#896). This is a property of the SPEC, not of the run.

``unverified`` and ``unverifiable`` are deliberately separate. Reporting a
contradictory criterion as ``unverified`` reads identically to "the generator
failed" and says nothing about why, so the spec stays wrong and the next
implementer inherits the same trap. ``unverifiable`` can never be ``verified``
(an accepted test against an unsatisfiable criterion proves the test wrong, not
the criterion right) and is capped exactly as a failure is — see
``agents.traceability``.

The honest headline is "verified X / Y acceptance criteria"; uncovered ACs are
named, never hidden. What it does NOT measure is the spec — only the criteria
derived from it — so the sections of the spec body that were never criteria are
named too, and the summary key says ``all_acs_verified``, not ``all_verified``
(#855). Pure + unit-tested; the triager writes it to findings and the completion
envelope.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_AC_PREFIX = re.compile(r"^\s*(AC#?\s*\d+)\s*[:\-]\s*(.*)$", re.IGNORECASE)

# Every grade the ledger can emit. Counted from this rather than a literal dict
# so a future grade cannot KeyError the summary.
_GRADES: tuple[str, ...] = ("verified", "flagged_only", "unverified", "unverifiable")


def _split_ac(phase_name: str, phase_num) -> tuple[str, str]:
    m = _AC_PREFIX.match(phase_name or "")
    if m:
        return m.group(1).replace(" ", ""), m.group(2).strip()
    return (f"phase-{phase_num}", (phase_name or "").strip())


def _ac_status(tests: list[dict], *, unverifiable: bool = False) -> str:
    # A provably unsatisfiable criterion outranks any verdict (#896). An
    # accepted test against a criterion that contradicts another proves the
    # TEST wrong, not the criterion right — so this must never report verified.
    if unverifiable:
        return "unverifiable"
    verdicts = {t.get("verdict") for t in tests}
    if "accept" in verdicts:
        return "verified"
    if "flag" in verdicts:
        return "flagged_only"
    return "unverified"  # only rejects, or no test


def _find_verdict(vby: dict, subtask_id: str) -> dict | None:
    """Match a subtask to its verdict by id, tolerating replan suffixes (-r1)."""
    if subtask_id in vby:
        return vby[subtask_id]
    base = re.sub(r"-r\d+$", "", subtask_id)
    for tid, v in vby.items():
        if tid == base or re.sub(r"-r\d+$", "", tid) == base:
            return v
    return None


def _unrepresented_sections(spec_markdown: str | None) -> list[str]:
    """Spec-body sections that never became acceptance criteria (#855).

    Best-effort: no spec text, or an older spec written before the body was
    carried through ingestion, yields an empty list — silence, not a claim.
    """
    if not spec_markdown:
        return []
    try:
        from spec_sources import unrepresented_sections  # noqa: PLC0415 - lazy

        sections: list[str] = unrepresented_sections(spec_markdown)
        return sections
    except Exception:  # noqa: BLE001 - the caveat must never break the ledger
        return []


def build_ac_ledger(
    test_plan: dict[str, Any],
    verdicts: list[dict[str, Any]],
    *,
    spec_markdown: str | None = None,
    conflicts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Per-AC coverage ledger from the plan phases + the evaluator verdicts.

    ``spec_markdown`` is the ingested spec (``context/aifactory_spec.md``). It
    is what lets the summary say which parts of the spec this run did NOT look
    at: the ledger only ever measures the acceptance criteria, and reporting
    that as though it measured the spec is the failure this argument fixes.

    ``conflicts`` is ``agents.criterion_conflict``'s finding records (#896). Any
    AC named on either side of a conflict is graded ``unverifiable`` — reported
    as what it is rather than folded into ``unverified``, where it would be
    indistinguishable from a generator failure. Omitted/empty leaves every grade
    exactly as before.
    """
    unverifiable_ids: set[str] = set()
    if conflicts:
        try:
            from agents.criterion_conflict import (  # noqa: PLC0415 - lazy by design
                conflicted_ac_ids,
            )

            unverifiable_ids = conflicted_ac_ids(conflicts)
        except Exception:  # noqa: BLE001 - the ledger must never break on this
            unverifiable_ids = set()

    vby = {v.get("test_id"): v for v in (verdicts or []) if v.get("test_id")}
    acs: list[dict] = []
    # Counted from the grades actually produced, so adding a grade can never
    # KeyError the ledger the way a fixed-key dict would.
    counts = dict.fromkeys(_GRADES, 0)
    for ph in test_plan.get("phases", []) or []:
        name = ph.get("name") or ""
        if name.lower().startswith("replan"):
            continue  # replan phases are retries of an existing AC, not new ACs
        ac_id, text = _split_ac(name, ph.get("phase"))
        tests: list[dict] = []
        for s in ph.get("subtasks", []) or []:
            v = _find_verdict(vby, s.get("id", ""))
            if v:
                tests.append(
                    {
                        "test_id": v.get("test_id"),
                        "test_file": v.get("test_file"),
                        "verdict": v.get("verdict"),
                    }
                )
        status = _ac_status(tests, unverifiable=ac_id in unverifiable_ids)
        counts[status] = counts.get(status, 0) + 1
        entry = {"ac_id": ac_id, "text": text, "status": status, "tests": tests}
        if status == "unverifiable":
            entry["conflicts"] = [
                c
                for c in conflicts or []
                if isinstance(c, dict)
                and ac_id in (c.get("ac_id"), c.get("conflicting_ac_id"))
            ]
        acs.append(entry)
    total = len(acs)
    return {
        "acceptance": acs,
        "summary": {
            **counts,
            "total": total,
            "verified_fraction": f"{counts['verified']}/{total}",
            # Named for what it measures. The old name, ``all_verified``, read
            # as "the spec is satisfied" and meant "every AC we kept has a
            # passing test" — so a requirement nobody restated as an AC made a
            # run green by being absent (#855).
            "scope": "acceptance_criteria",
            "all_acs_verified": total > 0 and counts["verified"] == total,
            "spec_sections_unverified": _unrepresented_sections(spec_markdown),
        },
    }


def attach_screenshots(ledger: dict, findings_dir: Path | str) -> dict:
    """Link any collected screenshots to the AC whose test produced them.

    Browser-lane screenshots land in ``findings/screenshots`` named after the spec
    (playwright writes ``<spec-stem>-...png``). Match by the test file stem so a
    stakeholder sees AC -> test -> screenshot. Best-effort; mutates + returns.
    """
    shots_dir = Path(findings_dir) / "screenshots"
    if not shots_dir.is_dir():
        return ledger
    pngs = [p for p in sorted(shots_dir.iterdir()) if p.suffix.lower() == ".png"]
    for ac in ledger.get("acceptance", []):
        for t in ac.get("tests", []):
            stem = Path(t.get("test_file") or "").stem.replace(".spec", "")
            if not stem:
                continue
            matched = [p.name for p in pngs if stem and stem in p.name]
            if matched:
                t["screenshots"] = matched
    return ledger


def render_markdown(ledger: dict) -> str:
    """Human-readable AC-fidelity report (no emojis)."""
    s = ledger.get("summary", {})
    unverifiable = s.get("unverifiable", 0)
    headline = (
        f"Verified {s.get('verified_fraction', '0/0')} acceptance criteria "
        f"(flagged-only: {s.get('flagged_only', 0)}, "
        f"unverified: {s.get('unverified', 0)}"
    )
    headline += f", unverifiable: {unverifiable}).\n" if unverifiable else ").\n"
    lines = [
        "# Acceptance-criteria fidelity",
        "",
        headline.rstrip("\n"),
        "",
        "Scope: the acceptance criteria listed below, NOT the full spec text.",
        "",
    ]
    if unverifiable:
        # Named first: a contradictory criterion is a defect in the SPEC, and it
        # blocks the run regardless of how good the tests are. Burying it under
        # the coverage note is how it stayed invisible.
        lines.append(
            f"NOTE: {unverifiable} acceptance criterion/criteria are UNVERIFIABLE - "
            "they contradict another criterion, so no test can satisfy them. This "
            "is a defect in the specification, not in the tests, and it must be "
            "corrected before this run can pass. Each is named below with the "
            "criterion it conflicts with.\n"
        )
    if not s.get("all_acs_verified", False) and s.get("total", 0):
        lines.append(
            "NOTE: not every acceptance criterion is verified by an accepted test "
            "- see UNVERIFIED / flagged-only below. This run is not a full pass.\n"
        )
    unrepresented = s.get("spec_sections_unverified") or []
    if unrepresented:
        lines.append(
            f"NOTE: {len(unrepresented)} sections of the spec body were not "
            "represented as acceptance criteria and were not verified: "
            + ", ".join(unrepresented)
            + ". A green result above says nothing about them.\n"
        )
    for ac in ledger.get("acceptance", []):
        lines.append(f"## {ac['ac_id']} [{ac['status'].upper()}]")
        lines.append(f"{ac['text']}")
        for conflict in ac.get("conflicts") or []:
            if not isinstance(conflict, dict):
                continue
            other = (
                conflict.get("conflicting_ac_id")
                if conflict.get("ac_id") == ac["ac_id"]
                else conflict.get("ac_id")
            )
            lines.append(
                f"  - CONFLICTS WITH {other}: {conflict.get('contradiction') or ''}"
            )
        if ac["tests"]:
            for t in ac["tests"]:
                shots = (
                    f" - screenshots: {', '.join(t['screenshots'])}"
                    if t.get("screenshots")
                    else ""
                )
                lines.append(f"  - {t['verdict']}: `{t['test_id']}`{shots}")
        else:
            lines.append("  - (no test covers this criterion)")
        lines.append("")
    return "\n".join(lines)
