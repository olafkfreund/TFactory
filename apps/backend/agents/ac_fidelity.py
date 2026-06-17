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
  - unverified    — every test rejected, or no test at all.

The honest headline is "verified X / Y acceptance criteria"; uncovered ACs are
named, never hidden. Pure + unit-tested; the triager writes it to findings and the
completion envelope.
"""

from __future__ import annotations

import re
from pathlib import Path

_AC_PREFIX = re.compile(r"^\s*(AC#?\s*\d+)\s*[:\-]\s*(.*)$", re.IGNORECASE)


def _split_ac(phase_name: str, phase_num) -> tuple[str, str]:
    m = _AC_PREFIX.match(phase_name or "")
    if m:
        return m.group(1).replace(" ", ""), m.group(2).strip()
    return (f"phase-{phase_num}", (phase_name or "").strip())


def _ac_status(tests: list[dict]) -> str:
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


def build_ac_ledger(test_plan: dict, verdicts: list[dict]) -> dict:
    """Per-AC coverage ledger from the plan phases + the evaluator verdicts."""
    vby = {v.get("test_id"): v for v in (verdicts or []) if v.get("test_id")}
    acs: list[dict] = []
    counts = {"verified": 0, "flagged_only": 0, "unverified": 0}
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
        status = _ac_status(tests)
        counts[status] += 1
        acs.append({"ac_id": ac_id, "text": text, "status": status, "tests": tests})
    total = len(acs)
    return {
        "acceptance": acs,
        "summary": {
            **counts,
            "total": total,
            "verified_fraction": f"{counts['verified']}/{total}",
            "all_verified": total > 0 and counts["verified"] == total,
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
    lines = [
        "# Acceptance-criteria fidelity",
        "",
        f"Verified {s.get('verified_fraction', '0/0')} acceptance criteria "
        f"(flagged-only: {s.get('flagged_only', 0)}, unverified: {s.get('unverified', 0)}).",
        "",
    ]
    if not s.get("all_verified", False) and s.get("total", 0):
        lines.append(
            "NOTE: not every acceptance criterion is verified by an accepted test "
            "- see UNVERIFIED / flagged-only below. This run is not a full pass.\n"
        )
    for ac in ledger.get("acceptance", []):
        lines.append(f"## {ac['ac_id']} [{ac['status'].upper()}]")
        lines.append(f"{ac['text']}")
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
