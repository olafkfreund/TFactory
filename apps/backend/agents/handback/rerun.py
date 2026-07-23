"""Pipeline rerun core — shared by the ``task_rerun`` MCP tool and the inbound
AIFactory completion webhook (epic #182, the automatic fail→handback→fix→re-test
loop).

Resets a TFactory task's lane + status to ``pending`` and re-fires the Planner,
which auto-chains Gen-Functional → Executor → Evaluator → Triager through the
``TFACTORY_AUTO_*`` gated ``schedule_<next>`` calls. ``schedule_planner`` runs the
Planner in-process as an ``asyncio`` task (``asyncio.create_task``), so this is
callable from any running event loop — the MCP tool *and* a FastAPI request
handler — without spawning a subprocess.

Self-contained path helpers (stdlib only) so importing this stays cheap and
avoids a circular import with ``task_control`` (which calls into here).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_DEFAULT_ROOT = Path.home() / ".tfactory"


def _workspace_root(root: Path | None = None) -> Path:
    """Resolve the TFactory workspace root. Explicit arg > env > default."""
    if root is not None:
        return root
    env = os.environ.get("TFACTORY_WORKSPACE_ROOT")
    return Path(env).expanduser() if env else _DEFAULT_ROOT


def spec_dir_for(project_id: str, spec_id: str, root: Path | None = None) -> Path:
    """Absolute path to a task's spec directory."""
    return _workspace_root(root) / "workspaces" / project_id / "specs" / spec_id


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _project_root(project_id: str, root: Path | None = None) -> Path:
    """The AIFactory project's checkout path, from projects.json (``.`` if absent)."""
    pf = _workspace_root(root) / "projects.json"
    if pf.exists():
        try:
            data = json.loads(pf.read_text())
        except (json.JSONDecodeError, OSError):
            data = {}
        for p in data.get("projects", []):
            if p.get("id") == project_id:
                return Path(p.get("root_path", ".")).expanduser()
    return Path(".")


def _rerun_sut_dir(spec_dir: Path, project_id: str, root: Path | None = None) -> Path:
    """The SUT directory a rerun should verify against.

    The original ingest materialized this spec's build as its OWN git worktree at
    ``<spec_dir>/.worktree`` (#742). A rerun must resolve to THAT worktree, not
    the shared project clone (``root_path``) whose HEAD another spec may now own —
    otherwise the rerun re-introduces the exact cross-spec leak worktrees fixed.
    Falls back to the shared clone when the worktree is absent (target-mode
    ingest, or a worktree GC'd / lost to a pod roll) — no worse than before #742.
    """
    worktree = spec_dir / ".worktree"
    if worktree.is_dir():
        return worktree
    return _project_root(project_id, root)


def rerun_pipeline(
    project_id: str,
    spec_id: str,
    *,
    lane: str = "unit",
    root: Path | None = None,
) -> dict[str, Any]:
    """Reset a task's lane + status to pending and re-fire the Planner.

    Raises ``FileNotFoundError`` if the task has no ``status.json``. Returns a
    summary dict (``task_id``, ``rerun_count``, ``status``, ``planner_scheduled``).
    ``planner_scheduled`` is ``False`` when ``TFACTORY_AUTO_PLAN=0`` or the
    Planner isn't importable (minimal venv / tests) — state is still reset so a
    later manual rerun is correct.
    """
    spec_dir = spec_dir_for(project_id, spec_id, root)
    status_file = spec_dir / "status.json"
    if not status_file.exists():
        raise FileNotFoundError(f"no status.json for {project_id}:{spec_id}")
    status = json.loads(status_file.read_text())

    rerun_count = int(status.get("rerun_count", 0)) + 1
    status["rerun_count"] = rerun_count
    status.setdefault("lane_progress", {})[lane] = "pending"
    status["status"] = "pending"
    status["phase"] = "created"
    status["updated_at"] = _now_iso()
    status_file.write_text(json.dumps(status, indent=2))

    # Re-fire the Planner against the existing snapshot. schedule_planner is
    # gated by TFACTORY_AUTO_PLAN and each stage's success path auto-chains the
    # next agent, so this one call drives the whole pipeline.
    planner_scheduled = False
    try:
        from agents.planner import schedule_planner

        task = schedule_planner(
            spec_dir=spec_dir,
            project_dir=_rerun_sut_dir(spec_dir, project_id, root),
            mode="initial",
        )
        planner_scheduled = task is not None
    except ImportError:
        pass  # planner not importable (minimal venv) — status stays pending

    return {
        "task_id": f"{project_id}:{spec_id}",
        "lane": lane,
        "rerun_count": rerun_count,
        "status": "pending",
        "planner_scheduled": planner_scheduled,
    }
