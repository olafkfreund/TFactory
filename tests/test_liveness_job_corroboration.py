"""A quiet run whose Job is still running is not stalled.

TFactory#1173: `evaluate_liveness` measured `status.json`'s `updated_at` alone --
a HEARTBEAT, not liveness. Spec 165 was flagged `watchdog_stalled` while its lane
Job had been Running for 26 minutes and had written 44 screenshots and 41 videos;
its browser lane finished 20 tests with 0 failures. The evaluator only writes
status at phase boundaries, so a long lane phase is indistinguishable from death.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agents.liveness import evaluate_liveness


def _spec(tmp_path, *, idle_seconds: float, status: str = "evaluating"):
    now = datetime(2026, 8, 25, 17, 30, tzinfo=UTC)
    updated = now - timedelta(seconds=idle_seconds)
    (tmp_path / "status.json").write_text(
        json.dumps(
            {
                "status": status,
                "phase": "evaluator_initial_started",
                "updated_at": updated.isoformat(),
            }
        )
    )
    return tmp_path, now


def test_quiet_but_job_running_is_not_stalled(tmp_path):
    """The spec-165 case: 26 minutes silent, Job alive, real work landing."""
    spec, now = _spec(tmp_path, idle_seconds=1560)
    v = evaluate_liveness(spec, now=now, deadline_seconds=600, job_active=lambda: True)
    assert v.stalled is False
    assert "still running" in v.reason


def test_quiet_and_job_gone_is_stalled(tmp_path):
    """Corroboration must not defang the watchdog: no Job means it really is dead."""
    spec, now = _spec(tmp_path, idle_seconds=1560)
    v = evaluate_liveness(spec, now=now, deadline_seconds=600, job_active=lambda: False)
    assert v.stalled is True


def test_without_the_predicate_behaviour_is_unchanged(tmp_path):
    """Omitting job_active keeps timestamp-only semantics, so no caller shifts."""
    spec, now = _spec(tmp_path, idle_seconds=1560)
    assert evaluate_liveness(spec, now=now, deadline_seconds=600).stalled is True


def test_a_fresh_heartbeat_never_consults_the_job(tmp_path):
    """Inside the deadline nothing is stalled, and no k8s call should be made."""
    spec, now = _spec(tmp_path, idle_seconds=5)

    def _boom() -> bool:  # pragma: no cover - must not be called
        raise AssertionError("job_active consulted for a healthy heartbeat")

    assert (
        evaluate_liveness(spec, now=now, deadline_seconds=600, job_active=_boom).stalled
        is False
    )


# ── The wiring (#1173, reopened) ────────────────────────────────────────────
#
# The hook above landed in #1175 and changed nothing on the cluster:
# `liveness_sweep.sweep` -- the ONLY driver -- kept calling `check_and_mark`
# without a predicate, so it defaulted to None and spec 170 was false-stalled
# again on the very build that carried the "fix". The tests below drive the
# SWEEP rather than the evaluator, because the evaluator was never the half that
# was broken; a test that only calls `evaluate_liveness` cannot see the gap.


def _swept_spec(
    root: Path, *, idle_seconds: float, status: str = "evaluating", ref: object = None
) -> tuple[Path, datetime]:
    """A spec dir laid out where iter_spec_dirs looks, optionally Job-backed."""
    now = datetime(2026, 8, 25, 17, 30, tzinfo=UTC)
    spec = root / "workspaces" / "proj" / "specs" / "spec170"
    spec.mkdir(parents=True)
    (spec / "status.json").write_text(
        json.dumps(
            {
                "status": status,
                "phase": "evaluator_initial_started",
                "updated_at": (now - timedelta(seconds=idle_seconds)).isoformat(),
            }
        )
    )
    if ref is not None:
        (spec / "worker_ref.json").write_text(json.dumps(ref))
    return spec, now


_K8S_REF = {"kind": "k8s-job", "namespace": "factory", "job_name": "factory-tfactory-x"}


def _probe_returning(*result: object):
    """Stand in for verify_dispatch._probe_job, which the closure awaits."""

    async def _probe(_namespace: str, _job_name: str) -> tuple[object, ...]:
        return result

    return _probe


def _status_of(spec: Path) -> str:
    return str(json.loads((spec / "status.json").read_text())["status"])


def test_sweep_consults_the_job_and_spares_a_live_run(tmp_path, monkeypatch):
    """Quiet + Job Running must survive the SWEEP, not just evaluate_liveness."""
    import agents.verify_dispatch as vd
    from agents.liveness_sweep import sweep

    spec, now = _swept_spec(tmp_path, idle_seconds=1560, ref=_K8S_REF)
    monkeypatch.setattr(vd, "_probe_job", _probe_returning(True, True, False))

    [(_dir, verdict)] = sweep(tmp_path, now=now, deadline_seconds=600)
    assert verdict.stalled is False
    assert _status_of(spec) == "evaluating"  # and never flipped on disk


def test_sweep_still_flips_when_the_job_is_gone(tmp_path, monkeypatch):
    """Corroboration must not defang the watchdog: no live Job means really dead."""
    import agents.verify_dispatch as vd
    from agents.liveness_sweep import sweep

    spec, now = _swept_spec(tmp_path, idle_seconds=1560, ref=_K8S_REF)
    monkeypatch.setattr(vd, "_probe_job", _probe_returning(True, False, False))

    [(_dir, verdict)] = sweep(tmp_path, now=now, deadline_seconds=600)
    assert verdict.stalled is True
    assert _status_of(spec) == "stalled"


def test_an_unanswerable_probe_reads_as_alive(tmp_path, monkeypatch):
    """FAIL CLOSED. A probe that cannot answer is not evidence of death.

    The asymmetry is the whole design: a false stall stops a live run and throws
    away work nobody can get back, while a missed stall costs the Job's own
    activeDeadlineSeconds, which k8s enforces regardless. So a raising probe --
    an API blip, a missing kube client, an import error -- must spare the run.
    """
    import agents.verify_dispatch as vd
    from agents.liveness_sweep import sweep

    async def _boom(_namespace: str, _job_name: str) -> tuple[object, ...]:
        raise RuntimeError("kube API unreachable")

    spec, now = _swept_spec(tmp_path, idle_seconds=1560, ref=_K8S_REF)
    monkeypatch.setattr(vd, "_probe_job", _boom)

    [(_dir, verdict)] = sweep(tmp_path, now=now, deadline_seconds=600)
    assert verdict.stalled is False
    assert _status_of(spec) == "evaluating"


def test_a_spec_with_no_worker_ref_keeps_timestamp_only_behaviour(tmp_path):
    """In-pod verifies (and every spec dispatched before this) are unchanged."""
    from agents.liveness_sweep import sweep

    spec, now = _swept_spec(tmp_path, idle_seconds=1560)
    [(_dir, verdict)] = sweep(tmp_path, now=now, deadline_seconds=600)
    assert verdict.stalled is True
    assert _status_of(spec) == "stalled"


def test_a_stale_ref_cannot_speak_for_an_inline_stage(tmp_path, monkeypatch):
    """planning/generating run in the control plane, so no Job is about them.

    A worker_ref left behind by an EARLIER verify would otherwise report a live
    Job and keep an orphaned inline stage alive forever -- the #774 hang the
    watchdog exists to end.
    """
    import agents.verify_dispatch as vd
    from agents.liveness_sweep import sweep

    spec, now = _swept_spec(
        tmp_path, idle_seconds=1560, status="generating", ref=_K8S_REF
    )
    monkeypatch.setattr(vd, "_probe_job", _probe_returning(True, True, False))

    [(_dir, verdict)] = sweep(tmp_path, now=now, deadline_seconds=600)
    assert verdict.stalled is True
    assert _status_of(spec) == "failed"  # inline stalls go terminal, not `stalled`


def test_job_active_probe_declines_a_ref_it_cannot_use(tmp_path):
    """Malformed / non-Job refs yield None -- "no second signal", not "no Job"."""
    from agents.liveness_sweep import job_active_probe

    spec = tmp_path / "spec"
    spec.mkdir()
    assert job_active_probe(spec) is None  # no file at all
    (spec / "worker_ref.json").write_text("{not json")
    assert job_active_probe(spec) is None
    (spec / "worker_ref.json").write_text(json.dumps({"kind": "inpod"}))
    assert job_active_probe(spec) is None
    (spec / "worker_ref.json").write_text(json.dumps({"kind": "k8s-job"}))
    assert job_active_probe(spec) is None  # no job_name to ask about


def test_dispatch_writes_the_ref_the_sweep_reads(tmp_path):
    """The two halves of the contract must agree on the file AND its shape."""
    from agents.liveness_sweep import job_active_probe
    from agents.verify_dispatch import _record_spec_worker_ref

    _record_spec_worker_ref(tmp_path, dict(_K8S_REF, spec_dir=str(tmp_path)))
    assert job_active_probe(tmp_path) is not None
