"""Map a contract's RFC-0013 ``deployment`` block onto a required-lane policy (#447).

RFC-0013 makes planning deployment-aware: when a change is high-risk or touches
production, TFactory must prove the deploy *would* work (DRY-RUN) before the work
is allowed to merge. This module reads the (optional, additive) ``deployment``
block and decides whether the ``deploy`` lane (tools.runners.deploy_runner) must
be in the *required* lane set, and whether that requirement feeds the merge
policy / handback as a blocking gate.

The rule (RFC-0013 §3/§6):

  - ``risk_class == "high"`` OR ``production_classification == "production"``
        => the ``deploy`` lane is REQUIRED, and a missing/failed deploy
           verification BLOCKS merge (the change must not auto-merge).
  - otherwise (low/medium, non-prod, or no deployment block at all)
        => the deploy lane is NOT forced; existing behaviour is unchanged.

This is **pure** and **additive**: an absent ``deployment`` block yields
``DeployRequirement(required=False, ...)`` so old contracts behave exactly as
before (back-compat). It never lowers anything the contract already declared — it
only raises the floor by adding the ``deploy`` lane when the risk warrants it.

Honesty (RFC-0013 §4): a high-risk change whose DORA context is
``available: false`` is treated as UNKNOWN delivery health, never healthy — the
requirement is never relaxed on the strength of missing metrics.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "DEPLOY_LANE",
    "DeployRequirement",
    "deploy_gate_for_spec",
    "deploy_requirement_from_contract",
    "deployment_block_from_contract",
    "evaluate_deploy_gate",
    "read_deploy_verification",
]

DEPLOY_LANE = "deploy"

_HIGH_RISK = "high"
_MEDIUM_RISK = "medium"
_PRODUCTION = "production"


@dataclass(frozen=True)
class DeployRequirement:
    """Whether the deploy lane is required, and why."""

    required: bool
    risk_class: str | None = None
    production_classification: str | None = None
    reasons: tuple[str, ...] = field(default_factory=tuple)
    # The DRY-RUN target level the deploy lane should aim for. Capped at the
    # dry-run ceiling — production apply (VAL-4) is never autonomous (RFC-0013).
    target_level: str = "VAL-2"

    def lanes(self, existing: tuple[str, ...] = ()) -> tuple[str, ...]:
        """Return ``existing`` with the ``deploy`` lane appended iff required.

        Ordered + de-duplicated; never removes a lane the caller already had.
        """
        lanes = list(existing)
        if self.required and DEPLOY_LANE not in lanes:
            lanes.append(DEPLOY_LANE)
        return tuple(lanes)


def deployment_block_from_contract(contract: dict | None) -> dict | None:
    """Return the contract's ``deployment`` block, or ``None`` if absent.

    Tolerant: a non-dict contract or a non-dict ``deployment`` value yields
    ``None`` so callers fall back to their prior behaviour (back-compat).
    """
    if not isinstance(contract, dict):
        return None
    block = contract.get("deployment")
    return block if isinstance(block, dict) else None


def _norm(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    v = value.strip().lower()
    return v or None


def deploy_requirement_from_contract(contract: dict | None) -> DeployRequirement:
    """Derive the deploy-lane requirement from a contract's ``deployment`` block.

    Returns ``required=True`` when any of the following hold (RFC-0013 §3):
      * ``risk_class == "high"``   — highest structural risk;
      * ``risk_class == "medium"`` — intermediate risk, still warrants a dry-run
                                      proof before merge (additive; was not_run before);
      * ``production_classification == "production"`` — touches prod.

    An absent deployment block, or a ``risk_class == "low"`` non-prod one, yields
    ``required=False``. The dry-run production guard (``assert_dry_run`` /
    ``ProductionApplyError``) lives in ``deploy_runner``, NOT here, so widening
    the trigger cannot cause an effectful apply.
    """
    block = deployment_block_from_contract(contract)
    if block is None:
        return DeployRequirement(required=False)

    risk = _norm(block.get("risk_class"))
    prod = _norm(block.get("production_classification"))

    reasons: list[str] = []
    if risk == _HIGH_RISK:
        reasons.append("risk_class=high")
    if risk == _MEDIUM_RISK:
        reasons.append("risk_class=medium")
    if prod == _PRODUCTION:
        reasons.append("production_classification=production")

    required = bool(reasons)
    return DeployRequirement(
        required=required,
        risk_class=risk,
        production_classification=prod,
        reasons=tuple(reasons),
    )


def evaluate_deploy_gate(
    contract: dict | None, deploy_verification: dict | None
) -> dict:
    """Decide whether the deploy gate blocks merge, for the merge policy/handback.

    Given the contract and the deploy lane's (gate-normalized) ``verification``
    block, returns a small verdict the merge policy consumes::

        {
          "required": bool,        # was the deploy lane required at all?
          "blocks_merge": bool,    # must this change be held back?
          "reason": str,           # human-readable explanation
          "achieved_level": str,   # what the deploy lane actually proved
        }

    Policy:
      - Not required           -> never blocks (back-compat / low-risk).
      - Required + no verify    -> BLOCKS (a high-risk change with no deploy proof).
      - Required + achieved below the target dry-run level -> BLOCKS.
      - Required + dry-run achieved (>= target) -> does NOT block on its own, but
        production_classification=production still carries the human-approval gate
        upstream (RFC-0013 §6) — surfaced via ``human_approval_required``.
    """
    req = deploy_requirement_from_contract(contract)
    if not req.required:
        return {
            "required": False,
            "blocks_merge": False,
            "reason": "deploy lane not required (no high-risk/production deployment)",
            "achieved_level": None,
            "human_approval_required": False,
        }

    human_approval = req.production_classification == _PRODUCTION

    if not isinstance(deploy_verification, dict) or not deploy_verification.get(
        "levels"
    ):
        return {
            "required": True,
            "blocks_merge": True,
            "reason": (
                "deploy lane required ("
                + ", ".join(req.reasons)
                + ") but no deploy verification was produced — DRY-RUN deploy "
                "proof is missing"
            ),
            "achieved_level": "VAL-0",
            "human_approval_required": human_approval,
        }

    achieved = str(deploy_verification.get("achieved_level") or "VAL-0")
    target = req.target_level
    blocks = _below(achieved, target)
    if blocks:
        reason = (
            f"deploy lane required ({', '.join(req.reasons)}); deploy verification "
            f"achieved {achieved} but the DRY-RUN floor is {target} — held back"
        )
    else:
        reason = (
            f"deploy lane required ({', '.join(req.reasons)}); DRY-RUN deploy "
            f"verification reached {achieved}"
        )
        if human_approval:
            reason += " — production apply still requires human-approval (RFC-0013)"
    return {
        "required": True,
        "blocks_merge": blocks,
        "reason": reason,
        "achieved_level": achieved,
        "human_approval_required": human_approval,
    }


def read_deploy_verification(spec_dir: Path | str) -> dict | None:
    """Read the deploy lane's verification block from ``findings/``, or None.

    The deploy lane (tools.runners.deploy_runner) persists its gate-normalized
    block to ``findings/deploy_verification.json``. Best-effort: a missing or
    unreadable file yields ``None`` (no deploy proof) — never raises.
    """
    try:
        path = Path(spec_dir) / "findings" / "deploy_verification.json"
        if path.exists():
            doc = json.loads(path.read_text())
            return doc if isinstance(doc, dict) else None
    except (OSError, ValueError):
        return None
    return None


def deploy_gate_for_spec(contract: dict | None, spec_dir: Path | str) -> dict:
    """Convenience: evaluate the deploy gate for a spec workspace.

    Reads the deploy verification from ``findings/`` and runs
    :func:`evaluate_deploy_gate`. The completion envelope / merge policy use this
    to learn whether a high-risk/production change has its DRY-RUN deploy proof.
    """
    return evaluate_deploy_gate(contract, read_deploy_verification(spec_dir))


_LADDER = ("VAL-0", "VAL-1", "VAL-2", "VAL-3", "VAL-4")


def _below(achieved: str, target: str) -> bool:
    """True when ``achieved`` is strictly below ``target`` on the VAL ladder."""
    try:
        return _LADDER.index(achieved) < _LADDER.index(target)
    except ValueError:
        # Unknown level => treat conservatively as below (block).
        return True
