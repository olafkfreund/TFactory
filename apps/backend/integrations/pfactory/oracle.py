"""PFactory metadata → test oracle (#196, epic #193).

Parses the ``pfactory:meta`` block PFactory appends to every governed issue body
(mirrored into ``.aifactory/specs/<plan_id>/requirements.json`` → ``metadata``)
into a structured **test oracle**: the acceptance criteria + ``citations[]`` the
generated tests must assert against, plus the priority mapped onto a TFactory
horizon.

This is the parse step of the pickup contract:
  - #195 — recognise + enqueue governed targets.
  - #196 (this module) — parse ``pfactory:meta`` as the oracle; map priority.
  - #197 — generate + run + report back.

The block (exact emitter shape, PFactory ``plan/emit/labels.py``)::

    <!-- pfactory:meta
    plan_id: 001-orders-platform
    plan_type: infra-change
    category: infra
    priority: p1
    risk: medium
    access_verified: true
    citations:
      - why: "A networked service needs auth."
        uri: "https://owasp.org/..."
        source: "owasp"
    taxonomy: v1
    -->

It is valid YAML, so we parse the inner text with ``yaml.safe_load`` and degrade
to ``{}`` on any malformation (tolerant of a missing/old ``taxonomy``).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from .pickup import priority_to_horizon

_META_OPEN = "<!-- pfactory:meta"
_META_CLOSE = "-->"


@dataclass(frozen=True)
class Citation:
    """A source backing a requested change (the "why" behind a criterion)."""

    why: str
    uri: str | None = None
    source: str | None = None


@dataclass(frozen=True)
class PFactoryOracle:
    """The structured test oracle the generator asserts against (#196)."""

    plan_id: str | None
    plan_type: str | None
    category: str | None
    priority: str | None  # raw PFactory p0..p3
    horizon: str | None  # mapped TFactory now/next/later
    risk: str | None
    access_verified: bool | None
    taxonomy_version: str | None
    citations: tuple[Citation, ...] = field(default_factory=tuple)
    acceptance_criteria: tuple[str, ...] = field(default_factory=tuple)
    cost_monthly_usd: float | None = None
    effort_points: int | None = None
    raw_meta: dict = field(default_factory=dict)


def extract_meta_block(body: str | None) -> str | None:
    """Return the YAML text inside ``<!-- pfactory:meta ... -->``, or ``None``."""
    if not body or _META_OPEN not in body:
        return None
    inner = body.split(_META_OPEN, 1)[1].split(_META_CLOSE, 1)[0]
    return inner.strip("\n")


def parse_meta_block(body: str | None) -> dict:
    """Parse the ``pfactory:meta`` block into a dict (YAML).

    Returns ``{}`` when the block is absent or not parseable — callers degrade
    gracefully rather than failing on a malformed block.
    """
    block = extract_meta_block(body)
    if block is None:
        return {}
    import yaml

    try:
        data = yaml.safe_load(block)
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _strip_meta_block(body: str) -> str:
    """Remove the meta-block HTML comment so AC extraction sees only prose."""
    if not body or _META_OPEN not in body:
        return body or ""
    before = body.split(_META_OPEN, 1)[0]
    after_parts = body.split(_META_CLOSE, 1)
    after = after_parts[1] if len(after_parts) > 1 else ""
    return (before + after).strip()


def _criteria_from_text(text: str) -> tuple[str, ...]:
    """Extract acceptance criteria from free text via spec_sources (#40).

    Best-effort: a parse failure yields no criteria rather than raising.
    """
    if not text or not text.strip():
        return ()
    try:
        from spec_sources import ingest

        spec = ingest(text)
        return tuple(ac.text for ac in spec.criteria)
    except Exception:
        return ()


def _citations(raw: Any) -> tuple[Citation, ...]:
    out: list[Citation] = []
    for c in raw or []:
        if isinstance(c, dict) and c.get("why"):
            out.append(
                Citation(why=str(c["why"]), uri=c.get("uri"), source=c.get("source"))
            )
    return tuple(out)


def _as_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("true", "yes", "1"):
            return True
        if v in ("false", "no", "0"):
            return False
    return None


def build_oracle(
    *,
    issue_body: str | None = None,
    requirements: dict | None = None,
    acceptance_criteria: Sequence[str] | None = None,
) -> PFactoryOracle:
    """Build the test oracle from a PFactory issue body and/or requirements.json.

    Metadata source preference (per the contract): ``requirements.json``
    ``metadata`` when present, else the issue-body ``pfactory:meta`` block.

    Acceptance-criteria source preference: an explicit list, else the
    ``requirements.json`` ``description``, else the issue body (meta block
    stripped) — parsed via ``spec_sources``.
    """
    meta: dict = {}
    if requirements:
        meta = requirements.get("metadata") or {}
    if not meta and issue_body:
        meta = parse_meta_block(issue_body)

    if acceptance_criteria is not None:
        criteria = tuple(acceptance_criteria)
    else:
        if requirements and requirements.get("description"):
            text = requirements["description"]
        else:
            text = _strip_meta_block(issue_body or "")
        criteria = _criteria_from_text(text)

    priority = meta.get("priority")
    return PFactoryOracle(
        plan_id=meta.get("plan_id"),
        plan_type=meta.get("plan_type") or None,
        category=meta.get("category") or None,
        priority=priority,
        horizon=priority_to_horizon(priority),
        risk=meta.get("risk"),
        access_verified=_as_bool(meta.get("access_verified")),
        taxonomy_version=meta.get("taxonomy"),
        citations=_citations(meta.get("citations")),
        acceptance_criteria=criteria,
        cost_monthly_usd=_as_float(meta.get("cost_monthly_usd")),
        effort_points=_as_int(meta.get("effort_points")),
        raw_meta=meta if isinstance(meta, dict) else {},
    )


def oracle_to_dict(oracle: PFactoryOracle) -> dict:
    """Render an oracle as a plain dict (CLI / JSON / downstream handoff)."""
    return {
        "plan_id": oracle.plan_id,
        "plan_type": oracle.plan_type,
        "category": oracle.category,
        "priority": oracle.priority,
        "horizon": oracle.horizon,
        "risk": oracle.risk,
        "access_verified": oracle.access_verified,
        "taxonomy_version": oracle.taxonomy_version,
        "acceptance_criteria": list(oracle.acceptance_criteria),
        "citations": [
            {"why": c.why, "uri": c.uri, "source": c.source} for c in oracle.citations
        ],
        "cost_monthly_usd": oracle.cost_monthly_usd,
        "effort_points": oracle.effort_points,
    }
