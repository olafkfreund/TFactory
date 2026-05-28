"""Evaluator agent — Task 7, issue #8.

Third agent in the six-agent TFactory pipeline:

    Planner → Gen-Functional → Executor → Evaluator → Triager

Reads completed Lane.FUNCTIONAL subtasks from test_plan.json, computes
five evaluation signals per generated test (coverage delta, 3× stability,
mutate-and-check, lint promotion + the LLM's semantic-relevance call),
hands them to an LLM via the evaluator.md prompt, then validates the
verdicts.json the LLM writes.

Task 7 commits (in flight):

  ✓ commit 1 — Auto-fire scaffold + stub
  ✓ commit 2 — Coverage-delta + 3× stability re-run primitives
  ✓ commit 3 — Mutate-and-check probe + flake-lint promotion primitives
  ✓ commit 4 — evaluator.md prompt + assembly helper
  ✓ commit 5 — Real run_evaluator with SDK + 5 signals → verdicts.json  (this commit)
  ⬜ commit 6 — Integration test + close #8
"""

from __future__ import annotations

import asyncio
import json
import logging as _logging
import os
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal, Protocol


_eval_log = _logging.getLogger(__name__)


# ─── Workspace helpers (local copy — same pattern as planner/gen_functional)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_status(spec_dir: Path) -> dict:
    status_path = spec_dir / "status.json"
    if not status_path.exists():
        return {}
    try:
        return json.loads(status_path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _write_status_patch(spec_dir: Path, **fields: object) -> None:
    status = _read_status(spec_dir)
    status.update(fields)
    status["updated_at"] = _now_iso()
    (spec_dir / "status.json").write_text(json.dumps(status, indent=2))


# ─── SDK seams (mockable in tests) ──────────────────────────────────────


async def _resolve_evaluator_client(spec_dir: Path, project_dir: Path):
    """Resolve the Claude Agent SDK client for the evaluation phase.

    Same pattern as ``planner._resolve_planner_client`` /
    ``gen_functional._resolve_client``. Heavy imports deferred to
    runtime so tests can mock this seam without the SDK chain.

    Uses the 'coding' phase model for now — same budget as
    Gen-Functional. A 'evaluation' phase can be added to phase_config
    once we know the right thinking-token budget. Conservative for now.
    """
    from core.client import create_client
    from phase_config import (
        get_phase_model,
        get_phase_thinking_budget,
        get_provider_extra_kwargs,
        infer_provider_from_model,
    )
    from providers.factory import get_provider

    eval_model = get_phase_model(spec_dir, "coding", None)
    provider_name = infer_provider_from_model(eval_model)
    if provider_name == "claude":
        thinking_budget = get_phase_thinking_budget(spec_dir, "coding")
        return create_client(
            project_dir, spec_dir, eval_model, max_thinking_tokens=thinking_budget,
        )
    return get_provider(
        provider_name,
        phase="coding",
        model=eval_model,
        working_dir=project_dir,
        **get_provider_extra_kwargs(provider_name, eval_model),
    )


async def _invoke_session(
    client, prompt: str, spec_dir: Path, verbose: bool,
) -> tuple[str, str, dict]:
    """Wrap run_agent_session so tests can patch one symbol."""
    from agents.session import run_agent_session
    from task_logger import LogPhase

    async with client:
        return await run_agent_session(
            client, prompt, spec_dir, verbose, phase=LogPhase.CODING,
        )


# ─── Runner-fn seam for stability + mutation primitives ─────────────────


class _RunResultLike(Protocol):
    """Same duck-type as stability_runner/mutate_probe expect."""
    @property
    def returncode(self) -> int: ...
    @property
    def stdout(self) -> str: ...
    @property
    def stderr(self) -> str: ...


def _resolve_runner_fn(
    spec_dir: Path, project_dir: Path,
) -> Callable[[Path, Path, int], _RunResultLike]:
    """Return a callable matching the runner_fn seam.

    The real implementation wires ``DockerRunner.run_pytest``; tests
    mock this whole function so the stability + mutation primitives
    can be exercised in unit tests without touching Docker.
    """
    from tools.runners.docker_runner import DockerRunner

    runner = DockerRunner()
    def _run(test_file: Path, project_dir_arg: Path, seed: int):
        return runner.run_pytest(
            test_file=test_file,
            project_dir=project_dir_arg,
            seed=seed,
        )
    return _run


# ─── Per-test signal bundle ─────────────────────────────────────────────


@dataclass
class EvaluatorSignals:
    """Per-test bundle of the four pre-computed signals plus identity.

    The fifth signal (semantic relevance) is the LLM's call — it
    doesn't live in this dataclass.

    Any of the four signal fields can be ``None`` if the primitive
    couldn't run (e.g., coverage XML not emitted by the Executor for
    this test). The prompt helper renders missing signals as
    "not computed" rather than crashing.
    """

    test_id: str
    test_file: Path
    target: str
    rationale: str
    coverage_delta: Any = None   # CoverageDelta | None
    stability: Any = None        # StabilityResult | None
    mutation: Any = None         # MutationResult | None
    lint_promotion: Any = None   # PromotionResult | None


# ─── Signal-bundle assembly ─────────────────────────────────────────────


def _completed_functional_subtasks(plan: dict) -> list[dict]:
    """Pick subtasks that Gen-Functional successfully generated
    (status='completed', lane='functional', has files_to_create)."""
    out = []
    for phase in plan.get("phases", []):
        for st in phase.get("subtasks", []):
            if (
                st.get("status") == "completed"
                and st.get("lane") == "functional"
                and st.get("files_to_create")
            ):
                out.append(st)
    return out


def _coverage_delta_for_subtask(
    spec_dir: Path, subtask: dict,
):
    """Try to compute coverage delta for one test.

    Looks for ``spec_dir/findings/baseline_coverage.xml`` and
    ``spec_dir/findings/runs/<test_id>/coverage.xml``. Returns
    None if either is missing — the LLM will see "not computed".
    """
    from agents.coverage_delta import compute_delta_from_paths

    baseline = spec_dir / "findings" / "baseline_coverage.xml"
    after = spec_dir / "findings" / "runs" / subtask["id"] / "coverage.xml"
    if not baseline.exists() or not after.exists():
        return None
    try:
        return compute_delta_from_paths(baseline, after)
    except Exception as exc:  # noqa: BLE001 — defensive
        _eval_log.warning(
            "coverage_delta failed for %s: %s", subtask["id"], exc,
        )
        return None


def _stability_for_subtask(
    spec_dir: Path,
    project_dir: Path,
    subtask: dict,
    runner_fn,
):
    """Run the 3× stability check for one test."""
    from agents.stability_runner import check_stability

    test_file = spec_dir / subtask["files_to_create"][0]
    if not test_file.exists():
        return None
    try:
        return check_stability(test_file, project_dir, runner_fn)
    except Exception as exc:  # noqa: BLE001
        _eval_log.warning(
            "stability check failed for %s: %s", subtask["id"], exc,
        )
        return None


def _mutation_for_subtask(
    spec_dir: Path,
    project_dir: Path,
    subtask: dict,
    runner_fn,
):
    """Run the mutate-and-check probe for one test.

    Writes the mutant to ``spec_dir/findings/mutants/<test_id>.py`` so
    the original test file stays clean.
    """
    from agents.mutate_probe import run_mutate_probe

    test_file = spec_dir / subtask["files_to_create"][0]
    if not test_file.exists():
        return None
    mutant_path = spec_dir / "findings" / "mutants" / f"{subtask['id']}.py"
    try:
        return run_mutate_probe(
            test_file, project_dir, runner_fn,
            write_mutant_to=mutant_path,
        )
    except Exception as exc:  # noqa: BLE001
        _eval_log.warning(
            "mutate probe failed for %s: %s", subtask["id"], exc,
        )
        return None


def _lint_promotion_for_subtask(spec_dir: Path, subtask: dict):
    """Run flake_risk_lint + promote findings for one test."""
    from agents.flake_risk_lint import flake_risk_lint
    from agents.lint_promotion import promote_flake_findings

    test_file = spec_dir / subtask["files_to_create"][0]
    if not test_file.exists():
        return None
    try:
        source = test_file.read_text()
    except OSError:
        return None
    result = flake_risk_lint(source)
    return promote_flake_findings(result, source)


def _build_signal_bundle(
    spec_dir: Path,
    project_dir: Path,
    subtask: dict,
    runner_fn,
) -> EvaluatorSignals:
    """Run every available signal primitive against ``subtask`` and
    return a bundle the prompt helper can format."""
    return EvaluatorSignals(
        test_id=subtask["id"],
        test_file=spec_dir / subtask["files_to_create"][0],
        target=subtask.get("target") or "?",
        rationale=subtask.get("rationale") or "?",
        coverage_delta=_coverage_delta_for_subtask(spec_dir, subtask),
        stability=_stability_for_subtask(spec_dir, project_dir, subtask, runner_fn),
        mutation=_mutation_for_subtask(spec_dir, project_dir, subtask, runner_fn),
        lint_promotion=_lint_promotion_for_subtask(spec_dir, subtask),
    )


# ─── Verdicts.json validation ───────────────────────────────────────────


_VALID_VERDICTS = frozenset({"accept", "reject", "flag"})


def _validate_verdicts(path: Path) -> tuple[bool, str, int]:
    """Validate the agent's verdicts.json.

    Returns:
        (ok, error_message, verdicts_count).
        On success: (True, "", N). On failure: (False, "reason", 0).
    """
    if not path.exists():
        return False, "verdicts.json not written by agent", 0
    try:
        doc = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        return False, f"verdicts.json is not valid JSON: {exc}", 0
    if not isinstance(doc, dict):
        return False, "verdicts.json root is not an object", 0
    verdicts = doc.get("verdicts")
    if not isinstance(verdicts, list):
        return False, "verdicts.json missing 'verdicts' array", 0
    for i, v in enumerate(verdicts):
        if not isinstance(v, dict):
            return False, f"verdict[{i}] is not an object", 0
        if "test_id" not in v:
            return False, f"verdict[{i}] missing 'test_id'", 0
        if v.get("verdict") not in _VALID_VERDICTS:
            return False, (
                f"verdict[{i}] has invalid 'verdict': "
                f"{v.get('verdict')!r} (must be one of {sorted(_VALID_VERDICTS)})"
            ), 0
    return True, "", len(verdicts)


# ─── The agent itself ───────────────────────────────────────────────────


async def run_evaluator(
    spec_dir: Path,
    project_dir: Path,
    mode: Literal["initial", "rerun"] = "initial",
    verbose: bool = False,
) -> bool:
    """Run the TFactory Evaluator agent.

    Args:
        spec_dir: TFactory workspace spec directory.
        project_dir: AIFactory project root (passed to docker runner +
            available to the LLM via Read/Glob/Grep).
        mode: 'initial' on first run; 'rerun' if invoked after a
            Triager-requested re-evaluation. Reserved — both modes
            currently share behaviour but the value is surfaced in
            status.json + verdicts.json for traceability.
        verbose: forwarded to ``run_agent_session``.

    Returns:
        True on a clean evaluation pass (including empty-test case);
        False on hard failure.

    Status transitions:
      generated   → evaluating          (in-flight marker)
                  → evaluated            (verdicts.json validated)
                  → evaluated_empty     (no tests to evaluate)
                  → evaluator_failed    (validation / session error)
    """
    try:
        _write_status_patch(
            spec_dir,
            status="evaluating",
            phase=f"evaluator_{mode}_started",
        )

        # 1. Load the plan + filter to completed functional subtasks.
        plan_path = spec_dir / "test_plan.json"
        if not plan_path.exists():
            _write_status_patch(
                spec_dir,
                status="evaluator_failed",
                phase="evaluator_no_plan",
                evaluator_error="test_plan.json not found",
            )
            return False

        try:
            plan = json.loads(plan_path.read_text())
        except json.JSONDecodeError as exc:
            _write_status_patch(
                spec_dir,
                status="evaluator_failed",
                phase="evaluator_plan_unparseable",
                evaluator_error=f"test_plan.json invalid: {exc}",
            )
            return False

        completed = _completed_functional_subtasks(plan)

        # 2. No work — early exit with evaluated_empty.
        if not completed:
            verdicts_dir = spec_dir / "findings"
            verdicts_dir.mkdir(parents=True, exist_ok=True)
            (verdicts_dir / "verdicts.json").write_text(json.dumps({
                "evaluator_version": "task7-commit5",
                "mode": mode,
                "verdicts": [],
                "generated_at": _now_iso(),
            }, indent=2))
            _write_status_patch(
                spec_dir,
                status="evaluated_empty",
                phase="evaluator_no_completed_subtasks",
                verdicts_count=0,
            )
            return True

        # 3. Per-test signal computation (real primitives; runner_fn
        #    seam mocked in tests so docker isn't required).
        runner_fn = _resolve_runner_fn(spec_dir, project_dir)
        bundles = [
            _build_signal_bundle(spec_dir, project_dir, st, runner_fn)
            for st in completed
        ]

        # 4. Build prompt + invoke SDK session.
        from prompts_pkg.prompts import get_tfactory_evaluator_prompt

        prompt = get_tfactory_evaluator_prompt(spec_dir, project_dir, bundles)
        client = await _resolve_evaluator_client(spec_dir, project_dir)
        try:
            session_status, _response, _err = await _invoke_session(
                client, prompt, spec_dir, verbose,
            )
        except Exception as exc:  # noqa: BLE001 — surface in status
            _eval_log.error(
                "evaluator session raised: %s\n%s", exc, traceback.format_exc()
            )
            _write_status_patch(
                spec_dir,
                status="evaluator_failed",
                phase="evaluator_session_error",
                evaluator_error=str(exc)[:500],
            )
            return False

        # 5. Validate the verdicts.json the agent wrote.
        verdicts_path = spec_dir / "findings" / "verdicts.json"
        ok, err, count = _validate_verdicts(verdicts_path)
        if not ok:
            _write_status_patch(
                spec_dir,
                status="evaluator_failed",
                phase="evaluator_invalid_verdicts",
                evaluator_error=err,
            )
            return False

        _write_status_patch(
            spec_dir,
            status="evaluated",
            phase="evaluator_complete",
            verdicts_count=count,
            tests_evaluated=len(bundles),
        )
        return True

    except Exception as exc:
        _eval_log.error(
            "evaluator failed: %s\n%s", exc, traceback.format_exc()
        )
        _write_status_patch(
            spec_dir,
            status="evaluator_failed",
            phase=f"evaluator_{mode}_exception",
            evaluator_error=str(exc)[:500],
        )
        return False


# ─── Auto-fire scheduler ─────────────────────────────────────────────────
#
# Same GC-anchor pattern as _BG_PLANNER_TASKS and _BG_GEN_FUNCTIONAL_TASKS.
# Gen-Functional's success path (status=generated, tests_generated >= 1)
# calls schedule_evaluator after writing the status — gated on env so the
# test suite stays deterministic.

_BG_EVALUATOR_TASKS: set[asyncio.Task] = set()


def schedule_evaluator(
    spec_dir: Path,
    project_dir: Path,
    mode: Literal["initial", "rerun"] = "initial",
) -> asyncio.Task | None:
    """Fire-and-forget Evaluator, gated by ``TFACTORY_AUTO_EVALUATE``.

    Default ON (env var unset or "1"). Test fixtures should set
    ``TFACTORY_AUTO_EVALUATE=0`` to keep gen_functional's success path
    from auto-advancing.

    Returns the scheduled asyncio.Task, or None if the env var disables
    auto-evaluation. Each scheduled task is anchored in
    ``_BG_EVALUATOR_TASKS`` until done (cleared via done_callback).
    """
    if os.environ.get("TFACTORY_AUTO_EVALUATE", "1") == "0":
        return None
    task = asyncio.create_task(
        run_evaluator(spec_dir, project_dir, mode=mode)
    )
    _BG_EVALUATOR_TASKS.add(task)
    task.add_done_callback(_BG_EVALUATOR_TASKS.discard)
    return task
