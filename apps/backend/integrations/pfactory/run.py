"""Generate + run tests for a picked-up PFactory target; report back (#197).

The capstone of the pickup contract (epic #193): take a governed target
(#195 pickup) + its parsed oracle (#196), seed a TFactory spec workspace from the
oracle's acceptance criteria + citations, and schedule the existing
Planner → Gen-Functional → Executor → Evaluator → Triager pipeline. The Triager
renders a triage report tied to the target's ``plan_id`` and reports back on the
originating issue/PR.

**No automatic pushes** (CLAUDE.md policy): the Triager's git-commit + PR-comment
side-effects stay DRY-RUN by default. The operator opts in via ``dry_run=False``
here (which sets TFactory's existing ``TFACTORY_TRIAGER_*`` flags) or by setting
those env flags directly.

The actual generation/run is the existing pipeline (already tested + live
verified); this module owns the orchestration seam and is parameterized by an
injectable ``schedule`` callable so it is unit-testable without an LLM/Docker.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .oracle import PFactoryOracle, build_oracle
from .pickup import classify_issue

# TFactory's existing Triager side-effect flags (dry-run by default).
_GIT_WRITE_ENV = "TFACTORY_TRIAGER_GIT_WRITE"
_PR_COMMENT_ENV = "TFACTORY_TRIAGER_PR_COMMENT"

_AC_PREFIX = re.compile(r"^\s*AC#?\d+[:.)]\s*", re.IGNORECASE)


@dataclass(frozen=True)
class RunHandle:
    """The outcome of enqueuing a PFactory target for test generation."""

    plan_id: str
    project_id: str
    spec_dir: Path
    scheduled: bool
    dry_run: bool


def _strip_ac_prefix(text: str) -> str:
    """Drop a leading ``AC#N:`` marker so it isn't doubled when re-rendered."""
    return _AC_PREFIX.sub("", text).strip()


def spec_markdown_from_oracle(oracle: PFactoryOracle, *, title: str) -> str:
    """Render the canonical ``aifactory_spec.md`` the Planner reads, from the oracle.

    Acceptance criteria become ``AC#N`` markers (the Planner's contract); the
    citations ride in the description as the "why + source" the tests must honour.
    """
    from spec_sources import AcceptanceCriterion, NormalizedSpec, SpecFormat

    criteria = tuple(
        AcceptanceCriterion(id=f"AC#{i + 1}", text=_strip_ac_prefix(text))
        for i, text in enumerate(oracle.acceptance_criteria)
    )
    desc_parts: list[str] = []
    if oracle.citations:
        desc_parts.append("Sources the generated tests must honour:")
        for c in oracle.citations:
            line = f"- {c.why}"
            if c.uri:
                line += f" ({c.uri})"
            if c.source:
                line += f" [{c.source}]"
            desc_parts.append(line)
    spec = NormalizedSpec(
        title=title or oracle.plan_id or "PFactory test target",
        description="\n".join(desc_parts),
        criteria=criteria,
        source_format=SpecFormat.MARKDOWN,
    )
    return spec.to_markdown()


def _resolve_plan_id(oracle: PFactoryOracle, target: dict) -> str:
    return (
        oracle.plan_id
        or target.get("plan_id")
        or f"pf-{target.get('issue_number') or 'unknown'}"
    )


def prepare_workspace(
    target: dict,
    oracle: PFactoryOracle,
    *,
    project_id: str,
    workspace_root: str | Path | None = None,
    repo: str | None = None,
    branch: str | None = None,
    base_ref: str = "main",
) -> Path:
    """Seed a TFactory spec workspace from a PFactory target + oracle.

    Writes ``context/aifactory_spec.md`` (from the oracle), ``context/source.json``
    (ties the eventual report back to the originating issue + ``plan_id``), and a
    pending ``status.json``. Returns the spec directory (``.../specs/<plan_id>``).
    """
    from agents.tools_pkg.tools import task_control as tc

    root = Path(workspace_root) if workspace_root else tc._workspace_root()
    plan_id = _resolve_plan_id(oracle, target)
    spec_dir = tc._spec_dir(project_id, plan_id, root)
    for sub in ("context", "tests", "findings", "logs"):
        (spec_dir / sub).mkdir(parents=True, exist_ok=True)

    title = target.get("title") or plan_id
    (spec_dir / "context" / "aifactory_spec.md").write_text(
        spec_markdown_from_oracle(oracle, title=title)
    )
    # source.json — `issue_number` is the spine correlation key the Triager's
    # completion envelope (#198) reports back on; `pfactory: true` marks provenance.
    source = {
        "plan_id": plan_id,
        "pfactory": True,
        "issue_number": target.get("issue_number"),
        "priority": oracle.priority,
        "horizon": oracle.horizon,
        "taxonomy": oracle.taxonomy_version,
        "repo": repo,
        "branch": branch,
        "base_ref": base_ref,
    }
    (spec_dir / "context" / "source.json").write_text(json.dumps(source, indent=2))
    (spec_dir / "status.json").write_text(
        json.dumps(
            {
                "task_id": plan_id,
                "project_id": project_id,
                "spec_id": plan_id,
                "status": "pending",
                "phase": "pfactory_picked_up",
            },
            indent=2,
        )
    )
    return spec_dir


def run_target(
    target: dict,
    oracle: PFactoryOracle,
    *,
    project_id: str,
    project_dir: str | Path,
    repo: str | None = None,
    branch: str | None = None,
    base_ref: str = "main",
    workspace_root: str | Path | None = None,
    schedule: Callable[[Path, Path], Any] | None = None,
    dry_run: bool = True,
) -> RunHandle:
    """Enqueue a governed PFactory target for test generation.

    Seeds the workspace, honours the no-auto-push policy (Triager side-effects
    stay dry-run unless ``dry_run=False``), and schedules the pipeline. The
    ``schedule`` callable is injectable for tests; by default it fires the
    Planner (which auto-chains Gen-Functional → Evaluator → Triager).
    """
    spec_dir = prepare_workspace(
        target,
        oracle,
        project_id=project_id,
        workspace_root=workspace_root,
        repo=repo,
        branch=branch,
        base_ref=base_ref,
    )

    # No automatic pushes by default. Only flip TFactory's existing Triager
    # side-effect flags when the operator explicitly opts in.
    if not dry_run:
        os.environ[_GIT_WRITE_ENV] = "1"
        os.environ[_PR_COMMENT_ENV] = "1"

    if schedule is None:
        schedule = _default_schedule()

    scheduled = False
    if schedule is not None:
        schedule(spec_dir, Path(project_dir))
        scheduled = True

    return RunHandle(
        plan_id=spec_dir.name,
        project_id=project_id,
        spec_dir=spec_dir,
        scheduled=scheduled,
        dry_run=dry_run,
    )


def _default_schedule() -> Callable[[Path, Path], Any] | None:
    """The default scheduler: fire the Planner in initial mode. None if unavailable."""
    try:
        from agents.planner import schedule_planner
    except Exception:
        return None

    def _schedule(spec_dir: Path, project_dir: Path) -> Any:
        return schedule_planner(spec_dir, project_dir, mode="initial")

    return _schedule


def pickup_and_run(
    issue: Any,
    *,
    project_id: str,
    project_dir: str | Path,
    repo: str | None = None,
    branch: str | None = None,
    base_ref: str = "main",
    workspace_root: str | Path | None = None,
    schedule: Callable[[Path, Path], Any] | None = None,
    dry_run: bool = True,
) -> RunHandle | None:
    """Recognise a GitHub issue and, if governed for TFactory, run it end-to-end.

    Returns the ``RunHandle`` when picked up, else ``None`` (issue untouched).
    """
    decision = classify_issue(issue)
    if not decision.picked_up:
        return None

    body = issue.get("body") if isinstance(issue, dict) else getattr(issue, "body", "")
    oracle = build_oracle(issue_body=body or "")
    target = {
        "plan_id": decision.plan_id,
        "issue_number": decision.issue_number,
        "title": issue.get("title")
        if isinstance(issue, dict)
        else getattr(issue, "title", ""),
    }
    return run_target(
        target,
        oracle,
        project_id=project_id,
        project_dir=project_dir,
        repo=repo,
        branch=branch,
        base_ref=base_ref,
        workspace_root=workspace_root,
        schedule=schedule,
        dry_run=dry_run,
    )
