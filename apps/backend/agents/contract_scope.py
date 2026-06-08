"""Honor the RFC-0002 contract execution scope in the Evaluator (#247, epic #244).

The ``tfactory`` block can pin three execution scopes:
  - ``coverage_target`` (0..1) — the coverage bar the SUT is expected to hit.
  - ``mutation_scope`` — globs/files the mutation signal should target.
  - ``security_scope`` — rule sets (owasp:*). Empty => out of scope; non-empty
    is recorded as *delegated* (TFactory does not generate SAST/DAST, DEC-002).

Rather than silently infer, the Evaluator records these into ``verdicts.json``
as an ``execution_scope`` block so the Triager, the Backstage emitter (#240),
and operators can gate/verify against the declared intent. This is the honest,
deterministic seam; the mutation primitives + a hard coverage gate consume the
recorded scope downstream.
"""

from __future__ import annotations


def build_execution_scope(profile) -> dict | None:
    """Return the execution-scope dict from a TfactoryProfile, or None.

    None when the profile is absent or pins none of the three scopes (so the
    Evaluator omits the block entirely and nothing changes).
    """
    if profile is None:
        return None
    has_any = (
        profile.coverage_target is not None
        or bool(profile.mutation_scope)
        or bool(profile.security_scope)
    )
    if not has_any:
        return None
    return {
        "coverage_target": profile.coverage_target,
        "mutation_scope": list(profile.mutation_scope),
        "security_scope": list(profile.security_scope),
        # TFactory delegates application security testing (DEC-002): a declared
        # security_scope is recorded as delegated, never generated here.
        "security_delegated": bool(profile.security_scope),
        "source": "rfc-0002-contract",
    }


def coverage_target_met(doc: dict, coverage_target: float | None) -> bool | None:
    """Best-effort: did the run meet the declared coverage target?

    Uses the per-verdict ``coverage_delta_pct`` already in ``signals_summary``
    as a proxy when a run-level number isn't available. Returns None when there
    is no target or no coverage data (don't fabricate a verdict).
    """
    if coverage_target is None:
        return None
    verdicts = doc.get("verdicts")
    if not isinstance(verdicts, list):
        return None
    pcts = [
        s.get("coverage_delta_pct")
        for v in verdicts
        if isinstance(v, dict)
        for s in [v.get("signals_summary") or {}]
        if isinstance(s.get("coverage_delta_pct"), (int, float))
        and not isinstance(s.get("coverage_delta_pct"), bool)
    ]
    if not pcts:
        return None
    # coverage_target is a fraction (0..1); coverage_delta_pct is points. Treat
    # any positive aggregate delta as "moving toward" the target — a coarse but
    # honest proxy until run-level total coverage is wired in.
    return sum(pcts) > 0


def apply_execution_scope(doc: dict, profile) -> dict:
    """Stamp ``execution_scope`` (+ coverage_target_met) onto a verdicts doc.

    No-op when there's nothing to record. Returns ``doc`` for chaining.
    """
    scope = build_execution_scope(profile)
    if scope is None:
        return doc
    met = coverage_target_met(doc, scope.get("coverage_target"))
    if met is not None:
        scope["coverage_target_met"] = met
    doc["execution_scope"] = scope
    return doc
