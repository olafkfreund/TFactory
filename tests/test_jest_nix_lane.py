"""The jest lane has an in-cluster path.

TFactory#1165: `_resolve_jest_runner_fn` returned a DockerRunner-only runner and
TFactory pods have no container runtime, so every JavaScript/TypeScript unit test
errored before it started -- spec 161 reported `unit: error` for all 8 subtasks.
"""

from __future__ import annotations

from pathlib import Path

from agents import nix_env
from agents.evaluator import _resolve_jest_runner_fn
from tools.runners.docker_runner import DockerRunResult


def test_jest_runner_prefers_the_nix_lane(monkeypatch, tmp_path):
    sentinel = DockerRunResult(returncode=0, stdout="ok", stderr="", argv=["nix"])
    seen: list[tuple] = []

    def fake(test_file, project_dir, spec_dir, **kw):
        seen.append((Path(test_file).name, Path(spec_dir)))
        return sentinel

    monkeypatch.setattr(nix_env, "run_jest_lane_via_nix", fake)
    run = _resolve_jest_runner_fn(spec_dir=tmp_path)
    got = run(tmp_path / "move-places-mark.test.ts", tmp_path, 0)

    assert got is sentinel, "the docker path cannot run in-cluster"
    assert seen == [("move-places-mark.test.ts", tmp_path)]


def test_jest_nix_lane_runs_bare_jest_not_npx():
    """npx re-fetches from the npm registry and undoes the flake (TFactory#1152)."""
    import ast
    import inspect
    import textwrap

    src = textwrap.dedent(inspect.getsource(nix_env.run_jest_lane_via_nix))
    fn = ast.parse(src).body[0]
    assert isinstance(fn, ast.FunctionDef)
    fn.body = fn.body[1:]  # drop the docstring -- it *names* npx to warn about it
    body = ast.unparse(fn)
    assert "npx" not in body
    assert "jest --ci" in body
