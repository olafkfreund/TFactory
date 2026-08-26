"""The jest lane must stage its test into the worktree at the authored path.

TFactory#1195: `run_jest_lane_via_nix` computed the test's path relative to
`project_dir`, but the generated test lives under `<spec_dir>/tests/...` while
project_dir IS `<spec_dir>/.worktree`. That raised ValueError and the fallback
handed jest a BARE FILENAME, which matches no test path -- reproduced live:
`Pattern: winner-draw-r3.test.ts - 0 matches`, exit 1. So the lane failed even
with jest installed.
"""

from __future__ import annotations

import json
from pathlib import Path

from agents import nix_env


def _spec(tmp_path):
    """The real layout: SUT in .worktree, generated test in the spec dir."""
    spec = tmp_path / "spec"
    (spec / "context").mkdir(parents=True)
    (spec / "context" / "source.json").write_text(json.dumps({"target_paths": []}))
    (spec / "tests" / "unit").mkdir(parents=True)
    tf = spec / "tests" / "unit" / "winner-draw.test.ts"
    tf.write_text("// generated")
    wt = spec / ".worktree"
    (wt / "games" / "tictactoe").mkdir(parents=True)
    (wt / "games" / "tictactoe" / "game.js").write_text("module.exports={};")
    return spec, tf, wt


def _stub(monkeypatch, cap):
    class _Res:
        returncode = 0
        stdout = "__PYTEST_RUN=1\n__PYTEST_EXIT=0\n"

    class _Sandbox:
        def run(self, cmds, **kw):
            wd = Path(kw["workdir"])
            cap["script"] = (wd / nix_env._JOB_SCRIPT).read_text()
            # What the worktree looks like AT RUN TIME, before cleanup.
            cap["staged"] = sorted(
                p.relative_to(wd).as_posix() for p in wd.rglob("*.test.ts")
            )
            return _Res()

    monkeypatch.setattr(nix_env, "materialize_flake", lambda *a, **k: object())
    monkeypatch.setattr(nix_env, "nix_runner_from_env", lambda: _Sandbox())


def test_the_test_is_staged_at_its_authored_path(tmp_path, monkeypatch):
    spec, tf, wt = _spec(tmp_path)
    cap: dict = {}
    _stub(monkeypatch, cap)

    nix_env.run_jest_lane_via_nix(tf, wt, spec)

    assert cap["staged"] == ["tests/unit/winner-draw.test.ts"], (
        "the test must exist in the worktree at its authored path so its own "
        f"'../../games/...' import resolves; got {cap['staged']}"
    )


def test_jest_is_given_the_relative_path_not_a_bare_name(tmp_path, monkeypatch):
    """A bare filename matches no test path -- jest reports '0 matches'."""
    spec, tf, wt = _spec(tmp_path)
    cap: dict = {}
    _stub(monkeypatch, cap)

    nix_env.run_jest_lane_via_nix(tf, wt, spec)

    assert "tests/unit/winner-draw.test.ts" in cap["script"], cap["script"]


def test_the_staged_copy_is_removed_afterwards(tmp_path, monkeypatch):
    """A leftover copy would be picked up by the NEXT lane as a repo file."""
    spec, tf, wt = _spec(tmp_path)
    cap: dict = {}
    _stub(monkeypatch, cap)

    nix_env.run_jest_lane_via_nix(tf, wt, spec)

    assert not (wt / "tests" / "unit" / "winner-draw.test.ts").exists()


def test_the_runner_is_installed_before_jest_is_invoked(tmp_path, monkeypatch):
    """TFactory#1195: the lane shell had NO jest -- `jest: command not found`,
    exit 127 -- so every jest test read `consistent_fail` for weeks and the
    import path was never even evaluated.

    nixpkgs has no jest package, so the flake cannot supply it; the job installs
    it from npm using the flake's node.
    """
    spec, tf, wt = _spec(tmp_path)
    cap: dict = {}
    _stub(monkeypatch, cap)

    nix_env.run_jest_lane_via_nix(tf, wt, spec)
    script = cap["script"]

    assert "npm install -g" in script, script
    assert "jest@29" in script and "ts-jest@29" in script, script
    # The install must come BEFORE the first jest invocation.
    assert script.index("npm install -g") < script.index("jest --ci"), script


def test_a_failed_runner_install_fails_loudly(tmp_path, monkeypatch):
    """A silent install failure is exactly how exit 127 went unnoticed."""
    spec, tf, wt = _spec(tmp_path)
    cap: dict = {}
    _stub(monkeypatch, cap)

    nix_env.run_jest_lane_via_nix(tf, wt, spec)
    script = cap["script"]

    assert "__JEST_SETUP_FAILED" in script, script
    assert "exit 127" in script, script


def test_the_runner_is_installed_once_not_per_sample(tmp_path, monkeypatch):
    """Installing inside the rerun loop would pay it 3x per spec."""
    spec, tf, wt = _spec(tmp_path)
    cap: dict = {}
    _stub(monkeypatch, cap)

    nix_env.run_jest_lane_via_nix(tf, wt, spec, reruns=3)
    script = cap["script"]

    assert script.count("npm install -g") == 1, script
    assert script.count("jest --ci") == 3, script
