"""Triager agent — Task 8, issue #9.

Final agent in the six-agent TFactory pipeline:

    Planner → Gen-Functional → Executor → Evaluator → Triager

Reads ``findings/verdicts.json`` (written by the Evaluator) plus the
generated test files, decides which to commit, dedups byte-identical
or whitespace-normalised duplicates, renders a triage report
(``findings/triage_report.md`` + ``findings/triage_report.json``),
writes accepted/flagged tests onto AIFactory's feature branch via
the git writer, and posts a PR comment via ``gh pr comment``.

Task 8 commits (in flight):

  ✓ commit 1 — Auto-fire scaffold + stub  (this commit)
  ⬜ commit 2 — Dedup + ranking primitives (byte-identical +
                 whitespace-normalised dedup, verdict-priority rank)
  ⬜ commit 3 — Triage report rendering (report.md golden-file +
                 report.json schema)
  ⬜ commit 4 — git_writer.py + pr_comment helper (dry-run + real-run)
  ⬜ commit 5 — Real run_triager wires everything; trim AIFactory's
                 runners/github/ to the small pr_comment.py
  ⬜ commit 6 — Integration test + close #9
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


_triage_log = _logging.getLogger(__name__)


# ─── Workspace helpers (local copy — same pattern as planner /
#    gen_functional / evaluator) ──────────────────────────────────────────


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


async def run_triager(
    spec_dir: Path,
    project_dir: Path,
    mode: Literal["initial", "rerun"] = "initial",
    verbose: bool = False,
) -> bool:
    """Run the TFactory Triager agent (STUB at commit 1).

    The real Triager (commits 2-5) will read verdicts.json, dedup +
    rank accepted/flagged tests, render the triage report files,
    commit accepted tests via the git writer, and post a PR comment.
    This stub just advances status from ``evaluated`` → ``triaging`` →
    ``triaged_empty`` so the pipeline auto-advance wiring can be tested
    end-to-end.

    Args:
        spec_dir: TFactory workspace spec directory.
        project_dir: AIFactory project root (used by the git writer
            + PR comment in future commits; unused in the stub).
        mode: 'initial' on first run; 'rerun' for retriggers from
            the portal. Reserved.
        verbose: forwarded to the SDK seam in future commits.

    Returns:
        True on a clean (possibly empty) triage pass; False on
        hard failure.

    Stub status transitions:
      evaluated / evaluated_empty  ← entry preconditions
      triaging                     ← in flight
      triaged_empty                ← no verdicts to act on
      triager_failed               ← hard error
    """
    del project_dir, verbose  # commit 1: not yet wired
    try:
        _write_status_patch(
            spec_dir,
            status="triaging",
            phase=f"triager_{mode}_started",
        )

        # In commits 2-5 this branch will:
        #   1. Load findings/verdicts.json (written by Evaluator).
        #   2. Filter to verdict in {accept, flag} (drop rejects).
        #   3. Dedup by byte-identical + whitespace-normalised hashes.
        #   4. Rank by (verdict_priority, coverage_delta, semantic_relevance).
        #   5. Render findings/triage_report.md + .json.
        #   6. Commit accepted/flagged tests via git_writer.
        #   7. Post PR comment via gh pr comment (or write argv to
        #      findings/pr_comment.cmd for dry-run).
        #
        # Commit 1: no-op. Emit empty placeholders so the downstream
        # portal (Task 9) has deterministic files to read.
        findings_dir = spec_dir / "findings"
        findings_dir.mkdir(parents=True, exist_ok=True)

        (findings_dir / "triage_report.json").write_text(json.dumps({
            "triager_version": "stub-task8-commit1",
            "mode": mode,
            "committed": [],
            "rejected": [],
            "flagged": [],
            "dedup_collisions": [],
            "generated_at": _now_iso(),
        }, indent=2))

        (findings_dir / "triage_report.md").write_text(
            "# Triage Report (stub)\n\n"
            f"Generated at {_now_iso()}.\n\n"
            "_Commit 1 stub — real report rendering lands in commit 3._\n"
        )

        _write_status_patch(
            spec_dir,
            status="triaged_empty",
            phase="triager_stub_no_op",
            committed_count=0,
            rejected_count=0,
            flagged_count=0,
        )
        return True

    except Exception as exc:
        _triage_log.error(
            "triager failed: %s\n%s", exc, traceback.format_exc()
        )
        _write_status_patch(
            spec_dir,
            status="triager_failed",
            phase=f"triager_{mode}_exception",
            triager_error=str(exc)[:500],
        )
        return False


# ─── Auto-fire scheduler ─────────────────────────────────────────────────
#
# Same GC-anchor pattern as _BG_PLANNER_TASKS, _BG_GEN_FUNCTIONAL_TASKS,
# _BG_EVALUATOR_TASKS. Evaluator's success path (status=evaluated) calls
# schedule_triager after writing the status — gated on env so the test
# suite stays deterministic.

_BG_TRIAGER_TASKS: set[asyncio.Task] = set()


def schedule_triager(
    spec_dir: Path,
    project_dir: Path,
    mode: Literal["initial", "rerun"] = "initial",
) -> asyncio.Task | None:
    """Fire-and-forget Triager, gated by ``TFACTORY_AUTO_TRIAGE``.

    Default ON (env var unset or "1"). Test fixtures should set
    ``TFACTORY_AUTO_TRIAGE=0`` to keep evaluator's success path from
    auto-advancing. The chain forward from Evaluator fires via
    ``_advance_to_triager`` in ``evaluator.py``.

    Returns the scheduled asyncio.Task, or None if the env var disables
    auto-triage. Each scheduled task is anchored in
    ``_BG_TRIAGER_TASKS`` until done (cleared via done_callback).
    """
    if os.environ.get("TFACTORY_AUTO_TRIAGE", "1") == "0":
        return None
    task = asyncio.create_task(
        run_triager(spec_dir, project_dir, mode=mode)
    )
    _BG_TRIAGER_TASKS.add(task)
    task.add_done_callback(_BG_TRIAGER_TASKS.discard)
    return task
