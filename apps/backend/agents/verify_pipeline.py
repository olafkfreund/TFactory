"""Verify orchestration entrypoint — the evaluate→triage pipeline for one spec.

RFC-0016 Phase 2 (TFactory #466). This is the thin, deterministic runnable that
the per-task k8s Job executes when the control/execution split is enabled
(``TFACTORY_VERIFY_EXEC=kubejob``). The Job runs it on the TFactory **runtime
image** (which ships this ``agents`` package) with the backend on PYTHONPATH —
NOT the thin nix-runner image, which has no ``agents`` (TFactory #466). The
per-lane test Jobs this pipeline dispatches still run on the nix-runner image and
get the SUT toolchain from ``nix develop``. It runs the same Evaluator → Triager
pipeline
the control plane runs in-pod today, but **synchronously** (no fire-and-forget
auto-advance) so the Job process owns the whole verify and exits with a real
status the reaper/reconciler can trust.

Why a dedicated entrypoint: the in-pod path chains stages by scheduling the next
one as a background asyncio task (``schedule_triager`` after the evaluator). That
is right for a long-lived control plane, but a one-shot Job must run the stages
to completion in its own process and then terminate — otherwise the Job pod would
exit while triage is still a detached task. So this module disables the
auto-advance env (``TFACTORY_AUTO_TRIAGE``/``TFACTORY_AUTO_EVALUATE``) for its own
run and calls ``run_evaluator`` then ``run_triager`` directly.

The Job updates its own ``job-state`` row (apis/job-state.schema.json) on exit so
the control plane reconciles by polling Postgres (a missed event never strands a
job — concurrency-conventions.md §3). The write is best-effort + idempotent: it
maps the spec's final ``status.json`` status to the canonical lifecycle state via
the durable store, and never raises (a store outage must not change the verify
verdict the Job already wrote to the workspace).

Run as the Job command:

    python -m agents.verify_pipeline --spec <spec_dir> --project <project_dir> \
        --job-id <job_id> [--correlation-key <key>]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from agents.utils import repair_linked_worktree
from tools.runners.job_tracing import init_agent_tracing

_log = logging.getLogger(__name__)

# Spec statuses that mean the verify produced a real verdict (the Triager ran to
# completion). Anything else terminal-by-name with no verdict is the
# "lanes pending, no verdict" stall the reaper/store classify as `stuck` (#464).
_VERDICT_STATUSES = frozenset({"triaged", "triaged_empty", "evaluated_empty"})
_FAILED_STATUSES = frozenset({"evaluator_failed", "triager_failed"})


async def run_verify_pipeline(
    spec_dir: Path, project_dir: Path, *, mode: str = "initial"
) -> tuple[bool, str]:
    """Run Evaluator then Triager synchronously for one spec.

    Returns ``(ok, final_status)`` where ``final_status`` is the spec's
    ``status.json`` status after the pipeline (the authoritative verdict marker).
    Disables the auto-advance scheduler for this process so the stages run inline
    (a one-shot Job must not leave triage as a detached background task).
    """
    # The Job owns the chain — turn off fire-and-forget auto-advance so the
    # evaluator does not also schedule a *second* background triager that would
    # outlive this process.
    os.environ["TFACTORY_AUTO_EVALUATE"] = "0"
    os.environ["TFACTORY_AUTO_TRIAGE"] = "0"

    from agents.deploy_lane import maybe_run_deploy_lane  # noqa: PLC0415
    from agents.evaluator import run_evaluator  # noqa: PLC0415 - lazy by design
    from agents.triager import run_triager  # noqa: PLC0415 - lazy by design
    from agents.workspace_status import read_status  # noqa: PLC0415 - lazy by design

    eval_ok = await run_evaluator(spec_dir, project_dir, mode=mode)  # type: ignore[arg-type]

    # RFC-0013 deploy gate (#597): when the contract marks the change high-risk or
    # production, run the DRY-RUN deploy lane and persist the proof BEFORE triage,
    # so the triager's deploy-gate (_deploy_gate_annotation) reads a real
    # verification instead of always holding for a missing proof. Best-effort +
    # DRY-RUN only — never blocks the pipeline on its own.
    maybe_run_deploy_lane(spec_dir, project_dir)

    # Always attempt triage even on a soft evaluator miss: an empty/failed
    # evaluation still needs the triager to render the honest report. A hard
    # evaluator crash already wrote evaluator_failed to status.json.
    triage_ok = await run_triager(spec_dir, project_dir, mode=mode)  # type: ignore[arg-type]

    final_status = str(read_status(spec_dir).get("status") or "")

    # Running-cost: when the triager reaches a terminal status it emits a
    # completion event carrying the full usage block. If the run spent LLM tokens
    # (planner / gen-functional / evaluator) but did NOT terminally triage (a gate
    # halt, or a non-terminal final status), no event carried that cost — emit a
    # usage snapshot so the cockpit still reflects the accrued spend. Best-effort.
    from agents.triager import (  # noqa: PLC0415 - lazy by design
        _TERMINAL_STATUSES,
        emit_usage_snapshot,
    )

    if final_status not in _TERMINAL_STATUSES:
        emit_usage_snapshot(spec_dir, read_status(spec_dir))

    ok = eval_ok and triage_ok
    return ok, final_status


async def _record_terminal(
    job_id: str,
    *,
    final_status: str,
    spec_dir: Path | None = None,
    correlation_key: str | int | None = None,
    error_override: str | None = None,
) -> None:
    """Idempotently write the Job's terminal job-state row. Best-effort.

    Imports the web-server durable store lazily (it lives in a sibling app) so
    the backend keeps working when that package isn't importable (dev/test). The
    row is keyed by ``job_id`` and was already enqueued (with its correlation
    key) at dispatch; this only advances it to a terminal state. ``has_verdict``
    drives the #464 no-verdict→stuck rule: a terminal-by-name status with no real
    verdict is recorded ``stuck``, not ``done``.

    RFC-0016 #190: before the terminal write, upload the verify's findings +
    evidence to object storage and stamp the resulting ``artifacts[]`` URIs onto
    the row. Both steps are fail-open — a store/upload failure never changes the
    verdict the Job already wrote to its workspace.

    ``error_override`` (#1243) is the one thing that DOES change the verdict: it
    says the workspace this verdict describes did not survive the pod (a failed
    restore, or a failed evidence push-back). ``has_verdict`` is forced False so
    the store's #464 rule records ``stuck`` instead of ``done`` — the Job exiting
    non-zero is not enough on its own, because ``reconcile_and_reap_once`` skips
    the k8s probe entirely once a terminal row exists.
    """
    has_verdict = final_status in _VERDICT_STATUSES
    error = None
    if final_status in _FAILED_STATUSES:
        error = f"verify failed in-Job (status={final_status})"
    if error_override:
        has_verdict = False
        error = error_override

    # RFC-0015 §4 D2: persist the requirement->test->VAL traceability matrix onto
    # the durable job_states row (the #468 store / #465 verification data) so the
    # CFactory matrix view (#126) can render AC x test x VAL x verdict straight
    # from Postgres, not just from the spec workspace. Best-effort: read it from
    # the verification block the triager already wrote to findings; a missing or
    # unreadable block just omits the key (the row's result is otherwise unchanged).
    result: dict[str, object] = {"status": final_status} if final_status else {}
    if spec_dir is not None:
        trace = _read_traceability(spec_dir)
        if trace:
            result["traceability"] = trace

    artifacts: list[dict[str, object]] | None = None
    if spec_dir is not None:
        from agents.verify_artifacts import (  # noqa: PLC0415 - lazy by design
            emit_verify_artifacts,
        )

        uploaded = emit_verify_artifacts(
            spec_dir, job_id=job_id, correlation_key=correlation_key
        )
        artifacts = uploaded or None

    try:
        from server.services import (  # type: ignore[import-not-found]  # noqa: PLC0415
            job_state_store as jss,
        )
    except ImportError:
        _log.warning(
            "[verify-pipeline] durable job-state store unavailable; "
            "skipping terminal write for job_id=%s (status=%s)",
            job_id,
            final_status,
        )
        return
    await jss.record_terminal(
        job_id,
        service_status=final_status or None,
        has_verdict=has_verdict,
        result=result or None,
        error=error,
        artifacts=artifacts,
    )


def _read_traceability(spec_dir: Path) -> list[dict[str, object]] | None:
    """Read the RFC-0015 D2 traceability rows from the emitted verification block.

    The triager writes ``findings/verification.json`` (the RFC-0006 block) with the
    additive ``traceability[]`` array. This reads it back so the durable terminal
    write can carry the matrix. Best-effort: a missing/unreadable block or a block
    without traceability yields ``None`` (the row's result is then unchanged).
    """
    try:
        path = spec_dir / "findings" / "verification.json"
        if not path.is_file():
            return None
        import json  # noqa: PLC0415 - lazy; this path is best-effort

        block = json.loads(path.read_text())
        if not isinstance(block, dict):
            return None
        trace = block.get("traceability")
        return trace if isinstance(trace, list) and trace else None
    except (OSError, ValueError):
        return None


def main(argv: list[str] | None = None) -> int:
    # First, before argument parsing: this is the only thing in the process that
    # can make the run's trace reach the verify (Factory#638). ``verify_dispatch``
    # puts TRACEPARENT + the OTLP config on this Job; this opens the one span
    # that continues it, and everything the pipeline starts below is a child of
    # it. No-op when TRACEPARENT is unset (the in-pod path, local CLI runs), and
    # it never raises — tracing is not allowed to be why a verify fails.
    #
    # Before argparse rather than after, so a Job that dies on a bad argument
    # still reports a span: an exit is a fact about the run, and the span is
    # flushed from an atexit hook on a bounded budget either way.
    init_agent_tracing()

    parser = argparse.ArgumentParser(
        prog="agents.verify_pipeline",
        description="Run the TFactory evaluate→triage verify pipeline for one spec.",
    )
    parser.add_argument("--spec", required=True, help="Spec workspace directory.")
    parser.add_argument("--project", required=True, help="Project (SUT) directory.")
    parser.add_argument(
        "--job-id",
        default=os.environ.get("JOB_ID"),
        help="Durable job-state id (defaults to $JOB_ID).",
    )
    parser.add_argument(
        "--correlation-key",
        default=os.environ.get("CORRELATION_KEY"),
        help="RFC-0001 correlation key (defaults to $CORRELATION_KEY).",
    )
    parser.add_argument("--mode", default="initial", choices=["initial", "rerun"])
    args = parser.parse_args(argv)

    # TFactory#1159 packed path: /work is an emptyDir and the workspace arrives by
    # reference, so it must be on disk before ANY stage (or the git repair below)
    # looks at it. Returns None on the co-mounted path — WORKSPACE_URI is unset
    # there — which is also how we know at the end whether to push anything back.
    from agents.verify_workspace import (  # noqa: PLC0415 - lazy by design
        push_back_workspace,
        restore_workspace,
    )

    try:
        packed_root = restore_workspace()
    except Exception as exc:  # noqa: BLE001 - any restore failure, same outcome
        # #1243: this used to raise straight out of main(), so the pod died with a
        # traceback and NOTHING wrote the job-state row — recovery then depended
        # entirely on the reaper. Record the terminal row here instead, under a
        # *_failed status: an empty status maps to `queued`, which would leave the
        # row holding an admission slot rather than releasing it.
        _log.exception("[verify-pipeline] restoring the packed workspace failed")
        _record_terminal_now(
            args,
            final_status="workspace_restore_failed",
            error_override=f"restoring the packed workspace failed: {exc}",
        )
        return 1

    spec_dir = Path(args.spec)
    project_dir = Path(args.project)

    # Before ANY stage touches git: the spec tree was created as a linked
    # worktree on the control plane and its gitdir pointer still names the
    # control-plane path, which does not exist under this Job's /work mount
    # (#868). Nothing has run git yet, so this is the one place a single repair
    # fixes every downstream caller (git_writer, dependency_review, planner,
    # agents.utils) at once.
    repair_linked_worktree(project_dir)

    ok, final_status = asyncio.run(
        run_verify_pipeline(spec_dir, project_dir, mode=args.mode)
    )

    # The evidence push-back, and the LAST thing that touches the workspace: on the
    # packed path everything above wrote into an emptyDir that dies with this pod.
    # emit_verify_artifacts (below, inside _record_terminal) already uploads the
    # findings + evidence tree file-by-file, but it is fail-open by design and
    # covers only what it enumerates — status.json, the lane staging dirs and the
    # screenshots outside findings/evidence are not in that set. This repacks the
    # whole root.
    #
    # BEFORE the terminal write, not after (#1243): the durable row is the only
    # thing the control plane reads. `reconcile_and_reap_once` short-circuits on
    # "a terminal row the Job already wrote wins" and never probes the Job, so a
    # verdict written first could no longer be corrected by a `return 1` here —
    # the run stayed green with its evidence gone. The failure is carried into
    # `_record_terminal` as error_override, which records `stuck` + the reason.
    push_back_error: str | None = None
    if packed_root is not None:
        if not args.job_id:
            _log.error(
                "[verify-pipeline] packed workspace with no --job-id/$JOB_ID; "
                "the evidence has nowhere to go"
            )
            return 1
        try:
            push_back_workspace(
                root=packed_root,
                job_id=args.job_id,
                correlation_key=args.correlation_key,
            )
        except Exception as exc:  # noqa: BLE001 - ANY failure here loses evidence
            _log.exception(
                "[verify-pipeline] pushing the packed workspace back failed; "
                "the run's evidence is lost with this pod"
            )
            push_back_error = f"pushing the packed workspace back failed: {exc}"

    if args.job_id:
        asyncio.run(
            _record_terminal(
                args.job_id,
                final_status=final_status,
                spec_dir=spec_dir,
                correlation_key=args.correlation_key,
                error_override=push_back_error,
            )
        )
    else:
        _log.warning(
            "[verify-pipeline] no --job-id/$JOB_ID; skipping durable terminal write"
        )

    # Exit non-zero on a hard failure so the Job is marked failed even if the
    # durable write was skipped (the reaper then reaps on the k8s-side signal).
    return 1 if push_back_error else (0 if ok else 1)


def _record_terminal_now(
    args: argparse.Namespace, *, final_status: str, error_override: str
) -> None:
    """Write the terminal row for a failure that happened outside the pipeline.

    Separate from the in-line write at the end of ``main`` only because it runs
    before there is a workspace on disk: no spec tree means no artifacts and no
    traceability to stamp. Best-effort, like every other durable write here.
    """
    if not args.job_id:
        _log.warning(
            "[verify-pipeline] no --job-id/$JOB_ID; skipping durable terminal write"
        )
        return
    asyncio.run(
        _record_terminal(
            args.job_id,
            final_status=final_status,
            correlation_key=args.correlation_key,
            error_override=error_override,
        )
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
