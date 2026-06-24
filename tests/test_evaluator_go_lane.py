"""Tests for the Evaluator Go lane (Go unit tests via the per-task Nix shell).

The Go lane mirrors the Jest lane: a unit-lane subtask whose ``language`` is
``go`` is dispatched to ``run_gotest_lane_via_nix`` (gotestsum ./... inside the
per-task Nix dev shell) instead of the pytest runner. Go ``_test.go`` files are
staged into the worktree at their repo-relative paths first, then the whole
module runs.

These exercise the pure dispatch seams without Docker/Kubernetes:
  - ``_completed_go_subtasks`` filters language==go unit subtasks (and excludes
    python/typescript so they aren't double-dispatched)
  - ``_stage_go_test`` copies generated ``_test.go`` into the worktree
  - ``_resolve_go_runner_fn`` passes a repo-relative hint and degrades to a
    failing result when the Nix-lane sandbox is unconfigured
  - ``_build_all_bundles`` stages + builds a Go signal bundle
"""

from __future__ import annotations

from pathlib import Path

from agents.evaluator import (
    _build_all_bundles,
    _completed_go_subtasks,
    _resolve_go_runner_fn,
    _stage_go_test,
)
from tools.runners.docker_runner import DockerRunResult


def _plan(*subtasks: dict) -> dict:
    return {"phases": [{"phase": 1, "name": "main", "subtasks": list(subtasks)}]}


def _go_subtask(stid: str = "st-go-0") -> dict:
    return {
        "id": stid,
        "status": "completed",
        "lane": "unit",
        "language": "go",
        "target": "greeting.Greet",
        "rationale": "AC#1",
        "files_to_create": ["scenarios/go-hello/greeting/greet_test.go"],
    }


# ── _completed_go_subtasks ─────────────────────────────────────────────


def test_completed_go_subtasks_picks_go_unit_only() -> None:
    plan = _plan(
        _go_subtask("st-go"),
        {  # python unit — belongs to the pytest lane, not here
            "id": "st-py",
            "status": "completed",
            "lane": "unit",
            "language": "python",
            "files_to_create": ["tests/test_x.py"],
        },
        {  # typescript unit — belongs to the Jest lane, not here
            "id": "st-ts",
            "status": "completed",
            "lane": "unit",
            "language": "typescript",
            "files_to_create": ["x.test.ts"],
        },
        {  # go but not completed — excluded
            "id": "st-go-pending",
            "status": "pending",
            "lane": "unit",
            "language": "go",
            "files_to_create": ["y_test.go"],
        },
    )
    picked = _completed_go_subtasks(plan)
    assert [s["id"] for s in picked] == ["st-go"]


def test_completed_go_subtasks_accepts_functional_alias() -> None:
    st = _go_subtask()
    st["lane"] = "functional"  # v0.1 deprecated alias
    assert [s["id"] for s in _completed_go_subtasks(_plan(st))] == ["st-go-0"]


# ── _stage_go_test ─────────────────────────────────────────────────────


def test_stage_go_test_copies_into_worktree(tmp_path: Path) -> None:
    spec_dir = tmp_path / "spec"
    project_dir = tmp_path / "project"
    rel = "scenarios/go-hello/greeting/greet_test.go"
    src = spec_dir / rel
    src.parent.mkdir(parents=True)
    src.write_text("package greeting\n")

    _stage_go_test(spec_dir, project_dir, _go_subtask())

    dst = project_dir / rel
    assert dst.is_file()
    assert dst.read_text() == "package greeting\n"


def test_stage_go_test_skips_missing_source(tmp_path: Path) -> None:
    # No source file written — staging must be a silent no-op, not an error.
    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()
    project_dir = tmp_path / "project"
    _stage_go_test(spec_dir, project_dir, _go_subtask())
    assert not (project_dir / "scenarios").exists()


# ── _resolve_go_runner_fn ──────────────────────────────────────────────


def test_go_runner_fails_closed_when_sandbox_unconfigured(
    tmp_path: Path, monkeypatch
) -> None:
    # run_gotest_lane_via_nix returns None (TFACTORY_NIX_RUNNER_IMAGE unset);
    # the runner must report a failure, never a silent pass.
    monkeypatch.setattr("agents.nix_env.run_gotest_lane_via_nix", lambda *a, **k: None)
    runner = _resolve_go_runner_fn(tmp_path / "spec", tmp_path / "project")
    res = runner(tmp_path / "spec" / "x_test.go", tmp_path / "project", 0)
    assert res.returncode == 1
    assert not res.ok
    assert "unavailable" in res.stderr


def test_go_runner_passes_repo_relative_hint(tmp_path: Path, monkeypatch) -> None:
    spec_dir = tmp_path / "spec"
    project_dir = tmp_path / "project"
    captured: dict = {}

    def _fake(spec, proj, *, hint=None, **k):
        captured["spec"] = spec
        captured["proj"] = proj
        captured["hint"] = hint
        return DockerRunResult(returncode=0, stdout="ok", argv=["go", "test"])

    monkeypatch.setattr("agents.nix_env.run_gotest_lane_via_nix", _fake)
    runner = _resolve_go_runner_fn(spec_dir, project_dir)
    test_file = spec_dir / "scenarios/go-hello/greeting/greet_test.go"
    res = runner(test_file, project_dir, 7)

    assert res.ok
    # the hint handed to the module resolver is repo-relative, not under spec_dir
    assert captured["hint"] == Path("scenarios/go-hello/greeting/greet_test.go")
    assert captured["spec"] == spec_dir


# ── _build_all_bundles (Go branch) ─────────────────────────────────────


def test_build_all_bundles_stages_and_builds_go_bundle(
    tmp_path: Path, monkeypatch
) -> None:
    spec_dir = tmp_path / "spec"
    project_dir = tmp_path / "project"
    rel = "scenarios/go-hello/greeting/greet_test.go"
    src = spec_dir / rel
    src.parent.mkdir(parents=True)
    src.write_text("package greeting\n")

    # Mock the Nix lane so no k8s Job runs; assert it's invoked once per
    # stability pass over the staged module.
    calls: list[Path] = []

    def _fake(spec, proj, *, hint=None, **k):
        calls.append(Path(proj))
        return DockerRunResult(returncode=0, stdout="PASS", argv=["go", "test"])

    monkeypatch.setattr("agents.nix_env.run_gotest_lane_via_nix", _fake)

    go = _completed_go_subtasks(
        {"phases": [{"phase": 1, "name": "m", "subtasks": [_go_subtask()]}]}
    )
    bundles = _build_all_bundles(spec_dir, project_dir, [], [], [], [], go)

    # The generated test was staged into the worktree before running.
    assert (project_dir / rel).is_file()
    # One bundle built for the single Go subtask.
    assert len(bundles) == 1
    assert bundles[0].test_id == "st-go-0"
    # The Nix lane ran (3× stability → at least one call).
    assert calls and all(c == project_dir for c in calls)


def test_go_module_stability_computed_once_and_shared(tmp_path, monkeypatch):
    # `go test ./...` is module-wide, so its stability is identical for every Go
    # unit subtask. The evaluator must compute it ONCE (3 reruns) and share it
    # across the module's subtasks — NOT 3 Nix Jobs per subtask (which a single
    # transient dispatch error would flip to stability=ERROR → accept downgrades
    # to flag).
    spec_dir = tmp_path / "spec"
    project_dir = tmp_path / "project"
    subtasks = []
    for i in range(3):
        rel = f"greet_{i}_test.go"
        (spec_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        (spec_dir / rel).write_text("package main\n")
        subtasks.append(
            {
                "id": f"st-go-{i}",
                "status": "completed",
                "lane": "unit",
                "language": "go",
                "files_to_create": [rel],
            }
        )

    calls = []

    def _fake(spec, proj, *, hint=None, **k):
        calls.append(hint)
        return DockerRunResult(returncode=0, stdout="PASS", argv=["go", "test"])

    monkeypatch.setattr("agents.nix_env.run_gotest_lane_via_nix", _fake)

    go = _completed_go_subtasks(
        {"phases": [{"phase": 1, "name": "m", "subtasks": subtasks}]}
    )
    bundles = _build_all_bundles(spec_dir, project_dir, [], [], [], [], go)

    # A bundle per subtask, but the module test ran ONCE (3 stability reruns),
    # not 3× per subtask (which would be 9 calls for 3 subtasks).
    assert len(bundles) == 3
    assert len(calls) == 3  # 3 reruns of the single shared module stability
    # Every bundle shares the SAME stability object (computed once).
    stabilities = {id(b.stability) for b in bundles}
    assert len(stabilities) == 1
