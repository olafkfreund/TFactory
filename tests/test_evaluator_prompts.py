"""Tests for the Evaluator prompt assembly helper — Task 7 (#8) commit 4.

The helper assembles a multi-test system prompt by loading
``apps/backend/prompts/evaluator.md`` and prepending an EVALUATOR
CONTEXT block with one sub-block per generated test (carrying the
four pre-computed numeric signals: coverage, stability, mutation,
lint-promotion).

Covered:
  - Path injection (spec_dir, project_dir, verdicts.json target)
  - Per-test sub-block injection (id, file, target, rationale)
  - Signal formatting from REAL dataclass instances
  - Signal formatting from dict-shaped bundles (post-JSON-load)
  - Missing signal fields degrade to "not computed" without crash
  - Empty bundle list — emit empty-verdicts hint
  - Body sanity (mentions all five signals, verdict types, tools)
  - FileNotFoundError when evaluator.md is missing
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agents.coverage_delta import CoverageDelta
from agents.flake_risk_lint import FlakeRiskResult
from agents.lint_promotion import promote_flake_findings
from agents.mutate_probe import (
    MutationApplied,
    MutationResult,
    MutationVerdict,
)
from agents.stability_runner import (
    StabilityResult,
    StabilityRun,
    StabilityVerdict,
)
from prompts_pkg.prompts import (
    _format_evaluator_per_test_block,
    get_tfactory_evaluator_prompt,
)


# ── Fixtures: real dataclass bundles ───────────────────────────────────


@pytest.fixture
def coverage_delta() -> CoverageDelta:
    return CoverageDelta(
        new_lines=frozenset({("app/auth/login.py", 12), ("app/auth/login.py", 13)}),
        new_files=0,
        delta_pct=5.25,
        baseline_total_covered=100,
        after_total_covered=102,
    )


@pytest.fixture
def stability_stable() -> StabilityResult:
    return StabilityResult(
        verdict=StabilityVerdict.STABLE,
        runs=(
            StabilityRun(returncode=0),
            StabilityRun(returncode=0),
            StabilityRun(returncode=0),
        ),
        seed=424242,
        rerun_count=3,
    )


@pytest.fixture
def mutation_killed() -> MutationResult:
    return MutationResult(
        verdict=MutationVerdict.KILLED,
        mutation=MutationApplied(
            operator="Eq->NotEq",
            lineno=4,
            before="1 == 1",
            after="1 != 1",
        ),
        mutated_source="def test_x():\n    assert 1 != 1\n",
        runner_stdout_tail="assertion failed",
    )


@pytest.fixture
def lint_promotion_clean():
    forced = FlakeRiskResult(ok=True, hits=[])
    return promote_flake_findings(forced, "def test_x(): pass\n")


@pytest.fixture
def dataclass_bundle(
    coverage_delta, stability_stable, mutation_killed, lint_promotion_clean,
):
    """A realistic per-test bundle as commit 5 will construct."""
    from dataclasses import dataclass, field
    from typing import Any

    @dataclass
    class _Bundle:
        test_id: str = "ac1-login-sets-24h-expiry"
        test_file: str = "/ws/spec/tests/test_login_expiry.py"
        target: str = "app/auth/login.py::login_user"
        rationale: str = "AC#1: login sets expires_at to +24h"
        coverage_delta: Any = None
        stability: Any = None
        mutation: Any = None
        lint_promotion: Any = None

    return _Bundle(
        coverage_delta=coverage_delta,
        stability=stability_stable,
        mutation=mutation_killed,
        lint_promotion=lint_promotion_clean,
    )


@pytest.fixture
def dict_bundle(
    coverage_delta, stability_stable, mutation_killed, lint_promotion_clean,
):
    """Dict-shaped variant (post-JSON-load shape)."""
    return {
        "test_id": "ac2-expired-returns-none",
        "test_file": "/ws/spec/tests/test_get_session_expired.py",
        "target": "app/auth/session.py::get_session",
        "rationale": "AC#2: returns None for expired session",
        "coverage_delta": coverage_delta,
        "stability": stability_stable,
        "mutation": mutation_killed,
        "lint_promotion": lint_promotion_clean,
    }


# ── Per-test sub-block formatting ──────────────────────────────────────


def test_block_includes_test_id(dataclass_bundle) -> None:
    block = _format_evaluator_per_test_block(dataclass_bundle)
    assert "ac1-login-sets-24h-expiry" in block


def test_block_includes_test_file_and_target(dataclass_bundle) -> None:
    block = _format_evaluator_per_test_block(dataclass_bundle)
    assert "/ws/spec/tests/test_login_expiry.py" in block
    assert "app/auth/login.py::login_user" in block


def test_block_includes_rationale(dataclass_bundle) -> None:
    block = _format_evaluator_per_test_block(dataclass_bundle)
    assert "AC#1" in block


def test_block_formats_coverage(dataclass_bundle) -> None:
    block = _format_evaluator_per_test_block(dataclass_bundle)
    # delta_pct=5.25 → "+5.25" with sign
    assert "+5.25" in block
    # new_lines=2 lines
    assert "new_lines=2" in block


def test_block_formats_stability(dataclass_bundle) -> None:
    block = _format_evaluator_per_test_block(dataclass_bundle)
    assert "stability: stable" in block
    assert "3 runs" in block


def test_block_formats_mutation(dataclass_bundle) -> None:
    block = _format_evaluator_per_test_block(dataclass_bundle)
    assert "mutation: killed" in block
    assert "op=Eq->NotEq" in block


def test_block_formats_lint_promotion(dataclass_bundle) -> None:
    block = _format_evaluator_per_test_block(dataclass_bundle)
    # No findings → "no findings" summary
    assert "no findings" in block


def test_block_accepts_dict_shape(dict_bundle) -> None:
    block = _format_evaluator_per_test_block(dict_bundle)
    assert "ac2-expired-returns-none" in block
    assert "stability: stable" in block
    assert "mutation: killed" in block


def test_block_missing_signals_degrades_gracefully() -> None:
    """A bundle missing the four numeric signals renders the labels
    but with 'not computed' values — no crash."""
    bundle = {
        "test_id": "minimal",
        "test_file": "/x.py",
        "target": "x::y",
        "rationale": "AC#X",
        # no coverage / stability / mutation / lint_promotion
    }
    block = _format_evaluator_per_test_block(bundle)
    assert "coverage: not computed" in block
    assert "stability: not computed" in block
    assert "mutation: not computed" in block
    assert "lint_promotion: not computed" in block


# ── Full prompt assembly ───────────────────────────────────────────────


def test_assembles_with_one_bundle(dataclass_bundle) -> None:
    p = get_tfactory_evaluator_prompt(
        Path("/ws/spec"), Path("/proj"), [dataclass_bundle],
    )
    assert "/ws/spec" in p
    assert "/proj" in p
    assert "/ws/spec/findings/verdicts.json" in p
    assert "Number of generated tests to evaluate: 1" in p
    assert "ac1-login-sets-24h-expiry" in p


def test_assembles_with_many_bundles(dataclass_bundle, dict_bundle) -> None:
    p = get_tfactory_evaluator_prompt(
        Path("/ws/spec"), Path("/proj"), [dataclass_bundle, dict_bundle],
    )
    assert "Number of generated tests to evaluate: 2" in p
    assert "ac1-login-sets-24h-expiry" in p
    assert "ac2-expired-returns-none" in p


def test_assembles_with_empty_bundle_list() -> None:
    p = get_tfactory_evaluator_prompt(Path("/ws"), Path("/p"), [])
    assert "Number of generated tests to evaluate: 0" in p
    assert "no tests in this batch" in p


# ── Body content sanity ────────────────────────────────────────────────


def test_body_mentions_all_five_signals(dataclass_bundle) -> None:
    p = get_tfactory_evaluator_prompt(Path("/ws"), Path("/p"), [dataclass_bundle])
    assert "Coverage delta" in p
    assert "stability" in p.lower()
    assert "mutate" in p.lower() or "mutation" in p.lower()
    assert "lint" in p.lower()
    assert "Semantic relevance" in p or "semantic_relevance" in p.lower()


def test_body_mentions_verdict_types(dataclass_bundle) -> None:
    p = get_tfactory_evaluator_prompt(Path("/ws"), Path("/p"), [dataclass_bundle])
    for v in ("accept", "reject", "flag"):
        assert v in p


def test_body_mentions_tool_grants(dataclass_bundle) -> None:
    p = get_tfactory_evaluator_prompt(Path("/ws"), Path("/p"), [dataclass_bundle])
    for tool in ("Read", "Write", "Glob", "Grep"):
        assert tool in p
    # Negative grants explicitly mentioned
    assert "Bash" in p  # in the "do NOT have" list
    assert "do NOT have" in p or "no bash" in p.lower()


def test_body_mentions_verdicts_json_schema(dataclass_bundle) -> None:
    p = get_tfactory_evaluator_prompt(Path("/ws"), Path("/p"), [dataclass_bundle])
    assert "verdicts.json" in p
    assert "evaluator_version" in p
    assert "signals_summary" in p


def test_total_size_in_range(dataclass_bundle) -> None:
    p = get_tfactory_evaluator_prompt(
        Path("/ws"), Path("/p"), [dataclass_bundle],
    )
    # Body ~8KB + context block ~2KB.
    assert 8_000 < len(p) < 20_000, f"unexpected size {len(p)}"


# ── Failure mode ───────────────────────────────────────────────────────


def test_raises_when_md_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, dataclass_bundle,
) -> None:
    import prompts_pkg.prompts as mod
    monkeypatch.setattr(mod, "PROMPTS_DIR", tmp_path)  # empty dir
    with pytest.raises(FileNotFoundError, match="evaluator.md"):
        get_tfactory_evaluator_prompt(
            Path("/ws"), Path("/p"), [dataclass_bundle],
        )
