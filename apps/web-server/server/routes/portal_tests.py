"""Portal-UI test routes (#553) — list portals + dispatch a portal-ui Job.

The portal-ui capability exercises a deployed portal behind Keycloak MFA and
publishes its findings into the Visual Inspection store, so results surface in
the existing "Visual Reports" tab (GET /api/visual-inspections). These routes
expose the portals and a dispatch trigger.
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

# portal_testing lives at the repo root (a nix-runtime harness, deliberately out
# of the strict backend package).
_ROOT = Path(__file__).resolve().parents[4]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from portal_testing import config as portal_config  # noqa: E402
from portal_testing.dispatch import dispatch_portal_ui  # noqa: E402

router = APIRouter(prefix="/api/portal-tests", tags=["Portal UI tests"])


class DispatchResult(BaseModel):
    portal: str
    run_id: str
    job: str | None = None
    dispatched: bool
    detail: str


@router.get("/portals", summary="List the portals the portal-ui capability covers")
def list_portals() -> dict:
    return {
        "capability": "portal-ui",
        "portals": [
            {"key": p.key, "name": p.name, "url": p.url, "oauth2_proxy": p.oauth2_proxy}
            for p in portal_config.PORTALS.values()
        ],
    }


@router.post("/{portal}/dispatch", summary="Dispatch a portal-ui test as a k8s Job")
def dispatch(portal: str, run_id: str) -> DispatchResult:
    if portal not in portal_config.PORTALS:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"unknown portal {portal!r}"
        )
    try:
        job = dispatch_portal_ui(portal, run_id)
    except RuntimeError as e:
        # Not in a cluster (e.g. local dev): the capability is still runnable via
        # `nix develop ./portal_testing --command python -m portal_testing.run`.
        return DispatchResult(
            portal=portal,
            run_id=run_id,
            dispatched=False,
            detail=f"k8s dispatch unavailable: {e}. Run locally via the portal_testing flake.",
        )
    return DispatchResult(
        portal=portal,
        run_id=run_id,
        job=job,
        dispatched=True,
        detail="portal-ui Job submitted; results will appear under /api/visual-inspections",
    )
