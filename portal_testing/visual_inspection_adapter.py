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
import os
import re
import shutil
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
    cov = _parse_coverage(report_md)
    logged_in = _logged_in(report_md)
    findings = cov["findings"]
    # Counts + verdict use the canonical Visual Inspection schema the portal's
    # tab renders: counts.{steps,passed,failed} and a verdict in
    # {pass, attention, fail} (fail → FAIL badge, attention → warning accent,
    # else → PASS). A control with a finding is a "fail"; the rest pass.
    steps = cov["screenshots"]
    failed = min(findings, steps)
    passed = max(0, steps - failed)
    if not logged_in:
        verdict = "fail"
    elif findings > 0:
        verdict = "attention"
    else:
        verdict = "pass"

    run_dir = dest_parent / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "report.md").write_text(report_md, encoding="utf-8")
    shots = report_dir / "screenshots"
    if shots.is_dir():
        shutil.copytree(shots, run_dir / "screenshots", dirs_exist_ok=True)
    meta = {
        "target": f"{portal.name} — {portal.url}",
        "verdict": verdict,
        "counts": {"steps": steps, "passed": passed, "failed": failed},
        "kind": "portal-ui",
        "portal": portal_key,
        "logged_in": logged_in,
        # Extra portal-ui coverage detail (harmless to the generic renderer).
        "coverage": {k: cov[k] for k in ("nav", "dropdowns", "dialogs", "findings")},
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


def visual_inspection_root() -> Path:
    """The Visual Inspection store root the portal's tab reads. Honours
    ``TFACTORY_VISUAL_INSPECTION_ROOT`` (set on the dispatch Job, pointing at the
    co-mounted control-plane data PVC) and falls back to ``~/.tfactory/
    visual-inspections`` — the same resolution as ``agents.visual_inspection.store``.
    """
    override = os.environ.get("TFACTORY_VISUAL_INSPECTION_ROOT")
    return (
        Path(override) if override else Path.home() / ".tfactory" / "visual-inspections"
    )


def tfactory_workspace_root() -> Path:
    """Root the ``/api/tfactory/tasks`` list globs (``<root>/workspaces/...``).
    Honours ``TFACTORY_WORKSPACE_ROOT``; defaults to ``~/.tfactory`` (the
    co-mounted control-plane data PVC in-cluster) — same as the backend's
    ``_resolve_workspace_root``.
    """
    override = os.environ.get("TFACTORY_WORKSPACE_ROOT")
    return Path(override) if override else Path.home() / ".tfactory"


def publish_as_tfactory_spec(portal_key: str, report_dir: Path, run_id: str) -> Path:
    """Register the portal-ui run as a TFactory spec so it appears as a finished
    TEST in BOTH the Pipeline "Report" lane AND the cockpit (both read
    ``/api/tfactory/tasks``, which globs ``workspaces/*/specs/*/status.json``).

    Writes ``status: triaged`` (the Report-lane terminal status) under a
    standalone ``portal-ui`` project, plus the verdict, the report, and the
    screenshots as artefacts. No upstream GitHub issue (a portal health test is
    not tied to a work item), so the cockpit threads it by its own run id and
    shows it in the Finished lane.
    """
    portal = config.PORTALS[portal_key]
    report_md = (report_dir / "report.md").read_text(encoding="utf-8")
    cov = _parse_coverage(report_md)
    logged_in = _logged_in(report_md)
    findings = cov["findings"]
    verdict = (
        "pass"
        if (logged_in and findings == 0)
        else ("attention" if logged_in else "fail")
    )

    spec_dir = tfactory_workspace_root() / "workspaces" / "portal-ui" / "specs" / run_id
    (spec_dir / "context").mkdir(parents=True, exist_ok=True)
    (spec_dir / "findings").mkdir(parents=True, exist_ok=True)

    status_doc = {
        "task_id": run_id,
        "project_id": "portal-ui",
        "status": "triaged",  # → Pipeline "Report" lane / cockpit terminal
        "phase": "triager_report_done",
        "title": f"Portal UI — {portal.name}",
        "verdict": verdict,
        "lane_progress": {"browser": "triaged"},
    }
    (spec_dir / "status.json").write_text(
        json.dumps(status_doc, indent=2), encoding="utf-8"
    )
    # Standalone test — no upstream issue (cockpit falls back to the run id).
    (spec_dir / "context" / "source.json").write_text(
        json.dumps({"aifactory": {"github_issue": None}}, indent=2), encoding="utf-8"
    )
    (spec_dir / "findings" / "verdicts.json").write_text(
        json.dumps(
            {
                "verdict": verdict,
                "counts": {"steps": cov["screenshots"], "findings": findings},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (spec_dir / "report.md").write_text(report_md, encoding="utf-8")
    shots = report_dir / "screenshots"
    if shots.is_dir():
        shutil.copytree(shots, spec_dir / "screenshots", dirs_exist_ok=True)
    return spec_dir


def publish(portal_key: str, report_dir: Path, run_id: str) -> Path | None:
    """Write the portal-ui run straight into the Visual Inspection store root.

    Self-contained (no backend import): writes the ``meta.json``/``report.md``/
    screenshots/``issues.json`` run-dir layout that ``agents.visual_inspection.
    store`` reads, directly under the store root. In-cluster the Job co-mounts
    the control-plane data PVC at that root so the run surfaces in the portal's
    Visual Reports tab. Returns the run path.
    """
    root = visual_inspection_root()
    root.mkdir(parents=True, exist_ok=True)
    return build_run_dir(portal_key, report_dir, run_id, dest_parent=root)
