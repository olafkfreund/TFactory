"""The jest lane's 3 stability samples run in ONE nix shell.

TFactory#1187: `_nix_batched_stability` called the pytest lane unconditionally,
which cannot run a `.test.ts`, so every jest subtask fell through to the
per-sample route -- three `nix develop` entries. Measured on a live lane Job:
44s mean between consecutive shell entries (+/-3s over 6 gaps), while a jest unit
test runs in milliseconds. A 13-test spec paid ~29 minutes of pure setup.
"""

from __future__ import annotations

from pathlib import Path

from agents import nix_env


def _stub(monkeypatch, captured: dict):
    class _Res:
        returncode = 0
        stdout = ""

    class _Sandbox:
        def run(self, cmds, **kw):
            captured["script"] = (Path(kw["workdir"]) / nix_env._JOB_SCRIPT).read_text()
            return _Res()

    monkeypatch.setattr(nix_env, "materialize_flake", lambda *a, **k: object())
    monkeypatch.setattr(nix_env, "nix_runner_from_env", lambda: _Sandbox())


def test_three_samples_share_one_shell(tmp_path, monkeypatch):
    cap: dict = {}
    _stub(monkeypatch, cap)
    (tmp_path / "tests").mkdir()
    tf = tmp_path / "tests" / "x.test.ts"
    tf.write_text("// t")

    nix_env.run_jest_lane_via_nix(tf, tmp_path, tmp_path, reruns=3)
    script = cap["script"]

    # ONE script -> ONE `nix develop` entry, three passes inside it.
    assert script.count("__PYTEST_RUN=") == 3, script
    assert script.count("jest --ci") == 3, script


def test_markers_are_the_ones_parse_pytest_exits_reads(tmp_path, monkeypatch):
    """Per-run codes must survive, or stability grading degrades silently.

    Feeds the ACTUAL generated script's markers through the real parser -- an
    earlier version of this test built its own fake string, so renaming the
    marker left it green.
    """
    cap: dict = {}
    _stub(monkeypatch, cap)
    (tmp_path / "tests").mkdir()
    tf = tmp_path / "tests" / "x.test.ts"
    tf.write_text("// t")
    nix_env.run_jest_lane_via_nix(tf, tmp_path, tmp_path, reruns=3)

    # Replay the generated script as if the shell had run it, substituting a
    # real exit code for each pass.
    out, codes = [], [0, 1, 0]
    n = 0
    for line in cap["script"].splitlines():
        if line.startswith("echo __PYTEST_RUN="):
            out.append(line.removeprefix("echo "))
        elif "__PYTEST_EXIT=$?" in line:
            out.append(f"__PYTEST_EXIT={codes[n]}")
            n += 1
    assert n == 3, f"generated script did not emit 3 exit markers: {cap['script']!r}"
    assert [c for c, _ in nix_env.parse_pytest_exits("\n".join(out))] == codes


def test_a_jest_subtask_is_routed_to_the_batched_lane(tmp_path, monkeypatch):
    """The fix itself: jest must reach the batched path, not fall through.

    `_nix_batched_stability` called the pytest lane unconditionally, so a jest
    subtask returned None and took the three-shell per-sample route.
    """
    from agents import evaluator

    seen: dict = {}

    def _fake_jest(test_file, project_dir, spec_dir, *, reruns=1, **kw):
        seen["reruns"] = reruns
        return None  # None -> helper returns None; we only assert it was CALLED

    def _boom(*a, **k):
        raise AssertionError("pytest lane must not run a jest subtask")

    monkeypatch.setattr(evaluator, "run_jest_lane_via_nix", _fake_jest)
    monkeypatch.setattr(evaluator, "run_pytest_lane_via_nix", _boom)

    evaluator._nix_batched_stability(
        tmp_path, tmp_path, tmp_path / "x.test.ts", "unit", framework="jest"
    )
    assert seen.get("reruns") == 3, "jest samples must be batched, not per-sample"


def test_default_is_a_single_pass(tmp_path, monkeypatch):
    """Omitting reruns must not change existing callers."""
    cap: dict = {}
    _stub(monkeypatch, cap)
    (tmp_path / "tests").mkdir()
    tf = tmp_path / "tests" / "x.test.ts"
    tf.write_text("// t")
    nix_env.run_jest_lane_via_nix(tf, tmp_path, tmp_path)
    assert cap["script"].count("jest --ci") == 1


def test_nix_mode_accepts_a_repo_owned_flake(tmp_path):
    """TFactory#1187: nix mode read False for every contract-less card.

    `plan_nix_env` learned in #1152 that a repo-owned flake IS a nix env; this
    predicate never did. So spec-ingest and low/medium skip_planning cards took
    the per-sample route and paid 24 `nix develop` entries at ~45s -- 71% of a
    25-minute lane phase -- with the batched path never entered.
    """
    import os

    from agents import evaluator

    os.environ["TFACTORY_NIX_RUNNER_IMAGE"] = "img:test"
    try:
        spec = tmp_path / "spec"
        (spec / "context").mkdir(parents=True)
        proj = tmp_path / "proj"
        proj.mkdir()
        # No contract -> the old rule said False.
        assert evaluator._nix_verify_mode(spec, proj) is False
        (proj / "flake.nix").write_text("{ }")
        assert evaluator._nix_verify_mode(spec, proj) is True
    finally:
        os.environ.pop("TFACTORY_NIX_RUNNER_IMAGE", None)


def test_a_jest_subtask_reaches_the_batched_lane_through_the_real_route(
    tmp_path, monkeypatch
):
    """Enter via `_stability_for_subtask`, NOT `_nix_batched_stability` directly.

    The #1188 test called the batched helper directly, so it passed while
    production was unchanged: the gate above it (`_nix_verify_mode`) was False
    and the branch was never reached. It tested the branch, not the route.
    """
    import os

    from agents import evaluator

    os.environ["TFACTORY_NIX_RUNNER_IMAGE"] = "img:test"
    try:
        spec = tmp_path / "spec"
        (spec / "context").mkdir(parents=True)
        (spec / "tests").mkdir()
        (spec / "tests" / "x.test.ts").write_text("// t")
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "flake.nix").write_text("{ }")

        seen: dict = {}

        from tools.runners.docker_runner import DockerRunResult

        def _fake(test_file, project_dir, spec_dir, *, reruns=1, **kw):
            seen["reruns"] = reruns
            # Three passes, all green, in the markers parse_pytest_exits reads.
            out = "".join(f"__PYTEST_RUN={i}\nok\n__PYTEST_EXIT=0\n" for i in (1, 2, 3))
            return DockerRunResult(returncode=0, stdout=out, stderr="", argv=["nix"])

        monkeypatch.setattr(evaluator, "run_jest_lane_via_nix", _fake)
        monkeypatch.setattr(evaluator, "run_pytest_lane_via_nix", lambda *a, **k: None)

        evaluator._stability_for_subtask(
            spec,
            proj,
            {
                "framework": "jest",
                "lane": "unit",
                "files_to_create": ["tests/x.test.ts"],
            },
            lambda *a, **k: None,
        )
        assert seen.get("reruns") == 3, (
            "jest must reach the batched lane, not per-sample"
        )
    finally:
        os.environ.pop("TFACTORY_NIX_RUNNER_IMAGE", None)
