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
import asyncio
import json
import os
import shutil
import sys
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

from agents.liveness import StallVerdict, check_and_mark
from agents.workspace_status import now_iso

__all__ = [
    "default_workspace_root",
    "gc_terminal_worktrees",
    "iter_spec_dirs",
    "job_active_probe",
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
    when = now or datetime.now(UTC)
    results: list[tuple[Path, StallVerdict]] = []
    for spec_dir in iter_spec_dirs(root):
        # #1173: the watchdog gained a second-signal hook but nothing ever passed
        # one, so it kept flipping on the heartbeat alone. Hand it the probe here
        # — the sweep is the only driver, so wiring it once covers every flip.
        verdict = check_and_mark(
            spec_dir,
            now=when,
            deadline_seconds=deadline_seconds,
            job_active=job_active_probe(spec_dir),
        )
        results.append((spec_dir, verdict))
    return results


def job_active_probe(spec_dir: Path) -> Callable[[], bool] | None:
    """Build the watchdog's SECOND liveness signal for a Job-backed spec (#1173).

    ``status.json``'s ``updated_at`` is a heartbeat, not liveness: the evaluator
    writes it only at phase boundaries, so a lane that spends 26 minutes
    materializing a flake, serving the page and recording video per spec is
    byte-identical to a dead one. Specs 165 and 170 were both flipped
    ``watchdog_stalled`` mid-flight and then finished green — one signal cannot
    tell "quiet" from "dead". The k8s Job the spec was dispatched as is a
    genuinely independent second signal, and ``verify_dispatch`` now writes its
    coordinates next to ``status.json`` so this filesystem walk can reach it.

    Returns ``None`` — meaning "no second signal, keep the timestamp-only
    verdict" — when the spec has no usable ref: an in-pod verify, a spec
    dispatched before this landed, or a ref we cannot parse. That is deliberately
    NOT the same as answering "no Job", which would read as death.

    The probe itself FAILS CLOSED to *alive*. Every unanswerable step — the lazy
    import, the event loop, the k8s API call (``_probe_job`` already reports a
    Job active on any API error) — returns ``True``. The asymmetry is the whole
    reason: a false stall stops a live run and discards work that cannot be
    recovered, while a missed stall costs at most the Job's own
    ``activeDeadlineSeconds``, which k8s enforces whatever we think. The tie goes
    to "still alive".
    """
    try:
        # Lazy so the backend's sweep entrypoint does not drag the dispatch module
        # in at import time; the constant lives with the writer so the two halves
        # of the contract cannot drift apart. Importing it costs nothing — the k8s
        # client itself is loaded lazily, deeper in, and only by ``_probe_job``.
        from agents.verify_dispatch import SPEC_WORKER_REF_FILE  # noqa: PLC0415

        ref = json.loads((spec_dir / SPEC_WORKER_REF_FILE).read_text(encoding="utf-8"))
    except (ImportError, json.JSONDecodeError, OSError):
        return None
    if not isinstance(ref, dict) or ref.get("kind") != "k8s-job":
        return None
    job_name = ref.get("job_name")
    if not isinstance(job_name, str) or not job_name:
        return None
    namespace = ref.get("namespace") or "factory"

    def _active() -> bool:
        # Inside the closure on purpose: evaluate_liveness only calls this once
        # a spec is already past its deadline, so the k8s round-trip never
        # happens on the common healthy tick — that stays a read of one file.
        try:
            from agents.verify_dispatch import _probe_job  # noqa: PLC0415

            # The sweep is sync (it runs in a worker thread off the web-server
            # loop, or straight from the CLI), so there is no loop to await on.
            _exists, active, _succeeded = asyncio.run(_probe_job(namespace, job_name))
        except Exception:  # noqa: BLE001 - an unanswerable probe must read alive
            return True
        return bool(active)

    return _active


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
