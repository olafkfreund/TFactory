"""Liveness sweep driver (#95) — periodically flag stalled tasks.

The watchdog in ``agents.liveness`` decides whether *one* task has stalled;
this is the driver that walks every in-flight task under the TFactory
workspace root and applies it. Run it on a timer — cron, a systemd timer, or
the web-server's background loop:

    python -m agents.liveness_sweep                 # sweep the default root
    python -m agents.liveness_sweep --deadline 600  # tighter idle budget

Backend-only and side-effect-light: it only ever flips a genuinely-silent
*active* stage to ``stalled`` (see ``agents.liveness`` for the fail-safe
rules) and emits a #95 stage event for each flip. Walking a workspace with no
in-flight tasks is a cheap no-op.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

from agents.liveness import StallVerdict, check_and_mark
from agents.workspace_status import now_iso

__all__ = [
    "default_workspace_root",
    "gc_terminal_worktrees",
    "iter_spec_dirs",
    "reconcile_inline_orphans",
    "sweep",
]

# Statuses at which a spec is DONE — no further verify will run, so its per-spec
# git worktree (#742) can be reclaimed. Union of the triager + verify-dispatch
# terminal sets plus the loud-fail states. Conservative on purpose: a status
# missed here just leaves a worktree lingering (wasted disk, never wrong); GC'ing
# a still-needed one would only degrade a later rerun to the shared-clone
# fallback, which is already handled.
_GC_TERMINAL_STATUSES = frozenset(
    {
        "triaged",
        "triaged_empty",
        "triager_failed",
        "failed",
        "generated_empty",
        "gen_functional_failed",
        "reviewed",
        "review_failed",
        "source_checkout_failed",
    }
)

# Inline stages that run in the control-plane process itself (not a k8s Job),
# so a pod roll/OOM/drain mid-run leaves no Job and no worker_ref for the #767
# reaper to see. These are the only statuses the startup reconcile fails.
_INLINE_ORPHAN_STATUSES = frozenset({"planning", "generating"})

_DEFAULT_ROOT = Path.home() / ".tfactory"


def default_workspace_root() -> Path:
    """Resolve the workspace root the same way as the rest of the backend:
    ``TFACTORY_WORKSPACE_ROOT`` (expanded) > ``~/.tfactory``."""
    root = os.environ.get("TFACTORY_WORKSPACE_ROOT")
    return Path(root).expanduser() if root else _DEFAULT_ROOT


def iter_spec_dirs(workspace_root: Path) -> Iterator[Path]:
    """Yield every ``<root>/workspaces/<project>/specs/<spec>`` dir that holds
    a ``status.json``. A missing/partial tree yields nothing (never raises)."""
    base = workspace_root / "workspaces"
    if not base.is_dir():
        return
    for project_dir in sorted(base.iterdir()):
        specs = project_dir / "specs"
        if not specs.is_dir():
            continue
        for spec_dir in sorted(specs.iterdir()):
            if (spec_dir / "status.json").is_file():
                yield spec_dir


def sweep(
    workspace_root: Path | None = None,
    *,
    now: datetime | None = None,
    deadline_seconds: float | None = None,
) -> list[tuple[Path, StallVerdict]]:
    """Apply the watchdog to every in-flight task under ``workspace_root``.

    Returns ``(spec_dir, verdict)`` for each task inspected; the ones just
    flipped have ``verdict.stalled is True``. ``now`` defaults to current UTC.
    """
    root = workspace_root or default_workspace_root()
    when = now or datetime.now(timezone.utc)
    results: list[tuple[Path, StallVerdict]] = []
    for spec_dir in iter_spec_dirs(root):
        verdict = check_and_mark(spec_dir, now=when, deadline_seconds=deadline_seconds)
        results.append((spec_dir, verdict))
    return results


def reconcile_inline_orphans(
    workspace_root: Path | None = None,
    *,
    now: datetime | None = None,
) -> list[tuple[Path, str]]:
    """Fail specs stranded in an INLINE stage by a control-plane restart (#774).

    The planner and gen_functional stages run in the control-plane process, not a
    k8s Job, so a pod roll / OOM / node drain mid-generation kills the in-flight
    session with no Job and no ``worker_ref`` for the #767 reaper to see — the
    spec sits at ``planning`` / ``generating`` forever, indistinguishable from
    "still working".

    Run this ONCE at web-server startup. Under the ReadWriteOnce workspaces PVC a
    fresh pod acquires the volume only after the previous holder has released it
    (is gone), and it has launched no generation of its own yet — so any spec
    still in an inline active status was necessarily orphaned by the pod that
    died. Job-backed stages (``evaluating`` / ``triaging``) are excluded on
    purpose: their Jobs survive a control-plane roll and the #767 reaper owns
    them; failing them here would clobber a live verify.

    Best-effort and fail-safe: an unreadable / non-inline spec is skipped, never
    raised on. Returns the ``(spec_dir, prior_status)`` pairs it failed.
    """
    root = workspace_root or default_workspace_root()
    when_iso = now.isoformat(timespec="seconds") if now is not None else now_iso()
    reconciled: list[tuple[Path, str]] = []
    for spec_dir in iter_spec_dirs(root):
        status_path = spec_dir / "status.json"
        try:
            status = json.loads(status_path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(status, dict):
            continue
        prior = status.get("status")
        if prior not in _INLINE_ORPHAN_STATUSES:
            continue
        status["status"] = "failed"
        status["orphaned_from"] = prior
        status["phase"] = "control_plane_restart"
        status["failed_reason"] = (
            f"control plane restarted mid-{prior}; the in-process stage had no "
            "Job to reconcile (#774)"
        )
        status["updated_at"] = when_iso
        try:
            status_path.write_text(json.dumps(status, indent=2))
        except OSError:
            continue
        reconciled.append((spec_dir, str(prior)))
    return reconciled


def gc_terminal_worktrees(
    workspace_root: Path | None = None,
) -> list[Path]:
    """Reclaim the per-spec git worktree (#742) of terminal specs.

    Each spec's build is materialized as its own worktree at ``<spec_dir>/
    .worktree``; the working tree is duplicated (objects live in the shared base
    clone), so on a small workspaces PVC (#781) they accumulate. Once a spec is
    terminal (:data:`_GC_TERMINAL_STATUSES`) no verify will touch it again, so the
    worktree is safe to remove.

    Best-effort and fail-safe: ``rmtree`` only ever removes the worktree's own
    working tree (never the base clone's objects); an unreadable / non-terminal /
    worktree-less spec is skipped, never raised on. The base clone's now-stale
    worktree registry entry is cleaned by the next ingest's ``git worktree
    prune``. Returns the spec dirs whose worktree was removed."""
    root = workspace_root or default_workspace_root()
    removed: list[Path] = []
    for spec_dir in iter_spec_dirs(root):
        worktree = spec_dir / ".worktree"
        if not worktree.is_dir():
            continue
        try:
            status = json.loads((spec_dir / "status.json").read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(status, dict):
            continue
        if status.get("status") not in _GC_TERMINAL_STATUSES:
            continue
        shutil.rmtree(worktree, ignore_errors=True)
        if not worktree.exists():
            removed.append(spec_dir)
    return removed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Flag stalled TFactory tasks as `stalled` (#95)."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Workspace root (default: $TFACTORY_WORKSPACE_ROOT or ~/.tfactory).",
    )
    parser.add_argument(
        "--deadline",
        type=float,
        default=None,
        help=(
            "Idle seconds before an active stage is stalled "
            "(default: $TFACTORY_STALL_DEADLINE_SECONDS or 900)."
        ),
    )
    args = parser.parse_args(argv)

    results = sweep(args.root, deadline_seconds=args.deadline)
    stalled = [(d, v) for d, v in results if v.stalled]
    for spec_dir, verdict in stalled:
        print(f"STALLED {spec_dir}  {verdict.reason}")
    print(f"swept {len(results)} task(s), flagged {len(stalled)} stalled")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
