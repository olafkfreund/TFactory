"""GitHub PR-operation endpoints — extracted from routes/github.py (#360).

The PR management routes on github.py's project_router (mounted under
/api/projects/{projectId}/github via routes/projects.py). Carved into their own
module; behaviour and paths unchanged — projects.py mounts this router at the
same /{projectId}/github prefix. Shared helpers/models stay in routes/github.py
and are imported here.

    GET    /prs | /prs/{prNumber}/review | /new-commits | /logs
    POST   /prs/{prNumber}/review | post-review | comment | approve | merge
                 | assign | cancel
    DELETE /prs/{prNumber}/review
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from . import github  # module-qualified so test patches on its helpers apply
from .github import (
    AssignPRRequest,
    MergePRRequest,
    PostPRCommentRequest,
    PostPRReviewRequest,
    PRReviewRequest,
)

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/prs")
async def get_project_github_prs(
    projectId: str,
    state: str | None = Query(None),
):
    """Get GitHub pull requests for a project."""
    project_path = github._resolve_project_path(projectId)
    if not project_path:
        return {"success": False, "error": f"Project {projectId} not found"}

    if github._use_provider_api(projectId):
        try:
            provider = github._get_project_provider(projectId)
            from runners.github.providers.protocol import PRFilters

            # Map state
            query_state = "open"
            if state and state in ("open", "closed", "merged", "all"):
                query_state = state

            filters = PRFilters(state=query_state)
            prs_raw = await provider.fetch_prs(filters)
            prs = [github._map_provider_pr(pr) for pr in prs_raw]
            return {"success": True, "data": prs}
        except Exception as e:
            return {"success": False, "error": str(e)}

    from ..services.pr_data_service import get_pr_data_service

    service = get_pr_data_service()
    return service.list_prs(project_path, state=state)


@router.post("/prs/{prNumber}/review")
async def trigger_pr_review(
    projectId: str,
    prNumber: int,
    request: PRReviewRequest | None = None,
):
    """Trigger an async PR review.

    Launches the GitHub runner's review-pr (or followup-review-pr) command
    as a background subprocess. Progress is emitted via WebSocket events:
    - pr:review-progress
    - pr:review-complete
    - pr:review-error

    Returns 202 Accepted immediately.
    """
    project_path = github._resolve_project_path(projectId)
    if not project_path:
        return JSONResponse(
            status_code=404,
            content={"success": False, "error": f"Project {projectId} not found"},
        )

    followup = request.followup if request else False

    from ..services.pr_review_service import get_pr_review_service

    service = get_pr_review_service()

    if service.is_running(projectId, prNumber):
        return JSONResponse(
            status_code=409,
            content={
                "success": False,
                "error": f"A review is already running for PR #{prNumber}",
            },
        )

    started = await service.start_review(
        project_id=projectId,
        pr_number=prNumber,
        project_path=project_path,
        followup=followup,
    )

    if not started:
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": "Failed to start PR review"},
        )

    return JSONResponse(
        status_code=202,
        content={
            "success": True,
            "data": {
                "message": f"PR #{prNumber} review started",
                "prNumber": prNumber,
                "followup": followup,
            },
        },
    )


@router.get("/prs/{prNumber}/review")
async def get_pr_review(projectId: str, prNumber: int):
    """Get stored PR review result.

    Reads the review result JSON from the project's
    .tfactory/github/pr/review_{prNumber}.json file.

    Returns PRReviewResult data or null if no review exists.
    """
    project_path = github._resolve_project_path(projectId)
    if not project_path:
        return JSONResponse(
            status_code=404,
            content={"success": False, "error": f"Project {projectId} not found"},
        )

    from ..services.pr_data_service import get_pr_data_service

    service = get_pr_data_service()
    result = service.get_review(project_path, prNumber)

    if not result["success"]:
        return JSONResponse(
            status_code=500,
            content=result,
        )
    return result


@router.delete("/prs/{prNumber}/review")
async def delete_pr_review(projectId: str, prNumber: int):
    """Delete a stored PR review result.

    Removes the review result JSON file and updates the index.
    """
    project_path = github._resolve_project_path(projectId)
    if not project_path:
        return JSONResponse(
            status_code=404,
            content={"success": False, "error": f"Project {projectId} not found"},
        )

    from ..services.pr_data_service import get_pr_data_service

    service = get_pr_data_service()
    result = service.delete_review(project_path, prNumber)

    if not result["success"]:
        return JSONResponse(
            status_code=500,
            content=result,
        )
    return result


@router.post("/prs/{prNumber}/post-review")
async def post_pr_review_to_github(
    projectId: str,
    prNumber: int,
    request: PostPRReviewRequest | None = None,
):
    """Post review findings as GitHub review comments.

    Reads the stored review result, filters by selectedFindingIds if provided,
    and posts each finding as a file-level review comment via gh CLI.
    """
    project_path = github._resolve_project_path(projectId)
    if not project_path:
        return JSONResponse(
            status_code=404,
            content={"success": False, "error": f"Project {projectId} not found"},
        )

    from ..services.pr_data_service import get_pr_data_service

    service = get_pr_data_service()
    selected_ids = request.selectedFindingIds if request else None
    result = service.post_review_to_github(
        project_path, prNumber, selected_finding_ids=selected_ids
    )

    if not result["success"]:
        # Determine appropriate status code based on error
        error_msg = result.get("error", "")
        if "No review found" in error_msg:
            status_code = 404
        elif "No findings to post" in error_msg:
            status_code = 400
        elif "Failed to read" in error_msg:
            status_code = 500
        else:
            status_code = 500
        return JSONResponse(status_code=status_code, content=result)

    return result


@router.post("/prs/{prNumber}/comment")
async def post_pr_comment(
    projectId: str,
    prNumber: int,
    request: PostPRCommentRequest,
):
    """Post a general comment on a PR via gh pr comment."""
    project_path = github._resolve_project_path(projectId)
    if not project_path:
        return JSONResponse(
            status_code=404,
            content={"success": False, "error": f"Project {projectId} not found"},
        )

    if github._use_provider_api(projectId):
        try:
            provider = github._get_project_provider(projectId)
            comment_id = await provider.add_comment(prNumber, request.body)
            return {"success": True, "data": {"commentId": comment_id}}
        except Exception as e:
            return JSONResponse(
                status_code=500, content={"success": False, "error": str(e)}
            )

    from ..services.pr_data_service import get_pr_data_service

    service = get_pr_data_service()
    result = service.post_comment(project_path, prNumber, request.body)

    if not result["success"]:
        error_msg = result.get("error", "")
        status_code = 400 if "cannot be empty" in error_msg else 500
        return JSONResponse(status_code=status_code, content=result)

    return result


@router.post("/prs/{prNumber}/approve")
async def approve_pr(
    projectId: str,
    prNumber: int,
    request: PostPRCommentRequest | None = None,
):
    """Approve a PR via gh pr review --approve."""
    project_path = github._resolve_project_path(projectId)
    if not project_path:
        return JSONResponse(
            status_code=404,
            content={"success": False, "error": f"Project {projectId} not found"},
        )

    if github._use_provider_api(projectId):
        try:
            provider = github._get_project_provider(projectId)
            from runners.github.providers.protocol import ReviewData

            review = ReviewData(
                pr_number=prNumber,
                event="approve",
                body=request.body if request else "Approved",
            )
            await provider.post_review(prNumber, review)
            return {"success": True}
        except Exception as e:
            return JSONResponse(
                status_code=500, content={"success": False, "error": str(e)}
            )

    from ..services.pr_data_service import get_pr_data_service

    service = get_pr_data_service()
    body = request.body if request else ""
    result = service.approve_pr(project_path, prNumber, body=body)

    if not result["success"]:
        return JSONResponse(status_code=500, content=result)

    return result


@router.post("/prs/{prNumber}/merge")
async def merge_pr(
    projectId: str,
    prNumber: int,
    request: MergePRRequest | None = None,
):
    """Merge a PR with configurable merge method (merge/squash/rebase)."""
    project_path = github._resolve_project_path(projectId)
    if not project_path:
        return JSONResponse(
            status_code=404,
            content={"success": False, "error": f"Project {projectId} not found"},
        )

    if github._use_provider_api(projectId):
        try:
            provider = github._get_project_provider(projectId)
            merge_method = request.mergeMethod if request else "squash"
            success = await provider.merge_pr(prNumber, merge_method=merge_method)
            if success:
                return {"success": True}
            else:
                return JSONResponse(
                    status_code=500,
                    content={"success": False, "error": "Failed to merge PR"},
                )
        except Exception as e:
            return JSONResponse(
                status_code=500, content={"success": False, "error": str(e)}
            )

    from ..services.pr_data_service import get_pr_data_service

    service = get_pr_data_service()
    merge_method = request.mergeMethod if request else "squash"
    result = service.merge_pr(project_path, prNumber, method=merge_method)

    if not result["success"]:
        error_msg = result.get("error", "")
        status_code = 400 if "Invalid merge method" in error_msg else 500
        return JSONResponse(status_code=status_code, content=result)

    return result


@router.post("/prs/{prNumber}/assign")
async def assign_pr(
    projectId: str,
    prNumber: int,
    request: AssignPRRequest,
):
    """Assign a user to a PR via gh pr edit --add-assignee."""
    project_path = github._resolve_project_path(projectId)
    if not project_path:
        return JSONResponse(
            status_code=404,
            content={"success": False, "error": f"Project {projectId} not found"},
        )

    from ..services.pr_data_service import get_pr_data_service

    service = get_pr_data_service()
    result = service.assign_pr(project_path, prNumber, request.username)

    if not result["success"]:
        error_msg = result.get("error", "")
        status_code = 400 if "cannot be empty" in error_msg else 500
        return JSONResponse(status_code=status_code, content=result)

    return result


@router.post("/prs/{prNumber}/cancel")
async def cancel_pr_review(
    projectId: str,
    prNumber: int,
):
    """Cancel an ongoing PR review process."""
    project_path = github._resolve_project_path(projectId)
    if not project_path:
        return JSONResponse(
            status_code=404,
            content={"success": False, "error": f"Project {projectId} not found"},
        )

    from ..services.pr_review_service import get_pr_review_service

    service = get_pr_review_service()

    if not service.is_running(projectId, prNumber):
        return {
            "success": True,
            "data": {"cancelled": False, "reason": "No review is running"},
        }

    cancelled = await service.cancel_review(projectId, prNumber)

    if not cancelled:
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": "Failed to cancel review"},
        )

    return {"success": True, "data": {"cancelled": True}}


@router.get("/prs/{prNumber}/new-commits")
async def check_pr_new_commits(projectId: str, prNumber: int):
    """Check if there are new commits since the last review.

    Compares the reviewed_commit_sha stored in the review result JSON against
    the current HEAD SHA of the PR (fetched via gh pr view).

    Returns NewCommitsCheck: hasNewCommits, newCommitCount,
    lastReviewedCommit, currentHeadCommit.
    """
    project_path = github._resolve_project_path(projectId)
    if not project_path:
        return JSONResponse(
            status_code=404,
            content={"success": False, "error": f"Project {projectId} not found"},
        )

    from ..services.pr_data_service import get_pr_data_service

    service = get_pr_data_service()
    return service.check_new_commits(project_path, prNumber)


@router.get("/prs/{prNumber}/logs")
async def get_pr_review_logs(projectId: str, prNumber: int):
    """Get PR review execution logs.

    Reads phase-level review logs from the project's
    .tfactory/github/pr/review_{prNumber}_logs.json file.

    Returns PRLogs data with per-phase timing and entries, or null
    if no logs are available.
    """
    project_path = github._resolve_project_path(projectId)
    if not project_path:
        return JSONResponse(
            status_code=404,
            content={"success": False, "error": f"Project {projectId} not found"},
        )

    from ..services.pr_data_service import get_pr_data_service

    service = get_pr_data_service()
    result = service.get_review_logs(project_path, prNumber)

    if not result["success"]:
        return JSONResponse(
            status_code=500,
            content=result,
        )
    return result
