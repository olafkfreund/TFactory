"""Regression run records — RFC-0018 #483 (Phase 1).

Immutable, comparable records of a *regression run*: a re-execution of the
already-persisted test corpus (``tests-catalog.json``) for a project at a
given commit. Two runs can be diffed to surface regressions vs fixes vs
flakes (see :mod:`agents.regression.diff`).

Design mirrors the existing signal primitives (``flaky_history`` etc.):

  - pure, dependency-light, frozen dataclasses
  - ``to_dict`` / ``from_dict`` round-trip for JSON persistence
  - no I/O and no clock here — ``run_id`` / ``ran_at`` are supplied by the
    caller so the model stays deterministic and trivially unit-testable

Store format (JSON, one file per run) is defined in
:mod:`agents.regression.store`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class TestStatus(str, Enum):
    """Outcome of a single test in a regression run."""

    __test__ = False  # not a pytest test class despite the Test* name

    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"  # harness/runtime error — treated as a failure for gating
    SKIPPED = "skipped"
    QUARANTINED = "quarantined"  # excluded from the gate (see RFC-0018 #485)

    @property
    def is_fail(self) -> bool:
        """True when this status counts as a failure for regression gating.

        ``skipped`` and ``quarantined`` deliberately do NOT count as failures.
        """
        return self in (TestStatus.FAILED, TestStatus.ERROR)

    @property
    def is_pass(self) -> bool:
        return self is TestStatus.PASSED


@dataclass(frozen=True)
class TestOutcome:
    """The result of one test in one regression run."""

    __test__ = False  # not a pytest test class despite the Test* name

    test_id: str
    lane: str
    framework: str
    status: TestStatus
    duration_ms: int | None = None
    coverage_pct: float | None = None
    evidence_uri: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "test_id": self.test_id,
            "lane": self.lane,
            "framework": self.framework,
            "status": self.status.value,
            "duration_ms": self.duration_ms,
            "coverage_pct": self.coverage_pct,
            "evidence_uri": self.evidence_uri,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TestOutcome:
        return cls(
            test_id=str(d["test_id"]),
            lane=str(d.get("lane", "")),
            framework=str(d.get("framework", "")),
            status=TestStatus(str(d["status"])),
            duration_ms=_opt_int(d.get("duration_ms")),
            coverage_pct=_opt_float(d.get("coverage_pct")),
            evidence_uri=_opt_str(d.get("evidence_uri")),
        )


@dataclass(frozen=True)
class RegressionRun:
    """An immutable record of one re-run of a project's persisted corpus."""

    run_id: str
    project_id: str
    ran_at: str  # ISO-8601, supplied by the caller
    results: tuple[TestOutcome, ...] = ()
    commit: str | None = None
    target_url: str | None = None
    baseline_run_id: str | None = None
    # Per-run coverage rollup (project-level), populated by RFC-0018 #486.
    coverage_pct: float | None = None

    # ── totals ──────────────────────────────────────────────────────────
    @property
    def totals(self) -> dict[str, int]:
        passed = failed = skipped = quarantined = 0
        for r in self.results:
            if r.status is TestStatus.QUARANTINED:
                quarantined += 1
            elif r.status is TestStatus.SKIPPED:
                skipped += 1
            elif r.status.is_fail:
                failed += 1
            else:
                passed += 1
        return {
            "total": len(self.results),
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "quarantined": quarantined,
        }

    @property
    def failed(self) -> bool:
        """True when any gated test failed (skipped/quarantined excluded)."""
        return self.totals["failed"] > 0

    def status_of(self, test_id: str) -> TestStatus | None:
        """Return the recorded status for *test_id*, or None if absent."""
        for r in self.results:
            if r.test_id == test_id:
                return r.status
        return None

    @property
    def test_ids(self) -> tuple[str, ...]:
        return tuple(r.test_id for r in self.results)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": _SCHEMA_VERSION,
            "run_id": self.run_id,
            "project_id": self.project_id,
            "ran_at": self.ran_at,
            "commit": self.commit,
            "target_url": self.target_url,
            "baseline_run_id": self.baseline_run_id,
            "coverage_pct": self.coverage_pct,
            "totals": self.totals,
            "results": [r.to_dict() for r in self.results],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RegressionRun:
        return cls(
            run_id=str(d["run_id"]),
            project_id=str(d["project_id"]),
            ran_at=str(d["ran_at"]),
            results=tuple(TestOutcome.from_dict(r) for r in d.get("results", []) or []),
            commit=_opt_str(d.get("commit")),
            target_url=_opt_str(d.get("target_url")),
            baseline_run_id=_opt_str(d.get("baseline_run_id")),
            coverage_pct=_opt_float(d.get("coverage_pct")),
        )


# Stamped into every persisted run so the store can migrate later.
_SCHEMA_VERSION = "1.0"


# ── small coercion helpers (loose JSON in, strict types out) ────────────
def _opt_str(v: Any) -> str | None:
    return None if v is None else str(v)


def _opt_int(v: Any) -> int | None:
    return None if v is None else int(v)


def _opt_float(v: Any) -> float | None:
    return None if v is None else float(v)
