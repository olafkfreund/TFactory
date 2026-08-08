"""The Nix pytest lane must not leak an artifact dir per dispatched test (#997).

`run_pytest_lane_via_nix` copies junit.xml/coverage.xml off the PVC scratch into
a host temp dir so the returned paths outlive the scratch. That contract is
deliberate — the caller reads the files after the call returns — but nothing
ever dropped them, so one directory accumulated per dispatched test for the life
of the pod: per corpus entry per regression run, per generated test per
evaluation.

Bounded by the pod's /tmp emptyDir rather than by disk, so it was a slow fill
rather than an outage. These tests pin the bound.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from agents import nix_env  # noqa: E402


def _use_tmp_root(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "tf-nixjunit"
    monkeypatch.setattr(nix_env, "_JUNIT_ARTIFACTS_ROOT", root)
    return root


def _age(path: Path, seconds: float) -> None:
    """Backdate a dir's mtime, standing in for a run that finished long ago."""
    past = time.time() - seconds
    os.utime(path, (past, past))


def test_a_fresh_dir_is_created_per_run(tmp_path, monkeypatch):
    """Each run still gets its own dir — the paths must not collide."""
    _use_tmp_root(tmp_path, monkeypatch)
    a = nix_env._new_junit_artifacts_dir()
    b = nix_env._new_junit_artifacts_dir()
    assert a != b
    assert a.is_dir() and b.is_dir()


def test_dispatching_many_runs_does_not_accumulate(tmp_path, monkeypatch):
    """The leak itself: one dir per dispatched test, forever.

    Simulates a regression run over a corpus, ageing each entry as it completes.
    Before the fix nothing removed any of them.
    """
    root = _use_tmp_root(tmp_path, monkeypatch)
    for _ in range(25):
        d = nix_env._new_junit_artifacts_dir()
        (d / "junit.xml").write_text("<testsuite/>")
        _age(d, nix_env._JUNIT_ARTIFACTS_MAX_AGE_S + 60)
    # One more run sweeps everything the corpus left behind.
    live = nix_env._new_junit_artifacts_dir()
    assert list(root.iterdir()) == [live]


def test_a_run_in_flight_is_never_swept(tmp_path, monkeypatch):
    """A dir younger than the cutoff keeps its files, however many runs follow.

    This is the whole reason the sweep is age-based rather than "delete the
    previous one": the caller may still be reading it.
    """
    root = _use_tmp_root(tmp_path, monkeypatch)
    in_flight = nix_env._new_junit_artifacts_dir()
    (in_flight / "coverage.xml").write_text("<coverage/>")
    for _ in range(5):
        nix_env._new_junit_artifacts_dir()
    assert (in_flight / "coverage.xml").read_text() == "<coverage/>"
    assert in_flight in list(root.iterdir())


def test_sweep_removes_only_what_is_past_the_cutoff(tmp_path, monkeypatch):
    root = _use_tmp_root(tmp_path, monkeypatch)
    old = nix_env._new_junit_artifacts_dir()
    young = nix_env._new_junit_artifacts_dir()
    _age(old, nix_env._JUNIT_ARTIFACTS_MAX_AGE_S + 1)

    assert nix_env._sweep_stale_junit_artifacts() == 1
    assert not old.exists()
    assert young.is_dir()


def test_sweep_on_a_missing_root_is_a_no_op(tmp_path, monkeypatch):
    """First call in a fresh pod: nothing to sweep, and no error."""
    _use_tmp_root(tmp_path, monkeypatch)
    assert nix_env._sweep_stale_junit_artifacts() == 0


def test_the_max_age_outlasts_a_lane_timeout():
    """A run must never lose its artifacts while it is still running.

    The pytest lane's default timeout is 900s; the cutoff has to clear that plus
    the caller's aggregation, or the sweep becomes a flaky "coverage report
    disappeared" bug.
    """
    assert nix_env._JUNIT_ARTIFACTS_MAX_AGE_S > 900 * 4
