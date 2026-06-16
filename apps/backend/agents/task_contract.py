"""Read the RFC-0002 Task Contract ``tfactory`` block from the handover (#245).

PFactory computes a VERIFY profile, AIFactory carries it, TFactory consumes it:
when the handover includes a ``tfactory`` block, the Planner uses the declared
lanes/frameworks/endpoints instead of inferring them from changed files. Absent
=> TFactory infers as before (this module returns ``None`` and the caller falls
back).

The contract (RFC-0002, a superset of AIFactory's implementation_plan.json) is
vendored at ``contracts/task-contract-v2.schema.json``. This module reads the
block from the snapshotted context — in precedence order:
  1. ``context/task_contract.json`` (a dedicated contract drop, if present)
  2. ``context/aifactory_plan.json`` (the contract == plan superset)
  3. ``context/source.json`` → ``contract`` / ``task_contract`` key

It parses the consumed subset only (tolerant: unknown keys ignored), so a
schema bump that adds fields never breaks ingest.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from agents.access_scope import map_access_for_tfactory

# Lanes TFactory recognises from the contract. "security" is accepted but
# TFactory treats app SAST/DAST as out of scope (DEC-002) — it's recorded so the
# Planner can note the delegation, not generate security tests.
_KNOWN_LANES = frozenset(
    {"unit", "api", "browser", "integration", "security", "mutation"}
)


@dataclass(frozen=True)
class TfactoryProfile:
    """The consumed subset of the RFC-0002 ``tfactory`` block."""

    lanes: tuple[str, ...] = ()
    frameworks: dict[str, str] = field(default_factory=dict)  # lane -> framework
    endpoints: dict[str, str] = field(default_factory=dict)  # e.g. api_base_url
    docker_compose: str | None = None
    coverage_target: float | None = None  # 0..1
    mutation_scope: tuple[str, ...] = ()  # globs/files to mutate
    security_scope: tuple[str, ...] = ()  # owasp:* ; empty => out of scope
    ac_to_code_map: dict[str, tuple[str, ...]] = field(default_factory=dict)
    correlation_key: str | None = None
    # RFC-0007 (#87): mapped access requirements — see agents.access_scope.
    # {needs_egress, credential_refs, ready, blocked}.
    access: dict = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return not (
            self.lanes
            or self.frameworks
            or self.endpoints
            or self.docker_compose
            or self.coverage_target is not None
            or self.mutation_scope
            or self.security_scope
            or self.ac_to_code_map
            or self.access.get("ready")
            or self.access.get("blocked")
        )


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def read_task_contract(spec_dir: Path) -> dict | None:
    """Return the top-level RFC-0002 contract dict from the handover, or None.

    Looks at the precedence sources and returns the first dict that looks like a
    contract (has a ``tfactory`` block or a ``contract_version``).
    """
    ctx = Path(spec_dir) / "context"
    candidates = [
        ctx / "task_contract.json",
        ctx / "aifactory_plan.json",
    ]
    for path in candidates:
        doc = _read_json(path)
        if doc and ("tfactory" in doc or "contract_version" in doc):
            return doc
    # source.json may embed the contract under a key.
    source = _read_json(ctx / "source.json") or {}
    for key in ("contract", "task_contract"):
        embedded = source.get(key)
        if isinstance(embedded, dict) and (
            "tfactory" in embedded or "contract_version" in embedded
        ):
            return embedded
    return None


def _coerce_float(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _str_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(v) for v in value if isinstance(v, str))


def parse_tfactory_profile(contract: dict | None) -> TfactoryProfile | None:
    """Extract the consumed ``tfactory`` subset from a contract dict.

    Returns None when there is no usable block (so callers fall back to
    inference). Tolerant of missing/extra keys.
    """
    if not isinstance(contract, dict):
        return None
    # RFC-0007 (#87): map the access block independently of the tfactory block —
    # an access-only contract must still surface its access requirements.
    access = map_access_for_tfactory(contract.get("access"))
    access_present = bool(access.get("ready") or access.get("blocked"))
    block = contract.get("tfactory")
    if not isinstance(block, dict):
        return TfactoryProfile(access=access) if access_present else None

    lanes = tuple(
        lane for lane in _str_tuple(block.get("lanes")) if lane in _KNOWN_LANES
    )
    frameworks = {
        str(k): str(v)
        for k, v in (block.get("frameworks") or {}).items()
        if isinstance(block.get("frameworks"), dict)
    }
    endpoints = {
        str(k): str(v)
        for k, v in (block.get("endpoints") or {}).items()
        if isinstance(block.get("endpoints"), dict)
    }
    ac_raw = block.get("ac_to_code_map")
    ac_map = (
        {str(k): _str_tuple(v) for k, v in ac_raw.items()}
        if isinstance(ac_raw, dict)
        else {}
    )
    dc = block.get("docker_compose")
    profile = TfactoryProfile(
        lanes=lanes,
        frameworks=frameworks,
        endpoints=endpoints,
        docker_compose=dc if isinstance(dc, str) else None,
        coverage_target=_coerce_float(block.get("coverage_target")),
        mutation_scope=_str_tuple(block.get("mutation_scope")),
        security_scope=_str_tuple(block.get("security_scope")),
        ac_to_code_map=ac_map,
        correlation_key=(
            str(contract["correlation_key"])
            if isinstance(contract.get("correlation_key"), str)
            else None
        ),
        access=access,
    )
    return None if profile.is_empty else profile


def ac_targets(profile: TfactoryProfile | None, ac_id: str) -> tuple[str, ...]:
    """Files/functions an acceptance criterion covers, for precise targeting (#248).

    Returns () when there's no profile or no mapping for ``ac_id``.
    """
    if profile is None:
        return ()
    return profile.ac_to_code_map.get(ac_id, ())


def read_tfactory_profile(spec_dir: Path) -> TfactoryProfile | None:
    """Convenience: read the contract from the handover + parse the block.

    Returns None when no contract / no usable ``tfactory`` block is present —
    the signal for the Planner to fall back to inference.
    """
    return parse_tfactory_profile(read_task_contract(spec_dir))
