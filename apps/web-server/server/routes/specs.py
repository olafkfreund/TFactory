"""Generic spec-ingestion route (WS2 / #40) — the portal "New test from spec" door.

``POST /api/specs/ingest`` accepts a raw acceptance-criteria spec (markdown /
Gherkin ``.feature`` / EARS) and creates a TFactory task **without** an AIFactory
branch, delegating to the backend's
``agents.tools_pkg.tools.task_control.create_spec_ingest_workspace``.

Auth is enforced by the global ``TokenAuthMiddleware`` (this is an ``/api/*``
route); no per-route dependency is needed. Errors map to HTTP codes:
``ValueError`` → 400 (unparseable / no criteria), ``FileExistsError`` → 409
(spec_id collision), unknown project → 404.
"""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, Field

from ._specpath import safe_component
from ._tenancy import resolve_tenant

# Pin ``agents.planner`` into ``sys.modules`` at startup (this route module is
# imported when the app boots). A request-time *fresh* import
# (``from agents.planner import schedule_planner`` inside
# ``create_spec_ingest_workspace``) was intermittently raising ImportError in
# the long-lived server process — the import resolves cleanly at startup but
# not always mid-request — which silently left every ingested spec at
# status=pending with ``planner_scheduled: false`` (TFactory #347). Importing
# here once, at boot, turns that lazy import into a fast sys.modules cache hit.
# Guarded so a minimal venv without the agent SDK can still load the route.
try:
    import agents.planner  # noqa: F401
except Exception:  # pragma: no cover — SDK-less env: lazy import will report it
    pass

router = APIRouter(prefix="/api/specs", tags=["Spec Ingestion"])


class SpecIngestRequest(BaseModel):
    project_id: str = Field(..., description="Registered project id")
    spec_id: str = Field(
        ..., min_length=1, description="New task/spec id (workspace dir name)"
    )
    spec_text: str = Field(
        ..., min_length=1, description="Raw markdown / Gherkin / EARS spec"
    )
    format: str | None = Field(
        default=None,
        description="markdown | gherkin | ears (auto-detected when omitted)",
    )
    target_paths: list[str] | None = Field(
        default=None, description="Repo-relative files/modules under test (target-mode)"
    )
    source_branch: str | None = Field(
        default=None,
        description=(
            "AIFactory build branch to fetch + check out into the project workspace "
            "before testing, so tests run against the ACTUAL built code (#96). When "
            "omitted, tests run against whatever is currently checked out."
        ),
    )
    contract: dict | None = Field(
        default=None,
        description=(
            "Full signed RFC-0002 Task Contract (the AIFactory implementation_plan). "
            "When present its `tfactory` block (lanes/frameworks/ac_to_code_map) is "
            "the AUTHORITATIVE test profile; persisted to context/task_contract.json. "
            "Absent → tests are inferred from spec_text."
        ),
    )
    tenant: str | None = Field(
        default=None,
        description=(
            "Tenant owning this verification spec (#683). Optional, service-local "
            "metadata — NOT part of the drift-gated task-contract schema. AIFactory "
            "stamps it on handoff; when omitted the tenant is resolved from the "
            "X-Tenant-Id header (multi-tenant mode) or falls back to 'default'."
        ),
    )
    git_url: str | None = Field(
        default=None,
        description=(
            "Clone URL of the repo under test. When the project_id is not already "
            "registered, TFactory clones this (at source_branch) and registers it, so "
            "an AIFactory build for a not-yet-known project can still hand off "
            "(no manual pre-registration). Ignored when the project is already known."
        ),
    )


class PrAttachRequest(BaseModel):
    """Attach the PR the AIFactory build opened to an already-ingested spec.

    The verifying handoff is sent BEFORE AIFactory opens the PR, so source.json
    has no `pr_number` and the triager's PR-comment side-effect skips. AIFactory
    calls this the moment the PR opens; the triager's later pr_comment step then
    posts the verdict — or, if verify already finished, we post it now (#964).
    """

    pr_number: int = Field(..., gt=0, description="The opened PR number")
    repo_slug: str | None = Field(
        default=None, description="owner/name of the PR's repo (for `gh pr comment -R`)"
    )


async def _clone_and_register_project(
    git_url: str, *, branch: str | None, name: str
) -> tuple[dict, str]:
    """Clone ``git_url`` (at ``branch``) into the TFactory workspace and register it.

    Returns ``(project_data, project_id)``. Reuses the same clone service
    ``/api/projects`` uses. Private repos authenticate via ``GITHUB_TOKEN`` when set.
    Idempotent: an existing registration of the cloned path is reused.
    """
    import os
    from datetime import datetime
    from pathlib import Path
    from uuid import uuid4

    from ..services.project_workspace_service import clone_or_update
    from .projects import load_projects, save_projects

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    credential = ("x-access-token", token) if token else None
    cloned = await clone_or_update(
        git_url=git_url, branch=branch, credential=credential
    )
    path = str(Path(cloned).resolve())

    projects = load_projects()
    for pid, pdata in projects.items():
        if pdata.get("path") == path:
            return pdata, pid  # already registered (e.g. a re-handoff)
    now = datetime.now().isoformat()
    pid = str(uuid4())
    pdata: dict = {
        "path": path,
        "name": name,
        "created_at": now,
        "updated_at": now,
        "clonedFrom": git_url,
    }
    if branch:
        pdata["clonedBranch"] = branch
    projects[pid] = pdata
    save_projects(projects)
    return pdata, pid


def _existing_project_dir(configured: str | None) -> str | None:
    """Return ``configured`` iff it is a safe, existing directory, else ``None``.

    Project paths are always persisted absolute + canonical
    (``projects.register`` stores ``str(Path(...).resolve())``). Re-assert that
    invariant before the filesystem touch: reject empty, non-absolute, or
    parent-traversal (``..``) values so a malformed/hostile project ``path`` —
    reachable from the spec-ingest request chain via self-registration — can
    never flow into ``Path.is_dir`` as an uncontrolled path expression.
    """
    from pathlib import Path

    if not configured:
        return None
    candidate = Path(configured)
    if not candidate.is_absolute() or ".." in candidate.parts:
        return None
    return configured if candidate.is_dir() else None


async def _ensure_project_clone(
    entry: dict, resolved_id: str, *, source_branch: str | None
) -> str:
    """Make sure the project's on-disk clone exists, re-cloning if it was recycled.

    A registered project's working tree can vanish out from under TFactory — a
    pod restart on an ephemeral volume, a PVC reset, a manual cleanup — while the
    project DB record persists. The planner then resolves ``project_dir`` to that
    now-missing path and the agent SDK raises "Working directory does not exist",
    surfacing as ``status=planner_failed`` / ``phase=planner_initial_exception``
    before any test lane runs (#539). Self-heal: if the path is gone but we know
    where it came from (``clonedFrom``), re-clone it — idempotent, since the same
    git URL maps to the same workspace slug → same path — and persist. If we
    don't know the origin, fail with a clear 409 rather than a downstream planner
    crash.

    Returns the resolved (possibly re-created) project path.
    """
    import os
    from pathlib import Path

    from ..services.project_workspace_service import clone_or_update
    from .projects import load_projects, save_projects

    configured = entry.get("path") or entry.get("root_path")
    existing = _existing_project_dir(configured)
    if existing is not None:
        return existing

    git_url = entry.get("clonedFrom")
    if not git_url:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=(
                f"project {resolved_id!r} working directory is missing "
                f"({configured!r}) and has no clonedFrom origin to restore it "
                "from; re-register the project with a git_url"
            ),
        )

    branch = entry.get("clonedBranch") or source_branch
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    credential = ("x-access-token", token) if token else None
    cloned = await clone_or_update(
        git_url=git_url, branch=branch, credential=credential
    )
    path = str(Path(cloned).resolve())

    # Persist the re-materialized path so subsequent reads see a live clone.
    projects = load_projects()
    if resolved_id in projects:
        projects[resolved_id]["path"] = path
        save_projects(projects)
    entry["path"] = path
    return path


@router.post(
    "/ingest", summary="Create a TFactory task from a raw spec (no AIFactory branch)"
)
async def ingest_spec(
    req: SpecIngestRequest,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
) -> dict:
    # Imported lazily so the route module loads even in environments where the
    # backend package isn't importable until runtime path setup.
    from agents.tools_pkg.tools.task_control import create_spec_ingest_workspace

    # Resolve the project from the web-server's project store (the same source
    # /api/projects uses), by id OR name. The agent-tools file store
    # (~/.tfactory/projects.json) can be empty/diverged from this one, which
    # 404'd every AIFactory→TFactory handoff (#517). AIFactory sends the project
    # *name*; accept either.
    from .projects import load_projects

    projects = load_projects()  # {id: project_data}
    resolved_id = req.project_id
    entry = projects.get(req.project_id)
    if entry is None:
        for pid, data in projects.items():
            if data.get("name") == req.project_id:
                entry, resolved_id = data, pid
                break
    if entry is None and req.git_url:
        # Self-materializing handoff (RFC-0007 / PARR seam): the project isn't
        # pre-registered, but the payload carries a clone URL — clone it (at the
        # build branch) and register it, reusing the same clone path /api/projects
        # uses. This makes the AIFactory->TFactory handoff work for ANY built
        # project without manual pre-registration (was: 404 on every such handoff).
        entry, resolved_id = await _clone_and_register_project(
            req.git_url, branch=req.source_branch, name=str(req.project_id)
        )
    if entry is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=(
                f"unknown project_id: {req.project_id!r} "
                "(and no git_url provided to self-register it)"
            ),
        )

    # The project is registered, but its on-disk clone may have been recycled
    # (pod/PVC restart) — re-materialize it before the planner resolves it as a
    # working dir, or the planner fails with planner_initial_exception (#539).
    project_root = await _ensure_project_clone(
        entry, resolved_id, source_branch=req.source_branch
    )

    # Both ids become path segments under workspaces/<project_id>/specs/<spec_id>
    # inside create_spec_ingest_workspace; they originate from the request body,
    # so reject path traversal before they reach the filesystem (py/path-injection).
    safe_project_id = safe_component(resolved_id)
    safe_spec_id = safe_component(req.spec_id)

    try:
        result = create_spec_ingest_workspace(
            project_id=safe_project_id,
            spec_id=safe_spec_id,
            spec_text=req.spec_text,
            fmt=req.format,
            target_paths=req.target_paths or [],
            project_root=project_root,
            contract=req.contract,
            source_branch=req.source_branch,
            tenant=resolve_tenant(x_tenant_id, req.tenant),
        )
    except FileExistsError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return {"task_id": req.spec_id, "project_id": req.project_id, **result}


@router.post(
    "/{project_id}/{spec_id}/pr",
    summary="Attach the opened PR to an ingested spec so the verdict posts back",
)
async def attach_pr(project_id: str, spec_id: str, req: PrAttachRequest) -> dict:
    """Record `pr_number`/`repo_slug` on an ingested spec's source.json (#964).

    The verifying handoff is sent before the PR exists, so this back-fills the
    PR onto source.json; the triager's later pr_comment step reads it. If verify
    already finished (status triaged), post the stored report immediately.
    """
    import json

    from agents.tools_pkg.tools.task_control import _spec_dir

    from .projects import load_projects

    # Same name-or-id resolution the ingest route uses (AIFactory sends the name).
    projects = load_projects()
    resolved_id = project_id
    if project_id not in projects:
        for pid, data in projects.items():
            if data.get("name") == project_id:
                resolved_id = pid
                break

    spec_dir = _spec_dir(safe_component(resolved_id), safe_component(spec_id))
    source_path = spec_dir / "context" / "source.json"
    if not source_path.exists():
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"no ingested spec {spec_id!r} for project {project_id!r}",
        )

    source = json.loads(source_path.read_text())
    source["pr_number"] = req.pr_number
    if req.repo_slug:
        source["repo_slug"] = req.repo_slug
    source_path.write_text(json.dumps(source, indent=2))

    # If verify already reached a terminal triaged state, its pr_comment step has
    # already run (and skipped for lack of a PR number, leaving the body on disk).
    # Post it now rather than waiting for a re-run.
    posted = None
    try:
        status_path = spec_dir / "status.json"
        st = json.loads(status_path.read_text()) if status_path.exists() else {}
        body_path = spec_dir / "findings" / "pr_comment_body.md"
        if st.get("status") in {"triaged", "triaged_empty"} and body_path.exists():
            from agents.triager import _pr_comment_dry_run
            from tools.pr_comment import PRCommentRequest, post_pr_comment

            result = post_pr_comment(
                PRCommentRequest(
                    repo_dir=spec_dir,
                    pr_number=req.pr_number,
                    body=body_path.read_text(),
                    repo_slug=req.repo_slug or None,
                ),
                dry_run=_pr_comment_dry_run(),
            )
            posted = {"ok": result.ok, "dry_run": result.dry_run}
    except Exception as exc:  # noqa: BLE001 — best-effort; source.json is the record
        posted = {"ok": False, "error": str(exc)[:200]}

    return {"attached": True, "pr_number": req.pr_number, "posted": posted}
