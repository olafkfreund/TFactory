"""Tests for the liveness sweep driver (#95)."""

from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from agents.liveness_sweep import (
    beat,
    default_workspace_root,
    drain_own_inline_stages,
    iter_spec_dirs,
    main,
    other_instance_alive,
    reconcile_inline_orphans,
    sweep,
)
from agents.workspace_status import INSTANCE_ID, write_status_patch

_NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="seconds")


def _spec(root: Path, project: str, spec: str, **status: object) -> Path:
    d = root / "workspaces" / project / "specs" / spec
    d.mkdir(parents=True, exist_ok=True)
    if status:
        (d / "status.json").write_text(json.dumps(status))
    return d


# ── iter_spec_dirs ──────────────────────────────────────────────────────────


def test_iter_finds_only_dirs_with_status(tmp_path: Path) -> None:
    a = _spec(tmp_path, "p1", "specA", status="generating", updated_at=_iso(_NOW))
    b = _spec(tmp_path, "p1", "specB", status="triaging", updated_at=_iso(_NOW))
    _spec(tmp_path, "p1", "specC")  # no status.json → skipped
    found = set(iter_spec_dirs(tmp_path))
    assert found == {a, b}


def test_iter_missing_tree_is_empty(tmp_path: Path) -> None:
    assert list(iter_spec_dirs(tmp_path / "nope")) == []


# ── sweep ───────────────────────────────────────────────────────────────────


def test_sweep_flips_stalled_and_leaves_others(tmp_path: Path) -> None:
    stale = _spec(
        tmp_path,
        "p1",
        "stalledSpec",
        status="generating",
        updated_at=_iso(_NOW - timedelta(seconds=3600)),
    )
    fresh = _spec(
        tmp_path,
        "p1",
        "freshSpec",
        status="triaging",
        updated_at=_iso(_NOW - timedelta(seconds=10)),
    )
    settled = _spec(
        tmp_path,
        "p2",
        "doneSpec",
        status="triaged",
        updated_at=_iso(_NOW - timedelta(days=1)),
    )

    results = sweep(tmp_path, now=_NOW, deadline_seconds=900)
    by_dir = {d: v for d, v in results}

    assert by_dir[stale].stalled is True
    assert by_dir[fresh].stalled is False
    assert by_dir[settled].stalled is False  # terminal → never flipped

    # An inline `generating` stall is taken terminal `failed` (not `stalled`), so
    # it leaves the cockpit's LIVE AGENTS rather than lingering (#742/#774).
    assert json.loads((stale / "status.json").read_text())["status"] == "failed"
    assert json.loads((fresh / "status.json").read_text())["status"] == "triaging"
    assert json.loads((settled / "status.json").read_text())["status"] == "triaged"


def test_sweep_empty_workspace_returns_empty(tmp_path: Path) -> None:
    assert sweep(tmp_path, now=_NOW) == []


def test_sweep_uses_env_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _spec(
        tmp_path,
        "p1",
        "s",
        status="evaluating",
        updated_at=_iso(_NOW - timedelta(seconds=5000)),
    )
    monkeypatch.setenv("TFACTORY_WORKSPACE_ROOT", str(tmp_path))
    assert default_workspace_root() == tmp_path
    # No explicit root → resolves from env.
    results = sweep(now=_NOW, deadline_seconds=900)
    assert len(results) == 1 and results[0][1].stalled is True


# ── CLI ─────────────────────────────────────────────────────────────────────


def test_main_reports_and_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # main() uses real wall-clock (no now injection), so pin updated_at far in
    # the past — always older than any deadline regardless of when CI runs.
    _spec(
        tmp_path,
        "p1",
        "s",
        status="planning",
        updated_at="2020-01-01T00:00:00+00:00",
    )
    monkeypatch.setenv("TFACTORY_WORKSPACE_ROOT", str(tmp_path))
    rc = main(["--deadline", "900"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "STALLED" in out
    assert "flagged 1 stalled" in out


# ── reconcile_inline_orphans (#774) ─────────────────────────────────────────


def test_reconcile_fails_inline_stranded_specs(tmp_path: Path) -> None:
    gen = _spec(
        tmp_path,
        "p1",
        "s-gen",
        status="generating",
        updated_at=_iso(_NOW - timedelta(seconds=901)),
    )
    plan = _spec(
        tmp_path,
        "p1",
        "s-plan",
        status="planning",
        updated_at=_iso(_NOW - timedelta(seconds=901)),
    )

    failed = reconcile_inline_orphans(tmp_path, now=_NOW)

    assert {d for d, _ in failed} == {gen, plan}
    for d, prior in failed:
        st = json.loads((d / "status.json").read_text())
        assert st["status"] == "failed"
        assert st["orphaned_from"] == prior
        assert st["phase"] == "control_plane_restart"
        assert "#774" in st["failed_reason"]


def test_reconcile_leaves_job_backed_and_settled_untouched(tmp_path: Path) -> None:
    """The critical safety property: a control-plane roll does NOT kill a live
    verify Job. evaluating/triaging/reviewing run in (or are reaped as) Jobs and
    must survive; terminal + not-yet-started statuses are none of our business."""
    from agents.liveness_sweep import reconcile_inline_orphans

    untouched = {
        "evaluating": _spec(tmp_path, "p1", "s-eval", status="evaluating"),
        "triaging": _spec(tmp_path, "p1", "s-tri", status="triaging"),
        "reviewing": _spec(tmp_path, "p1", "s-rev", status="reviewing"),
        "generated": _spec(tmp_path, "p1", "s-done", status="generated"),
        "pending": _spec(tmp_path, "p1", "s-pend", status="pending"),
        "triaged": _spec(tmp_path, "p1", "s-fin", status="triaged"),
    }

    failed = reconcile_inline_orphans(tmp_path, now=_NOW)

    assert failed == []
    for status, d in untouched.items():
        assert json.loads((d / "status.json").read_text())["status"] == status


def test_reconcile_skips_missing_or_corrupt_status(tmp_path: Path) -> None:
    from agents.liveness_sweep import reconcile_inline_orphans

    _spec(tmp_path, "p1", "s-nostatus")  # dir, no status.json
    bad = _spec(tmp_path, "p1", "s-bad", status="generating", updated_at=_iso(_NOW))
    (bad / "status.json").write_text("{not json")  # corrupt → skipped, not raised

    assert reconcile_inline_orphans(tmp_path, now=_NOW) == []


@pytest.mark.parametrize("stage", ["planning", "generating"])
def test_reconcile_keeps_other_live_stage(tmp_path: Path, stage: str) -> None:
    spec = _spec(tmp_path, "p", "s", status=stage, owner_instance="A")
    beat(tmp_path, "A")
    os.utime(tmp_path / "instances" / "A", (_NOW.timestamp(), _NOW.timestamp()))
    assert reconcile_inline_orphans(tmp_path, now=_NOW, instance_id="B") == []
    assert json.loads((spec / "status.json").read_text())["status"] == stage


@pytest.mark.parametrize("heartbeat", ["stale", "missing"])
def test_reconcile_fails_dead_owner(tmp_path: Path, heartbeat: str) -> None:
    spec = _spec(tmp_path, "p", "s", status="planning", owner_instance="A")
    if heartbeat == "stale":
        beat(tmp_path, "A")
        old = (_NOW - timedelta(seconds=61)).timestamp()
        os.utime(tmp_path / "instances" / "A", (old, old))
    assert reconcile_inline_orphans(tmp_path, now=_NOW, instance_id="B") == [
        (spec, "planning")
    ]
    status = json.loads((spec / "status.json").read_text())
    assert status["phase"] == "control_plane_restart"


def test_reconcile_keeps_own_and_fresh_legacy_stages(tmp_path: Path) -> None:
    own = _spec(tmp_path, "p", "own", status="planning", owner_instance="A")
    legacy = _spec(tmp_path, "p", "legacy", status="generating", updated_at=_iso(_NOW))
    assert reconcile_inline_orphans(tmp_path, now=_NOW, instance_id="A") == []
    assert json.loads((own / "status.json").read_text())["status"] == "planning"
    assert json.loads((legacy / "status.json").read_text())["status"] == "generating"


def test_reconcile_aborts_when_owner_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import agents.liveness_sweep as sweep_module

    spec = _spec(tmp_path, "p", "s", status="planning", owner_instance="A")
    original = sweep_module.instance_alive

    def owner_changed(*args: object, **kwargs: object) -> bool:
        (spec / "status.json").write_text(
            json.dumps({"status": "planning", "owner_instance": "C"})
        )
        return original(*args, **kwargs)

    monkeypatch.setattr(sweep_module, "instance_alive", owner_changed)
    assert reconcile_inline_orphans(tmp_path, now=_NOW, instance_id="B") == []
    assert json.loads((spec / "status.json").read_text())["owner_instance"] == "C"


def test_status_patch_stamps_inline_owner_only(tmp_path: Path) -> None:
    spec = _spec(tmp_path, "p", "s", status="pending")
    write_status_patch(spec, "planner", status="planning")
    assert (
        json.loads((spec / "status.json").read_text())["owner_instance"] == INSTANCE_ID
    )
    other = _spec(tmp_path, "p", "other", status="pending")
    write_status_patch(other, "planner", status="planned")
    assert "owner_instance" not in json.loads((other / "status.json").read_text())


def test_drain_returns_when_own_stage_finishes(tmp_path: Path) -> None:
    spec = _spec(tmp_path, "p", "s", status="planning", owner_instance="A")
    beat(tmp_path, "A")

    def finish(_seconds: float) -> None:
        (spec / "status.json").write_text(
            json.dumps({"status": "planned", "owner_instance": "A"})
        )

    assert (
        drain_own_inline_stages(tmp_path, timeout=10, instance_id="A", sleep=finish)
        == []
    )
    assert not (tmp_path / "instances" / "A").exists()


def test_drain_fails_leftover_after_timeout(tmp_path: Path) -> None:
    spec = _spec(tmp_path, "p", "s", status="generating", owner_instance="A")
    beat(tmp_path, "A")
    ticks = iter([0.0, 0.0, 3.0])
    assert drain_own_inline_stages(
        tmp_path,
        timeout=2,
        instance_id="A",
        sleep=lambda _: None,
        monotonic=lambda: next(ticks),
    ) == [spec]
    status = json.loads((spec / "status.json").read_text())
    assert status["status"] == "failed"
    assert status["phase"] == "control_plane_restart"
    assert "shut down mid-generating" in status["failed_reason"]
    assert not (tmp_path / "instances" / "A").exists()


def test_other_instance_alive(tmp_path: Path) -> None:
    beat(tmp_path, "A")
    os.utime(tmp_path / "instances" / "A", (_NOW.timestamp(), _NOW.timestamp()))
    assert other_instance_alive(tmp_path, now=_NOW, instance_id="B")
    assert not other_instance_alive(tmp_path, now=_NOW, instance_id="A")


def test_blocked_reconcile_does_not_block_beat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import agents.liveness_sweep as sweep_module

    _spec(tmp_path, "p", "s", status="planning", owner_instance="A")
    entered = threading.Event()
    release = threading.Event()

    def slow_alive(*_args: object, **_kwargs: object) -> bool:
        entered.set()
        assert release.wait(2)
        return True

    monkeypatch.setattr(sweep_module, "instance_alive", slow_alive)
    thread = threading.Thread(
        target=reconcile_inline_orphans,
        args=(tmp_path,),
        kwargs={"now": _NOW, "instance_id": "B"},
    )
    thread.start()
    try:
        assert entered.wait(2)
        beat(tmp_path, "B")
        assert (tmp_path / "instances" / "B").is_file()
    finally:
        release.set()
        thread.join(2)
    assert not thread.is_alive()


# ── gc_terminal_worktrees (#742) ────────────────────────────────────────────


def _spec_with_worktree(root, project, spec, status):
    d = _spec(root, project, spec, status=status, updated_at=_iso(_NOW))
    wt = d / ".worktree"
    wt.mkdir(parents=True)
    (wt / "built.py").write_text("x = 1")
    return d, wt


def test_gc_removes_terminal_worktrees(tmp_path: Path) -> None:
    from agents.liveness_sweep import gc_terminal_worktrees

    triaged, wt_t = _spec_with_worktree(tmp_path, "p1", "s-triaged", "triaged")
    failed, wt_f = _spec_with_worktree(tmp_path, "p1", "s-failed", "failed")

    reclaimed = gc_terminal_worktrees(tmp_path)

    assert set(reclaimed) == {triaged, failed}
    assert not wt_t.exists() and not wt_f.exists()
    # The spec dirs + status.json survive — only the worktree is reclaimed.
    assert (triaged / "status.json").exists()


def test_gc_keeps_active_and_worktreeless(tmp_path: Path) -> None:
    """A worktree of a still-active spec must NOT be GC'd (its verify may still
    run); a terminal spec with no worktree is a no-op."""
    from agents.liveness_sweep import gc_terminal_worktrees

    _, wt_gen = _spec_with_worktree(tmp_path, "p1", "s-gen", "generating")
    _, wt_eval = _spec_with_worktree(tmp_path, "p1", "s-eval", "evaluating")
    _spec(tmp_path, "p1", "s-done-nowt", status="triaged", updated_at=_iso(_NOW))

    reclaimed = gc_terminal_worktrees(tmp_path)

    assert reclaimed == []
    assert wt_gen.exists() and wt_eval.exists()
