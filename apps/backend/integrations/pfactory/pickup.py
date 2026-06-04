"""PFactory governed test-target pickup (#195, epic #193).

Recognises the testing work PFactory hands off to TFactory and turns it into a
normalized test-generation target. This is the RECOGNITION + ENQUEUE gate only:

  - #195 (this module) — recognise ``pfactory`` + ``handoff:tfactory`` issues /
    ``requirements.json`` and enqueue them as governed test targets.
  - #196 — parse the ``pfactory:meta`` block as the full test oracle.
  - #197 — generate + run + report back.

Tag taxonomy v1 lives in the PFactory repo ``docs/tag-taxonomy.md``. The labels
are the "secret language": PFactory writes them on dual approval (AI gates pass
*and* a human approves); TFactory reads them. Nothing is picked up
automatically — an issue must carry the governed marker *and* be routed to us.

Label notes from the contract (§6.3): TFactory has no ``sev:*`` and uses a
horizon scheme (``now``/``next``/``later``) rather than ``p0..p3``; it maps the
PFactory ``priority:p*`` onto its horizons here.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

# ─── Taxonomy v1 labels (PFactory writes; TFactory reads) ───────────────
LABEL_PFACTORY = "pfactory"  # mandatory governed marker — on every PFactory issue
LABEL_HANDOFF_TFACTORY = "handoff:tfactory"  # routed to TFactory for tests
LABEL_HANDOFF_AIFACTORY = "handoff:aifactory"  # routed to AIFactory (build + test)
LABEL_TYPE_TESTING = "type:testing"  # typical on testing children
LABEL_EPIC = "epic"  # marker on the parent epic issue

# PFactory p0..p3 → TFactory now/next/later horizons (contract §6.3).
_PRIORITY_HORIZON: dict[str, str] = {
    "p0": "now",
    "p1": "next",
    "p2": "later",
    "p3": "later",
}


def priority_to_horizon(priority: str | None) -> str | None:
    """Map a PFactory ``priority:p*`` value onto a TFactory horizon.

    Returns ``None`` for an unknown/absent priority.
    """
    if not priority:
        return None
    return _PRIORITY_HORIZON.get(priority.strip().lower())


@dataclass(frozen=True)
class PickupDecision:
    """The outcome of classifying an issue / requirements.json for pickup.

    ``picked_up`` is the gate: ``True`` only for a governed PFactory issue that
    is routed to TFactory. The remaining fields describe the target for the
    downstream enqueue (#196/#197 consume them).
    """

    picked_up: bool
    reason: str
    source: str = ""  # "issue" | "requirements"
    is_epic: bool = False
    also_aifactory: bool = False  # child also routed to AIFactory (build + test)
    priority: str | None = None  # raw PFactory priority (p0..p3)
    horizon: str | None = None  # mapped TFactory horizon (now/next/later)
    plan_id: str | None = None
    issue_number: int | None = None
    taxonomy_version: str | None = None
    labels: tuple[str, ...] = field(default_factory=tuple)


def _norm_labels(labels: Iterable[Any]) -> set[str]:
    """Lower-case, strip, and drop non-strings from a label collection."""
    return {lbl.strip().lower() for lbl in (labels or ()) if isinstance(lbl, str)}


def _priority_from_labels(norm_labels: set[str]) -> str | None:
    """Extract the ``priority:p*`` value from a normalized label set."""
    for lbl in norm_labels:
        if lbl.startswith("priority:"):
            return lbl.split(":", 1)[1] or None
    return None


def classify_labels(
    labels: Iterable[Any],
    *,
    source: str = "issue",
    plan_id: str | None = None,
    issue_number: int | None = None,
    taxonomy_version: str | None = None,
) -> PickupDecision:
    """Core gate: is this label set a governed TFactory test target?

    Requires BOTH ``pfactory`` (governed) and ``handoff:tfactory`` (routed to
    us). ``type:testing`` is typical but not required — per the contract
    ``handoff:tfactory`` is carried by ``type:testing`` children *and* by any
    child whose acceptance criteria need an independent test pass.
    """
    norm = _norm_labels(labels)
    label_tuple = tuple(sorted(norm))

    if LABEL_PFACTORY not in norm:
        return PickupDecision(
            False,
            "not a PFactory issue (no 'pfactory' label) — left to existing behaviour",
            source=source,
            issue_number=issue_number,
            labels=label_tuple,
        )
    if LABEL_HANDOFF_TFACTORY not in norm:
        return PickupDecision(
            False,
            "PFactory issue not routed to TFactory (no 'handoff:tfactory')",
            source=source,
            plan_id=plan_id,
            issue_number=issue_number,
            taxonomy_version=taxonomy_version,
            labels=label_tuple,
        )

    priority = _priority_from_labels(norm)
    return PickupDecision(
        picked_up=True,
        reason="governed PFactory test target",
        source=source,
        is_epic=LABEL_EPIC in norm,
        also_aifactory=LABEL_HANDOFF_AIFACTORY in norm,
        priority=priority,
        horizon=priority_to_horizon(priority),
        plan_id=plan_id,
        issue_number=issue_number,
        taxonomy_version=taxonomy_version,
        labels=label_tuple,
    )


def _get(obj: Any, *names: str, default: Any = None) -> Any:
    """Read the first present attribute/key from an object or dict."""
    for name in names:
        if isinstance(obj, dict):
            if name in obj:
                return obj[name]
        elif hasattr(obj, name):
            return getattr(obj, name)
    return default


def classify_issue(issue: Any) -> PickupDecision:
    """Classify a GitHub issue (an ``IssueData``-like object or a dict).

    Reads ``labels`` and ``number``; the taxonomy version, if present in the
    ``pfactory:meta`` block of the body, is surfaced for downstream branching
    (full meta parsing is #196).
    """
    labels = _get(issue, "labels", default=[]) or []
    number = _get(issue, "number")
    body = _get(issue, "body", default="") or ""
    taxonomy_version = _taxonomy_version_from_body(body)
    return classify_labels(
        labels,
        source="issue",
        issue_number=number if isinstance(number, int) else None,
        taxonomy_version=taxonomy_version,
    )


def _taxonomy_version_from_body(body: str) -> str | None:
    """Best-effort ``taxonomy: v<N>`` lookup inside a pfactory:meta block.

    A lightweight scan — #196 owns full ``pfactory:meta`` parsing. Returns the
    version string (e.g. ``"v1"``) or ``None``.
    """
    for line in (body or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("taxonomy:"):
            value = stripped.split(":", 1)[1].strip()
            if value:
                return value
    return None


def classify_requirements(requirements: dict) -> PickupDecision:
    """Classify a PFactory-written ``requirements.json`` for TFactory pickup.

    The firm routing path is GitHub labels; ``requirements.json`` is primarily
    the AIFactory handoff. We pick it up for TFactory only when its ``metadata``
    explicitly signals TFactory routing, via either:

      - ``metadata.labels``  — a mirrored label list (reuses ``classify_labels``),
      - ``metadata.handoffs`` / ``metadata.handoff`` — a list/str naming
        ``"tfactory"`` (requires a ``pfactory`` governance marker too).

    Anything else returns ``picked_up=False`` so AIFactory-only requirements
    files are left untouched.
    """
    meta = (requirements or {}).get("metadata") or {}
    plan_id = meta.get("plan_id") or meta.get("planId")
    issue_number = meta.get("githubIssueNumber") or meta.get("github_issue_number")
    issue_number = issue_number if isinstance(issue_number, int) else None
    taxonomy_version = meta.get("taxonomy")

    # Path A: a mirrored label list — reuse the label gate verbatim.
    if isinstance(meta.get("labels"), (list, tuple)):
        return classify_labels(
            meta["labels"],
            source="requirements",
            plan_id=plan_id,
            issue_number=issue_number,
            taxonomy_version=taxonomy_version,
        )

    # Path B: explicit handoff routing + a governance marker.
    handoffs = meta.get("handoffs")
    if handoffs is None and meta.get("handoff") is not None:
        handoffs = meta["handoff"]
    norm_handoffs = _norm_labels(
        handoffs if isinstance(handoffs, (list, tuple)) else [handoffs]
    )
    governed = bool(meta.get("pfactory")) or taxonomy_version is not None

    if not governed:
        return PickupDecision(
            False,
            "requirements.json carries no PFactory governance marker",
            source="requirements",
            plan_id=plan_id,
            issue_number=issue_number,
        )
    if "tfactory" not in norm_handoffs:
        return PickupDecision(
            False,
            "requirements.json not routed to TFactory (no 'tfactory' handoff)",
            source="requirements",
            plan_id=plan_id,
            issue_number=issue_number,
            taxonomy_version=taxonomy_version,
        )

    priority = meta.get("priority")
    return PickupDecision(
        picked_up=True,
        reason="governed PFactory test target (requirements.json)",
        source="requirements",
        also_aifactory="aifactory" in norm_handoffs,
        priority=priority,
        horizon=priority_to_horizon(priority),
        plan_id=plan_id,
        issue_number=issue_number,
        taxonomy_version=taxonomy_version,
    )


def _normalize_target(decision: PickupDecision, *, title: str, body: str) -> dict:
    """Build the normalized test-target record the enqueue callback receives."""
    return {
        "source": decision.source,
        "plan_id": decision.plan_id,
        "issue_number": decision.issue_number,
        "title": title,
        "body": body,
        "priority": decision.priority,
        "horizon": decision.horizon,
        "is_epic": decision.is_epic,
        "also_aifactory": decision.also_aifactory,
        "taxonomy_version": decision.taxonomy_version,
        "labels": list(decision.labels),
    }


def pickup_issue(
    issue: Any,
    *,
    enqueue: Callable[[dict], Any] | None = None,
) -> PickupDecision:
    """Recognise a GitHub issue and, if governed for TFactory, enqueue it.

    When ``picked_up`` and an ``enqueue`` callback is supplied, the callback is
    invoked once with the normalized target record (the #197 generation flow
    supplies the real enqueue — e.g. ``task_create_and_run``). Non-PFactory or
    non-TFactory issues are left untouched (callback not invoked).
    """
    decision = classify_issue(issue)
    if decision.picked_up and enqueue is not None:
        enqueue(
            _normalize_target(
                decision,
                title=_get(issue, "title", default="") or "",
                body=_get(issue, "body", default="") or "",
            )
        )
    return decision


def pickup_requirements(
    requirements: dict,
    *,
    enqueue: Callable[[dict], Any] | None = None,
) -> PickupDecision:
    """Recognise a ``requirements.json`` and, if governed for TFactory, enqueue it."""
    decision = classify_requirements(requirements)
    if decision.picked_up and enqueue is not None:
        enqueue(
            _normalize_target(
                decision,
                title=(requirements or {}).get("title", "") or "",
                body=(requirements or {}).get("description", "") or "",
            )
        )
    return decision


def _decision_dict(decision: PickupDecision) -> dict:
    """Render a PickupDecision as a plain dict for CLI / JSON output."""
    return {
        "picked_up": decision.picked_up,
        "reason": decision.reason,
        "source": decision.source,
        "is_epic": decision.is_epic,
        "also_aifactory": decision.also_aifactory,
        "priority": decision.priority,
        "horizon": decision.horizon,
        "plan_id": decision.plan_id,
        "issue_number": decision.issue_number,
        "taxonomy_version": decision.taxonomy_version,
        "labels": list(decision.labels),
    }


def _main(argv: list[str] | None = None) -> int:
    """CLI: classify a GitHub issue or requirements.json for TFactory pickup.

        python -m integrations.pfactory --issue issue.json
        gh issue view 412 --json number,title,body,labels | \
            python -m integrations.pfactory --issue -
        python -m integrations.pfactory --requirements requirements.json

    Prints the decision as JSON. Exit 0 when picked up, 1 when not, 2 on usage
    error — so a shell can branch on whether to enqueue.
    """
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(prog="pfactory-pickup")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--issue", help="GitHub issue JSON file ('-' for stdin)")
    group.add_argument("--requirements", help="requirements.json file ('-' for stdin)")
    args = parser.parse_args(argv)

    path = args.issue or args.requirements
    raw = sys.stdin.read() if path == "-" else open(path, encoding="utf-8").read()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"pfactory-pickup: invalid JSON: {exc}", file=sys.stderr)
        return 2

    # `gh issue view --json labels` returns [{"name": "..."}]; normalize to names.
    if args.issue and isinstance(payload.get("labels"), list):
        payload["labels"] = [
            lbl.get("name") if isinstance(lbl, dict) else lbl
            for lbl in payload["labels"]
        ]

    decision = classify_issue(payload) if args.issue else classify_requirements(payload)
    print(json.dumps(_decision_dict(decision), indent=2))
    return 0 if decision.picked_up else 1


if __name__ == "__main__":
    raise SystemExit(_main())
