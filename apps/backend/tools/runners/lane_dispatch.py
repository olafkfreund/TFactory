"""Lane → runner dispatcher — Task 4 (#5).

Given a Subtask's ``lane``, route to the right execution path:

  functional → DockerRunner (sandboxed container)
  sast       → NotImplementedError (phase 3, see TFACTORY_MVP_LANES)
  dast       → NotImplementedError (phase 5)
  fuzz       → NotImplementedError (phase 5)
  mutation   → NotImplementedError (phase 2)

The static lanes (sast / deps / secrets) will eventually use a native
pass-through executor that walks the filesystem and shells out to
Semgrep / pip-audit / gitleaks directly — those tools are read-only and
don't need a sandbox. That's the "tiered execution" decision from the
design plan. The pass-through is stubbed here with an explicit
``LaneNotImplementedError`` so callers fail loudly rather than silently
skipping.

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

# Lanes that have a real runner at MVP. Single source of truth so tests
# can iterate over the gap.
_MVP_LIT_LANES: frozenset[str] = frozenset({"functional"})

# Phase mapping for lanes that aren't lit yet — the error message points
# at the right roadmap milestone.
_LANE_PHASES: dict[str, str] = {
    "sast":     "phase 3 (#TBD, Task 3 of phase 3 in roadmap)",
    "deps":     "phase 3 (same phase as sast)",
    "secrets":  "phase 3",
    "mutation": "phase 2 (#TBD, mutmut/stryker integration)",
    "dast":     "phase 5 (#TBD, OWASP ZAP automation)",
    "fuzz":     "phase 5 (atheris / jsfuzz)",
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
    if lane not in _MVP_LIT_LANES:
        phase = _LANE_PHASES.get(lane, "unknown lane")
        raise LaneNotImplementedError(
            f"lane {lane!r} is not implemented at MVP — wires up in {phase}. "
            f"MVP lit lanes: {sorted(_MVP_LIT_LANES)}"
        )

    if lane == "functional":
        if docker_runner is None:
            raise ValueError("dispatch_lane('functional') requires docker_runner")
        if not docker_run_kwargs:
            raise ValueError(
                "dispatch_lane('functional') requires docker_run_kwargs "
                "(at minimum repo_path, scratch_path, command)"
            )
        run_result = docker_runner.run(**docker_run_kwargs)
        return DispatchResult(
            lane=lane,
            runner_used="docker",
            docker_result=run_result,
        )

    # Defensive — _MVP_LIT_LANES contains only "functional" at MVP, so
    # this branch is unreachable. Kept so the lit set can grow without
    # silently degrading.
    raise LaneNotImplementedError(
        f"lane {lane!r} listed as lit but no handler wired"
    )


def is_lane_lit(lane: str) -> bool:
    """Cheap check for whether a lane has a real runner at MVP."""
    return lane in _MVP_LIT_LANES
