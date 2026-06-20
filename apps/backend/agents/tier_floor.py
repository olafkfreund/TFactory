"""Map a contract's ``execution.autonomy_tier`` onto a VAL floor + lane set (RFC-0011).

RFC-0011 (label-driven intake & difficulty tiers) classifies incoming work into
three difficulty tiers — ``low`` / ``medium`` / ``hard`` — and each tier drives a
*minimum* verification rigor TFactory must reach:

    | tier   | VAL floor | lanes                                              |
    |--------|-----------|----------------------------------------------------|
    | low    | VAL-1     | unit (+api when detected)                          |
    | medium | VAL-2     | unit, api, integration                             |
    | hard   | VAL-3     | unit, api, integration, mutation                   |

When ``change_mode == "migration"`` (a rewrite, per RFC-0010) the ``equivalence``
lane is forced on top of the tier's lanes regardless of tier — a rewrite must be
proven behaviour-equivalent to the legacy reference.

This module is **pure** — given a tier (and optional change_mode) it returns the
floor and the required lane set. It is *additive*: when ``autonomy_tier`` is
absent/unknown, callers fall back to their prior behaviour (the default
``DEFAULT_TARGET_LEVEL`` / the contract's own ``tfactory.lanes``), so existing
contracts are unaffected (back-compat).

The tier raises the *floor* and *adds* lanes; it never lowers what the contract
already declared. Never-overclaim still lives in
``Factory/scripts/verification_gate.py`` (vendored as
``agents/verification_gate.py``): the floor is a *target*, and the gate recomputes
``achieved_level`` from what truly ran — a higher floor can never fake a result.
"""

from __future__ import annotations

__all__ = [
    "EQUIVALENCE_LANE",
    "MIGRATION_MODE",
    "change_mode_from_contract",
    "lanes_for",
    "tier_from_contract",
    "val_floor_for",
]

EQUIVALENCE_LANE = "equivalence"
MIGRATION_MODE = "migration"

# tier -> (VAL floor, ordered required lanes). "low" keeps api optional (added by
# the planner/contract when an API surface is detected), so its required set is
# just unit; the policy table's "(+api)" is a may-add, not a must-have.
_TIER_FLOOR: dict[str, str] = {
    "low": "VAL-1",
    "medium": "VAL-2",
    "hard": "VAL-3",
}

_TIER_LANES: dict[str, tuple[str, ...]] = {
    "low": ("unit",),
    "medium": ("unit", "api", "integration"),
    "hard": ("unit", "api", "integration", "mutation"),
}


def _normalize(tier: object) -> str | None:
    """Lower-case a tier string; return None for absent/unknown values."""
    if not isinstance(tier, str):
        return None
    t = tier.strip().lower()
    return t if t in _TIER_FLOOR else None


def val_floor_for(tier: object) -> str | None:
    """Return the minimum VAL target level for ``tier`` (e.g. ``"VAL-2"``).

    Returns ``None`` when the tier is absent or unrecognised — the signal for the
    caller to keep its existing default target level (back-compat).
    """
    t = _normalize(tier)
    return _TIER_FLOOR.get(t) if t else None


def lanes_for(tier: object, change_mode: object = None) -> tuple[str, ...]:
    """Return the required lane set for ``tier``, forcing ``equivalence`` on migration.

    ``change_mode == "migration"`` (RFC-0010 rewrite) appends the ``equivalence``
    lane regardless of tier. An absent/unknown tier yields no required lanes
    (``()``) *unless* it is a migration, in which case ``equivalence`` is still
    forced — a rewrite must always prove parity. The returned tuple is ordered
    and de-duplicated.
    """
    t = _normalize(tier)
    lanes: list[str] = list(_TIER_LANES.get(t, ())) if t else []
    if isinstance(change_mode, str) and change_mode.strip().lower() == MIGRATION_MODE:
        if EQUIVALENCE_LANE not in lanes:
            lanes.append(EQUIVALENCE_LANE)
    return tuple(lanes)


def tier_from_contract(contract: dict | None) -> str | None:
    """Read ``execution.autonomy_tier`` from a contract dict (normalized) or None.

    Tolerant: a missing ``execution`` block or a non-string/unknown tier yields
    ``None`` so callers fall back to their prior behaviour.
    """
    if not isinstance(contract, dict):
        return None
    execution = contract.get("execution")
    if not isinstance(execution, dict):
        return None
    return _normalize(execution.get("autonomy_tier"))


def change_mode_from_contract(contract: dict | None) -> str | None:
    """Derive the change mode (e.g. ``"migration"``) from a contract dict.

    Prefers an explicit ``change_mode`` field; falls back to ``workflow_type ==
    "migration"`` (RFC-0010's rewrite signal). Returns ``None`` when neither is a
    migration — the signal to not force the ``equivalence`` lane.
    """
    if not isinstance(contract, dict):
        return None
    explicit = contract.get("change_mode")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip().lower()
    workflow = contract.get("workflow_type")
    if isinstance(workflow, str) and workflow.strip().lower() == MIGRATION_MODE:
        return MIGRATION_MODE
    return None
