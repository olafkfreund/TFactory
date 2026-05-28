"""Lane → runner dispatcher — Task 4 (#5), restructured in v0.2 Task 0 (#16).

Given a Subtask's ``lane``, route to the right execution path. The v0.2
spine reorganises lanes around modality (browser-first), not security
category — see docs/plans/2026-05-28-enterprise-test-frameworks-design.md
Decision 2.

  v0.2 lit lanes (real runner present):
    unit        → DockerRunner (per-framework container: pytest / Jest / JUnit / …)
    browser     → DockerRunner + AppRuntime (Playwright/Cypress in container; app via docker-compose)
    api         → DockerRunner (httpx/supertest/REST Assured in container)
    integration → DockerRunner + AppRuntime (TestContainers etc.)
    mutation    → DockerRunner (per-framework: mutmut/Stryker/PIT)

  v0.1 alias lanes (deprecated through v0.2, removed in v0.3):
    functional  → collapse to unit (most v0.1 usage was unit tests)
    sast/dast/fuzz → out of scope per Decision 2; map to unit + warn

The dispatcher is intentionally thin — it owns lane→runner mapping and
nothing else. Test-plan iteration / fan-out / aggregation is the
Executor's (Task 8) job.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .docker_runner import DockerRunner, DockerRunResult


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class LaneNotImplementedError(NotImplementedError):
    """Raised when the requested lane has no executor in this MVP."""


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

# Lanes that have a real runner. v0.1 had only "functional"; v0.2 lights
# the full modality spine. Single source of truth so tests can iterate.
_MVP_LIT_LANES: frozenset[str] = frozenset({
    "unit",         # v0.2 (Task 7 — Jest / pytest images)
    "browser",      # v0.2 (Task 7 + Task 8 — Playwright image + AppRuntime)
    "api",          # v0.2 (Task 7 — same runner images as unit)
    "integration",  # v0.2 (Task 7 + Task 8 — runner + AppRuntime)
    "mutation",     # v0.2 (Task 7 — Stryker / mutmut images)
})

# Deprecated v0.1 names — see test_plan.enums._V01_LANE_ALIASES. Listed
# here so the dispatcher can route legacy plans (still parsing through
# v0.2) cleanly. Removed in v0.3.
_DEPRECATED_V01_ALIASES: dict[str, str] = {
    "functional": "unit",
    "sast":       "unit",  # out of scope per Decision 2
    "dast":       "unit",  # out of scope
    "fuzz":       "unit",  # out of scope
}

# Out-of-scope lane keys that should NEVER be in v0.2 plans. If they appear,
# emit a structured error rather than a generic NotImplementedError so the
# Planner can detect + replan.
_LANE_PHASES: dict[str, str] = {
    "sast":     "out of scope (Decision 2 — use a separate security pipeline)",
    "deps":     "out of scope (security pipeline)",
    "secrets":  "out of scope (security pipeline)",
    "dast":     "out of scope (security pipeline)",
    "fuzz":     "out of scope (Decision 2 — property-based testing folded into 'unit' lane)",
}


@dataclass
class DispatchResult:
    """What the dispatcher hands back to the Executor (Task 8)."""

    lane: str
    runner_used: str  # "docker" | "native" | "stub"
    docker_result: DockerRunResult | None = None
    notes: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.notes is None:
            self.notes = []


def dispatch_lane(
    *,
    lane: str,
    docker_runner: DockerRunner | None = None,
    docker_run_kwargs: dict[str, Any] | None = None,
) -> DispatchResult:
    """Route a lane to its runner.

    Args:
        lane: One of the strings from ``test_plan.enums.Lane`` (snake_case
            value, e.g. ``"functional"``).
        docker_runner: Required when ``lane == "functional"``.
        docker_run_kwargs: Forwarded to ``docker_runner.run(**kwargs)``.
            Must include at minimum ``repo_path``, ``scratch_path``,
            ``command``.

    Raises:
        LaneNotImplementedError: for any lane not in ``_MVP_LIT_LANES``.
        ValueError: when lane is functional but docker_runner is None.
    """
    # v0.1 alias compatibility — collapse old names to v0.2 lanes with warning
    if lane in _DEPRECATED_V01_ALIASES:
        import warnings
        new_lane = _DEPRECATED_V01_ALIASES[lane]
        warnings.warn(
            f"lane {lane!r} is a deprecated v0.1 alias; mapped to {new_lane!r}. "
            f"v0.3 will remove the alias. See Decision 2.",
            DeprecationWarning,
            stacklevel=2,
        )
        lane = new_lane

    if lane not in _MVP_LIT_LANES:
        phase = _LANE_PHASES.get(lane, "unknown lane")
        raise LaneNotImplementedError(
            f"lane {lane!r} is not supported in v0.2 — {phase}. "
            f"v0.2 lit lanes: {sorted(_MVP_LIT_LANES)}"
        )

    # All v0.2 lit lanes currently dispatch through the same DockerRunner
    # interface — the per-framework runner image is supplied by the caller
    # via docker_run_kwargs (see Task 6's Gen-Functional dispatcher). The
    # Browser + Integration lanes additionally need AppRuntime wired in
    # (Task 8); that integration lives in the Executor, not here.
    if docker_runner is None:
        raise ValueError(f"dispatch_lane({lane!r}) requires docker_runner")
    if not docker_run_kwargs:
        raise ValueError(
            f"dispatch_lane({lane!r}) requires docker_run_kwargs "
            f"(at minimum repo_path, scratch_path, command)"
        )
    run_result = docker_runner.run(**docker_run_kwargs)
    return DispatchResult(
        lane=lane,
        runner_used="docker",
        docker_result=run_result,
    )


def is_lane_lit(lane: str) -> bool:
    """Cheap check for whether a lane has a real runner.

    Accepts v0.1 aliases (functional/sast/dast/fuzz) — they're considered
    'lit' for compatibility through v0.2 (they map to 'unit' on dispatch).
    """
    return lane in _MVP_LIT_LANES or lane in _DEPRECATED_V01_ALIASES
