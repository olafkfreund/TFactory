"""Evaluator agent — Task 7, issue #8.

Third agent in the six-agent TFactory pipeline:

    Planner → Gen-Functional → Executor → Evaluator → Triager

The Evaluator is a *structurally separate* agent from Gen-Functional
(research-mandated for non-self-validation). It reads the Executor's
run output for each generated test and emits a per-test verdict via
five evaluation signals:

  - coverage-delta computation
  - 3× stability re-run
  - LLM semantic relevance (mocked in unit tests)
  - mutate-and-check probe (fixed seed — catches `assert True`)
  - flake-risk lint **promotion** (commit 3 of Task 6 already
    flagged medium-severity patterns; the evaluator decides whether
    to promote a flag to a reject)

Output: a ``verdicts.json`` file with one Verdict per generated test
file the Triager (Task 8) consumes when deciding which tests get
committed.

Task 7 commits (in flight):

  ✓ commit 1 — Auto-fire scaffold + stub  (this commit)
  ⬜ commit 2 — Coverage-delta + 3× stability re-run primitives
  ⬜ commit 3 — Mutate-and-check probe + flake-lint promotion
  ⬜ commit 4 — evaluator.md prompt + assembly helper
  ⬜ commit 5 — Real run_evaluator with SDK + 5 signals → verdicts.json
  ⬜ commit 6 — Integration test + close #8
"""

from __future__ import annotations

import asyncio
import json
import logging as _logging
import os
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal


_eval_log = _logging.getLogger(__name__)


# ─── Workspace helpers (local copy — same pattern as planner/gen_functional;
#    we'll factor into agents/_workspace_io.py if the duplication starts
#    biting). ──────────────────────────────────────────────────────────────


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


# ─── The agent itself ───────────────────────────────────────────────────


async def run_evaluator(
    spec_dir: Path,
    project_dir: Path,
    mode: Literal["initial", "rerun"] = "initial",
    verbose: bool = False,
) -> bool:
    """Run the TFactory Evaluator agent (STUB at commit 1).

    The real Evaluator (commits 2-5) will read the Executor's run
    output, compute the 5 evaluation signals per generated test, and
    write verdicts.json. This stub just advances status from
    ``generated`` → ``evaluating`` → ``evaluated_empty`` so the
    pipeline auto-advance wiring can be tested end-to-end.

    Args:
        spec_dir: TFactory workspace spec directory.
        project_dir: AIFactory project root (reserved for SDK in
            future commits; unused in the stub).
        mode: 'initial' on first run; 'rerun' if invoked after a
            Triager-requested re-evaluation. Reserved.
        verbose: forwarded to the SDK seam in future commits.

    Returns:
        True on a clean (possibly empty) evaluation pass; False on
        hard failure.

    Stub status transitions:
      generated / evaluated_empty  ← entry preconditions
      evaluating                   ← in flight
      evaluated_empty              ← no generated tests to score
      evaluator_failed             ← hard error
    """
    del project_dir, verbose  # commit 1: not yet wired into SDK
    try:
        # Mark in-flight so the portal (Task 9) can show progress.
        _write_status_patch(
            spec_dir,
            status="evaluating",
            phase=f"evaluator_{mode}_started",
        )

        # In commits 2-5 this branch will:
        #   1. Read spec_dir/test_plan.json — pick completed Lane.FUNCTIONAL
        #      subtasks (status=completed; have a generated file under
        #      spec_dir/tests/)
        #   2. Read the Executor's run output for each test
        #   3. Compute the 5 signals + assemble verdicts
        #   4. Write spec_dir/findings/verdicts.json
        #
        # Commit 1: no-op. Emit an empty verdicts.json so the
        # downstream Triager has a deterministic file to read.
        verdicts_dir = spec_dir / "findings"
        verdicts_dir.mkdir(parents=True, exist_ok=True)
        verdicts_path = verdicts_dir / "verdicts.json"
        verdicts_path.write_text(json.dumps({
            "evaluator_version": "stub-task7-commit1",
            "mode": mode,
            "verdicts": [],
            "generated_at": _now_iso(),
        }, indent=2))

        _write_status_patch(
            spec_dir,
            status="evaluated_empty",
            phase="evaluator_stub_no_op",
            verdicts_count=0,
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
    from auto-advancing. The chain forward from Gen-Functional fires
    via ``_advance_to_evaluator`` in ``gen_functional.py``.

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
