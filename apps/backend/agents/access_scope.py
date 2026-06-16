"""RFC-0007 (#87): map the contract ``access`` block to what TFactory needs.

PFactory emits an ``access`` block on the task contract (RFC-0007): per-resource
auth class, a broker credential ref, and whether it has been human-curated. This
pure mapper turns that into TFactory's runtime needs:

  - ``needs_egress``     — any usable (curated) external resource implies network,
                           so the egress gate must be enabled for those lanes;
  - ``credential_refs``  — the broker refs (``env:``/``store:``/``vault:``) to
                           resolve for the curated resources (never the secrets);
  - ``ready``            — resources curated and usable;
  - ``blocked``          — resources that are NOT curated (or class D): TFactory
                           must NOT attempt the credentialed (VAL-3) lane for them
                           and must report them honestly (RFC-0006 — a skipped
                           level is never green).

Pure + dependency-free. Wiring it into the run/egress path is a separate PR.
"""

from __future__ import annotations


def map_access_for_tfactory(access_block: dict | None) -> dict:
    """Map a contract ``access`` block to TFactory's runtime needs (see module doc)."""
    requirements = (access_block or {}).get("requirements") or []
    ready: list[str] = []
    blocked: list[dict] = []
    credential_refs: list[str] = []

    for req in requirements:
        resource = req.get("resource", "unknown")
        if req.get("curated"):
            ready.append(resource)
            ref = req.get("credential_ref")
            if ref:
                credential_refs.append(ref)
        elif req.get("auth_class") == "D-un-automatable":
            blocked.append(
                {
                    "resource": resource,
                    "reason": req.get("mvp_note")
                    or "un-automatable (interactive MFA); human-driven",
                }
            )
        else:
            blocked.append(
                {
                    "resource": resource,
                    "reason": "access not curated (needs human approval / credential)",
                }
            )

    return {
        "needs_egress": bool(ready),
        "credential_refs": sorted(set(credential_refs)),
        "ready": ready,
        "blocked": blocked,
    }


def val3_blocked(mapping: dict) -> bool:
    """True when some access requirement is un-curated/un-automatable — so the
    credentialed (VAL-3) lane cannot honestly run for the whole task."""
    return bool(mapping.get("blocked"))
