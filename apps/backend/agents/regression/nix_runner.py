"""Nix-Job regression runner — RFC-0018 #484 (part 3).

The real :class:`RegressionRunner`: runs each persisted test on the
**Nix-flake-per-task k8s Job substrate** (RFC-0005 Tier A) — the same
`run_pytest_lane_via_nix` path the evaluator uses and the same
`nix_provisioner` + `kube_sandbox` substrate AIFactory build/verify runs on.
The toolchain comes from the materialized flake declared in the contract
`environment` manifest, so the regression run's environment matches the build
environment with no drift.

Per the #484 substrate requirement, this runner does NOT silently fall back to
the in-pod host-venv path: if the Nix-Job substrate isn't configured it raises
:class:`NixSubstrateUnavailableError` (surfaced as a loud ERROR via
:func:`agents.regression.runner.run_corpus`). A logged, flagged host-fallback
is a separate later slice.

Browser / non-pytest frameworks land in a later #484 slice; for now they raise
:class:`UnsupportedFrameworkError`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from agents.coverage_delta import parse_coverage
from agents.nix_env import run_pytest_lane_via_nix
from tools.runners.docker_runner import DockerRunResult

from .corpus import CorpusEntry
from .models import TestOutcome, TestStatus

logger = logging.getLogger(__name__)

# Frameworks the Nix pytest-lane path can run today. Others (playwright, jest,
# cypress, …) are dispatched in a later #484 slice.
_PYTEST_FRAMEWORKS = frozenset({"pytest"})


class NixSubstrateUnavailableError(RuntimeError):
    """The Nix-flake-per-task k8s Job substrate is not configured.

    Surfaced loudly (never a silent downgrade) because the regression executor
    MUST run on the Nix-Job substrate per RFC-0018 #484.
    """


class UnsupportedFrameworkError(NotImplementedError):
    """No Nix-Job runner yet for this corpus entry's framework (later slice)."""


def _line_rate_pct(path: Path | None) -> float | None:
    """This test's own project-wide line rate as a percentage, or None.

    The Job ran ``--cov=.``, so the figure is what this ONE test reached across
    the whole project — an honest per-test signal, and the only thing this
    float ever claims to be. The run's project figure is the union of the
    covered-line sets, computed by the orchestrator (#865).
    """
    if path is None:
        return None
    try:
        return round(parse_coverage(Path(path)).line_rate * 100.0, 2)
    except (OSError, ValueError) as exc:  # CoverageParseError is a ValueError
        logger.warning("regression: unreadable coverage report at %s: %s", path, exc)
        return None


def outcome_from_run_result(entry: CorpusEntry, result: DockerRunResult) -> TestOutcome:
    """Map a runner :class:`DockerRunResult` onto a :class:`TestOutcome` (pure).

    ``coverage_xml_path`` survives the Job: ``run_pytest_lane_via_nix`` copies
    the report off the PVC scratch into a host temp dir before cleaning up, so
    the path is readable in this process. Dropping it here was the first of the
    three breaks that left the coverage ledger permanently empty (#865).
    """
    status = TestStatus.PASSED if result.ok else TestStatus.FAILED
    cov_path = result.coverage_xml_path
    return TestOutcome(
        test_id=entry.test_id,
        lane=entry.lane,
        framework=entry.framework,
        status=status,
        coverage_pct=_line_rate_pct(cov_path),
        coverage_report_path=str(cov_path) if cov_path else None,
    )


@dataclass
class NixJobRunner:
    """Run corpus entries on the Nix-flake-per-task k8s Job substrate.

    Constructed with the ``spec_dir`` carrying the contract ``environment``
    manifest and the checked-out ``project_dir`` worktree; the orchestrator
    (#484 part 4) sets these up per regression run.
    """

    spec_dir: Path
    project_dir: Path
    extra_env: dict[str, str] | None = None

    def run(self, entry: CorpusEntry) -> TestOutcome:
        if entry.framework not in _PYTEST_FRAMEWORKS:
            raise UnsupportedFrameworkError(
                f"no Nix-Job runner yet for framework={entry.framework!r} "
                f"(test_id={entry.test_id}); deferred to a later #484 slice"
            )
        test_path = Path(self.project_dir) / entry.test_file
        result = run_pytest_lane_via_nix(
            self.spec_dir, self.project_dir, test_path, extra_env=self.extra_env
        )
        if result is None:
            raise NixSubstrateUnavailableError(
                "Nix-Job substrate unavailable for "
                f"test_id={entry.test_id} (no contract environment, or "
                "TFACTORY_NIX_RUNNER_IMAGE unset); refusing to silently "
                "fall back to the host runner"
            )
        logger.info(
            "regression: ran test_id=%s on Nix-Job substrate -> rc=%s",
            entry.test_id,
            result.returncode,
        )
        return outcome_from_run_result(entry, result)
