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


def _classify_findings(report_md: str) -> tuple[int, int]:
    """Split the report's Findings into (interaction_failures, console_errors).

    A failed *interaction* (a click that didn't work) is a real test failure; a
    *console error* (e.g. the portal's expected 401s) is informational.
    """
    section = report_md.split("## Findings")[-1].split("## Walkthrough")[0]
    interaction = 0
    console = 0
    for ln in section.splitlines():
        if not ln.strip().startswith("- "):
            continue
        if "Interaction failed" in ln or "Login did not complete" in ln:
            interaction += 1
        elif "Console error" in ln:
            console += 1
    return interaction, console


def _verdict(logged_in: bool, failed: int, steps: int, console_errors: int) -> str:
    """fail = couldn't run (no login) or a majority of controls failed;
    attention = ran but some controls failed / console errors; else pass."""
    if not logged_in:
        return "fail"
    if steps and failed > steps // 2:
        return "fail"
    if failed > 0 or console_errors > 0:
        return "attention"
    return "pass"


def _strip_image_links(md: str) -> str:
    """Drop markdown image embeds so the detail "Report" tab has no broken
    images (screenshots render in the detail gallery from findings/screenshots/)."""
    return re.sub(r"!\[[^\]]*\]\([^)]*\)", "", md)


def build_run_dir(
    portal_key: str, report_dir: Path, run_id: str, dest_parent: Path
) -> Path:
    """Assemble a visual-inspection run dir from a portal-ui report. Returns it."""
    portal = config.PORTALS[portal_key]
    report_md = (report_dir / "report.md").read_text(encoding="utf-8")
    cov = _parse_coverage(report_md)
    logged_in = _logged_in(report_md)
    # Counts + verdict use the canonical Visual Inspection schema the portal's
    # tab renders: counts.{steps,passed,failed} and a verdict in
    # {pass, attention, fail}. "steps" = the controls exercised (nav + dropdowns
    # + dialogs). A "failed" step is a FAILED INTERACTION (a click that didn't
    # work) — NOT a benign console error (e.g. the portal's expected 401s), which
    # is informational and only nudges the verdict to "attention".
    steps = cov["nav"] + cov["dropdowns"] + cov["dialogs"]
    interaction_failures, console_errors = _classify_findings(report_md)
    failed = min(interaction_failures, steps)
    passed = max(0, steps - failed)
    verdict = _verdict(logged_in, failed, steps, console_errors)

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
    interaction_failures, console_errors = _classify_findings(report_md)
    steps = cov["nav"] + cov["dropdowns"] + cov["dialogs"]
    verdict = _verdict(
        logged_in, min(interaction_failures, steps), steps, console_errors
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
    # The task-detail "Report" tab renders findings/triage_report.md; its inline
    # image links would 404 (the page serves screenshots from the artefact
    # endpoint, not relative paths), so strip them — the screenshots show in the
    # detail's gallery from findings/screenshots/ instead.
    (spec_dir / "findings" / "triage_report.md").write_text(
        _strip_image_links(report_md), encoding="utf-8"
    )
    (spec_dir / "report.md").write_text(report_md, encoding="utf-8")
    shots = report_dir / "screenshots"
    if shots.is_dir():
        shutil.copytree(
            shots, spec_dir / "findings" / "screenshots", dirs_exist_ok=True
        )
    # NB: no findings/verdicts.json — portal-ui is a report+evidence run, not a
    # per-test accept/flag verdict set; the Verdicts tab stays cleanly disabled.
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
