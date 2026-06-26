"""Publish a portal-ui run into the Visual Inspection store (#553 → #170 surface).

The portal's "Visual Reports" tab reads ``agents.visual_inspection.store``: any
run dir with ``meta.json`` (target / verdict / counts) + ``report.md`` +
screenshots shows up automatically. This adapter converts a
``reports/<portal>/`` run into that shape and registers it — so a portal-ui Job's
findings land in the same place humans already review visual evidence, with no
frontend change.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

from . import config


def _parse_coverage(report_md: str) -> dict[str, int]:
    m = re.search(r"\| (\d+) \| (\d+) \| (\d+) \| (\d+) \| (\d+) \|", report_md)
    keys = ("nav", "dropdowns", "dialogs", "screenshots", "findings")
    return (
        dict(zip(keys, (int(g) for g in m.groups()), strict=True))
        if m
        else dict.fromkeys(keys, 0)
    )


def _logged_in(report_md: str) -> bool:
    return "logged in: **True**" in report_md


def build_run_dir(
    portal_key: str, report_dir: Path, run_id: str, dest_parent: Path
) -> Path:
    """Assemble a visual-inspection run dir from a portal-ui report. Returns it."""
    portal = config.PORTALS[portal_key]
    report_md = (report_dir / "report.md").read_text(encoding="utf-8")
    counts = _parse_coverage(report_md)
    logged_in = _logged_in(report_md)
    findings = counts["findings"]
    verdict = (
        "pass"
        if (logged_in and findings == 0)
        else ("needs-review" if logged_in else "fail")
    )

    run_dir = dest_parent / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "report.md").write_text(report_md, encoding="utf-8")
    shots = report_dir / "screenshots"
    if shots.is_dir():
        shutil.copytree(shots, run_dir / "screenshots", dirs_exist_ok=True)
    meta = {
        "target": f"{portal.name} — {portal.url}",
        "verdict": verdict,
        "counts": {k: counts[k] for k in ("nav", "dropdowns", "dialogs", "findings")},
        "kind": "portal-ui",
        "portal": portal_key,
        "logged_in": logged_in,
    }
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    # Findings as issues.json (the tab + GitHub-issues flow both read this shape).
    issues = [
        {"title": line.lstrip("- ").strip()}
        for line in report_md.split("## Findings")[-1]
        .split("## Walkthrough")[0]
        .splitlines()
        if line.strip().startswith("- ") and "None —" not in line
    ]
    (run_dir / "issues.json").write_text(json.dumps(issues, indent=2), encoding="utf-8")
    return run_dir


def publish(portal_key: str, report_dir: Path, run_id: str) -> Path | None:
    """Register the portal-ui run in the visual-inspection store. Best-effort:
    returns the store path, or None if the backend store isn't importable."""
    try:
        # The store lives in the TFactory backend; on the runtime image it's on
        # PYTHONPATH. Add the conventional path as a fallback.
        backend = Path(__file__).resolve().parents[1] / "apps" / "backend"
        if backend.is_dir() and str(backend) not in sys.path:
            sys.path.insert(0, str(backend))
        from agents.visual_inspection import store  # type: ignore
    except ImportError:
        return None
    staged = build_run_dir(
        portal_key, report_dir, run_id, dest_parent=report_dir.parent / "_vi"
    )
    return store.write_run(staged)
