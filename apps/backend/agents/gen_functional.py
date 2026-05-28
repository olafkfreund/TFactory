"""Gen-Functional agent — Task 6, issue #7.

Second agent in the six-agent TFactory pipeline (Planner ← Gen-Functional →
Executor → Evaluator → Triager). Reads the Planner's emitted
`test_plan.json`, generates pytest test code for each `Lane.FUNCTIONAL`
subtask via the Claude Agent SDK, runs two MVP guardrails per subtask
(pre-flight static check + flake-risk lint), and either commits the
test file or writes a `context/replan_request.json` for the Planner.

This file is the STUB at commit 1 of 6 of Task 6 — it just advances the
status from `planned`/`planned_empty` → `generating` → `generated_empty`
so the pipeline auto-advance wiring can be tested end-to-end. Real
SDK invocation + the two guardrails land in commits 2-5.

  ✓ commit 1 — Auto-fire scaffold + stub  (this commit)
  ⬜ commit 2 — Pre-flight static check (subprocess introspection)
  ⬜ commit 3 — Flake-risk lint (AST patterns)
  ⬜ commit 4 — gen_functional.md prompt + assembly helper
  ⬜ commit 5 — Real run_gen_functional with SDK + guards + replan_request
  ⬜ commit 6 — Integration test + close #7
"""

import asyncio
import json
import logging as _logging
import os
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal


_gen_log = _logging.getLogger(__name__)


# ─── Helpers shared with planner.py — keep these local to avoid an extra
#    import surface. If the duplication starts hurting, factor into a
#    new agents/_workspace_io.py module. ──────────────────────────────────

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


# ─── The agent itself ─────────────────────────────────────────────────────

async def run_gen_functional(
    spec_dir: Path,
    project_dir: Path,
    mode: Literal["initial", "rerun"] = "initial",
    verbose: bool = False,
) -> bool:
    """Run the Gen-Functional agent — STUB at commit 1/6.

    Args:
        spec_dir: TFactory workspace spec dir
            (``~/.tfactory/workspaces/<project_id>/specs/<spec_id>/``).
        project_dir: AIFactory project root_path (Glob/Grep target;
            unused in stub).
        mode: 'initial' for first generation pass, 'rerun' for a
            re-execution (e.g. after a replan landed). Stub ignores;
            real impl will branch.
        verbose: forwarded to ``run_agent_session`` once real impl lands.

    Returns:
        True on success (including ``generated_empty`` — that's a
        warning, not a failure). False on hard failure.

    Stub behavior:
        - status.json: status=generating, phase=gen_functional_started
        - status.json: status=generated_empty + planner_warning that
          the stub didn't actually generate anything
    """
    if not spec_dir.is_dir():
        _gen_log.error("gen_functional: spec_dir %s does not exist", spec_dir)
        return False

    try:
        _write_status_patch(
            spec_dir,
            status="generating",
            phase=f"gen_functional_{mode}_started",
        )

        # Yield to the event loop so callers can observe the generating
        # state. The real impl spends seconds-to-minutes here (one SDK
        # session per Lane.FUNCTIONAL subtask).
        await asyncio.sleep(0)

        # Sanity check: a plan exists. Without it Gen-Functional can't
        # do anything; surface as a generated_empty warning rather than
        # silent success.
        plan_file = spec_dir / "test_plan.json"
        warnings: list[str] = [
            "stub gen_functional (commit 1/6) — no tests generated; real "
            "agent lands in commit 5"
        ]
        if not plan_file.exists():
            warnings.append("test_plan.json missing — Planner didn't run?")

        _write_status_patch(
            spec_dir,
            status="generated_empty",
            phase=f"gen_functional_{mode}_stub_complete",
            gen_functional_warnings=warnings,
            tests_generated=0,
        )
        return True

    except Exception as exc:
        _gen_log.error(
            "gen_functional stub failed: %s\n%s", exc, traceback.format_exc()
        )
        _write_status_patch(
            spec_dir,
            status="gen_functional_failed",
            phase=f"gen_functional_{mode}_error",
            gen_functional_error=str(exc)[:500],
        )
        return False


# ─── Auto-fire scheduler ─────────────────────────────────────────────────
#
# Same GC-anchor pattern as planner's _BG_PLANNER_TASKS. The planner
# success paths call schedule_gen_functional after writing
# status=planned / planned_empty; gating on env keeps tests
# deterministic.

_BG_GEN_FUNCTIONAL_TASKS: set[asyncio.Task] = set()


def schedule_gen_functional(
    spec_dir: Path,
    project_dir: Path,
    mode: Literal["initial", "rerun"] = "initial",
) -> asyncio.Task | None:
    """Fire-and-forget Gen-Functional, gated by ``TFACTORY_AUTO_GENERATE``.

    Default off in test fixtures (set ``TFACTORY_AUTO_GENERATE=0``);
    production sets ``=1`` so the pipeline auto-advances from Planner
    to Gen-Functional with no manual step.

    Returns the scheduled asyncio.Task, or None if the env var disables
    auto-generation. Each scheduled task is anchored in
    ``_BG_GEN_FUNCTIONAL_TASKS`` until done (cleared via done_callback).
    """
    if os.environ.get("TFACTORY_AUTO_GENERATE", "1") == "0":
        return None
    task = asyncio.create_task(
        run_gen_functional(spec_dir, project_dir, mode=mode)
    )
    _BG_GEN_FUNCTIONAL_TASKS.add(task)
    task.add_done_callback(_BG_GEN_FUNCTIONAL_TASKS.discard)
    return task
