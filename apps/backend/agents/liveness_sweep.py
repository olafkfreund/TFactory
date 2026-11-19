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
import contextlib
import json
import math
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

from agents.liveness import StallVerdict, check_and_mark
from agents.workspace_status import (
    INLINE_STATUSES,
    INSTANCE_ID,
    notify_terminal,
    now_iso,
)

__all__ = [
    "beat",
    "default_workspace_root",
    "drain_own_inline_stages",
    "gc_terminal_worktrees",
    "instance_alive",
    "instance_dir",
    "iter_spec_dirs",
    "job_active_probe",
    "other_instance_alive",
    "purge_old_instances",
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

_DEFAULT_ROOT = Path.home() / ".tfactory"


def _stale_seconds() -> float:
    try:
        value = float(os.environ.get("TFACTORY_INSTANCE_STALE_SECONDS", "60"))
        return value if math.isfinite(value) and value > 0 else 60.0
    except ValueError:
        return 60.0


def _legacy_seconds() -> float:
    try:
        value = float(os.environ.get("TFACTORY_LEGACY_ORPHAN_SECONDS", "900"))
        return value if math.isfinite(value) and value > 0 else 900.0
    except ValueError:
        return 900.0


def instance_dir(root: Path) -> Path:
    return root / "instances"


def beat(root: Path | None = None, instance_id: str = INSTANCE_ID) -> None:
    directory = instance_dir(root or default_workspace_root())
    directory.mkdir(parents=True, exist_ok=True)
    (directory / instance_id).touch()


def instance_alive(
    root: Path, instance_id: str, *, now: datetime, stale: float | None = None
) -> bool:
    try:
        path = instance_dir(root) / instance_id
        return path.is_file() and now.timestamp() - path.stat().st_mtime < (
            _stale_seconds() if stale is None else stale
        )
    except OSError:
        return False


def other_instance_alive(
    root: Path | None = None,
    *,
    now: datetime | None = None,
    stale: float | None = None,
    instance_id: str = INSTANCE_ID,
) -> bool:
    base = root or default_workspace_root()
    when = now or datetime.now(UTC)
    try:
        return any(
            path.name != instance_id
            and instance_alive(base, path.name, now=when, stale=stale)
            for path in instance_dir(base).iterdir()
        )
    except OSError:
        return False


def purge_old_instances(
    root: Path | None = None, *, now: datetime | None = None, max_age: float = 86400.0
) -> int:
    directory = instance_dir(root or default_workspace_root())
    when = (now or datetime.now(UTC)).timestamp()
    removed = 0
    try:
        paths = list(directory.iterdir())
    except OSError:
        return 0
    for path in paths:
        if path.name == INSTANCE_ID:
            continue
        try:
            if path.is_file() and when - path.stat().st_mtime > max_age:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def drain_own_inline_stages(  # noqa: PLR0913 - sleep/monotonic are test seams
    root: Path | None = None,
    *,
    timeout: float,
    poll: float = 2.0,
    instance_id: str = INSTANCE_ID,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> list[Path]:
    base = root or default_workspace_root()
    deadline = monotonic() + timeout
    failed: list[Path] = []
    try:
        while True:
            active = []
            for spec_dir in iter_spec_dirs(base):
                try:
                    status = json.loads((spec_dir / "status.json").read_text())
                except (json.JSONDecodeError, OSError):
                    continue
                if (
                    isinstance(status, dict)
                    and status.get("status") in INLINE_STATUSES
                    and status.get("owner_instance") == instance_id
                ):
                    active.append((spec_dir, status["status"]))
            if not active:
                break
            remaining = deadline - monotonic()
            if remaining <= 0:
                for spec_dir, prior in active:
                    status_path = spec_dir / "status.json"
                    try:
                        status = json.loads(status_path.read_text())
                    except (json.JSONDecodeError, OSError):
                        continue
                    if (
                        not isinstance(status, dict)
                        or status.get("status") != prior
                        or status.get("owner_instance") != instance_id
                    ):
                        continue
                    status.update(
                        status="failed",
                        orphaned_from=prior,
                        phase="control_plane_restart",
                        failed_reason=(
                            f"shut down mid-{prior}; the in-process stage did not finish "
                            "within the grace period (#1599)"
                        ),
                        updated_at=now_iso(),
                    )
                    try:
                        status_path.write_text(json.dumps(status, indent=2))
                    except OSError:
                        continue
                    notify_terminal(spec_dir)
                    failed.append(spec_dir)
                break
            sleep(min(poll, remaining))
    finally:
        with contextlib.suppress(OSError):
            (instance_dir(base) / instance_id).unlink()
    return failed


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


def reconcile_inline_orphans(  # noqa: PLR0912 - owner/legacy/CAS branches
    workspace_root: Path | None = None,
    *,
    now: datetime | None = None,
    instance_id: str = INSTANCE_ID,
    stale_seconds: float | None = None,
    legacy_seconds: float | None = None,
) -> list[tuple[Path, str]]:
    """Fail inline stages whose owner is gone or whose legacy status is old.

    The planner and gen_functional stages run in the control-plane process, not a
    k8s Job, so a pod roll / OOM / node drain mid-generation kills the in-flight
    session with no Job and no ``worker_ref`` for the #767 reaper to see — the
    spec sits at ``planning`` / ``generating`` forever, indistinguishable from
    "still working".

    Skip stages owned by this process or a process with a fresh heartbeat.
    Ownerless legacy stages get a grace period based on ``updated_at``.
    Job-backed stages are excluded; the #767 reaper owns them.

    Best-effort and fail-safe: an unreadable / non-inline spec is skipped, never
    raised on. Returns the ``(spec_dir, prior_status)`` pairs it failed.
    """
    root = workspace_root or default_workspace_root()
    when = now or datetime.now(UTC)
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
        if prior not in INLINE_STATUSES:
            continue
        owner = status.get("owner_instance")
        if owner == instance_id:
            continue
        if owner:
            if instance_alive(root, owner, now=when, stale=stale_seconds):
                continue
        else:
            try:
                updated = datetime.fromisoformat(status["updated_at"])
                if when.timestamp() - updated.timestamp() < (
                    _legacy_seconds() if legacy_seconds is None else legacy_seconds
                ):
                    continue
            except (KeyError, TypeError, ValueError):
                pass
        # A stage may have changed owner while the heartbeat was checked.
        try:
            current = json.loads(status_path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if (
            not isinstance(current, dict)
            or current.get("status") != prior
            or current.get("owner_instance") != owner
        ):
            continue
        current["status"] = "failed"
        current["orphaned_from"] = prior
        current["phase"] = "control_plane_restart"
        current["failed_reason"] = (
            f"control plane restarted mid-{prior}; the in-process stage had no "
            "Job to reconcile (#774)"
        )
        current["updated_at"] = when_iso
        try:
            status_path.write_text(json.dumps(current, indent=2))
        except OSError:
            continue
        notify_terminal(spec_dir)
        reconciled.append((spec_dir, str(prior)))
    return reconciled


def _delete_spec_ref(worktree: Path, spec_id: str) -> None:
    """Delete ``refs/tfactory/specs/<spec_id>`` (#1416), the per-spec fetch ref
    ``_add_spec_worktree`` made. Refs are shared by every worktree of a clone,
    so running from the worktree deletes it in the base clone. Best-effort."""
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR")
    }
    ref = f"refs/tfactory/specs/{spec_id}"
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.run(  # noqa: S603 — fixed argv; spec_id is a spec dir name
            ["git", "-C", str(worktree), "update-ref", "-d", ref],  # noqa: S607 - git from PATH
            check=False,
            capture_output=True,
            timeout=60,
            cwd=str(worktree),
            env=env,
        )


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
    prune``. The spec's own fetch ref is deleted with its worktree (#1416) so
    its objects can be collected. Returns the spec dirs whose worktree was
    removed."""
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
        if (worktree / ".git").is_file():
            _delete_spec_ref(worktree, spec_dir.name)
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
